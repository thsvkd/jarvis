#!/usr/bin/env python3
"""시험 포트로 모니터를 띄워 /api/snapshot·history·health 를 받고, 키 구조(스키마)를 기준선과 비교한다.

    python3 tools/snapcheck.py [--port 9391] [--baseline fixtures/pit5-baseline-20260928] [--save DIR] [--wait 20]

- 값·서비스 목록은 기기마다 다르므로 비교하지 않는다. 키 경로와 값의 종류(수·문자열·목록·객체)만 본다.
- 실패: 크래시·HTTP 오류, 재부팅 가드 계약 필드 누락, 기준선에 있고 부모도 있는데 빠진 키, 종류가 바뀐 키.
- 참고로만 출력: 기준선에 없는 새 키, 부모가 비어 있어서(이 기기에 해당 항목 없음) 비교할 수 없는 키.
- 기기 차이(예: s2 에는 Caddy·pm2 가 없어 라우트·pm2 전용 필드가 안 나옴)는 리팩토링 전 코드로 같은 기기에서 뽑은
  불일치 목록을 --known 으로 주면 걸러진다(--write-known 으로 만든다). 목록에 없는 새 불일치만 실패다.
"""

import argparse
import gzip
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 키가 이름·호스트·라벨인 맵: 키 자체는 비교하지 않는다
DYNAMIC = {".meta.collected_at", ".meta.errors", ".services[].probes", ".monitor.loops",
           ".services[].extra.monitor.loops", ".ingress.tunnel.responses_by_code", ".system.gpu.queues",
           ".system.clocks_mhz", ".ingress.traffic.last_1h.by_status", ".ingress.traffic.last_24h.by_status",
           ".system.disk_io.devices", ".tailscale.serve",
           ".ingress.traffic.last_1h.by_via", ".ingress.traffic.last_24h.by_via"}
# 2026-10-19 OpenClaw 를 지우며(Hermes 로 대체) 일부러 없앤 키: 기준선에 있어도 누락으로 치지 않는다
REMOVED = (".openclaw", ".summary.openclaw", ".tailscale.peers[].openclaw", ".topology_physical.nodes[].openclaw",
           ".topology.nodes[].access_nodes", ".services[].runtime.access_nodes", ".services[].runtime.ping_ms",
           ".services[].runtime.path", ".services[].runtime.os")
# pit5 재부팅 가드가 읽는 필드
CONTRACT = [".services[].id", ".services[].status", ".services[].probes", ".services[].runtime"]


