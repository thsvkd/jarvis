"""update-ai: bring every installed AI harness and tool up to date, in one pass.

장비마다 갈라져 있던 update-ai.sh(pi·mac)와 Update-Ai.ps1(win)을 한 벌로 합친 구현이다(SPEC D2).
대상과 업데이트 방법은 res/catalog.json 이 정한다. 설치 방식(npm·winget·brew)을 실행 파일
위치로 판별해 그 방식으로 올리고, 해당이 없으면 카탈로그의 기본 update 명령을 쓴다.

- 깔려 있는 것만 손댄다. 없는 것은 설치하지 않고 건너뛴다(SPEC 불변식).
- --check 는 현재·최신 버전만 읽는다. 업데이트 명령과 잠금을 만들지 않는다.
- 실행 중인 세션이 있어도 올린다. 버전별 배치(native 버전 디렉터리, npm-release, brew)는 세션이 쓰는
  파일을 지우거나 바꾸지 않기 때문이다. 세션을 실제로 끊을 수 있는 단계(이전 설치 npm -g·winget,
  codex 관리형 데몬 교체)만 세션이 돌 때 --allow-interrupt 를 요구하고, 없으면 보류(실패)로 남긴다.
- 하나가 실패해도 나머지를 계속하고, 결과 표를 찍은 뒤 exit 1 로 끝난다.

exit: 0 = 모두 성공, 1 = 하나 이상 실패(또는 다른 실행이 잠금을 쥠), 2 = 인자 오류.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "res" / "tools"))
sys.path.insert(0, str(ROOT / "res"))
from host import kill_tree, locations, platform_key  # noqa: E402
import npm_release  # noqa: E402

CATALOG = ROOT / "res" / "catalog.json"
# 시험은 UPDATE_AI_CODEX_LATEST 로 가짜 주소(file://)를 준다(네트워크에 닿지 않게)
CODEX_WINDOWS_UPDATE = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-c",
                        "$env:CODEX_NON_INTERACTIVE=1; irm https://chatgpt.com/codex/install.ps1 | iex"]
CODEX_LATEST = os.environ.get("UPDATE_AI_CODEX_LATEST") or "https://api.github.com/repos/openai/codex/releases/latest"
# 세션이 쓰는 파일을 그 자리에서 바꾸거나 세션을 끝내는 설치 방식. 세션이 돌면 --allow-interrupt 없이는 손대지 않는다.
INTERRUPTING = ("npm", "winget")
STEP_TIMEOUT = 900
VERSION_RE = re.compile(r"\d+\.\d+\.\d+[-.\w]*")
# winget 은 올릴 것이 없으면 0x8A15002B 로 끝난다. 실패가 아니다.
WINGET_NO_UPDATE = -1978335189
WINGET_FLAGS = ["--exact", "--silent", "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity"]


def say(text=""):
    print(text, flush=True)


# ── 설치 위치 판별 ─────────────────────────────────────────────────────

def npm_for(path, package):
    """그 실행 파일이 npm 전역 설치로 깔린 package 이면 그 npm, 아니면 None.

    같은 bin 에 npm 이 있다는 것만으로는 부족하다(brew 의 node 옆 brew 판 gemini 등). 패키지가
    그 npm 의 node_modules 안에 실제로 있는지 본다.
    """
    if os.name == "nt":
        directory = os.path.dirname(path)
        if not os.path.isdir(os.path.join(directory, "node_modules", *package.split("/"))):
            return None
        for name in ("npm.cmd", "npm.exe"):
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate):
                return candidate  # nvm-windows: 셔임과 npm 이 같은 node 디렉터리에 있다
        # 표준 MSI: 셔임은 %APPDATA%\npm 에, npm 은 Program Files\nodejs 에 있다. 그 node 의 npm 이 PATH 에 있다.
        return shutil.which("npm")
    real = os.path.realpath(path)
    marker = os.sep + "node_modules" + os.sep
    if marker + package.replace("/", os.sep) + os.sep not in real:
        return None
    prefix = real.split(marker, 1)[0]
    if os.path.basename(prefix) == "lib":
        prefix = os.path.dirname(prefix)
    candidate = os.path.join(prefix, "bin", "npm")
    return candidate if os.path.isfile(candidate) else None


def run_quiet(argv, timeout=60, env=None):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             stdin=subprocess.DEVNULL, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return out


def succeeded(argv):
    out = run_quiet(argv)
    return bool(out) and out.returncode == 0


def path_first(directory):
    """npm 은 자기를 돌리는 node 의 전역 경로에 설치하므로, 그 npm 의 bin 을 PATH 맨 앞에 둔다."""
    env = dict(os.environ)
    env["PATH"] = directory + os.pathsep + env.get("PATH", "")
    return env


def method_for(entry, path):
    """(방식, 값). 방식: npm·winget·brew·brew_cask·default·none.

    먼저 '찾은 실행 파일이 그 패키지 관리자의 것인가'로 고른다. 같은 도구가 다른 방식으로도
    깔려 있을 수 있어서(win 의 네이티브 claude + winget claude) 패키지 목록만 보면 엉뚱한 사본을 올린다.
    패키지 목록은 기본 update 명령이 없는 도구의 마지막 확인에만 쓴다.
    """
    packages = entry.get("packages") or {}
    plat = platform_key()
    if packages.get("npm") and npm_release.owns(path):
        return "npm-release", None
    if packages.get("npm"):
        npm = npm_for(path, packages["npm"])
        if npm:
            return "npm", npm
    if plat == "windows" and packages.get("winget") and "\\winget\\" in path.lower():
        return "winget", packages["winget"]
    if plat == "macos" and shutil.which("brew"):
        out = run_quiet(["brew", "--prefix"])
        brew_prefix = out.stdout.strip() if out and out.returncode == 0 else ""
        real = os.path.realpath(path)
        if brew_prefix and (real.startswith(brew_prefix + os.sep) or "/Caskroom/" in real or "/Cellar/" in real):
            for key in ("brew_cask", "brew"):
                if packages.get(key):
                    return key, packages[key]
    if entry.get("update"):
        return "default", entry["update"]
    if plat == "windows" and packages.get("winget") and shutil.which("winget") and succeeded(
            ["winget", "list", "--id", packages["winget"], "--exact", "--accept-source-agreements", "--disable-interactivity"]):
        return "winget", packages["winget"]
    if plat == "macos" and shutil.which("brew"):
        for key, flag in (("brew_cask", ["--cask"]), ("brew", ["--formula"])):
            if packages.get(key) and succeeded(["brew", "list"] + flag + [packages[key].split("/")[-1]]):
                return key, packages[key]
    return "none", None


# ── 버전 ──────────────────────────────────────────────────────────────

def version_of(path):
    out = run_quiet([path, "--version"], timeout=30)
    if not out or out.returncode != 0:
        return ""
    match = VERSION_RE.search((out.stdout or "") + (out.stderr or ""))
    # "GitHub Copilot CLI 1.0.105." 처럼 문장 끝 마침표가 붙는다. 붙은 채로 두면 최신 판과 같아도 다르게 본다
    return match.group(0).rstrip(".") if match else ""


def latest_of(entry, path, method, value):
    """--check 용 최신 버전. 레지스트리에 물어볼 길이 있는 것만 답한다."""
    packages = entry.get("packages") or {}
    if method == "npm":
        out = run_quiet([value, "view", packages["npm"], "version"], env=path_first(os.path.dirname(value)))
        return out.stdout.strip() if out and out.returncode == 0 else ""
    if method == "npm-release":
        return npm_release.latest(packages["npm"])
    if entry["id"] == "codex":
        # standalone 설치는 레지스트리가 없다. Codex TUI 의 갱신 확인과 같은 곳(GitHub 최신 릴리스, 태그 rust-v<판>)을 본다.
        # 출처: openai/codex codex-rs/tui/src/updates.rs 의 LATEST_RELEASE_URL
        try:
            with urllib.request.urlopen(urllib.request.Request(CODEX_LATEST, headers={"User-Agent": "update-ai"}), timeout=15) as r:
                tag = json.loads(r.read().decode("utf-8")).get("tag_name") or ""
        except (OSError, ValueError):
            return ""
        return tag[len("rust-v"):] if tag.startswith("rust-v") else ""
    if entry["id"] == "grok":
        out = run_quiet([path, "update", "--check", "--json"])
        if out and out.returncode == 0:
            try:
                return json.loads(out.stdout).get("latestVersion") or ""
            except ValueError:
                return ""
    return ""


# ── 실행 중인 세션 ─────────────────────────────────────────────────────

def process_table():
    """[(pid, 명령줄)]. 확인할 수 없으면 None (그때는 실행 중으로 본다)."""
    if os.name == "nt":
        script = "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
        # cron·작업 스케줄러처럼 PATH 가 비어 있어도 찾도록 절대 경로를 쓴다.
        powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        out = run_quiet([powershell, "-NoProfile", "-Command", script])
        if not out or out.returncode != 0:
            return None
        rows = [line.split("\t", 1) for line in out.stdout.splitlines() if "\t" in line]
    else:
        # -ww: 터미널이 아니면 ps 가 명령줄을 COLUMNS(기본 80)에서 잘라, 버전 경로가 뒤에 있는 세션을 놓친다.
        out = run_quiet([shutil.which("ps") or "/bin/ps", "-ww", "-U", str(os.getuid()), "-o", "pid=", "-o", "args="])
        if not out or out.returncode != 0:
            return None
        rows = [line.strip().split(None, 1) for line in out.stdout.splitlines() if line.strip()]
    table = []
    for row in rows:
        if len(row) == 2 and row[0].strip().isdigit():
            table.append((int(row[0]), row[1].strip()))
    return table


def runs_target(args, entry):
    """이 명령줄이 이 대상의 세션인가.

    카탈로그의 sessions.ignore(데몬·데스크톱 앱 등 세션이 아닌 것)와 sessions.match(이름이 다른
    실행 파일: qemu 로 도는 grok)를 먼저 본다. 그다음 명령줄 앞 다섯 단어에서
    실행 파일 이름을 찾는다. node 로 도는 CLI 는 프로세스 이름이 node 라서 이름으로는 못 찾는다.
    명령줄 전체를 보면 그 이름을 글자로 품은 셸 명령(update-ai.sh claude 등)도 잡히므로,
    첫 단어(argv0)이거나 경로가 붙은 단어만 본다.
    """
    sessions = entry["detect"].get("sessions") or {}
    low = args.lower()
    if any(marker.lower() in low for marker in sessions.get("ignore", [])):
        return False
    if any(marker.lower() in low for marker in sessions.get("match", [])):
        return True
    if ".app/contents/" in low:
        return False  # macOS 앱 번들 사본은 앱이 따로 관리한다
    return names_bin(low, entry)


def names_bin(low, entry):
    """소문자 명령줄 low 의 앞 다섯 단어가 이 대상의 실행 파일을 가리키는가."""
    name = entry["detect"]["bin"].lower()
    npm = ((entry.get("packages") or {}).get("npm") or "").lower()
    for index, word in enumerate(low.split()[:5]):
        word = word.strip("\"'")
        has_sep = "/" in word or "\\" in word
        if not has_sep and index:
            continue
        base = re.split(r"[\\/]", word)[-1]
        stem = re.sub(r"\.(exe|cmd|bat|ps1|js|mjs|cjs|py)$", "", base)
        if stem == name or (npm and has_sep and ("/" + npm + "/") in word.replace("\\", "/")):
            return True
    return False


def running_pids(entry, table):
    me = {os.getpid(), os.getppid()}
    return [pid for pid, args in table if pid not in me and runs_target(args, entry)]


# ── 잠금 ──────────────────────────────────────────────────────────────

def pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # 접근 거부(5)는 살아 있는 다른 권한의 프로세스다. 잠금을 뺏지 않는다.
            return ctypes.get_last_error() == 5
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock(home):
    """잠금 디렉터리 경로, 다른 실행이 쥐고 있으면 None. PID 가 죽은 잠금은 한 번 치우고 다시 잡는다.

    ponytail: mkdir 과 PID 기록 사이의 짧은 틈에 두 번째 실행이 오면 잠금을 뺏을 수 있다.
    동시에 손으로 두 번 돌릴 일이 생기면 OS 파일 잠금으로 바꾼다.
    """
    lock = home / ".cache" / "update-ai.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            lock.mkdir()
        except FileExistsError:
            try:
                holder = int((lock / "pid").read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                holder = 0
            if pid_alive(holder):
                print("update-ai is already running (PID {}): {}".format(holder, lock), file=sys.stderr)
                return None
            shutil.rmtree(lock, ignore_errors=True)
            continue
        (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")
        return lock
    print("cannot create the lock: {}".format(lock), file=sys.stderr)
    return None


# ── 단계 실행 ─────────────────────────────────────────────────────────

def run_step(label, argv, ok_codes=(0,), env=None):
    """출력을 들여 써서 그대로 흘린다. 성공이면 True."""
    say("  {:<14}  {}".format(label, " ".join(argv)))
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, start_new_session=(os.name != "nt"))
    except OSError as error:
        say("      {}".format(error.strerror or type(error).__name__))
        return False
    # 네트워크가 멈추면 잠금을 쥔 채 영영 기다린다. 시간이 지나면 프로세스 트리째 끊는다.
    timer = threading.Timer(STEP_TIMEOUT, kill_tree, [proc])
    timer.start()
    try:
        for raw in iter(proc.stdout.readline, b""):
            say("      " + raw.decode("utf-8", errors="replace").rstrip())
        code = proc.wait()
    except BaseException:
        # 자식은 별도 세션이라 터미널의 Ctrl-C 가 닿지 않는다. 잠금을 풀기 전에 직접 끊는다.
        kill_tree(proc)
        raise
    finally:
        timer.cancel()
    if code not in ok_codes:
        say("      -> {} step failed (rc={})".format(label, code))
        return False
    return True


def claude_user_plugins(path):
    """사용자 범위로 설치된 Claude Code 플러그인 id. marketplace update 는 목록만 갱신하므로 따로 올린다."""
    out = run_quiet([path, "plugin", "list", "--json"])
    if not out or out.returncode != 0:
        return None
    try:
        return [p["id"] for p in json.loads(out.stdout) if p.get("scope") == "user" and p.get("id")]
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def version_key(text):
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def is_current(installed, latest):
    """설치본이 최신 이상인가. 앱 안에서 받은 prerelease·늦게 갱신된 매니페스트로 설치본이 더 새로울 수 있다."""
    return installed == latest or version_key(str(installed or "")) > version_key(str(latest or ""))


def codex_daemon_state(path):
    """codex app-server daemon version 의 JSON. 관리형 데몬 기능이 없으면 None."""
    out = run_quiet([path, "app-server", "daemon", "version"])
    if not out or out.returncode != 0:
        return None
    try:
        state = json.loads(out.stdout)
    except ValueError:
        return None
    return state if isinstance(state, dict) else None


def codex_unmanaged_servers(entry, table):
    """원격 세션 등이 직접 띄운 `codex app-server --listen`(관리형 데몬이 아닌 것)의 PID.

    `daemon update` 는 이것을 교체하지 못한다. CLI 만 올리면 그 서버는 지워진 옛 바이너리로
    계속 돌며 옛 모델 목록을 내준다.
    """
    pids = []
    for pid, args in table or []:
        low = args.lower()
        if " app-server " in low and "--listen" in low and "--managed-daemon" not in low and names_bin(low, entry):
            pids.append(pid)
    return pids


def codex_daemon_sync(path, allow_interrupt):
    """관리형 app-server 데몬을 CLI 버전에 맞춘다. (필수 실패, 선택 실패).

    공식 경로(daemon update)를 먼저 쓰고, 그래도 어긋날 때만 --from-cli 로 데몬을 CLI 에
    고정한다(고정되면 데몬 자동 갱신이 꺼진다). 데몬이 이미 CLI 보다 새 버전이면 그대로 둔다.
    """
    state = codex_daemon_state(path)
    if state is None:
        say("  {:<14}  no managed daemon; skipped".format("daemon"))
        return [], []
    cli = state.get("cliVersion") or ""
    managed = state.get("managedCodexVersion") or ""
    server = state.get("appServerVersion") or ""
    status = state.get("status") or ""
    if not cli:
        say("  {:<14}  cannot read the daemon version".format("daemon"))
        return ["daemon-version"], []
    if not managed and status != "running":
        say("  {:<14}  no managed daemon install; skipped".format("daemon"))
        return [], []
    if managed == cli and (status != "running" or server == cli):
        say("  {:<14}  managed {} / running {}: in sync".format("daemon", managed, server or "none"))
        return [], []
    if managed and version_key(managed) > version_key(cli):
        say("  {:<14}  managed {} is newer than the CLI {}; left as is (run again when the CLI catches up)".format("daemon", managed, cli))
        return [], ["daemon-newer"]
    if status == "running" and not allow_interrupt:
        say("  {:<14}  the running daemon ({}) was not replaced; run again with --allow-interrupt".format("daemon", server))
        return [], ["daemon-deferred"]
    was_running = status == "running"
    # codex 0.166 은 고정된 데몬에 --from-cli 없는 update 를 인자 오류(rc=2)로 거부한다.
    # 그래서 실패해도 멈추지 않고, 아래 버전 대조가 --from-cli 고정으로 넘어갈지 판정한다.
    run_step("daemon", [path, "app-server", "daemon", "update", "--yes"])
    if ((codex_daemon_state(path) or {}).get("managedCodexVersion") or "") != cli:
        if not run_step("daemon-pin", [path, "app-server", "daemon", "update", "--from-cli", "--yes"]):
            return ["daemon-pin"], []
    if was_running and (codex_daemon_state(path) or {}).get("status") != "running":
        if not run_step("daemon-start", [path, "app-server", "daemon", "start"]):
            return ["daemon-start"], []
    for _ in range(20):
        state = codex_daemon_state(path) or {}
        managed = state.get("managedCodexVersion") or ""
        server = state.get("appServerVersion") or ""
        # 돌던 데몬이면 새 버전으로 다시 떠 있어야 맞춘 것이다.
        if managed == cli and (not was_running or (state.get("status") == "running" and server == cli)):
            say("  {:<14}  managed {} / running {}: in sync".format("daemon", managed, server or "none"))
            return [], []
        time.sleep(1)
    say("  {:<14}  managed {} / running {} does not match the CLI {}".format("daemon", managed, server or "none", cli))
    return ["daemon-verify"], []


def codex_check_rows(path, target, table):
    """--check 에서 데몬·실행 중 서버의 상태 행."""
    rows = []
    state = codex_daemon_state(path) or {}
    managed, server = state.get("managedCodexVersion") or "", state.get("appServerVersion") or ""
    if managed:
        rows.append(("codex-daemon", "up to date" if managed == target else "needs sync", managed, target))
    if state.get("status") == "running":
        rows.append(("codex-server", "up to date" if server == target else "needs sync", server or "unknown", target))
    return rows


def update_target(entry, path, method, value, allow_interrupt=False):
    """(필수 단계 실패 목록, 선택 단계 실패 목록, 건너뛴 이유 또는 None)."""
    broke, warnings = [], []
    packages = entry.get("packages") or {}
    if method == "npm-release":
        try:
            outcome = npm_release.install(entry, process_table)
        except OSError as error:
            # 이 대상만 실패로 남기고 나머지 대상과 결과 표는 그대로 간다.
            say("      {}: {}".format(type(error).__name__, error))
            outcome = "failed"
        if outcome == "partial":
            warnings.append("old npm copy in use")
            outcome = "ok"
    elif method == "npm":
        outcome = run_step("package", [value, "install", "-g", packages["npm"] + "@latest"],
                           env=path_first(os.path.dirname(value)))
    elif method == "winget":
        outcome = run_step("package", ["winget", "upgrade", "--id", value] + WINGET_FLAGS, ok_codes=(0, WINGET_NO_UPDATE))
    elif method in ("brew", "brew_cask"):
        # 기본값이면 brew 가 올린 직후 이전 버전(Cellar·Caskroom)을 지워 실행 중인 세션의 파일이 사라진다.
        outcome = run_step("package", ["brew", "upgrade"] + (["--cask"] if method == "brew_cask" else []) + [value],
                           env=dict(os.environ, HOMEBREW_NO_INSTALL_CLEANUP="1"))
    elif method == "default" and entry["id"] == "codex" and os.name == "nt":
        # `codex update` 는 공식 설치기를 프로필을 읽는 powershell 로 띄운다. 사용자 프로필(oh-my-posh 등)이 있으면
        # 설치기의 StrictMode 가 RuntimeInformation::OSArchitecture 에서 멈춘다(kgolf 실측). 같은 공식 명령을 -NoProfile 로 부른다
        outcome = run_step("self-update", CODEX_WINDOWS_UPDATE)
    elif method == "default":
        outcome = run_step("self-update", [path] + list(value[1:]))
    else:
        say("  {:<14}  no known update method for how this copy was installed".format("package"))
        outcome = "skipped: unknown install method"
    if isinstance(outcome, str) and outcome.startswith("skipped"):
        return broke, warnings, outcome
    if outcome in (False, "failed"):
        broke.append("update")
        return broke, warnings, None
    for argv in entry.get("plugin_update") or []:
        if not run_step("plugins", [path] + list(argv[1:])):
            warnings.append("plugins")
    if entry["id"] == "claude":
        plugins = claude_user_plugins(path)
        if plugins is None:
            warnings.append("plugin-list")
        for plugin in plugins or []:
            if not run_step("plugin", [path, "plugin", "update", "--scope", "user", plugin]):
                warnings.append(plugin)
    if entry["id"] == "codex":
        daemon_broke, daemon_warnings = codex_daemon_sync(path, allow_interrupt)
        broke += daemon_broke
        warnings += daemon_warnings
    return broke, warnings, None


# ── 진입점 ────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(prog="update-ai", description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only read current and latest versions; change nothing")
    parser.add_argument("--only", action="append", default=[], metavar="A,B", help="update only these targets")
    parser.add_argument("--allow-interrupt", action="store_true",
                        help="also run steps that can break running sessions (old npm -g/winget installs, "
                             "restarting the codex daemon)")
    parser.add_argument("targets", nargs="*", help="same as --only")
    args = parser.parse_args(argv)

    try:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("cannot read {}".format(CATALOG), file=sys.stderr)
        return 2
    ids = [e["id"] for e in catalog]
    only = [t for item in args.only + args.targets for t in item.split(",") if t]
    unknown = [t for t in only if t not in ids]
    if unknown:
        print("unknown target: {} (choose from: {})".format(", ".join(unknown), ",".join(ids)), file=sys.stderr)
        return 2

    home = Path(os.environ.get("USERPROFILE") if os.name == "nt" and os.environ.get("USERPROFILE") else Path.home())
    say("AI harness update" + (" (check only; nothing is changed)" if args.check else " (installed targets only)"))
    lock = None
    if not args.check:
        lock = acquire_lock(home)
        if lock is None:
            return 1
    try:
        return run_all(catalog, only, args)
    finally:
        if lock is not None:
            shutil.rmtree(lock, ignore_errors=True)


def runnable(cmd):
    """카탈로그 명령을 어디서든 붙여 넣어 돌릴 수 있게 한다.

    npm-release 명령(python3 res/tools/...)은 저장소 기준 상대 경로이고, Windows 의 python 은 Store 별칭일 수 있다.
    """
    head, _, rest = cmd.partition(" ")
    if head not in ("python3", "python"):
        return cmd
    script, _, tail = rest.partition(" ")
    if os.name == "nt":
        return '& "{}" "{}" {}'.format(sys.executable, ROOT / script, tail)
    return "{} {} {}".format(shlex.quote(sys.executable), shlex.quote(str(ROOT / script)), tail)


def run_all(catalog, only, args):
    cache = {}

    def table():
        if "table" not in cache:
            cache["table"] = process_table()
        return cache["table"]

    results, failed, notes = [], 0, []
    for entry in catalog:
        if only and entry["id"] not in only:
            continue
        found = locations(entry["detect"]["bin"])
        if not found:
            results.append((entry["id"], "not installed", "", ""))
            continue
        path = found[0]
        method, value = method_for(entry, path)
        before = version_of(path)
        if args.check:
            latest = latest_of(entry, path, method, value)
            state = "cannot check latest" if not latest else ("up to date" if is_current(before, latest) else "new version")
            results.append((entry["id"], state, before, latest or before))
            if entry["id"] == "codex":
                results += codex_check_rows(path, latest or before, table())
            continue

        say("")
        say("── {}  {}  ({}) ──".format(entry["id"], before or "version unknown", method))
        if entry["id"] == "codex":
            servers = codex_unmanaged_servers(entry, table())
            if servers:
                say("  {:<14}  an unmanaged codex app-server is running (PID {}); not updated".format("blocked", " ".join(map(str, servers))))
                say("  {:<14}  stop it, start the managed daemon with: codex app-server daemon start, then run again".format(""))
                results.append((entry["id"], "failed: unmanaged app-server", before, before))
                failed += 1
                continue
        pids = running_pids(entry, table()) if table() is not None else ["unknown"]
        if pids and method in INTERRUPTING and not args.allow_interrupt:
            say("  {:<14}  a {} session is running (PID {}) and a {} update replaces its files in place; deferred".format(
                "deferred", entry["id"], " ".join(map(str, pids)), method))
            say("  {:<14}  close it and run again, or pass --allow-interrupt".format(""))
            official = ((entry.get("install") or {}).get(platform_key()) or [{}])[0]
            if official.get("method") not in (None, method):
                say("  {:<14}  installed the catalog way ({}), it updates live: {}".format(
                    "", official["method"], runnable(official["cmd"])))
            results.append((entry["id"], "failed: deferred (in use)", before, before))
            failed += 1
            continue
        broke, warnings, skipped = update_target(entry, path, method, value, args.allow_interrupt)
        if skipped:
            results.append((entry["id"], skipped, before, before))
            continue
        after = version_of(path)
        if not broke and not after:
            broke.append("version")
        if not broke:
            latest = latest_of(entry, path, method, value)
            if latest and not is_current(after, latest):
                broke.append("latest is " + latest)
        if broke:
            state = "failed: " + ", ".join(broke + warnings)
            failed += 1
        elif warnings:
            state = "partly failed: " + ", ".join(warnings)
            failed += 1
        else:
            state = "updated" if before != after else "up to date"
        results.append((entry["id"], state, before, after))
        if before != after and pids and pids != ["unknown"]:
            notes.append("{}: running sessions (PID {}) keep {} until restarted".format(
                entry["id"], " ".join(map(str, pids)), before or "the old version"))

    say("")
    say("── Results ──")
    for name, state, before, after in results:
        if state == "not installed":
            say("    {:<10} not installed".format(name))
        elif state.startswith(("skipped", "cannot check")):
            say("  ? {:<10} {}  ({})".format(name, after, state))
        elif state in ("updated", "new version", "needs sync"):
            say("  ^ {:<10} {} -> {}  ({})".format(name, before, after, state))
        elif state.startswith(("failed", "partly")):
            say("  x {:<10} {}  ({})".format(name, after, state))
        else:
            say("  v {:<10} {}  ({})".format(name, after, state))
    for note in notes:
        say("  note: " + note)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
