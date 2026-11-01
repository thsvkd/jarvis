"""npm-release: install an npm-only CLI one version per directory, so an update never touches a running session.

npm 외의 공식 설치 방식이 없는 대상(gemini, codegraph, Windows 의 opencode·copilot)을 위한 배치다(SPEC).
`npm install -g` 는 패키지 디렉터리를 그 자리에서 바꾸므로, 실행 중인 세션이 나중에 읽는 파일(해시 청크 등)이
사라진다. 여기서는 버전마다 디렉터리를 따로 두고 진입점만 바꾼다.

- <root>/<id>/<ver>/      `npm install --prefix`. 완료 표시(.complete)가 있어야 버전으로 친다.
- <root>/<id>/current     지금 버전. 임시 파일을 만든 뒤 원자적으로 교체한다. previous 는 직전 버전.
- 셔임                    POSIX ~/.local/bin/<bin>, Windows %LOCALAPPDATA%\\ai-setup\\bin\\<bin>.cmd.
                          내용이 고정이라 다시 쓰지 않는다. current 를 읽어 구체적인 버전 경로를 실행하므로,
                          이미 뜬 세션은 자기 버전 경로를 계속 쓴다.
- 정리                    current·previous 와, 명령줄이 그 버전 경로를 가리키는 프로세스가 있는 버전은 남긴다.
                          프로세스 목록을 읽지 못하면 아무것도 지우지 않는다.
- 이전 사본               PATH 의 npm 이 전역으로 깔아 둔 같은 패키지는, 그것을 쓰는 프로세스가 없을 때만 지운다.

exit: 0 = 성공, 1 = 실패(또는 사용 중이라 남긴 것이 있음), 2 = 인자 오류.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "res" / "catalog.json"
MARK = "ai-setup npm-release shim"
WINDOWS = os.name == "nt"


def say(text=""):
    print(text, flush=True)


def home():
    if WINDOWS and os.environ.get("USERPROFILE"):
        return Path(os.environ["USERPROFILE"])
    return Path.home()


def local_appdata():
    return Path(os.environ.get("LOCALAPPDATA") or home() / "AppData" / "Local")


def store(entry):
    base = local_appdata() / "ai-setup" / "npm" if WINDOWS else home() / ".local" / "share" / "ai-setup" / "npm"
    return base / entry["id"]


def shim_dir():
    return local_appdata() / "ai-setup" / "bin" if WINDOWS else home() / ".local" / "bin"


def shim_path(entry):
    name = entry["detect"]["bin"]
    return shim_dir() / (name + ".cmd" if WINDOWS else name)


def shim_text(entry):
    name, root = entry["detect"]["bin"], store(entry)
    if WINDOWS:
        # 다른 .cmd 로 CALL 없이 넘기면 cmd 는 이 파일을 다시 읽지 않는다. 내용도 바뀌지 않는다.
        # cmd 는 배치 파일을 OEM 코드 페이지로 읽으므로 절대 경로(비ASCII 사용자 이름) 대신
        # 셔임 위치(%~dp0) 기준 상대 경로를 쓴다. bin 과 npm 은 같은 ai-setup 디렉터리 아래다.
        here = "%~dp0..\\npm\\" + entry["id"]
        return "\r\n".join([
            "@echo off",
            "rem " + MARK + ". Never rewritten: an update only replaces the current file.",
            "setlocal",
            "set AI_SETUP_NPM_RELEASE=",
            'set /p AI_SETUP_NPM_RELEASE=<"{}\\current"'.format(here),
            "if not defined AI_SETUP_NPM_RELEASE exit /b 1",
            '"{}\\%AI_SETUP_NPM_RELEASE%\\node_modules\\.bin\\{}.cmd" %*'.format(here, name),
            "",
        ])
    # ponytail: 세션 판정은 명령줄의 버전 경로로 한다. process.title 로 argv 를 덮어쓰는 CLI 가 오면
    # /proc/<pid>/environ 같은 다른 표지가 필요하다(gemini·codegraph 는 덮어쓰지 않는다, 2026-10-28 확인).
    return "\n".join([
        "#!/bin/sh",
        "# " + MARK + ". Never rewritten: an update only replaces the current file.",
        "r={}".format(shlex.quote(str(root))),
        'v=$(cat "$r/current") || exit 1',
        'exec "$r/$v/node_modules/.bin/{}" "$@"'.format(name),
        "",
    ])


# ponytail: 셔임은 다시 쓰지 않으므로 shim_text 를 바꿔도 이미 깔린 셔임에는 닿지 않는다.
# 형식을 바꿀 일이 생기면 MARK 에 판 번호를 붙이고 예전 판만 원자적으로 교체한다.
def owns(path):
    """path 가 이 모듈이 쓴 셔임이면 True."""
    try:
        with open(path, "rb") as handle:
            return MARK.encode() in handle.read(400)
    except OSError:
        return False


def run_quiet(argv, timeout=120):
    try:
        return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def npm():
    return shutil.which("npm")


def latest(package):
    tool = npm()
    out = run_quiet([tool, "view", package, "version"]) if tool else None
    return out.stdout.strip() if out and out.returncode == 0 else ""


def read_pointer(root, name):
    try:
        return (root / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_pointer(root, name, value):
    tmp = root / ".{}.{}".format(name, os.getpid())
    tmp.write_text(value + "\n", encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(tmp, root / name)
            return
        except PermissionError:
            # Windows: 셔임이 막 읽는 중이면 공유 위반이 난다. 잠깐 뒤 다시 한다.
            if attempt == 9:
                raise
            time.sleep(0.2)


# ── 사용 중 판정 ─────────────────────────────────────────────────────

def norm(text):
    text = text.replace("\\", "/")
    return text.lower() if WINDOWS else text


def users(directory, table):
    """명령줄이 directory 안의 파일을 가리키는 프로세스 PID.

    명령줄 전체에서 경로 문자열을 찾고, 앞 네 단어는 realpath 로도 본다. PATH 의 링크로 띄운
    node CLI 는 명령줄에 링크 경로(bin/gemini)만 남기 때문이다.
    """
    want = norm(os.path.realpath(str(directory))).rstrip("/") + "/"
    raw = norm(str(directory)).rstrip("/") + "/"
    found = []
    for pid, args in table:
        low = norm(args)
        if want in low or raw in low:
            found.append(pid)
            continue
        for word in args.split()[:4]:
            word = word.strip("\"'")
            if (os.sep in word or "/" in word) and os.path.exists(word) and \
                    (norm(os.path.realpath(word)) + "/").startswith(want):
                found.append(pid)
                break
    return found


# ── 셔임·PATH ────────────────────────────────────────────────────────

def ensure_shim(entry, check_only=False):
    """셔임을 만들거나 그대로 둔다. 다른 것이 그 자리에 있으면 오류 문장, 아니면 None.

    npm 이 깔아 둔 같은 패키지의 링크(예전 설치)만 바꿔 끼운다. 그 밖의 파일은 사용자 것이다.
    """
    path = shim_path(entry)
    if os.path.lexists(path):
        if owns(path):
            return None
        package = (entry.get("packages") or {})["npm"]
        legacy = os.path.islink(path) and "/node_modules/{}/".format(package) in \
            norm(os.path.realpath(path)).replace("\\", "/")
        if not legacy:
            return "{} exists and was not written by ai-setup; move it away and run again".format(path)
    if check_only:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / ".{}.{}".format(path.name, os.getpid())
    tmp.write_text(shim_text(entry), encoding="utf-8", newline="")
    if not WINDOWS:
        tmp.chmod(0o755)
    os.replace(tmp, path)
    return None


def _user_path(edit):
    """Windows 사용자 PATH(레지스트리)를 edit(목록) -> 새 목록 또는 None(그대로)으로 고친다."""
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        parts = edit(value.split(";") if value else [])
        if parts is None:
            return
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(parts))
    # 새로 여는 터미널이 바뀐 PATH 를 보게 알린다. 실패해도 다음 로그인부터는 맞다.
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)


def _same_dir(entry, directory):
    return os.path.normcase(os.path.expandvars(entry).rstrip("\\")) == os.path.normcase(str(directory))


def ensure_user_path(directory):
    """directory 가 사용자 PATH 에 없으면 맨 앞에 넣는다(npm 의 %APPDATA%\\npm 보다 먼저 보이게).

    이미 있으면 자리를 옮기지 않는다. 사용자가 정한 순서를 존중한다.
    """
    _user_path(lambda parts: None if any(_same_dir(p, directory) for p in parts) else [str(directory)] + parts)


def remove_user_path(directory):
    _user_path(lambda parts: [p for p in parts if not _same_dir(p, directory)]
               if any(_same_dir(p, directory) for p in parts) else None)


# ── 설치·정리 ────────────────────────────────────────────────────────

def install_version(entry, root, version):
    """root/version 에 패키지를 설치한다. 성공하면 True."""
    package = entry["packages"]["npm"]
    target = root / version
    if (target / ".complete").is_file():
        return True
    if target.exists():
        # current 가 된 적이 없는 미완성 설치다. 아무도 이 경로로 실행하지 않았다.
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    # npm 12 는 허용하지 않은 패키지의 install 스크립트를 막고, --prefix(프로젝트 범위) 설치에는 --allow-scripts 를
    # 받지 않는다(https://docs.npmjs.com/cli/v12/commands/npm-install-scripts). 이 버전 디렉터리의 .npmrc 에
    # 우리가 고른 그 패키지만 허용한다. postinstall 로 네이티브 바이너리를 놓는 패키지(@opencode/cli)가 있다.
    # 의존성의 스크립트는 계속 막힌다. 예전 npm 은 모르는 설정으로 보고 무시한다.
    (target / ".npmrc").write_text("allow-scripts=" + package + "\n", encoding="utf-8")
    say("  {:<14}  npm install --prefix {} {}@{}".format("package", target, package, version))
    out = run_quiet([npm(), "install", "--prefix", str(target), "--no-fund", "--no-audit",
                     "{}@{}".format(package, version)], timeout=900)
    name = entry["detect"]["bin"]
    entry_bin = target / "node_modules" / ".bin" / (name + ".cmd" if WINDOWS else name)
    if not out or out.returncode != 0 or not os.path.lexists(entry_bin):
        for line in ((out.stdout + out.stderr) if out else "npm did not run").splitlines()[-5:]:
            say("      " + line)
        say("      -> npm install failed; current version left as is")
        shutil.rmtree(target, ignore_errors=True)
        return False
    # postinstall 이 실제 바이너리를 못 놓으면(opencode-ai 1.x 의 자리표시자 exe 를 실제로 봤다) 링크만 있고 실행은 안 된다.
    # current 로 바꾸기 전에 그 버전을 직접 실행해 본다.
    if not runs(entry_bin):
        say("      -> the new version does not run ({} --version failed); current version left as is".format(name))
        shutil.rmtree(target, ignore_errors=True)
        return False
    (target / ".complete").write_text(version + "\n", encoding="utf-8")
    return True


def runs(entry_bin):
    """entry_bin --version 이 60초 안에 0 으로 끝나면 True.

    출력을 파이프로 받지 않는다. Windows 는 cmd 가 node 를 손자로 띄워, 시간 초과에 cmd 만 끝내면
    파이프를 쥔 node 때문에 기다림이 끝나지 않는다. 시간이 지나면 프로세스 트리째 끝낸다.
    """
    sys.path.insert(0, str(ROOT / "res" / "tools"))
    from host import kill_tree  # noqa: E402

    # /s 와 바깥 따옴표: 경로에 ( ) & 가 있어도 cmd 가 따옴표를 지운다. /d: AutoRun 을 건너뛴다.
    argv = 'cmd /d /s /c ""{}" --version"'.format(entry_bin) if WINDOWS else [str(entry_bin), "--version"]
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=not WINDOWS)
    except OSError:
        return False
    try:
        return proc.wait(timeout=60) == 0
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        return False


def prune(entry, root, table):
    """current·previous·사용 중이 아닌 버전을 지운다. 남긴 (버전, PID 목록)을 돌려준다."""
    keep = {read_pointer(root, "current"), read_pointer(root, "previous")}
    kept = []
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        if not child.is_dir() or child.name in keep:
            continue
        if child.name.startswith(".trash-"):
            shutil.rmtree(child, ignore_errors=True)
            continue
        if table is None:
            kept.append((child.name, ["unknown"]))
            continue
        pids = users(child, table)
        if pids:
            kept.append((child.name, pids))
            continue
        trash = root / ".trash-{}-{}".format(child.name, os.getpid())
        try:
            # Windows 는 열린 파일이 있으면 이름을 못 바꾼다. 그때도 사용 중으로 보고 남긴다.
            os.rename(child, trash)
        except OSError:
            kept.append((child.name, ["locked"]))
            continue
        shutil.rmtree(trash, ignore_errors=True)
    return kept


def legacy_users(entry, table):
    """PATH 의 npm 이 전역으로 깐 같은 패키지: 없으면 None, 있으면 그것을 쓰는 PID 목록.

    셔임이 그 링크를 바꿔 끼우기 전에 불러야 한다. 링크로 띄운 세션은 명령줄에 링크 경로만 남는다.
    ponytail: PATH 의 npm 하나만 본다. 다른 node(nvm 의 다른 버전)에 깔린 사본은 그대로 둔다.
    """
    package = entry["packages"]["npm"]
    tool = npm()
    out = run_quiet([tool, "root", "-g"]) if tool else None
    if not out or out.returncode != 0:
        return None
    directory = Path(out.stdout.strip()) / Path(*package.split("/"))
    if not directory.is_dir():
        return None
    return ["unknown"] if table is None else users(directory, table)


def remove_legacy(entry, pids):
    """legacy_users 의 결과로, 쓰는 프로세스가 없을 때만 지운다. 남긴 PID 목록 또는 []."""
    if pids is None:
        return []
    if pids:
        return pids
    package = entry["packages"]["npm"]
    tool = npm()
    say("  {:<14}  npm uninstall -g {} (old global copy)".format("legacy", package))
    out = run_quiet([tool, "uninstall", "-g", package], timeout=300)
    if not out or out.returncode != 0:
        say("      -> could not remove the old global copy")
        return ["uninstall-failed"]
    return []


def foreign_command(entry):
    """PATH 에서 같은 이름의 명령이 ai-setup 셔임도, 같은 패키지의 npm 사본도 아니면 그 경로를 알리는 문장.

    다른 방식(brew, winget, 다른 npm 패키지)으로 깔린 같은 이름의 도구 옆에 두 번째 사본을 만들지 않는다.
    """
    found = shutil.which(entry["detect"]["bin"])
    if not found or owns(found):
        return None
    # 찾은 명령 자체가 같은 패키지를 가리킬 때만 옮겨 갈 예전 사본이다. POSIX 는 링크, Windows 는 npm 셔임 내용.
    package = entry["packages"]["npm"]
    if "/node_modules/{}/".format(package) in norm(os.path.realpath(found)).replace("\\", "/"):
        return None
    if WINDOWS:
        try:
            with open(found, "rb") as handle:
                text = handle.read(4000).decode("utf-8", "replace")
        except OSError:
            text = ""
        if "node_modules\\{}\\".format(package.replace("/", "\\")).lower() in text.lower():
            return None
    return "{} already provides {} from another install; remove it first, or keep it and skip this".format(
        found, entry["detect"]["bin"])


def remove_winget(entry):
    """npm-release 이전 Windows 카탈로그가 winget 으로 깐 사본(copilot)을 지운다. 실패하면 False.

    winget portable 은 실행 중이면 지우지 못하고 실패한다. 영수증이 남아 다음 실행이 다시 시도한다.
    """
    package = (entry.get("packages") or {}).get("winget")
    winget = shutil.which("winget") if package else None
    flags = ["--id", package, "--exact", "--accept-source-agreements", "--disable-interactivity"] if winget else []
    listed = run_quiet([winget, "list"] + flags) if winget else None
    if not listed or listed.returncode != 0:
        return True
    say("  {:<14}  winget uninstall --id {} (old winget copy)".format("legacy", package))
    out = run_quiet([winget, "uninstall"] + flags, timeout=300)
    if not out or out.returncode != 0:
        say("      -> could not remove the old winget copy (still running?); run the uninstall again after it ends")
        return False
    return True


def install(entry, processes):
    """최신 버전으로 설치하거나 올린다. 결과: ok·failed·partial(새 버전은 됐고 이전 사본을 사용 중이라 남김).

    processes 는 프로세스 목록을 돌려주는 함수다. npm install 이 오래 걸릴 수 있어, 지우기 직전마다 새로 읽는다.
    """
    if not npm():
        say("  {:<14}  npm not found; install Node first".format("package"))
        return "failed"
    problem = ensure_shim(entry, check_only=True) or foreign_command(entry)
    if problem:
        say("  {:<14}  {}".format("shim", problem))
        return "failed"
    version = latest(entry["packages"]["npm"])
    if not version:
        say("  {:<14}  cannot read the latest version from the npm registry".format("package"))
        return "failed"
    root = store(entry)
    root.mkdir(parents=True, exist_ok=True)
    current = read_pointer(root, "current")
    if current == version and (root / version / ".complete").is_file():
        say("  {:<14}  {} {} is already current".format("package", entry["id"], version))
    else:
        if not install_version(entry, root, version):
            return "failed"
        if current:
            write_pointer(root, "previous", current)
        write_pointer(root, "current", version)
        say("  {:<14}  current {} -> {}".format("switch", current or "none", version))
    in_use = legacy_users(entry, processes())
    ensure_shim(entry)
    if WINDOWS:
        ensure_user_path(shim_dir())
    result = "ok"
    legacy = remove_legacy(entry, in_use)
    # npm uninstall -g 는 자기 bin 의 링크 이름을 가리키는 곳과 상관없이 지운다(접두가 ~/.local 이면 셔임).
    ensure_shim(entry)
    if legacy:
        say("  {:<14}  old global npm copy kept: in use (PID {}); run again after those sessions end".format(
            "legacy", " ".join(map(str, legacy))))
        result = "partial"
    for name, pids in prune(entry, root, processes()):
        say("  {:<14}  {} kept: in use (PID {})".format("prune", name, " ".join(map(str, pids))))
    return result


def uninstall(entry, processes):
    """셔임과 사용 중이 아닌 버전을 지운다. 남긴 것이 있으면 failed(영수증을 남겨 다시 시도하게).

    npm-release 배치가 없으면 예전에 npm -g 로 깐 설치의 영수증이다. 그 사본을 같은 규칙으로 지운다.
    """
    path = shim_path(entry)
    if os.path.lexists(path) and owns(path):
        os.remove(path)
        say("  {:<14}  removed {}".format("shim", path))
    if WINDOWS and shim_dir().is_dir() and not any(shim_dir().iterdir()):
        remove_user_path(shim_dir())
        shim_dir().rmdir()
    root = store(entry)
    if not root.is_dir():
        if WINDOWS and not remove_winget(entry):
            return "failed"
        legacy = remove_legacy(entry, legacy_users(entry, processes()))
        if legacy:
            say("  {:<14}  old global npm copy kept: in use (PID {}); run the uninstall again after it ends".format(
                "legacy", " ".join(map(str, legacy))))
            return "failed"
        return "ok"
    for name in ("current", "previous"):
        try:
            (root / name).unlink()
        except OSError:
            pass
    kept = prune(entry, root, processes())
    for name, pids in kept:
        say("  {:<14}  {} kept: in use (PID {}); run the uninstall again after it ends".format(
            "version", name, " ".join(map(str, pids))))
    if kept:
        return "failed"
    shutil.rmtree(root, ignore_errors=True)
    return "ok"


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("install", "uninstall"):
        print("usage: npm_release.py install|uninstall <id>", file=sys.stderr)
        return 2
    try:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("cannot read {}".format(CATALOG), file=sys.stderr)
        return 2
    entry = next((e for e in catalog if e["id"] == args[1] and (e.get("packages") or {}).get("npm")), None)
    if entry is None:
        print("no npm package in the catalog for {}".format(args[1]), file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT / "res" / "update"))
    from update_ai import process_table  # noqa: E402  (세션 판정과 같은 프로세스 목록)

    action = install if args[0] == "install" else uninstall
    # partial 은 설치가 된 것이다. 영수증에 남아야 uninstall 이 이 배치를 지운다.
    return 0 if action(entry, process_table) in ("ok", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
