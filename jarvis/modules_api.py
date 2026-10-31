"""AI 셋업 화면의 API(docs/SPEC.md 의 "AI 셋업 화면"). 게이트웨이는 모듈 명령(`python -m jarvis module …`)을
하위 프로세스로 돌리고, core 는 기기마다 그것을 모으거나 전달한다.

module.cli 는 fd 1 을 표준 오류로 돌리므로 서버 스레드 안에서 부르지 않는다. apply 는 한 번에 하나만 돌린다
(Orca 설치처럼 몇 분 걸리고, 모듈끼리 영수증을 함께 쓴다).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import config, module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTL = {"orca": 60, "doctor": 600}  # status 캐시(초). doctor 는 하네스마다 --version 을 불러 수 초 걸린다
STATUS_TIMEOUT_S = 120
PLAN_TIMEOUT_S = 120
APPLY_TIMEOUT_S = 3600
PLAN_ID_RE = re.compile(r"[0-9a-f]{64}")
PLAN_TTL_S = 10  # 같은 계획 조회가 몰려도 하위 프로세스는 하나만 돈다
MARK = os.path.join(config.STATE_DIR, "module-apply.json")  # 진행 중 표시. 재시작 뒤 남아 있으면 중단된 것이다
# 화면에 viewers 가 아닌 기기에도 보일 status 키. doctor 의 details(시크릿 위치·경로)와 작업 결과 문구는 viewers 만 본다
PUBLIC_KEYS = ("module", "installed", "owned", "version", "latest", "health", "checked_at", "ok", "error")

_cache = {}  # 이름 -> (시각, 종료 코드, 응답)
_plans = {}  # 이름 -> (시각, http 상태, 계획)
_status_lock = threading.Lock()
_plan_lock = threading.Lock()
_job_lock = threading.Lock()
job = {}  # 마지막 apply: {module, plan_id, state: running/finished/interrupted, started_at, finished_at, code, result}


def run(*args, timeout):
    """(종료 코드, JSON 응답). 출력은 파이프가 아니라 임시 파일로 받는다(남은 손자가 파이프를 잡으면 시간 초과 뒤에도 막힌다).
    출력이 JSON 이 아니면 종료 코드만 내보내고 표준 오류의 마지막 줄은 게이트웨이 로그에만 남긴다."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            code = subprocess.run([sys.executable, "-m", "jarvis", "module", *args, "--json"], cwd=ROOT, stdout=out,
                                  stderr=err, timeout=timeout, stdin=subprocess.DEVNULL).returncode
        except subprocess.TimeoutExpired:
            return 1, {"module": args[0], "error": f"{timeout}초 안에 끝나지 않았습니다"}
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")
    try:
        body = json.loads(stdout)
        if isinstance(body, dict):
            return code, body
    except ValueError:
        pass
    last = (stderr.strip().splitlines() or ["출력이 없습니다"])[-1]
    print(f"[jarvis] module {' '.join(args[:2])}: 종료 코드 {code}, {last[:300]}", flush=True)
    return code or 1, {"module": args[0], "error": f"모듈 명령이 결과 없이 끝났습니다(종료 코드 {code}). 게이트웨이 로그를 보세요"}


def running():
    with _job_lock:
        return job.get("state") == "running"


def _busy():
    """이 서버에서 기기를 바꾸는 다른 작업. 모듈 적용과 동시에 돌면 서로를 끊거나(재시작) 같은 파일을 쓴다."""
    from . import orca_issue, orca_update, self_update
    if self_update._lock.locked():
        return "jarvis 갱신이 끝난 뒤에 적용하세요"
    if (orca_update.job or {}).get("state") == "running" or os.path.exists(orca_update.MARK):
        return "Orca 업데이트(복구·세션 검증 포함)가 끝난 뒤에 적용하세요"
    if (orca_issue.job or {}).get("state") == "running":
        return "페어링 코드 발급이 끝난 뒤에 적용하세요"
    return None


def _write_mark(record):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    tmp = MARK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f)
    os.replace(tmp, MARK)


def status(force=False):
    """게이트웨이 GET /api/modules: 모듈마다 status 와 마지막 apply 작업."""
    out = {}
    with _status_lock:  # 같은 점검을 동시에 두 번 돌리지 않는다
        for name in module.MODULES:
            at, code, body = _cache.get(name, (0, 0, None))
            if force or body is None or time.time() - at > TTL.get(name, 60):
                code, body = run(name, "status", timeout=STATUS_TIMEOUT_S)
                at = time.time()
                _cache[name] = (at, code, body)
            out[name] = {**body, "checked_at": at, "ok": code == 0}
    with _job_lock:
        if not job and os.path.exists(MARK):  # 적용 중에 게이트웨이가 재시작했다
            try:
                with open(MARK, encoding="utf-8") as f:
                    job.update(json.load(f), state="interrupted")
            except (OSError, ValueError):
                job.update(state="interrupted")
            os.remove(MARK)
        return {"modules": out, "job": dict(job) or None}


