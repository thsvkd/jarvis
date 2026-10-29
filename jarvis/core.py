"""core: 설정 devices 의 게이트웨이(각 기기의 jarvis)를 10초마다 pull 해서 모은다.

- 스냅샷은 마지막 성공본을 들고 있다가 /api/devices(요약)·/api/devices/<id>/snapshot(그대로) 로 준다.
- history·changes 는 크기가 커서 모아 두지 않고 요청이 올 때 전달한다.
- Orca 페어링 코드(/api/orca)는 비밀이라 모아 두지 않는다. 페어링 버튼을 누를 때 그 기기 하나만 받아 QR 을 붙여 넘긴다.
- 게이트웨이는 기기마다 pull 시점이 달라 버전이 섞인다: 모르는 필드는 무시하고, 없는 API(404)는 '미지원'으로 본다.
"""

import gzip
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor

EVERY_S = 10
TIMEOUT_S = 5  # 연결·읽기 한 번
CHECK_TIMEOUT_S = 70  # 업데이트 확인은 게이트웨이가 릴리스 매니페스트를 받느라 60초까지 쓴다
PULL_TIMEOUT_S = 100  # jarvis 갱신은 게이트웨이가 git pull 을 90초까지 기다린다
USAGE_TIMEOUT_S = 70  # 사용량 새로고침은 게이트웨이 Orca 가 계정마다 프로바이더 API 를 부른다(45초 + 보관값 12초)
LOGIN_TIMEOUT_S = 40  # 게이트웨이가 CLI 의 URL·코드를 30초까지 기다린다
DEADLINE_S = 15  # 응답 하나 전체(조금씩 흘려 보내는 응답이 스레드를 붙잡지 못하게)
STALE_S = 3 * EVERY_S  # 이보다 오래된 스냅샷은 주지 않는다(기기 화면이 '응답 없음'을 띄우게)
MAX_BYTES = 16 << 20  # 압축을 푼 뒤 기준. 24h history 가 수 MB 다
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트는 따르지 않는다(HTTPError 가 된다). 따르면 장악된 게이트웨이가 core 를 루프백이나 다른 게이트웨이의
    /api/orca 로 보내, 코드가 viewers 제한이 없는 changes·history·snapshot 경로로 새어 나간다."""

    def redirect_request(self, *a, **k):
        return None


# 환경 변수 http_proxy 가 있어도 tailnet 으로 바로 간다
_open = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect).open


def devices_from(items):
    """설정 devices = [{id, url}, …] 중 형식이 맞는 것만."""
    out, seen = [], set()
    for d in items if isinstance(items, list) else []:
        if not isinstance(d, dict):
            print(f"[jarvis] core: devices 항목은 {{id, url}} 테이블이어야 한다: {d!r}"[:300], flush=True)
            continue
        i, u = str(d.get("id") or ""), str(d.get("url") or "").rstrip("/")
        if ID_RE.match(i) and re.match(r"^https?://[^/?#]+$", u) and i not in seen:
            seen.add(i)
            out.append({"id": i, "url": u})
        else:
            print(f"[jarvis] core: 잘못된 devices 항목은 건너뛴다: {d!r}"[:300], flush=True)
    return out


def fetch(url):
    """(json bytes, gzip bytes 또는 None). 크기·시간 상한을 넘기면 예외."""
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    end = time.monotonic() + DEADLINE_S
    chunks, size = [], 0
    with _open(req, timeout=TIMEOUT_S) as r:
        gz = r.headers.get("Content-Encoding") == "gzip"
        while chunk := r.read(64 << 10):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("응답이 너무 큽니다")
            if time.monotonic() > end:
                raise TimeoutError("응답이 너무 느립니다")
    body = b"".join(chunks)
    if not gz:
        return body, None
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = d.decompress(body, MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or d.unconsumed_tail:
        raise ValueError("응답이 너무 큽니다")
    if not d.eof:
        raise ValueError("gzip 응답이 잘렸습니다")
    return raw, body


def why(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        e = e.reason
    return (str(e) or type(e).__name__)[:200]


def qr_rows(text):
    try:
        import qrcode
    except ImportError:  # QR 없이 코드만 보인다
        return None
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_L)
    q.add_data(text)
    q.make(fit=True)
    return ["".join("1" if c else "0" for c in row) for row in q.get_matrix()]


def _summary(s):
    meta, summ, sysm = s.get("meta") or {}, s.get("summary") or {}, s.get("system") or {}
    root = next((d for m in ("/", "/System/Volumes/Data", "C:\\")  # macOS 는 사용자 데이터가 Data 볼륨, Windows 는 C:
                 for d in sysm.get("disks") or [] if isinstance(d, dict) and d.get("mount") == m), {})
    return {"host": meta.get("host"), "version": meta.get("version"), "commit": (s.get("monitor") or {}).get("commit"),
            "generated_at": meta.get("generated_at"),
            "status": summ.get("status"), "alerts": summ.get("alerts"), "services": summ.get("services"),
            "model": (sysm.get("host") or {}).get("model"), "cpu_pct": (sysm.get("cpu") or {}).get("usage_pct"),
            "mem_pct": (sysm.get("memory") or {}).get("usage_pct"), "disk_pct": root.get("usage_pct")}


class Core:
    def __init__(self, devices):
        self.devices = {d["id"]: d for d in devices}
        self.state = {i: {"ok_at": None, "error": None, "fails": 0, "summary": None, "snap": None} for i in self.devices}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=max(1, min(16, len(devices))), thread_name_prefix="jarvis-core")
        self._stop = threading.Event()
        self._busy = set()  # 지난 주기의 요청이 아직 끝나지 않은 기기(느린 기기가 다른 기기의 주기를 늦추지 않게)

    def start(self):
        threading.Thread(target=self._loop, name="jarvis-core", daemon=True).start()

    def shutdown(self):
        self._stop.set()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self):
        while not self._stop.is_set():
            for i in self.devices:
                with self.lock:
                    if i in self._busy:
                        continue
                    self._busy.add(i)
                self.pool.submit(self._poll_done, i)
            self._stop.wait(EVERY_S)

    def _poll_done(self, i):
        try:
            self.poll(i)
        finally:
            with self.lock:
                self._busy.discard(i)

    def poll(self, i):
        try:
            raw, gz = fetch(self.devices[i]["url"] + "/api/snapshot")
            summary = _summary(json.loads(raw))
        except Exception as e:  # 한 기기의 실패가 루프를 멈추지 않게
            with self.lock:
                st = self.state[i]
                st["error"], st["fails"] = why(e), st["fails"] + 1
            return
        with self.lock:
            self.state[i].update(ok_at=time.time(), error=None, fails=0, summary=summary,
                                 snap=(raw, gz or gzip.compress(raw, 5)))

    def has(self, i):
        return i in self.devices

    def list(self):
        with self.lock:
            rows = [{"id": i, "url": self.devices[i]["url"], "reachable": st["fails"] == 0 and st["ok_at"] is not None,
                     "ok_at": st["ok_at"], "error": st["error"], "fails": st["fails"], **(st["summary"] or {})}
                    for i, st in self.state.items()]
        return {"generated_at": time.time(), "devices": rows}

    def snapshot(self, i):
        """(json bytes, gzip bytes) 또는 (None, 이유). 오래된 스냅샷은 주지 않는다."""
        with self.lock:
            st = self.state[i]
            if st["snap"] and time.time() - st["ok_at"] <= STALE_S:
                return st["snap"]
            return None, st["error"] or "아직 받은 스냅샷이 없습니다"

    def relay(self, i, path):
        """history·changes: 그 게이트웨이 응답(json bytes)을 그대로."""
        return fetch(self.devices[i]["url"] + path)[0]

    def _ask(self, i, req, timeout, unsupported):
        """그 게이트웨이의 Orca 동작 하나를 그대로 전달한다. (http 상태, 응답). 404 는 갱신 전 게이트웨이(501)."""
        try:
            with _open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read(1 << 20))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 501, {"error": f"이 기기의 jarvis 는 {unsupported} 지원하지 않습니다(갱신 필요)"}
            try:
                return e.code, json.loads(e.read(1 << 16))
            except ValueError:
                return e.code, {"error": f"HTTP {e.code}"}
        except Exception as e:
            return 502, {"error": f"{i} 에 닿지 못했습니다: {why(e)}"}

    def orca_issue(self, i, scope):
        """발급 버튼: 그 게이트웨이 POST /api/orca/issue 를 그대로 전달한다. (http 상태, 응답)."""
        req = urllib.request.Request(self.devices[i]["url"] + "/api/orca/issue?scope=" + urllib.parse.quote(scope),
                                     data=b"", method="POST", headers={"X-Jarvis-Action": "orca-issue"})
        return self._ask(i, req, TIMEOUT_S, "자동 발급을")

    def orca_update_check(self, i):
        """확인 버튼. 그 게이트웨이 GET /api/orca/update. 설치나 재시작은 없다. (http 상태, 응답)."""
        return self._ask(i, urllib.request.Request(self.devices[i]["url"] + "/api/orca/update"), CHECK_TIMEOUT_S, "업데이트 확인을")

    def orca_update(self, i):
        """업데이트 버튼: 그 게이트웨이 POST /api/orca/update 를 그대로 전달한다. (http 상태, 응답)."""
        req = urllib.request.Request(self.devices[i]["url"] + "/api/orca/update", data=b"", method="POST",
                                     headers={"X-Jarvis-Action": "orca-update"})
        return self._ask(i, req, TIMEOUT_S, "Orca 업데이트를")

    def jarvis_update(self, i):
        """jarvis 업데이트 버튼: 그 게이트웨이 POST /api/jarvis/update. 받은 코드가 다르면 게이트웨이가 스스로 재시작한다."""
        req = urllib.request.Request(self.devices[i]["url"] + "/api/jarvis/update", data=b"", method="POST",
                                     headers={"X-Jarvis-Action": "jarvis-update"})
        return self._ask(i, req, PULL_TIMEOUT_S, "자체 갱신을")

    def jarvis_update_all(self):
        """기기마다 갱신한다. core 자신은 다른 기기가 다 끝난 뒤에 한다(먼저 재시작하면 이 요청이 끊긴다).
        아직 요약을 못 받은 기기도 core 자신일 수 있어 마지막에 둔다."""
        me = socket.gethostname().split(".")[0].lower()
        with self.lock:
            mine = {i for i, st in self.state.items()
                    if ((st["summary"] or {}).get("host") or me).split(".")[0].lower() == me}

        def one(i):
            code, body = self.jarvis_update(i)
            return {"id": i, "status": code, **(body if isinstance(body, dict) else {"error": str(body)})}

        others = [i for i in self.devices if i not in mine]
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(others)))) as pool:
            out = list(pool.map(one, others))
        return out + [one(i) for i in self.devices if i in mine]

    def usage(self, i, force=False):
        """AI 구독 사용량: 그 게이트웨이 GET /api/orca/usage. 모아 두지 않는다(계정 이메일이 들어 있다)."""
        req = urllib.request.Request(self.devices[i]["url"] + "/api/orca/usage" + ("?refresh=1" if force else ""))
        return self._ask(i, req, USAGE_TIMEOUT_S, "사용량 조회를")

    def usage_login(self, i, provider):
        """다시 로그인 버튼: 그 게이트웨이 POST /api/orca/login 을 그대로 전달한다(로그인 URL·일회용 코드)."""
        req = urllib.request.Request(self.devices[i]["url"] + "/api/orca/login?provider=" + urllib.parse.quote(provider),
                                     data=b"", method="POST", headers={"X-Jarvis-Action": "usage-login"})
        return self._ask(i, req, LOGIN_TIMEOUT_S, "다시 로그인을")

    def usage_login_status(self, i, provider):
        """다시 로그인 창이 완료를 알아채도록 그 게이트웨이 GET /api/orca/login 을 전달한다."""
        req = urllib.request.Request(self.devices[i]["url"] + "/api/orca/login?provider=" + urllib.parse.quote(provider))
        return self._ask(i, req, TIMEOUT_S, "다시 로그인을")

    def usage_all(self, force=False):
        """모든 기기의 사용량을 동시에 묻는다. 한 대가 느리거나 실패해도 나머지는 그대로 준다."""
        ids = list(self.devices)

        def one(i):
            with self.lock:
                host = (self.state[i]["summary"] or {}).get("host")
                err = self.state[i]["fails"] and self.state[i]["error"]
            # 폴링이 실패 중인 기기(꺼진 tailnet 피어)는 묻지 않는다. 물으면 70초를 기다려 섹션 전체가 늦게 뜬다
            code, body = (502, {"error": f"{i} 에 닿지 못했습니다: {err}"}) if err else self.usage(i, force)
            return {**(body if isinstance(body, dict) else {"error": str(body)}), "id": i, "host": host, "status": code}

        with ThreadPoolExecutor(max_workers=max(1, min(16, len(ids)))) as pool:
            return {"generated_at": time.time(), "devices": list(pool.map(one, ids))}

    def orca_update_all(self):
        """기기마다 업데이트를 시작한다. 한 대 실패가 나머지를 멈추지 않는다."""
        ids = list(self.devices)

        def one(i):
            code, body = self.orca_update(i)
            return {"id": i, "status": code, **(body if isinstance(body, dict) else {"error": str(body)})}

        with ThreadPoolExecutor(max_workers=max(1, min(8, len(ids)))) as pool:
            return list(pool.map(one, ids))

    def orca(self, i):
        """페어링 버튼. 게이트웨이 /api/orca 에 QR 행렬을 붙인다. 캐시하지 않는다(셸 권한 비밀이라 core 에 남기지 않는다)."""
        try:
            d = json.loads(fetch(self.devices[i]["url"] + "/api/orca")[0])
            if not isinstance(d, dict) or not isinstance(d.get("codes"), dict):
                raise ValueError("이상한 /api/orca 응답")
        except urllib.error.HTTPError as e:
            if e.code == 404:  # 아직 git pull 하지 않은 게이트웨이
                return {"supported": False, "error": "이 기기의 jarvis 는 Orca 페어링을 지원하지 않습니다(git pull 필요)"}
            if e.code == 403:
                return {"supported": False, "error": "이 기기 jarvis 설정 core 에 이 core 의 tailnet IP 가 없습니다"}
            raise
        for c in d["codes"].values():
            if isinstance(c, dict) and isinstance(c.get("url"), str):
                c["qr"] = qr_rows(c["url"])
        d["supported"] = True
        return d
