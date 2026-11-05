"""실행본 이전: 1.8.0 이전 설치기가 ~/Workspace/jarvis(Windows %USERPROFILE%\\Workspace\\jarvis)에 푼 실행본을
launch.ROOT/app 으로 옮기고 서비스 등록(LaunchAgent·systemd 사용자 유닛·예약 작업 jarvis)이 app 을 가리키게 고친다.
서비스(_confirm 뒤 1회)·CLI(`python -m jarvis relocate`)·설치기(`python -m jarvis.relocate`)가 이 코드 하나를 부른다.
표준 라이브러리만 쓴다(psutil 은 있으면 쓰고, 없으면 옛 폴더 정리를 미룬다). 몇 번 돌아도 같은 결과다.

- 옛 폴더에 .git 이 있으면(개발 체크아웃) 아무것도 읽거나 부르지 않는다.
- 등록이 옛 폴더를 가리키지 않으면(pm2·수동 설치) 아무것도 하지 않는다.
- 옛 폴더는 지우지 않고 ROOT/workspace-old 로 옮긴다. 서비스 관리자가 실제로 로드한 설정이 app 을 가리키고, 옛 폴더의 코드나
  작업 폴더로 도는 프로세스가 없을 때만 옮긴다.
- macOS 는 launchd 가 plist 를 다시 읽을 때(로그인·재부팅·사용자의 bootout/bootstrap)까지 로드된 작업 폴더가 옛 폴더다.
  그때까지 옛 폴더를 남긴다(pending_reload): 지우면 KeepAlive 재시작이 chdir 에 실패해 기기가 꺼진다.
  jarvis 는 스스로 bootout/bootstrap 하지 않는다(원격 기기가 꺼질 수 있다).

한 번에 하나만 돈다: ROOT/.relocate.lock 을 잡지 못하면 기다리지 않고 busy 로 끝난다(판정·복사·등록·실패 정리 모두 잠금 안).

결과: {"state": noop|busy|registered|pending_reload|cleaned|failed (dry-run 은 planned), "detail": 설명, "steps": 한 일·할 일}
"""

import contextlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

from . import launch

PLATFORM = sys.platform
HERE = launch.HERE  # 지금 도는 코드의 저장소(또는 받은 릴리스) 폴더
LABEL = "dev.jarvis"
BAK = ".bak-relocate"
TMP = ".app-tmp-"
LOCK = ".relocate.lock"
TASK = "jarvis"  # 예약 작업 이름. PowerShell 에는 환경 변수로 넘긴다(실기기 시험에서 다른 이름으로 바꿀 수 있게)
UNIT_WD = re.compile(rb"^WorkingDirectory=(.*?)\r?$", re.M)
# 예약 작업의 action 만 바꾼다(principal·trigger·settings 는 그대로). 값은 환경 변수로 넘겨 따옴표 해석을 피한다
PS_GET = ("[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false; "
          "$t = Get-ScheduledTask -TaskName $env:JARVIS_TASK_NAME -ErrorAction SilentlyContinue; "
          "if ($t) { ConvertTo-Json -Compress -InputObject @($t.Actions | ForEach-Object { "
          "@{Execute = $_.Execute; Arguments = $_.Arguments; WorkingDirectory = $_.WorkingDirectory} }) }")
PS_SET = ("$ErrorActionPreference = 'Stop'; "
          "$a = New-ScheduledTaskAction -Execute $env:JARVIS_TASK_EXE -Argument $env:JARVIS_TASK_ARGS "
          "-WorkingDirectory $env:JARVIS_TASK_WD; "
          "Set-ScheduledTask -TaskName $env:JARVIS_TASK_NAME -Action $a | Out-Null")


def old():
    return os.path.join(os.path.expanduser("~"), "Workspace", "jarvis")


def app():
    return os.path.join(launch.ROOT, "app")


def _key(p):
    return os.path.normcase(os.path.realpath(p))


