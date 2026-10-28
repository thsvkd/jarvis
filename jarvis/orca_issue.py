"""Orca 페어링 코드 1회 발급: 그 scope 의 기기가 하나도 없을 때만(처음 한 번) Orca 를 잠시 내리고 serve 를 그 scope 로
한 번 띄워 대기 기기를 만든 뒤 원래대로 다시 띄운다. Orca 는 serve 를 시작할 때(옵션 없음 = 데스크톱, --mobile-pairing =
모바일)와 데스크톱 UI 에서만 코드를 만들고, 실행 중에 밖에서 부를 길이 없다.

- Linux: systemd 사용자 유닛 orca-serve 의 ExecStart 를 그대로 쓰고 모바일 플래그만 바꾼다(xvfb-run·--no-sandbox 같은 감싸개 유지).
- macOS: orca serve LaunchAgent 의 ProgramArguments 로 같은 일을 한다(bootout → serve → bootstrap).
- Windows: 데스크톱 앱이다. 앱을 끝내고 orca.exe serve 로 발급한 뒤 explorer 로 앱을 다시 띄운다.
그 기기 Orca 에서 돌던 작업(에이전트·터미널)이 끊긴다: core 화면이 확인을 받은 뒤 부른다. 결과는 status 의 issue 로 본다.
"""

import glob
import json
import os
import plistlib
import re
import shlex
import socket
import subprocess
import sys
import threading
import time

import psutil

from . import config, orca
from .util import run

SCOPE = {"desktop": "runtime", "mobile": "mobile"}
WAIT_S = 120  # serve 가 뜨고 대기 기기를 만들 때까지
COOLDOWN_S = 900  # 실패한 뒤 다시 누를 수 있기까지(누를 때마다 Orca 가 끊기지 않게)
MARK = os.path.join(config.STATE_DIR, "orca-issue.json")  # 발급 중 표시: jarvis 가 도중에 죽으면 다음 기동 때 Orca 를 되살린다
_lock = threading.RLock()
job = None  # {"scope", "state": running|done|failed, "error", "started_at", "finished_at"}


def flags(argv, scope):
    """실행 인자에서 페어링 모드만 바꾼다. CLI 형식은 --mobile-pairing, Electron 직접 실행은 --serve-mobile-pairing."""
    out = [a for a in argv if a not in ("--mobile-pairing", "--serve-mobile-pairing")]
    if scope == "mobile":
        out.append("--serve-mobile-pairing" if "--serve" in out else "--mobile-pairing")
    return out


def manager(pairing=True):
    """이 기기 Orca 를 띄우는 방법. 모르면 None(발급 버튼을 내지 않는다)."""
    if sys.platform.startswith("linux"):
        rc, out, _ = run(["systemctl", "--user", "show", "orca-serve", "-p", "LoadState", "-p", "ExecStart", "-p", "Environment"])
        props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        m = re.search(r"argv\[\]=([^;]*)", props.get("ExecStart", ""))
        if rc != 0 or props.get("LoadState") != "loaded" or not m:
            return None
        env = dict(kv.split("=", 1) for kv in shlex.split(props.get("Environment", "")) if "=" in kv)
        result = {"kind": "systemd", "argv": shlex.split(m.group(1)), "env": env}
        return _ok(result) if pairing else result
    if sys.platform == "darwin":
        for p in sorted(glob.glob(os.path.expanduser("~/Library/LaunchAgents/*.plist"))):
            try:
                with open(p, "rb") as f:
                    d = plistlib.load(f)
            except Exception:
                continue
            a = [str(x) for x in d.get("ProgramArguments") or []]
            if "serve" in a and any("orca" in x.lower() for x in a) and d.get("Label") \
                    and run(["launchctl", "print", f"gui/{os.getuid()}/{d['Label']}"])[0] == 0:  # 적재된 에이전트만
                result = {"kind": "launchd", "argv": a, "env": d.get("EnvironmentVariables") or {}, "label": d["Label"], "plist": p}
                return _ok(result) if pairing else result
        return None
    if sys.platform == "win32":
        base = os.path.join(os.environ.get("LOCALAPPDATA") or "", "Programs", "orca")
        app, cli = os.path.join(base, "Orca.exe"), os.path.join(base, "resources", "bin", "orca.exe")
        if os.path.exists(app) and os.path.exists(cli):
            return {"kind": "app", "app": app, "cli": cli, "env": {}}
    return None


