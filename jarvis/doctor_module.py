"""doctor 모듈의 계약 층(docs/SPEC.md): 하네스·도구 점검(modules/doctor, ai-setup 에서 옮긴 코드)을 status 로 낸다.

읽기 전용이다. 홈 아래 어떤 파일도 쓰지 않으므로 plan 은 언제나 단계가 없고, apply 는 current, uninstall 은 지울 것이 없다.
시크릿은 파일 위치와 키 이름만 낸다(값은 doctor 가 애초에 꺼내지 않는다).
"""
import contextlib
import importlib.util
import io
import os
import shutil
import socket
import tempfile
from pathlib import Path

from . import module

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
        with contextlib.redirect_stdout(io.StringIO()):  # 점검 함수의 제목 줄은 사람용 보고다
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
    return {"module": "doctor", "installed": True, "owned": False, "version": None, "latest": None,
            "health": "degraded" if findings else "ok", "details": {"tools": rows, "findings": findings}}


def plan():
    return module.seal({"module": "doctor", "device": socket.gethostname(), "action": "update", "steps": [], "blocked": []})


def apply(plan_id):
    current = plan()
    if current["plan_id"] != plan_id:
        return {"status": "stale", "plan_id": current["plan_id"], "steps": []}
    return {"status": "current", "steps": []}


def uninstall(dry_run=False, plan_id=None):
    current = module.seal({"module": "doctor", "device": socket.gethostname(), "action": "uninstall", "steps": [], "blocked": []})
    if not dry_run and plan_id != current["plan_id"]:
        return {"status": "stale", "plan_id": current["plan_id"], "steps": []}
    return {"status": "done", "plan_id": current["plan_id"], "steps": []}