def same(a, b):
    """같은 폴더인가. $HOME 이 심링크로 다른 이름일 수 있어(/home/x = /Users/x) 문자열이 아니라 실제 경로로 비교한다."""
    return bool(a) and bool(b) and _key(a) == _key(b)


def under(p, d):
    p, d = _key(p), _key(d)
    return p == d or p.startswith(d.rstrip(os.sep) + os.sep)


def _code(d):
    return os.path.isfile(os.path.join(d, "jarvis", "__init__.py"))


def _run(argv, env=None):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                           env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, r.stdout, r.stderr.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)


def _ps(script, env=None):
    env = {**(env or os.environ), "JARVIS_TASK_NAME": TASK}
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script], env)


@contextlib.contextmanager
def _lock():
    """ROOT/.relocate.lock 을 기다리지 않고 잡는다. 잡으면 True, 다른 프로세스가 쥐고 있으면 False."""
    os.makedirs(launch.ROOT, exist_ok=True)
    with open(os.path.join(launch.ROOT, LOCK), "a+") as f:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            # POSIX 는 파일을 닫으면 풀린다


def _task_dir(a):
    """설치기(install-windows.ps1)가 만든 모양의 action 이면 그 폴더, 아니면 None.
    pythonw.exe "<폴더>\\deploy\\jarvis-win.pyw" 하나(따옴표는 없어도 됨, 다른 인자 없음), 작업 폴더는 <폴더> 또는 비어 있음."""
    exe, args, wd = a.get("Execute") or "", (a.get("Arguments") or "").strip(), a.get("WorkingDirectory") or ""
    if os.path.basename(exe.replace("\\", "/")).lower() != "pythonw.exe":
        return None
    if len(args) >= 2 and args[0] == args[-1] == '"':
        args = args[1:-1]
    elif any(c.isspace() for c in args):  # 따옴표 없이 공백이 있으면 인자가 여럿이다
        return None
    if '"' in args or not os.path.isabs(args):
        return None
    deploy, pyw = os.path.split(args)
    d, name = os.path.split(deploy)
    if pyw.lower() != "jarvis-win.pyw" or name.lower() != "deploy" or (wd and not same(wd, d)):
        return None
    return d


def _write(path, data):
    """원본을 ROOT/<파일 이름>.bak-relocate 로 한 번만 남기고 임시 파일에서 이름을 바꿔 쓴다.
    백업은 LaunchAgents·유닛 폴더 밖에 둔다(서비스 관리자가 같은 Label 의 옛 정의로 읽지 않게)."""
    bak = os.path.join(launch.ROOT, os.path.basename(path) + BAK)
    if not os.path.exists(bak):
        os.makedirs(launch.ROOT, exist_ok=True)
        shutil.copy2(path, bak)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + "-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _swap(v, lit, new):
    """plist 값 안의 옛 경로(lit 로 시작하는 경로 조각)를 새 경로로."""
    if isinstance(v, str):
        return re.sub(re.escape(lit) + r"(?![^/:;\s])", lambda _: new, v)
    if isinstance(v, list):
        return [_swap(x, lit, new) for x in v]
    if isinstance(v, dict):
        return {k: _swap(x, lit, new) for k, x in v.items()}
    return v


def _unit_dir(v):
    v = v.strip().lstrip("-")
    for pre in ("%h", "~"):
        if v.startswith(pre):
            return os.path.expanduser("~") + v[len(pre):]
    return v


