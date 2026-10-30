"""Orca 모듈의 계약 층(docs/SPEC.md): 설치·서버·스킬은 modules/orca(ai-setup 에서 옮긴 코드), 갱신은 orca_update 가 한다.

status·plan 은 읽기만 한다(파일·설정·패키지를 바꾸지 않는다). 영수증은 이 모듈(modules/orca 와 이 계약 층)만 쓰고,
jarvis 본체는 읽기만 한다. 페어링 링크·코드가 든 경로나 값은 내보내지 않는다.
"""

import getpass
import importlib.util
import os
import platform
import socket
import sys
import time

from . import module, orca_install, orca_rpc, orca_serve, orca_update
from .util import run

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "orca")
_tools = None
# 영수증의 서버 기록 가운데 내보내도 되는 것(페어링 파일 경로·환경 ID 는 뺀다)
SERVER_KEYS = ("state", "service_manager", "service_task")


def tools():
    """modules/orca/orca.py. Windows 러너가 단독 실행하는 파일이라 패키지가 아니고 경로로 읽는다."""
    global _tools
    if _tools is None:
        spec = importlib.util.spec_from_file_location("jarvis_orca_tools", os.path.join(TOOLS, "orca.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _tools = mod
    return _tools


def _receipt_path():
    return tools().home_dir() / ".claude/.claude-code-setup.json"


def _receipt():
    return tools().read_receipt(_receipt_path())


def _save_server(record):
    """서버 기록만 바꾼다. 다른 키는 그때의 영수증 그대로 둔다(None 이면 지운다)."""
    t, path = tools(), _receipt_path()
    receipt = t.read_receipt(path)
    installs = receipt.setdefault("tool_installs", {})
    if record is None:
        installs.pop("orca_server", None)
    else:
        installs["orca_server"] = record
    t.save_receipt(path, receipt)


def _server_tools():
    """modules/orca/orca_server.py(페어링 주소 찾기). orca.py 와 같은 방법으로 형제 모듈을 읽는다."""
    tools()
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import orca_server
    return orca_server


def _installed():
    try:
        return orca_install.installed()
    except (OSError, RuntimeError, ValueError, KeyError):
        return None


def status():
    installs = _receipt().get("tool_installs", {})
    version = _installed()
    check = orca_update.check() if version else {}
    elevated = False
    try:
        running = orca_rpc.call("updater.getStatus").get("appVersion") if version else None
        runtime_error = None
    except orca_rpc.Elevated as e:  # Windows S4U 서버: 살아 있지만 일반 권한으로는 묻지 못한다
        running, runtime_error, elevated = None, f"elevated server; run elevated to inspect ({e})"[:200], True
    except Exception as e:
        running, runtime_error = None, f"{type(e).__name__}: {e}"[:200]
    health = "degraded" if elevated else "down" if not running else ("ok" if running == version else "degraded")
    server = installs.get("orca_server") if isinstance(installs.get("orca_server"), dict) else None
    details = {"kind": orca_install.profile()["kind"], "running_version": running, "update_supported": check.get("supported"),
               "update_reason": check.get("reason"), "error": check.get("error") or runtime_error,
               "server": {k: server[k] for k in SERVER_KEYS if k in server} if server else None}
    return {"module": "orca", "installed": bool(version), "owned": "orca" in installs, "version": version,
            "latest": check.get("latest"), "health": health, "details": details}


def plan():
    """지금 기기에 필요한 단계들. 할 일이 없으면 steps 가 비어 있다. 바꾸는 단계는 목표 버전에 묶인다(plan_id 에 들어간다)."""
    version = _installed()
    p = orca_install.profile()
    windows = sys.platform == "win32"
    if not version:
        pkg = tools().package_plan(sys.platform, platform.machine())
        target, _ = orca_update.Live().manifest()
        steps = [{"id": "package", "target": target,
                  "summary": f"공식 Orca {target} {pkg['method']} 설치(크기·SHA-512 검증)" + (", S4U 서버 구성·페어링" if windows else ""),
                  "risk": "sudo" if pkg["method"] == "deb" else "install", "argv": None},
                 {"id": "skills", "summary": "Orca 번들 스킬 설치", "risk": "install", "argv": None}]
        server_steps, blocked = ([], []) if windows else _server_steps(p["cli"])
        return module.seal({"module": "orca", "device": socket.gethostname(), "action": "install",
                            "steps": steps + server_steps, "blocked": blocked})
    check = orca_update.check()
    if check.get("error"):
        raise RuntimeError(check["error"])
    steps, blocked = [], []
    if not check["current"]:
        deb, s4u = p["kind"] == "linux-deb", orca_update._s4u_server()
        note = (" (크기·SHA-512 를 검증한 deb 를 설치)" if deb else
                " (검증한 설치기를 `/S --updated` 로 실행, 열린 데스크톱 앱이 있으면 보류)" if s4u else "")
        argv = (["sudo", "-n", "/usr/bin/apt", "install", "-y", "--", "<검증한 deb>"] if deb else
                ["<검증한 orca-windows-setup.exe>", "/S", "--updated"] if s4u else None)
        steps.append({"id": "package", "target": check["latest"], "summary": f"Orca {version} → {check['latest']}" + note,
                      "risk": "sudo" if deb else "update", "argv": argv})
        if not check["supported"]:
            blocked.append({"step": "package", "reason": check["reason"]})
    # 앱을 올릴 때, 또는 번들 스킬을 마지막으로 맞춘 판이 지금 설치본과 다를 때만 스킬을 계획한다(같은 판이면 끝난 것이다)
    if steps or orca_install.skills_version() != version:
        steps.append({"id": "skills", "summary": "Orca 번들 스킬을 모두 이 판의 사본으로 갱신(사용자가 고친 스킬도 덮어씀)",
                      "risk": "update", "argv": [p["cli"], "skills", "update", "--all"]})
    server_steps, server_blocked = ([], []) if windows else _server_steps(p["cli"])
    action = "update" if steps or not server_steps else "reconfigure"
    return module.seal({"module": "orca", "device": socket.gethostname(), "action": action,
                        "steps": steps + server_steps, "blocked": blocked + server_blocked})


def _linger_steps():
    if orca_serve.linger() is False:  # 권한 변경이라 승인 뒤에만 켠다
        return [{"id": "linger", "summary": "로그아웃 뒤에도 Orca 서버가 돌도록 systemd 사용자 매니저 유지(linger)", "risk": "config",
                 "argv": ["loginctl", "enable-linger", getpass.getuser()]}]
    return []


def _server_steps(cli):
    """POSIX 서버 서비스: 기록이 없을 때만 판정한다. 같은 형태면 받아들이고(파일은 그대로), 없으면 새로 쓰고, 다르면 막는다."""
    record = _receipt().get("tool_installs", {}).get("orca_server")
    if isinstance(record, dict) and record.get("state") == "configuring" and record.get("service_digest"):
        return [{"id": "server", "summary": "작성한 Orca 서버 서비스의 활성화를 이어서 함", "risk": "config",
                 "argv": orca_serve.activate_argv()[-1]}], []
    if record:
        return [], []
    shape, why = orca_serve.form(cli, orca_serve.default_profile())
    if shape == "same":
        return [{"id": "server", "summary": "기존 Orca 서버 서비스를 받아들여 기록(파일·실행 중인 서버는 바꾸지 않음)",
                 "risk": "update", "argv": None}], []
    if shape == "different":
        return [], [{"step": "server", "reason": why}]
    return [{"id": "server", "summary": "Orca 서버 서비스 작성(serve --supervise, 로그인 셸의 PATH·SHELL)", "risk": "config",
             "argv": orca_serve.activate_argv()[-1]}] + _linger_steps(), []


def _step(step, result, message=""):
    return {"id": step["id"], "result": result, "message": str(message)[:400]}


def _update(target):
    """orca_update 의 갱신(앱을 올린 뒤 소유 스킬까지)을 목표 버전에 묶어 이 프로세스에서 끝까지 기다린다. (결과, 메시지)."""
    code, body = orca_update.start(expect=target)
    if code != 202:
        return "failed", body.get("error")
    while (orca_update.snapshot() or {}).get("state") == "running":
        time.sleep(1)
    job = orca_update.snapshot() or {}
    if job.get("state") == "done" and job.get("skills") != "failed":
        server = f" server={job['server']}" + (f" ({job['server_reason']})" if job.get("server_reason") else "") if job.get("server") else ""
        return "ok", f"app={job.get('app')} {job.get('from_version')} → {job.get('to_version')}{server}"
    return ("deferred" if job.get("deferred") else "failed"), job.get("error") or "스킬 갱신 실패"


def _skills():
    """앱은 그대로 두고 스킬만 맞춘다(패키지 단계가 없는 계획). 갱신 작업을 돌리지 않으므로 패키지를 건드리지 않는다."""
    orca_install.skills()
    return "ok", "updated all bundled skills"


def _install(target):
    """옮긴 설치기로 패키지·스킬(Windows 는 S4U 서버까지)을 목표 버전에 묶어 설치한다. POSIX 서버는 server 단계가 따로 한다."""
    argv = ["--expect-version", target] + (["--no-server"] if sys.platform != "win32" else [])
    return ("ok", "") if tools().main(argv) == 0 else ("failed", "Orca 설치 실패(표준 오류의 진행 로그를 보십시오)")


def _server(cli):
    record = _receipt().get("tool_installs", {}).get("orca_server")
    if isinstance(record, dict) and record.get("state") == "configuring" and record.get("service_digest"):
        orca_serve.write(cli, None, None, None, _save_server, record=record)
        return "ok", "활성화를 이어서 마쳤습니다"
    if record:  # 예전 ai-setup 기록(service_root·환경 ID)을 덮어쓰면 uninstall 이 그 서버를 더는 정리하지 못한다
        return "failed", "영수증에 다른 Orca 서버 기록이 있어 덮어쓰지 않습니다"
    profile = orca_serve.default_profile()
    shape, why = orca_serve.form(cli, profile)
    if shape == "same":
        _save_server(orca_serve.adopted_record(cli, profile))
        return "ok", "기존 서비스를 받아들였습니다(파일은 그대로)"
    if shape == "different":
        return "failed", why
    address = _server_tools().discover_pairing_address()  # Tailscale IPv4 만(루프백·LAN 은 받지 않는다)
    orca_serve.write(cli, address, profile, orca_serve.login_env(), _save_server)
    return "ok", "serve --supervise 서비스를 쓰고 띄웠습니다"


def _linger(step):
    rc, out, err = run(step["argv"], timeout=30)
    if rc:
        return "failed", (err or out).strip()
    t, path = tools(), _receipt_path()  # 우리가 켰다는 기록: uninstall 이 이것이 있을 때만 다시 끈다
    receipt = t.read_receipt(path)
    receipt.setdefault("tool_installs", {})["orca_linger"] = True
    t.save_receipt(path, receipt)
    return "ok", ""


def apply(plan_id):
    """승인받은 계획(plan_id)과 지금 다시 세운 계획이 같을 때만, 그 계획의 단계만 실행한다."""
    current = plan()
    if current["plan_id"] != plan_id:
        return {"status": "stale", "plan_id": current["plan_id"], "steps": []}
    if not current["steps"]:
        return {"status": "current", "steps": []}
    blocked = {b["step"]: b["reason"] for b in current["blocked"]}
    target = next((s["target"] for s in current["steps"] if s["id"] == "package"), None)
    cli = orca_install.profile()["cli"]
    results, done = [], set()
    for step in current["steps"]:
        if step["id"] in done:
            continue
        if step["id"] in blocked:
            results.append(_step(step, "skipped", blocked[step["id"]]))
            continue
        try:
            if current["action"] == "install" and step["id"] in ("package", "skills"):
                result, message = _install(target)
                done |= {"package", "skills"}
            elif step["id"] == "package":  # 갱신: 앱을 올린 뒤 소유 스킬까지 orca_update 가 한다
                result, message = _update(target)
                done |= {"package", "skills"}
            elif step["id"] == "skills":
                result, message = _skills()
            elif step["id"] == "server":
                result, message = _server(cli)
            elif step["id"] == "linger":
                result, message = _linger(step)
            else:
                result, message = "failed", "알 수 없는 단계"
        except Exception as e:
            result, message = "failed", f"{type(e).__name__}: {e}"
        results.append(_step(step, result, message))
        if result == "failed":
            break  # 앞 단계가 실패하면 뒤 단계(서버 등)는 실행하지 않는다
    outcome = [r["result"] for r in results]
    # 막힌 단계가 있으면 계획을 다 하지 못한 것이다(사람이 막힌 이유를 풀어야 한다)
    status = "failed" if "failed" in outcome or "skipped" in outcome else "deferred" if "deferred" in outcome else "done"
    return {"status": status, "steps": results}


def _uninstall_plan(receipt):
    """지울 근거(영수증의 Orca 기록) 그대로에 묶인 계획. 영수증이 바뀌면 plan_id 가 달라진다."""
    installs = receipt.get("tool_installs", {})
    return module.seal({"module": "orca", "device": socket.gethostname(), "action": "uninstall",
                        "records": {k: installs.get(k) for k in ("orca", "orca_server", "orca_linger")},
                        "attempt": receipt.get("tool_attempts", {}).get("orca")})


def uninstall(dry_run=False, plan_id=None):
    """영수증에 기록한 것만 지운다. 받아들인 서버 서비스는 지우지 않고 기록만 지운다.
    dry-run 은 plan_id 를 내고, 실제 실행은 그 plan_id 가 지금 영수증과 맞을 때만 한다."""
    t, path = tools(), _receipt_path()
    receipt = t.read_receipt(path)
    sealed = _uninstall_plan(receipt)
    if not dry_run and plan_id != sealed["plan_id"]:
        return {"status": "stale", "plan_id": sealed["plan_id"], "steps": []}
    pkg = t.package_plan(sys.platform, platform.machine())
    if receipt.get("tool_attempts", {}).get("orca") and t.existing_install(pkg):  # 무엇이든 지우기 전에 멈춘다
        raise RuntimeError("중단된 Orca 설치 시도가 영수증에 남아 있습니다. 설치 상태를 확인한 뒤 다시 하십시오")
    steps = []
    server = receipt.get("tool_installs", {}).get("orca_server")
    if isinstance(server, dict) and (server.get("adopted") or server.get("service_digest")):  # Linux·macOS 의 새 형태
        outcome = orca_serve.remove(server, dry_run)
        if outcome == "changed":
            return {"status": "deferred", "plan_id": sealed["plan_id"], "steps": [{"id": "server", "result": "skipped",
                    "message": "우리가 쓴 뒤 바뀐 서버 서비스 파일을 남기고 영수증을 보존했습니다"}]}
        steps.append({"id": "server", "result": "ok",
                      "message": "preserved adopted unit(기록만 지움)" if outcome == "kept" else "서버 서비스를 지웠습니다"})
        del receipt["tool_installs"]["orca_server"]
        if not dry_run:
            t.save_receipt(path, receipt)
    if receipt.get("tool_installs", {}).get("orca_linger"):
        if not dry_run:
            rc, out, err = run(["loginctl", "disable-linger", getpass.getuser()], timeout=30)
            if rc:
                return {"status": "failed", "plan_id": sealed["plan_id"],
                        "steps": steps + [{"id": "linger", "result": "failed", "message": (err or out).strip()[:400]}]}
            del receipt["tool_installs"]["orca_linger"]
            t.save_receipt(path, receipt)
        steps.append({"id": "linger", "result": "ok", "message": "우리가 켠 linger 를 껐습니다"})
    rc = t.uninstall(pkg, receipt, path, dry_run)
    steps.append({"id": "package", "result": "ok" if rc == 0 else "skipped",
                  "message": "" if rc == 0 else "사용자가 바꾼 스킬을 남기고 영수증을 보존했습니다"})
    return {"status": "done" if rc == 0 else "deferred", "plan_id": sealed["plan_id"], "steps": steps}
