"""doctor 모듈의 계약 층(docs/SPEC.md): 하네스·도구 점검(modules/doctor, ai-setup 에서 옮긴 코드)을 status 로 낸다.

홈 아래 어떤 파일도 쓰거나 지우지 않는다. 바꾸는 것은 하나뿐이다: 정식 Orca 서버와 Orca SSH 호스트 연결 orcad 가 함께 돌면(orca_split)
열린 터미널이 0 인 그 orcad 프로세스를 승인받은 plan_id 로만 멈춘다(DOCTOR-9). 그 밖에는 plan 이 단계가 없고 apply 는 current 다.
정식 서버는 건드리지 않고, 강제 종료(SIGKILL)는 하지 않는다. uninstall 은 지울 것이 없다.
시크릿은 파일 위치와 키 이름만 낸다(값은 doctor 가 애초에 꺼내지 않는다). Orca 토큰도 읽어 내지 않는다.
"""
import contextlib
import importlib.util
import io
import os
import shutil
import socket
import tempfile
from pathlib import Path

import psutil

from . import module, orca, orca_rpc
from .harness_module import _env_path

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "doctor", "doctor.py")
_tools = None


def tools():
    """modules/doctor/doctor.py. ai-setup 처럼 단독 실행도 하는 파일이라 경로로 읽는다."""
    global _tools
    if _tools is None:
        spec = importlib.util.spec_from_file_location("jarvis_doctor_tools", TOOLS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _tools = mod
    return _tools


def status():
    d, home = tools(), Path.home()
    catalog = d.read_json(d.CATALOG)
    if not isinstance(catalog, list):
        raise RuntimeError(f"카탈로그를 읽지 못했습니다: {d.CATALOG}")
    findings = []

    def collect(kind):
        return lambda message: findings.append({"kind": kind, "message": message})

    scratch = tempfile.mkdtemp(prefix="jarvis-doctor-")
    try:
        # 게이트웨이 서비스 PATH 에는 ~/.local/bin·nvm 이 없어 설치된 하네스를 없다고 낸다(harness 모듈과 같은 보강)
        with _env_path(), contextlib.redirect_stdout(io.StringIO()):  # 점검 함수의 제목 줄은 사람용 보고다
            rows = d.inventory(catalog, d.probe_env(scratch), scratch)
            d.check_hook_drift(home, collect("hook_drift"))
            d.check_secrets(home, collect("secret"))
            d.check_skills(home, collect("skill"))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    for row in rows:
        row["found"] = [d.tilde(Path(p), home) for p in row["found"]]
        if len(row["found"]) > 1:
            findings.insert(0, {"kind": "duplicate", "message": "{}: duplicate installs on PATH; first wins: {}; also: {}".format(
                row["id"], row["found"][0], ", ".join(row["found"][1:]))})
    s = split()
    if s:
        findings.append({"kind": "orca_split", "message": (
            "Orca 런타임이 둘로 나뉘어 돕니다: 정식 서버 {} (pid {}) 와 SSH 호스트 연결 orcad {} (pid {}, 클라이언트: {}). "
            "클라이언트는 정식 페어링 환경에 SSH 터널로 붙거나, 그 클라이언트에서 'orca environment stop' 으로 SSH 호스트 연결을 멈추세요. "
            "doctor plan 은 그 orcad 에 열린 터미널이 없을 때 멈추는 단계를 냅니다. 클라이언트가 다시 SSH 호스트로 붙으면 Orca 가 orcad 를 다시 배포할 수 있습니다"
        ).format(s["official"]["dir"], s["official"]["pid"], s["ssh"]["dir"], s["ssh"]["pid"], ", ".join(s["ssh"]["clients"]) or "없음")})
    return {"module": "doctor", "installed": True, "owned": False, "version": None, "latest": None,
            "health": "degraded" if findings else "ok", "details": {"tools": rows, "findings": findings}}


def split():
    """정식 Orca 서버(orca.DIRS)와 SSH 호스트 연결 orcad(orca.SSH_DIRS)가 둘 다 살아 있으면 {official:{dir, pid}, ssh}. 아니면 None."""
    d = orca.find_dir(orca.DIRS)
    pid, alive = orca.runtime_pid(d) if d else (None, False)
    ssh = orca.ssh_host()
    if not (alive and ssh and ssh["running"]):
        return None
    return {"official": {"dir": tools().tilde(Path(d), Path.home()), "pid": pid}, "ssh": ssh}


def _live():
    """SSH 호스트 연결 orcad 의 열린 터미널 수(orcad.terminalCensus). 셀 수 없으면 예외."""
    d = orca.find_dir(orca.SSH_DIRS)
    if not d:  # 없으면 orca_rpc 가 정식 서버에 묻게 되므로 셀 수 없다고 본다
        raise RuntimeError("SSH 호스트 연결 orcad 의 런타임 파일이 없습니다")
    c = orca_rpc.call("orcad.terminalCensus", {"activatedAt": 0}, d=d)
    n = [c.get(k) if isinstance(c, dict) else None for k in ("liveSessions", "inProcessSessions")]
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in n):
        raise RuntimeError("orcad 터미널 수 응답이 이상합니다")
    return sum(n)


