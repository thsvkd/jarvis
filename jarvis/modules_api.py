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
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTL = {"orca": 60, "doctor": 600}  # status 캐시(초). doctor 는 하네스마다 --version 을 불러 수 초 걸린다
STATUS_TIMEOUT_S = 120
PLAN_TIMEOUT_S = 120
APPLY_TIMEOUT_S = 3600
PLAN_ID_RE = re.compile(r"^[0-9a-f]{64}$")

_cache = {}  # 이름 -> (시각, 종료 코드, 응답)
_status_lock = threading.Lock()
_job_lock = threading.Lock()
job = {}  # 마지막 apply: {module, plan_id, state: running/finished, started_at, finished_at, code, result}


def run(*args, timeout):
    """(종료 코드, JSON 응답). 출력이 JSON 이 아니면 표준 오류의 마지막 줄을 오류로 낸다."""
    try:
        r = subprocess.run([sys.executable, "-m", "jarvis", "module", *args, "--json"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 1, {"module": args[0], "error": f"{timeout}초 안에 끝나지 않았습니다"}
    try:
        body = json.loads(r.stdout)
        if isinstance(body, dict):
            return r.returncode, body
    except ValueError:
        pass
    last = (r.stderr.strip().splitlines() or ["출력이 없습니다"])[-1]
    return r.returncode or 1, {"module": args[0], "error": last[:300]}


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
        return {"modules": out, "job": dict(job) or None}


def plan(name):
    """게이트웨이 GET /api/modules/<이름>/plan. (http 상태, 계획)."""
    if name not in module.MODULES:
        return 404, {"error": "not found"}
    code, body = run(name, "plan", timeout=PLAN_TIMEOUT_S)
    return (200 if code == 0 else 502), body


def apply(name, plan_id):
    """게이트웨이 POST /api/modules/<이름>/apply?plan_id=…. 뒤에서 돌리고 바로 202 를 준다. (http 상태, 작업)."""
    if name not in module.MODULES:
        return 404, {"error": "not found"}
    if not PLAN_ID_RE.match(plan_id or ""):
        return 400, {"error": "plan_id 는 계획에서 받은 sha256 이어야 합니다"}
    with _job_lock:
        if job.get("state") == "running":
            return 409, {"error": f"{job['module']} 적용이 진행 중입니다", "job": dict(job)}
        job.clear()
        job.update(module=name, plan_id=plan_id, state="running", started_at=time.time())
        snapshot = dict(job)

    def work():
        code, body = run(name, "apply", "--plan-id", plan_id, timeout=APPLY_TIMEOUT_S)
        with _status_lock:
            _cache.pop(name, None)  # 바뀐 상태를 다음 조회에서 다시 읽는다
        with _job_lock:
            job.update(state="finished", finished_at=time.time(), code=code, result=body)

    threading.Thread(target=work, daemon=True, name=f"jarvis-module-{name}").start()
    return 202, snapshot


class CoreModules:
    """core: 기기마다 게이트웨이의 /api/modules 를 모으고, 계획·적용을 그대로 전달한다."""

    def __init__(self, core):
        self.core = core

    def _url(self, i, path):
        return self.core.devices[i]["url"] + path

    def all(self, force=False):
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
            return {**(body if isinstance(body, dict) else {"error": str(body)}), "id": i, "host": host, "status": code}

        with ThreadPoolExecutor(max_workers=max(1, min(16, len(ids)))) as pool:
            return {"generated_at": time.time(), "devices": list(pool.map(one, ids))}

    def plan(self, i, name):
        req = urllib.request.Request(self._url(i, f"/api/modules/{urllib.parse.quote(name)}/plan"))
        return self.core._ask(i, req, PLAN_TIMEOUT_S + 10, "AI 셋업 모듈을")

    def apply(self, i, name, plan_id):
        req = urllib.request.Request(
            self._url(i, f"/api/modules/{urllib.parse.quote(name)}/apply?plan_id={urllib.parse.quote(plan_id)}"),
            data=b"", method="POST", headers={"X-Jarvis-Action": "module-apply"})
        return self.core._ask(i, req, 30, "AI 셋업 모듈을")