def registered():
    """서비스 등록: {"kind", "path", "raw", "wd"(원문), "dir"(실제 경로)} 또는 None(없음·읽지 못함)."""
    home = os.path.expanduser("~")
    try:
        if PLATFORM == "darwin":
            path = os.path.join(home, "Library", "LaunchAgents", LABEL + ".plist")
            with open(path, "rb") as f:
                raw = f.read()
            wd = plistlib.loads(raw).get("WorkingDirectory")
            return {"kind": "launchd", "path": path, "raw": raw, "wd": wd, "dir": wd} if isinstance(wd, str) else None
        if PLATFORM == "win32":
            code, out, _ = _ps(PS_GET)
            acts = json.loads(out) if code == 0 and out.strip() else None
            acts = [acts] if isinstance(acts, dict) else acts
            if not acts or len(acts) != 1:  # 설치기가 만드는 작업은 action 이 하나다. 다른 모양은 손대지 않는다
                return None
            d = _task_dir(acts[0])
            return {"kind": "task", "path": None, "raw": acts[0], "wd": d, "dir": d} if d else None
        path = os.path.join(home, ".config", "systemd", "user", "jarvis.service")
        with open(path, "rb") as f:
            raw = f.read()
        m = UNIT_WD.search(raw)
        if not m:
            return None
        wd = m.group(1).decode("utf-8", "replace")
        return {"kind": "systemd", "path": path, "raw": raw, "wd": wd, "dir": _unit_dir(wd)}
    except FileNotFoundError:
        return None
    except Exception as e:  # 깨진 plist·유닛·PowerShell 출력: 손대지 않는다
        print(f"[jarvis] 이전: 서비스 등록을 읽지 못했습니다: {e}", file=sys.stderr, flush=True)  # --json 출력을 깨지 않게
        return None


def _register(reg, new):
    """등록을 new 로 고쳐 쓴다. 실패하면 원상태로 두고 이유를 돌려준다."""
    kind = reg["kind"]
    if kind == "launchd":
        data = _swap(plistlib.loads(reg["raw"]), reg["wd"], new)
        data["WorkingDirectory"] = new
        _write(reg["path"], plistlib.dumps(data, sort_keys=False))
        return None
    if kind == "systemd":
        m = UNIT_WD.search(reg["raw"])
        _write(reg["path"], reg["raw"][:m.start(1)] + new.encode("utf-8") + reg["raw"][m.end(1):])
        code, _, err = _run(["systemctl", "--user", "daemon-reload"])
        if code != 0:
            _write(reg["path"], reg["raw"])
            return f"systemctl --user daemon-reload 실패({code}): {err}"
        return None
    a = reg["raw"]
    args = '"' + os.path.join(new, "deploy", "jarvis-win.pyw") + '"'  # 고쳐 쓰지 않고 설치기 모양으로 새로 만든다
    env = {**os.environ, "JARVIS_TASK_EXE": a.get("Execute") or "", "JARVIS_TASK_ARGS": args, "JARVIS_TASK_WD": new}
    code, _, err = _ps(PS_SET, env)
    return None if code == 0 else f"Set-ScheduledTask 실패({code}, 관리자 권한이 필요할 수 있습니다): {err}"


def _loaded(reg):
    """서비스 관리자가 실제로 쓰는 작업 폴더. 모르면 None."""
    if reg["kind"] == "launchd":
        _, out, _ = _run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
        m = re.search(r"^\s*working directory = (.+?)\s*$", out, re.M)
        return m.group(1) if m else None
    if reg["kind"] == "systemd":
        code, out, _ = _run(["systemctl", "--user", "show", "jarvis", "-p", "WorkingDirectory", "--value"])
        return (out.strip().lstrip("!-") or None) if code == 0 else None
    return reg["dir"]  # 예약 작업은 등록된 정의가 곧 다음 실행이다(Set-ScheduledTask 성공 = 로드)


def _users(d):
    """d 아래를 작업 폴더로 쓰거나 명령줄에 d 아래 경로가 있는 다른 프로세스. psutil 이 없으면 확인할 수 없으므로 있다고 본다."""
    try:
        import psutil
    except ImportError:
        return ["?"]
    found = []
    for p in psutil.process_iter(["pid", "cwd", "cmdline"]):
        paths = [p.info.get("cwd")] + [a for a in p.info.get("cmdline") or [] if os.path.isabs(a)]
        if p.info["pid"] != os.getpid() and any(x and under(x, d) for x in paths):
            found.append(p.info["pid"])
    return found


