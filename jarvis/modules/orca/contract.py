"""Orca 모듈의 계약 층(docs/SPEC.md): 설치·서버·스킬은 이 폴더의 코드(ai-setup 에서 옮긴 것), 갱신은 orca_update 가 한다.

status·plan 은 읽기만 한다(파일·설정·패키지를 바꾸지 않는다). 영수증은 이 모듈(modules/orca 와 이 계약 층)만 쓰고,
jarvis 본체는 읽기만 한다. 페어링 링크·코드가 든 경로나 값은 내보내지 않는다.
"""

import getpass
import os
import platform
import socket
import subprocess
import sys
import time

from ... import module, orca_install, orca_pairing, orca_rpc, orca_serve, orca_update
from ...orca_install import TOOLS, tools
from ...util import run

# 영수증의 서버 기록 가운데 내보내도 되는 것(페어링 파일 경로·환경 ID 는 뺀다)
SERVER_KEYS = ("state", "service_manager", "service_task")


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
    """이식한 페어링 워커. orca.py와 같은 방법으로 형제 모듈을 읽는다."""
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
    unmanaged = windows and not orca_update._s4u_server()  # K4: S4U 서버가 없는 Windows 는 스킬도 맞추지 않는다
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
    if not unmanaged and (steps or orca_install.skills_version() != version):
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
    """POSIX 페어링·서비스 계획. 기존 연결을 받아들이고 중단된 구성을 재개하며, 실행 중 서버는 보존한다."""
    record = _receipt().get("tool_installs", {}).get("orca_server")
    # 형식을 모르는 기록도 다른 도구의 기록처럼 둔다(건드리지 않는다)
    if record and not (isinstance(record, dict) and (record.get('service_digest') or record.get('adopted')
                                                      or record.get('pairing_owner') == orca_pairing.OWNER)):
        return [], []
    resume = bool(record and record.get('state') == 'configuring' and record.get('service_digest'))
    shape, why = orca_serve.form(cli, orca_serve.default_profile())
    if shape == 'different' and not resume:
        return [], [{"step": "server", "reason": why}]
    profiles = _server_tools().profile_paths(sys.platform, tools().home_dir())
    try:
        state = orca_pairing.inspect(profiles['server'], profiles['client'], record)
    except (RuntimeError, OSError) as e:
        return [], [{'step': 'pairing', 'reason': str(e)}]
    steps, blocked = [], []
    needs_pairing = not state['environment_id'] or bool(record and record.get('state') == 'pairing')
    if needs_pairing:
        step = {'id': 'pairing', 'summary': '데스크톱 클라이언트에 이 서버 등록(임시 서버·중단 복구 기록)',
                'risk': 'config', 'argv': None, 'pairing_state': state['fingerprint']}
        steps.append(step)
        try:
            step['address'] = orca_pairing.tailnet_address(_server_tools().discover_pairing_address())
            pending = record and record.get('state') == 'pairing' and 'client_environment_ids_before_pairing' in record
            if (not state['environment_id'] or pending) and _server_tools().runtime_is_active(profiles['server']):
                blocked.append({'step': 'pairing', 'reason': '실행 중인 서버를 멈춰야 페어링할 수 있습니다. 기존 서버는 바꾸지 않습니다'})
        except RuntimeError as e:
            blocked.append({'step': 'pairing', 'reason': str(e)})
    if shape == 'same' and not resume:
        if not record or needs_pairing:
            # 적용(_server)은 우리가 만든 서비스면 파일을 검증하고 활성화·접속 확인을 재개한다. 계획도 그 일을 그대로 보인다
            owned = bool(record and record.get('service_digest') and os.path.lexists(orca_serve.service_path()))
            steps.append({'id': 'server', 'pairing_state': state['fingerprint'], **(
                {'summary': '소유 Orca 서버 서비스 다시 활성화(파일은 검증만)·데스크톱 연결 확인', 'risk': 'config',
                 'argv': orca_serve.activate_argv()[-1]} if owned else
                {'summary': '기존 Orca 서버 서비스를 받아들여 기록(파일·실행 중인 서버는 바꾸지 않음)', 'risk': 'update', 'argv': None})})
    elif shape == 'missing' or resume:
        server_step = {'id': 'server', 'summary': 'Orca 서버 서비스 작성·활성화(serve --supervise, 데스크톱 연결 확인)',
                       'risk': 'config', 'argv': orca_serve.activate_argv()[-1], 'pairing_state': state['fingerprint']}
        steps.append(server_step)
        if shape == 'missing':
            try:
                server_step['address'] = orca_pairing.tailnet_address(_server_tools().discover_pairing_address())
            except RuntimeError as e:
                blocked.append({'step': 'server', 'reason': str(e)})
        if not resume:
            steps += _linger_steps()
    if blocked:
        blocked_ids = {b['step'] for b in blocked}
        blocked += [{'step': s['id'], 'reason': '데스크톱 페어링이 막혀 서버 구성을 진행하지 않습니다'}
                    for s in steps if s['id'] not in blocked_ids]
    return steps, blocked


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
    """옮긴 설치기로 패키지·스킬(Windows는 S4U 서버까지)을 목표 버전에 묶어 설치한다. POSIX 페어링·서비스는 별도 단계다."""
    argv = ["--expect-version", target] + (["--no-server"] if sys.platform != "win32" else [])
    return ("ok", "") if tools().main(argv) == 0 else ("failed", "Orca 설치 실패(표준 오류의 진행 로그를 보십시오)")