def _ok(m):
    """운영자가 --no-pairing 으로 페어링을 끈 Orca 는 원격 버튼으로 켜지 않는다."""
    return None if {"--no-pairing", "--serve-no-pairing"} & set(m["argv"]) else m


def issuable(st):
    """발급 버튼을 낼 scope: 그 scope 의 기기가 하나도 없고(처음 한 번), 띄우는 방법을 알 때."""
    d = orca.find_dir(orca.DIRS)
    if not (st.get("installed") and d) or (job and job["state"] == "running") or not manager():
        return []
    have = {x.get("scope") for x in orca.devices(d)}
    return [k for k, s in SCOPE.items() if s not in have]


def start(scope, address, port):
    """(http 상태, 응답). 발급은 스레드에서 돈다."""
    global job
    if scope not in SCOPE:
        return 400, {"error": "scope 는 desktop 또는 mobile"}
    if not address:
        return 503, {"error": "이 기기의 tailnet 주소를 아직 모릅니다"}
    with _lock:
        if job and job["state"] == "running":
            return 409, {"error": "이미 발급하고 있습니다", "issue": job}
        from . import orca_update
        if os.path.exists(orca_update.MARK):
            return 409, {"error": "이전 Orca 업데이트의 복구·세션 검증이 끝난 뒤에 발급하세요"}
        if orca_update.job and orca_update.job.get("state") == "running":
            return 409, {"error": "Orca 업데이트가 끝난 뒤에 발급하세요", "update": orca_update.snapshot()}
        from . import self_update  # 받은 뒤 재시작하면 발급이 끊긴다
        if self_update._lock.locked():
            return 409, {"error": "jarvis 갱신이 끝난 뒤에 발급하세요"}
        if job and job["state"] == "failed" and time.time() - job["finished_at"] < COOLDOWN_S:
            return 429, {"error": f"직전 발급이 실패했습니다. {COOLDOWN_S // 60}분 뒤에 다시 시도하세요", "issue": job}
        st = orca.status(address)
        if scope not in issuable(st):
            return 409, {"error": "이 기기는 이미 그 페어링 기록이 있거나 자동 발급을 지원하지 않습니다"}
        m = manager()
        job = {"scope": scope, "state": "running", "error": None, "started_at": time.time(), "finished_at": None}
        threading.Thread(target=_run, args=(scope, m, address, port), name="jarvis-orca-issue", daemon=True).start()
        return 202, {"issue": job}


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _wait(cond, secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(1)
    return False


def _kill_tree(pid):
    try:
        procs = [psutil.Process(pid)]
        procs += procs[0].children(recursive=True)
    except psutil.Error:
        return
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=20)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass


def _leftovers(m):
    """임시 serve 의 남은 프로세스(분리된 자식·Windows shim 이 띄운 Orca). 이때 원래 Orca 는 내려가 있다."""
    names = {os.path.basename(x).lower() for x in (m.get("argv") or [])[:3] + [m.get("cli") or "", m.get("app") or ""] if x}
    out = []
    for p in psutil.process_iter(["exe", "cmdline", "username"]):
        cmd = [str(c) for c in p.info.get("cmdline") or []]
        if p.pid != os.getpid() and cmd and ("serve" in cmd or "--serve" in cmd) and \
                any("orca" in c.lower() for c in cmd[:2]) and (names & {os.path.basename(c).lower() for c in cmd[:2]} or
                                                                os.path.basename(p.info.get("exe") or "").lower() in names):
            out.append(p)
    return out


def _stop(m):
    if m["kind"] == "systemd":
        run(["systemctl", "--user", "stop", "orca-serve"], timeout=60)
    elif m["kind"] == "launchd":
        run(["launchctl", "bootout", f"gui/{os.getuid()}/{m['label']}"], timeout=60)
    else:  # 데스크톱 앱 본체만 끝낸다(터미널 daemon-host 는 둔다)
        app = os.path.normcase(m["app"])
        procs = [p for p in psutil.process_iter(["exe"]) if os.path.normcase(p.info.get("exe") or "") == app]
        for p in procs:  # 먼저 창 닫기(WM_CLOSE)로 정상 종료를 청하고, 남으면 끝낸다
            run(["taskkill", "/PID", str(p.pid)], timeout=10)
        _, alive = psutil.wait_procs(procs, timeout=15)
        for p in alive:
            try:
                p.terminate()
            except psutil.Error:
                pass
        psutil.wait_procs(alive, timeout=10)