def _mentions(reg, o):
    """등록 내용(유닛은 주석 빼고) 어딘가에 아직 옛 경로가 있는가. 있으면 옛 폴더를 옮기면 그 값이 깨진다."""
    if reg["kind"] == "launchd":
        text = json.dumps(plistlib.loads(reg["raw"]), default=str, ensure_ascii=False)
    elif reg["kind"] == "systemd":
        text = "\n".join(l for l in reg["raw"].decode("utf-8", "replace").splitlines() if not l.lstrip().startswith(("#", ";")))
    else:
        text = " ".join(str(v) for v in reg["raw"].values())
    text = text.lower().replace("\\", "/")
    return any(x.lower().replace("\\", "/") in text for x in (o, os.path.realpath(o), "%h/Workspace/jarvis"))


def _discard(d):
    """이번에 만든 app 을 지운다. 먼저 임시 이름으로 바꿔, 지우다 멈춰도 반쯤 남은 app 을 다음 실행이 쓰지 않게 한다."""
    dead = os.path.join(os.path.dirname(d), TMP + "dead-" + str(os.getpid()))
    try:
        os.replace(d, dead)
    except OSError:
        return
    shutil.rmtree(dead, ignore_errors=True)  # 남으면 다음 _copy 가 치운다


def _copy(src, dest):
    """반쯤 복사된 app 이 생기지 않게 ROOT 안의 임시 폴더에 다 복사한 뒤에만 이름을 준다."""
    root = os.path.dirname(dest)
    os.makedirs(root, exist_ok=True)
    for name in os.listdir(root):
        if name.startswith(TMP):  # 지난번에 끊긴 복사
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    tmp = tempfile.mkdtemp(prefix=TMP, dir=root)
    try:
        shutil.copytree(src, tmp, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)


def _cleanup(reg, o, a, steps, dry_run):
    """등록이 app 을 가리킬 때 옛 폴더를 ROOT/workspace-old 로 옮긴다. (state, detail)."""
    if _mentions(reg, o):
        return "registered", "서비스 등록에 아직 옛 경로가 있어 옛 폴더를 남깁니다(손으로 고친 값일 수 있습니다)"
    loaded = _loaded(reg)
    if not same(loaded, a):
        if reg["kind"] == "launchd":
            return "pending_reload", f"launchd 가 아직 {loaded} 에서 띄웁니다: plist 를 다시 읽은 뒤(로그인·재부팅) 옛 폴더를 옮깁니다"
        return "registered", f"서비스 관리자가 아직 {loaded} 를 씁니다"
    if under(HERE, o):
        return "registered", "지금 프로세스가 옛 폴더의 코드로 돕니다: 다음 기동 뒤 옛 폴더를 옮깁니다"
    busy = _users(o)
    if busy:
        return "registered", f"옛 폴더를 작업 폴더로 쓰는 프로세스가 있어 남깁니다(pid {', '.join(map(str, busy))})"
    dest = os.path.join(launch.ROOT, "workspace-old")
    if os.path.lexists(dest):
        dest += time.strftime("-%Y%m%d-%H%M%S")
    steps.append(f"move {o} -> {dest}")
    if dry_run:
        return "planned", "옛 폴더를 옮깁니다"
    try:
        os.rename(o, dest)  # 다른 파일시스템이면 실패한다: 복사·삭제 대신 그대로 둔다
    except OSError as e:
        return "registered", f"옛 폴더를 옮기지 못해 그대로 둡니다: {e}"
    return "cleaned", f"옛 폴더를 {dest} 로 옮겼습니다"