def _pair(cli, address):
    profiles = _server_tools().profile_paths(sys.platform, tools().home_dir())
    record = _receipt().get('tool_installs', {}).get('orca_server') or {}
    record = {**record, 'state': 'pairing', 'pairing_owner': orca_pairing.OWNER,
              'cli': cli, 'server_profile': str(profiles['server']), 'client_profile': str(profiles['client']),
              'service_path': orca_serve.service_path(), 'pairing_address': address}
    orca_pairing.pair(cli, record, _server_tools(), _save_server)
    return 'ok', '데스크톱 서버 연결을 등록·확인했습니다'


def _server(cli, address=None):
    record = _receipt().get("tool_installs", {}).get("orca_server")
    if record and not (isinstance(record, dict) and (record.get('pairing_owner') == orca_pairing.OWNER
                                                      or record.get('service_digest') or record.get('adopted'))):
        return "failed", "영수증에 다른 Orca 서버 기록이 있어 덮어쓰지 않습니다"
    profile = orca_serve.default_profile()
    profiles = _server_tools().profile_paths(sys.platform, tools().home_dir())
    state = orca_pairing.inspect(profiles['server'], profiles['client'], record)
    if not state['environment_id']:
        return 'failed', '이 서버의 데스크톱 연결이 없습니다. 페어링 계획을 먼저 적용하십시오'
    record = {**(record or {}), 'client_profile': str(profiles['client']), 'desktop_environment_id': state['environment_id']}
    ready = lambda: _server_tools().wait_for_remote_environment(cli, record['client_profile'], record['desktop_environment_id'])
    if record.get('service_digest') and os.path.lexists(orca_serve.service_path()):
        # 페어링을 더한 기존 소유 서비스도 그대로 우리 소유다. 기존 파일을 검증하고 활성화·접속 확인만 재개한다.
        orca_serve.write(cli, None, None, None, _save_server, record={**record, 'state': 'configuring'}, on_ready=ready)
        return 'ok', '활성화와 데스크톱 연결 확인을 이어서 마쳤습니다'
    shape, why = orca_serve.form(cli, profile)
    if shape == "same":
        _save_server({**record, **orca_serve.adopted_record(cli, profile)})
        return "ok", "기존 서비스를 받아들였습니다(파일은 그대로)"
    if shape == "different":
        return "failed", why
    if _server_tools().runtime_is_active(profile):
        return 'failed', '이 프로필의 서버가 이미 실행 중이어서 새 서비스를 시작하지 않습니다'
    address = orca_pairing.tailnet_address(address or _server_tools().discover_pairing_address())
    orca_serve.write(cli, address, profile, orca_serve.login_env(), _save_server, record=record, on_ready=ready)
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
        return {"status": "failed" if current['blocked'] else "current", "steps": [
            {'id': b['step'], 'result': 'skipped', 'message': b['reason']} for b in current['blocked']]}
    blocked = {b["step"]: b["reason"] for b in current["blocked"]}
    target = next((s["target"] for s in current["steps"] if s["id"] == "package"), None)
    cli = orca_install.profile()["cli"]
    step_ids = {s['id'] for s in current['steps']}
    results = [_step({'id': name}, 'skipped', reason) for name, reason in blocked.items() if name not in step_ids]
    done = set()
    for step in current["steps"]:
        if step["id"] in done:
            continue
        if step["id"] in blocked:
            results.append(_step(step, "skipped", blocked[step["id"]]))
            continue
        earlier = set(done)
        try:
            if current["action"] == "install" and step["id"] in ("package", "skills"):
                result, message = _install(target)
                done |= {"package", "skills"}
            elif step["id"] == "package":  # 갱신: 앱을 올린 뒤 소유 스킬까지 orca_update 가 한다
                result, message = _update(target)
                done |= {"package", "skills"}
            elif step["id"] == "skills":
                result, message = _skills()
            elif step['id'] == 'pairing':
                result, message = _pair(cli, step['address'])
            elif step["id"] == "server":
                result, message = _server(cli, address=step['address']) if 'address' in step else _server(cli)
            elif step["id"] == "linger":
                result, message = _linger(step)
            else:
                result, message = "failed", "알 수 없는 단계"
        except subprocess.TimeoutExpired as e:  # 문자열에 argv 전체(페어링 링크 포함)가 들어간다
            result, message = "failed", f"{os.path.basename(str(e.cmd[0]))} 가 {e.timeout}초 안에 끝나지 않았습니다"
        except Exception as e:
            result, message = "failed", _server_tools().redact_reason(f"{type(e).__name__}: {e}")
        results.append(_step(step, result, message))
        # 이 단계가 함께 처리한 단계(갱신·설치 뒤의 skills)도 결과에 남긴다: 승인한 단계마다 결과가 하나씩 있다
        results += [_step(s, result, f"{step['id']} 단계에서 함께 처리") for s in current["steps"]
                    if s["id"] in done - earlier and s["id"] != step["id"]]
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
    if isinstance(server, dict) and server.get('pairing_owner') == orca_pairing.OWNER and not (server.get('service_digest') or server.get('adopted')):
        return {'status': 'deferred', 'plan_id': sealed['plan_id'], 'steps': [
            {'id': 'pairing', 'result': 'skipped', 'message': '미완료 페어링 기록을 보존했습니다. 서버 구성을 재개한 뒤 제거하십시오'}]}
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