def _start(m):
    rc, err = 0, ""
    if m["kind"] == "systemd":
        rc, _, err = run(["systemctl", "--user", "start", "orca-serve"], timeout=60)
    elif m["kind"] == "launchd":
        rc, _, err = run(["launchctl", "bootstrap", f"gui/{os.getuid()}", m["plist"]], timeout=60)
    else:  # explorer 가 띄우게 해서 jarvis 작업이 재시작돼도 앱이 함께 죽지 않게 한다
        subprocess.Popen([os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "explorer.exe"), m["app"]],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if rc != 0:
        raise RuntimeError(f"rc={rc} {err.strip()[:100]}")


def recover():
    """jarvis 기동 때: 지난 발급이 도중에 끊겨(jarvis 재시작·죽음) Orca 가 내려간 채면 다시 띄운다."""
    try:
        with open(MARK, encoding="utf-8") as f:
            m = json.load(f)["manager"]
    except (OSError, ValueError, KeyError):
        return
    try:
        cur = manager() or {}  # 남은 임시 serve 를 알아보려면 지금의 실행 인자가 필요하다(표시 파일에는 남기지 않았다)
        for p in _leftovers({**cur, **m}):
            _kill_tree(p.pid)
        _start(m)
        print("[jarvis] 끊긴 Orca 코드 발급 뒤 Orca 를 다시 띄웠다", flush=True)
    except Exception as e:
        print(f"[jarvis] 끊긴 발급 뒤 Orca 재기동 실패: {type(e).__name__}: {e}", flush=True)
    try:
        os.remove(MARK)
    except OSError:
        pass


def _run(scope, m, address, port):
    global job
    error, proc = None, None
    d = orca.find_dir(orca.DIRS)
    pending = lambda: any(x.get("scope") == SCOPE[scope] and x.get("lastSeenAt") == 0 for x in orca.devices(d))
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        keep = {k: m[k] for k in ("kind", "label", "plist", "app") if k in m}  # 복구에 필요한 것만(실행 인자·환경 변수는 빼고)
        fd = os.open(MARK, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"manager": keep, "scope": scope, "at": time.time()}, f)
        _stop(m)
        if not _wait(lambda: not _port_open(port), 30):
            raise RuntimeError(f"Orca 가 :{port} 를 놓지 않습니다")
        if m["kind"] == "app":
            argv = [m["cli"], "serve", "--port", str(port), "--pairing-address", address, "--json"]
        else:
            argv = m["argv"]
        posix = sys.platform != "win32"
        proc = subprocess.Popen(flags(argv, scope), env={**os.environ, **m["env"]}, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                **({"start_new_session": True} if posix else
                                   {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}))
        # serve 가 살아 있는 채로 포트를 열고 대기 기기를 만들어야 성공이다(일찍 끝나면 실패)
        ok = _wait(lambda: proc.poll() is not None or (pending() and _port_open(port)), WAIT_S)
        if proc.poll() is not None:
            raise RuntimeError(f"serve 가 일찍 끝났습니다(종료 코드 {proc.returncode})")
        if not ok or not pending():
            raise RuntimeError(f"serve 가 {WAIT_S}초 안에 코드를 만들지 못했습니다")
    except Exception as e:
        error = f"{type(e).__name__}: {e}"[:300]
    finally:
        if proc and proc.poll() is None:
            _kill_tree(proc.pid)
        for p in _leftovers(m):
            _kill_tree(p.pid)
        if not _wait(lambda: not _port_open(port), 30):
            error = (error + " / " if error else "") + f"임시 serve 가 :{port} 를 놓지 않습니다"
        try:
            _start(m)
            if not _wait(lambda: _port_open(port), 90):
                error = (error + " / " if error else "") + "Orca 가 다시 뜨지 않았습니다"
        except Exception as e:
            error = (error + " / " if error else "") + f"Orca 재시작 실패: {type(e).__name__}: {e}"[:200]
        try:
            os.remove(MARK)
        except OSError:
            pass
        with _lock:
            job = {**job, "state": "failed" if error else "done", "error": error, "finished_at": time.time()}
        print(f"[jarvis] Orca {scope} 코드 발급 {'실패: ' + error if error else '완료'}", flush=True)