def plan():
    steps, blocked = [], []
    s = split()
    if s:
        pid = s["ssh"]["pid"]
        try:
            live = _live()
            started = round(psutil.Process(pid).create_time(), 2)
            if live:
                blocked.append({"step": "stop_ssh_orcad", "reason": f"SSH 호스트 연결 orcad 에 열린 터미널이 {live}개 있어 멈추지 않습니다"})
            else:
                # pid·시작 시각이 plan_id 에 들어가 승인이 이 프로세스 하나에만 묶인다
                steps.append({"id": "stop_ssh_orcad", "summary": f"SSH 호스트 연결 orcad ({s['ssh']['dir']}, pid {pid}) 정지. 열린 터미널 0개, 파일은 지우지 않음",
                              "risk": "config", "argv": None, "pid": pid, "started": started})
        except Exception as e:
            blocked.append({"step": "stop_ssh_orcad", "reason": f"orcad 의 열린 터미널을 셀 수 없어 멈추지 않습니다: {type(e).__name__}: {e}"[:300]})
    return module.seal({"module": "doctor", "device": socket.gethostname(), "action": "update", "steps": steps, "blocked": blocked})


def _stop(step):
    """계획한 그 orcad 만 멈춘다. (결과, 메시지). 신원·터미널 수를 다시 확인하고, 멈추지 않으면 강제 종료하지 않고 실패로 둔다."""
    try:
        p = psutil.Process(step["pid"])
        if round(p.create_time(), 2) != step["started"] or not orca.is_orcad(step["pid"]):
            return "failed", "계획한 orcad 프로세스가 아닙니다(pid 가 재사용됨). 아무것도 멈추지 않았습니다"
        live = _live()
        if live:
            return "deferred", f"그사이 orcad 에 열린 터미널이 {live}개 생겨 멈추지 않았습니다"
        p.terminate()
        if psutil.wait_procs([p], timeout=10)[1]:
            return "failed", "orcad 가 10초 안에 멈추지 않았습니다(강제 종료하지 않습니다)"
        return "ok", f"SSH 호스트 연결 orcad (pid {step['pid']}) 를 멈췄습니다"
    except psutil.NoSuchProcess:
        return "ok", "orcad 가 이미 멈춰 있습니다"
    except Exception as e:
        return "failed", f"{type(e).__name__}: {e}"[:300]


def apply(plan_id):
    current = plan()
    if current["plan_id"] != plan_id:
        return {"status": "stale", "plan_id": current["plan_id"], "steps": []}
    if current["blocked"]:  # 열린 터미널이 닫히면 풀리는 보류다: 아무것도 하지 않는다
        return {"status": "deferred", "steps": [{"id": b["step"], "result": "deferred", "message": b["reason"]} for b in current["blocked"]]}
    if not current["steps"]:
        return {"status": "current", "steps": []}
    results = [{"id": st["id"], "result": r, "message": m} for st in current["steps"] for r, m in [_stop(st)]]
    outcome = [r["result"] for r in results]
    return {"status": "failed" if "failed" in outcome else "deferred" if "deferred" in outcome else "done", "steps": results}


def uninstall(dry_run=False, plan_id=None):
    current = module.seal({"module": "doctor", "device": socket.gethostname(), "action": "uninstall", "steps": [], "blocked": []})
    if not dry_run and plan_id != current["plan_id"]:
        return {"status": "stale", "plan_id": current["plan_id"], "steps": []}
    return {"status": "done", "plan_id": current["plan_id"], "steps": []}