def kind(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "num"
    return {str: "str", list: "list", dict: "dict"}[type(v)]


def schema(v, p="", out=None):
    """경로 → 관측된 종류 집합. 리스트 원소는 []로 합친다."""
    out = {} if out is None else out
    out.setdefault(p, set()).add(kind(v))
    if isinstance(v, dict):
        if p in DYNAMIC:
            for x in v.values():
                schema(x, p + ".*", out)
        else:
            for k, x in v.items():
                schema(x, f"{p}.{k}", out)
    elif isinstance(v, list):
        for x in v:
            schema(x, p + "[]", out)
    return out


def parent(p):
    if p.endswith("[]"):
        return p[:-2]
    return p.rsplit(".", 1)[0]


def compare(base, cur):
    """(errors, notes)"""
    errors, notes = [], []
    for p in sorted(base):
        if p.startswith(REMOVED):
            continue
        if p in cur:
            b, c = base[p] - {"null"}, cur[p] - {"null"}
            if b and c and not (b & c) and not ({"num", "bool"} >= b | c):
                errors.append(f"종류 변경 {p}: {sorted(b)} → {sorted(c)}")
            continue
        par = parent(p)
        while par and par not in cur:
            par = parent(par)
        # 부모가 비었거나 null 이면 이 기기에 해당 항목이 없어서 못 보는 것이다
        pk = cur.get(par, set())
        if p.startswith(par + "[]") and "list" in pk or pk <= {"null"} or par != parent(p):
            notes.append(f"비교 불가(부모 비어 있음) {p}")
        elif par in DYNAMIC or p.startswith(tuple(d + "." for d in DYNAMIC)):
            notes.append(f"동적 맵 {p}")
        else:
            errors.append(f"누락 {p}")
    for p in sorted(set(cur) - set(base)):
        notes.append(f"새 키 {p}")
    return errors, notes


def get(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 headers={"Host": f"127.0.0.1:{port}", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return json.loads(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9391)
    ap.add_argument("--baseline", default=os.path.join(ROOT, "fixtures", "pit5-baseline-20260928"))
    ap.add_argument("--module", default="jarvis")
    ap.add_argument("--save", help="받은 JSON 을 이 디렉터리에 저장")
    ap.add_argument("--wait", type=float, default=20, help="느린 수집기가 한 바퀴 돌 때까지 기다릴 초")
    ap.add_argument("--known", help="이미 알려진(기기 차이로 인한) 불일치 목록 파일")
    ap.add_argument("--write-known", help="이번 불일치 목록을 이 파일에 쓴다")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    if a.port == 9300:
        sys.exit("9300 은 운영 포트다. 시험 포트를 쓴다.")

    state = tempfile.mkdtemp(prefix="jarvis-check-")
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    env.update(JARVIS_PORT=str(a.port), JARVIS_BIND="127.0.0.1", JARVIS_STATE=state)
    log = open(os.path.join(state, "server.log"), "w+")
    proc = subprocess.Popen([sys.executable, "-m", a.module], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    fails = []
    try:
        t0 = time.time()
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"서버가 종료됨 (rc={proc.returncode})")
            try:
                get(a.port, "/api/health")
                break
            except OSError:
                if time.time() - t0 > 60:
                    raise RuntimeError("60초 안에 /api/health 가 응답하지 않음")
                time.sleep(0.5)
        time.sleep(a.wait)
        got = {n: get(a.port, p) for n, p in (("snapshot", "/api/snapshot"), ("history", "/api/history?range=1h"),
                                               ("health", "/api/health"))}
        if proc.poll() is not None:
            raise RuntimeError(f"응답 뒤 서버가 종료됨 (rc={proc.returncode})")
        if a.save:
            os.makedirs(a.save, exist_ok=True)
            for n, d in got.items():
                with open(os.path.join(a.save, n + ".json"), "w") as f:
                    json.dump(d, f, ensure_ascii=False, indent=1)
        snap = got["snapshot"]
        svc = snap.get("services") or []
        for f in CONTRACT:
            key = f.rsplit(".", 1)[1]
            if not svc or any(key not in s for s in svc):
                fails.append(f"계약 필드 누락: {f}")
        known = set(open(a.known).read().splitlines()) if a.known else set()
        seen = []
        print(f"서비스 {len(svc)}개, 수집 오류: {snap.get('meta', {}).get('errors') or '없음'}")
        for n, d in got.items():
            path = os.path.join(a.baseline, n + ".json")
            if not os.path.exists(path):
                print(f"[{n}] 기준선 없음, 건너뜀")
                continue
            with open(path) as f:
                errors, notes = compare(schema(json.load(f)), schema(d))
            errors = [f"[{n}] {e}" for e in errors]
            seen += errors
            print(f"[{n}] 스키마 불일치 {len(errors)}건(알려진 기기 차이 {len(set(errors) & known)}건), 참고 {len(notes)}건")
            errors = [e for e in errors if e not in known]
            for e in errors:
                print("  ✗", e)
            for x in notes if a.verbose else [x for x in notes if x.startswith("새 키") and ".*" not in x]:
                print("  ·", x)
            fails += errors
        if a.write_known:
            with open(a.write_known, "w") as f:
                f.write("".join(e + "\n" for e in seen))
    except Exception as e:
        fails.append(f"{type(e).__name__}: {e}")
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.seek(0)
        out = log.read()
        crash = "Traceback" in out
        if crash or fails:
            print("---- 서버 로그 ----\n" + out[-4000:])
        if crash:
            fails.append("서버 로그에 Traceback")
    print("결과:", "OK" if not fails else f"실패 {len(fails)}건")
    for f in fails:
        print("  ✗", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