def _skip(o):
    """옮길 것이 아니면 그 이유. 파일을 읽기만 한다(.git 이면 등록도 읽지 않는다)."""
    if not os.path.isdir(o):
        return "옛 배치(~/Workspace/jarvis)가 없습니다"
    if os.path.lexists(os.path.join(o, ".git")):
        return "개발 체크아웃(.git)이라 건드리지 않습니다"
    if not _code(o):
        return "옛 폴더에 jarvis 코드가 없습니다"
    return None


def run(dry_run=False):
    """한 번 판정하고 할 수 있는 만큼 한다. 옮길 것이 있으면 잠금 안에서만 한다(dry-run 은 읽기만 하므로 잠그지 않는다)."""
    why = _skip(old())
    if why:
        return {"state": "noop", "detail": why, "steps": [], **({"dry_run": True} if dry_run else {})}
    if dry_run:
        return _relocate(True)
    with _lock() as held:
        if not held:
            return {"state": "busy", "detail": "다른 프로세스가 이전 중입니다", "steps": []}
        return _relocate(False)


def _relocate(dry_run):
    o, a, steps = old(), app(), []

    def done(state, detail):
        r = {"state": state, "detail": detail, "steps": steps}
        return {**r, "dry_run": True} if dry_run else r

    why = _skip(o)  # 잠금을 잡는 사이에 다른 프로세스가 끝냈을 수 있다
    if why:
        return done("noop", why)
    reg = registered()
    if not reg:
        return done("noop", "서비스 등록을 찾지 못했습니다")
    if same(reg["dir"], a):
        if not _code(a):
            return done("noop", "등록이 가리키는 app 에 jarvis 코드가 없어 옛 폴더를 남깁니다")
    elif not same(reg["dir"], o):
        return done("noop", f"서비스 등록이 옛 배치가 아닌 {reg['dir']} 를 가리킵니다")
    else:
        created = False
        if os.path.lexists(a):
            if not _code(a):
                return done("failed", f"{a} 가 있지만 jarvis 코드가 없어 손대지 않습니다")
            steps.append(f"reuse {a}")
        else:
            steps.append(f"copy {o} -> {a}")
            if not dry_run:
                try:
                    _copy(o, a)
                except OSError as e:
                    return done("failed", f"옛 폴더를 복사하지 못했습니다: {e}")
                created = True
        steps.append(f"point {reg['kind']} at {a}")
        if dry_run:
            return done("planned", "실행본을 옮기고 서비스 등록을 고칩니다")
        try:
            err = _register(reg, a)
        except Exception as e:
            err = f"서비스 등록을 고치지 못했습니다: {e}"
        now = registered() if not err else None
        if not err and not (now and same(now["dir"], a)):
            err = "고친 서비스 등록이 app 을 가리키지 않습니다"
        if err:
            # 등록이 이미 바뀌었을 수 있다(시간 초과·확인 실패). 등록과 로드된 설정이 모두 옛 폴더일 때만 app 을 지운다
            back = registered()
            if created and back and same(back["dir"], o) and not same(_loaded(back), a):
                _discard(a)
            return done("failed", err)
        reg = now
    return done(*_cleanup(reg, o, a, steps, dry_run))


def cli(args):
    """python -m jarvis relocate [--dry-run] [--json]"""
    if any(x not in ("--dry-run", "--json") for x in args):
        print("사용법: python -m jarvis relocate [--dry-run] [--json]", file=sys.stderr)
        return 2
    r = run(dry_run="--dry-run" in args)
    if "--json" in args:
        print(json.dumps(r, ensure_ascii=False))
    else:
        for s in r["steps"]:
            print(("would: " if r.get("dry_run") else "  ") + s)
        print(f"{r['state']}: {r['detail']}")
    return 1 if r["state"] == "failed" else 0


if __name__ == "__main__":  # 설치기는 무거운 `python -m jarvis` 대신 이것을 부른다(릴리스로 넘어가지 않고 app 의 코드로 돈다)
    sys.exit(cli(sys.argv[1:]))