def public(body):
    """viewers 가 아닌 기기에 줄 모양: 모듈은 PUBLIC_KEYS 만, 작업은 상태와 결과 종류만."""
    mods = {k: {key: v[key] for key in PUBLIC_KEYS if key in v} for k, v in (body.get("modules") or {}).items()
            if isinstance(v, dict)}
    j = body.get("job")
    if isinstance(j, dict):
        j = {key: j[key] for key in ("module", "state", "started_at", "finished_at") if key in j} \
            | {"status": (j.get("result") or {}).get("status")}
    return {**{k: v for k, v in body.items() if k not in ("modules", "job")}, "modules": mods, "job": j}


def plan(name):
    """게이트웨이 GET /api/modules/<이름>/plan. (http 상태, 계획)."""
    if name not in module.MODULES:
        return 404, {"error": "not found"}
    with _plan_lock:
        at, status_code, body = _plans.get(name, (0, 0, None))
        if body is None or time.time() - at > PLAN_TTL_S:
            code, body = run(name, "plan", timeout=PLAN_TIMEOUT_S)
            status_code = 200 if code == 0 else 502
            _plans[name] = (time.time(), status_code, body)
    return status_code, body


def apply(name, plan_id):
    """게이트웨이 POST /api/modules/<이름>/apply?plan_id=…. 뒤에서 돌리고 바로 202 를 준다. (http 상태, 작업)."""
    if name not in module.MODULES:
        return 404, {"error": "not found"}
    if not PLAN_ID_RE.fullmatch(plan_id or ""):
        return 400, {"error": "plan_id 는 계획에서 받은 sha256 이어야 합니다"}
    with _job_lock:
        if job.get("state") == "running":
            return 409, {"error": f"{job['module']} 적용이 진행 중입니다", "job": dict(job)}
        why = _busy()
        if why:
            return 409, {"error": why}
        job.clear()
        job.update(module=name, plan_id=plan_id, state="running", started_at=time.time())
        snapshot = dict(job)
        _write_mark(snapshot)
    print(f"[jarvis] module apply 시작: {name} plan_id={plan_id[:12]}", flush=True)

    def work():
        code, body = run(name, "apply", "--plan-id", plan_id, timeout=APPLY_TIMEOUT_S)
        with _status_lock:
            _cache.pop(name, None)  # 바뀐 상태를 다음 조회에서 다시 읽는다
        with _plan_lock:
            _plans.pop(name, None)
        with _job_lock:
            job.update(state="finished", finished_at=time.time(), code=code, result=body)
            try:
                os.remove(MARK)
            except OSError:
                pass
        print(f"[jarvis] module apply 끝: {name} plan_id={plan_id[:12]} -> {body.get('status') or body.get('error')}",
              flush=True)

    threading.Thread(target=work, daemon=True, name=f"jarvis-module-{name}").start()
    return 202, snapshot


class CoreModules:
    """core: 기기마다 게이트웨이의 /api/modules 를 모으고, 계획·적용을 그대로 전달한다."""

    def __init__(self, core):
        self.core = core

    def _url(self, i, path):
        return self.core.devices[i]["url"] + path

    def all(self, force=False, full=False):
        """full 이 아니면(viewers 가 아니면) public() 모양만 준다."""
        ids = list(self.core.devices)

        def one(i):
            with self.core.lock:
                err = self.core.state[i]["fails"] and self.core.state[i]["error"]
                host = (self.core.state[i]["summary"] or {}).get("host")
            if err:  # 꺼진 기기는 묻지 않는다(기다리면 화면 전체가 늦는다)
                code, body = 502, {"error": f"{i} 에 닿지 못했습니다: {err}"}
            else:
                req = urllib.request.Request(self._url(i, "/api/modules" + ("?refresh=1" if force else "")),
                                             headers={"X-Jarvis-Action": "modules-refresh"} if force else {})
                code, body = self.core._ask(i, req, STATUS_TIMEOUT_S + 10, "AI 셋업 모듈을")
            body = body if isinstance(body, dict) else {"error": str(body)}
            return {**(body if full else public(body)), "id": i, "host": host, "status": code}

        with ThreadPoolExecutor(max_workers=max(1, min(16, len(ids)))) as pool:
            return {"generated_at": time.time(), "full": full, "devices": list(pool.map(one, ids))}

    def plan(self, i, name):
        req = urllib.request.Request(self._url(i, f"/api/modules/{urllib.parse.quote(name)}/plan"))
        return self.core._ask(i, req, PLAN_TIMEOUT_S + 10, "AI 셋업 모듈을")

    def apply(self, i, name, plan_id):
        req = urllib.request.Request(
            self._url(i, f"/api/modules/{urllib.parse.quote(name)}/apply?plan_id={urllib.parse.quote(plan_id)}"),
            data=b"", method="POST", headers={"X-Jarvis-Action": "module-apply"})
        return self.core._ask(i, req, 30, "AI 셋업 모듈을")
