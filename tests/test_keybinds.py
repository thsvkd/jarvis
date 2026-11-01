"""줄바꿈 키바인딩 설치 계약 (Test-1..Test-27 중 CLI·파일 쪽).

이 모듈은 구현이 아니라 **공개 계약**을 고정한다. setup.sh / setup.ps1 에
`--keybinds`/`-Keybinds`, `--newline`/`-Newline` 이 아직 없으면 RED 가 맞다.

구현자가 존중해야 하는 경로 주입 환경 변수:

- ``CLAUDE_SETUP_VSCODE_KEYBINDINGS`` — VS Code keybindings.json
- ``CLAUDE_SETUP_CURSOR_KEYBINDINGS`` — Cursor keybindings.json
- ``CLAUDE_SETUP_WT_SETTINGS`` — Windows Terminal settings.json
  (비우거나 대상 파일이 없으면 WT skip, Test-27)
- ``CLAUDE_SETUP_DETECTED_TERMINAL`` — 감지된 터미널 이름 (예: gnome-terminal, Test-24)

매니페스트 필드: ``~/.claude/.claude-code-setup.json`` 의
``keybinds.newline_chord`` = ``shift-enter`` | ``ctrl-enter``.

HOME/USERPROFILE/APPDATA/LOCALAPPDATA 를 샌드박스로 돌린다. 실제 사용자
프로필·SSH·TUI·실제 Windows Terminal 창은 쓰지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "jarvis" / "modules" / "setup"
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

SETUP_SH = ROOT / "scripts" / "setup.sh"
SETUP_PS1 = ROOT / "scripts" / "setup.ps1"

# 줄바꿈 시퀀스 SSoT 후보. 구현은 이 셋 중 하나를 고른다. 단독 \\n 과 Ctrl 변종 CSI 는 금지.
SSOT_SEQUENCES = (
    "\u001b[13;2u",
    "\u001b\r",
    "\u001b[27;2;13~",
)
SSOT_ESCAPED = (
    r"\u001b[13;2u",
    r"\u001b\r",
    r"\u001b[27;2;13~",
    r"\x1b[13;2u",
    r"\x1b\r",
    r"\x1b[27;2;13~",
)

AGENT_FILE_NAMES = (
    "claude", "claude.exe", "claude.cmd", "claude.bat", "claude.ps1",
    "codex", "codex.exe", "codex.cmd", "codex.bat", "codex.ps1",
    "grok", "grok.exe", "grok.cmd", "grok.bat", "grok.ps1",
)

FOREIGN_VSCODE_KEY = "ctrl+alt+f9"
FOREIGN_VSCODE_COMMAND = "workbench.action.terminal.clear"
FOREIGN_CURSOR_COMMAND = "aichat.newchataction"
EDITOR_SHIFT_ENTER_COMMAND = "editor.action.insertLineAfter"
NOTEBOOK_SHIFT_ENTER_COMMAND = "notebook.cell.executeAndFocusContainer"
CLAUDE_PRESERVE_COMMAND = "chat:compact"
CLAUDE_PRESERVE_KEY = "ctrl+g"
CODEX_SENTINEL_MODEL = "sentinel-model"
TMUX_SENTINEL = "# user-sentinel-tmux"
GROK_SENTINEL = 'sentinel_multiline_key = "keep-me"'
WT_FOREIGN_KEYS = "ctrl+shift+c"
# 사용자가 수정 Enter 에 직접 걸어둔 sendInput. 우리 SSoT 시퀀스가 아니므로 살아남아야 한다.
WT_FOREIGN_SENDINPUT = "user-owned-payload"

posix_only = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="POSIX only (needs pty / bash)",
)
tmux_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="tmux.conf is a POSIX install target",
)


def _powershell_exe():
    return shutil.which("pwsh") or shutil.which("powershell")


def posix_available():
    return sys.platform != "win32" and shutil.which("bash") is not None


def powershell_available():
    return _powershell_exe() is not None


def native_worker():
    if sys.platform == "win32":
        if not powershell_available():
            pytest.skip("PowerShell required")
        return "powershell"
    if not posix_available():
        pytest.skip("POSIX bash required")
    return "posix"


def skip_if_worker_missing(worker):
    if worker == "posix" and not posix_available():
        pytest.skip("POSIX bash required")
    if worker == "powershell" and not powershell_available():
        pytest.skip("PowerShell required")


# ── JSONC / 시퀀스 / 키 표기 ──────────────────────────────────────────


def parse_jsonc(text: str):
    """VS Code/Cursor keybindings.json 은 // 와 /* */ 주석을 허용한다."""
    if text.startswith("\ufeff"):
        text = text[1:]
    out = []
    i = 0
    n = len(text)
    in_str = False
    escape = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(n, i + 2)
            continue
        out.append(ch)
        i += 1
    stripped = "".join(out).strip()
    if not stripped:
        return []
    return json.loads(stripped)


def load_keybindings(path: Path):
    assert path.exists(), f"keybindings file is missing: {path}"
    data = parse_jsonc(path.read_text(encoding="utf-8"))
    assert isinstance(data, list), f"keybindings.json top level must be an array: {path}"
    return data


def vscode_key(chord: str) -> str:
    return "shift+enter" if chord == "shift-enter" else "ctrl+enter"


def vscode_other_key(chord: str) -> str:
    return "ctrl+enter" if chord == "shift-enter" else "shift+enter"


def codex_key(chord: str) -> str:
    return chord  # shift-enter / ctrl-enter


def normalize_seq(text) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if text in SSOT_SEQUENCES or text in SSOT_ESCAPED:
        return text
    try:
        decoded = text.encode("utf-8").decode("unicode_escape")
    except Exception:
        decoded = text
    return decoded


def is_allowed_sequence(text: str) -> bool:
    raw = text if isinstance(text, str) else str(text)
    if raw in SSOT_SEQUENCES or raw in SSOT_ESCAPED:
        return True
    return normalize_seq(raw) in SSOT_SEQUENCES


def is_forbidden_sequence(text: str) -> bool:
    raw = text if isinstance(text, str) else str(text)
    if raw in ("\n", "\\n", "\r\n"):
        return True
    if normalize_seq(raw) in ("\n", "\r\n"):
        return True
    if "13;5u" in raw or "13;5~" in raw:
        return True
    return False


def sendsequence_text(entry: dict) -> str:
    args = entry.get("args") or {}
    if isinstance(args, dict):
        return args.get("text") or args.get("sequence") or ""
    return ""


def is_managed_sendsequence(entry: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("command") != "workbench.action.terminal.sendSequence":
        return False
    when = str(entry.get("when") or "")
    if "terminalFocus" not in when:
        return False
    if re.search(r"editor|notebook", when, re.I):
        return False
    seq = sendsequence_text(entry)
    if not is_allowed_sequence(seq) or is_forbidden_sequence(seq):
        return False
    return True


def managed_sendsequences(entries):
    return [e for e in entries if is_managed_sendsequence(e)]


def norm_key(value) -> str:
    return str(value or "").lower().replace(" ", "")


# ── 픽스처 본문 ──────────────────────────────────────────────────────


DEFAULT_VSCODE_JSONC = """\
// Place your key bindings in this file to override the defaults
[
    {
        "key": "%s",
        "command": "%s",
        "when": "terminalFocus"
    },
    {
        "key": "shift+enter",
        "command": "%s",
        "when": "editorTextFocus && !editorReadonly"
    },
    {
        "key": "shift+enter",
        "command": "%s",
        "when": "notebookEditorFocused && !inputFocus"
    },
    {
        "key": "ctrl+alt+k",
        "command": "workbench.action.terminal.sendSequence",
        "args": {"text": "keep-me"},
        "when": "terminalFocus"
    }
]
""" % (FOREIGN_VSCODE_KEY, FOREIGN_VSCODE_COMMAND,
       EDITOR_SHIFT_ENTER_COMMAND, NOTEBOOK_SHIFT_ENTER_COMMAND)

DEFAULT_CURSOR_JSONC = """\
// Cursor user keybindings
[
    {
        "key": "%s",
        "command": "%s",
        "when": "terminalFocus"
    },
    {
        "key": "ctrl+shift+u",
        "command": "%s",
        "when": "editorTextFocus"
    },
    {
        "key": "shift+enter",
        "command": "%s",
        "when": "editorTextFocus && !editorReadonly"
    },
    {
        "key": "shift+enter",
        "command": "%s",
        "when": "notebookEditorFocused && !inputFocus"
    },
    {
        "key": "ctrl+alt+k",
        "command": "workbench.action.terminal.sendSequence",
        "args": {"text": "keep-me"},
        "when": "terminalFocus"
    }
]
""" % (FOREIGN_VSCODE_KEY, FOREIGN_VSCODE_COMMAND, FOREIGN_CURSOR_COMMAND,
       EDITOR_SHIFT_ENTER_COMMAND, NOTEBOOK_SHIFT_ENTER_COMMAND)

# Claude Code 가 읽는 형식: https://code.claude.com/docs/en/keybindings
# (스키마 https://www.schemastore.org/claude-code-keybindings.json). 최상위 "bindings" 배열이 없으면
# Claude Code 는 파일 전체를 무시한다.
DEFAULT_CLAUDE_KEYBINDINGS = {
    "$schema": "https://www.schemastore.org/claude-code-keybindings.json",
    "bindings": [
        {"context": "Global", "bindings": {"f9": "app:toggleHelp"}},
        {"context": "Chat", "bindings": {
            CLAUDE_PRESERVE_KEY: CLAUDE_PRESERVE_COMMAND,
            "escape": "chat:cancel",
        }},
    ],
}

DEFAULT_CODEX_TOML = """\
model = "%s"

[mcp]
enabled = true

[tui.keymap.editor]
insert_newline = ["ctrl-j"]
indent = ["tab"]

[projects.sentinel]
trust = true
""" % CODEX_SENTINEL_MODEL

DEFAULT_GROK_TOML = """\
# grok user config
multiline = false
%s

[ui]
multiline = false
""" % GROK_SENTINEL

DEFAULT_TMUX = """\
%s
set -g mouse on
""" % TMUX_SENTINEL

DEFAULT_WT_SETTINGS = """\
{
    "actions": [
        {
            "command": "copy",
            "keys": "%s"
        },
        {
            "command": { "action": "sendInput", "input": "%s" },
            "keys": "ctrl+enter"
        }
    ],
    "profiles": {
        "defaults": {},
        "list": []
    }
}
""" % (WT_FOREIGN_KEYS, WT_FOREIGN_SENDINPUT)


def _dir_has_agent(path: Path) -> bool:
    try:
        return any((path / name).exists() for name in AGENT_FILE_NAMES)
    except OSError:
        return False


def isolated_path(stub: Path) -> str:
    """claude/codex/grok 가 잡히지 않는 PATH. 셸·파이썬만 남긴다."""
    keep = [str(stub.resolve())]
    seen = set(keep)
    needed = ["bash", "pwsh", "powershell", "python", "python3", "py"]
    # 시스템 디렉터리라도 claude/codex/grok 이 거기 설치돼 있으면 PATH 에 넣으면 안 된다.
    # 그러면 "에이전트가 없는 환경" 을 전제한 skip 테스트가 그 머신에서만 깨진다 (범용 테스트).
    def _keep_if_clean(d: str):
        if not d or d in seen:
            return
        if _dir_has_agent(Path(d)):
            return
        keep.append(d)
        seen.add(d)

    if sys.platform == "win32":
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        _keep_if_clean(str(Path(windir) / "System32"))
        _keep_if_clean(windir)
    else:
        for d in ("/usr/bin", "/bin"):
            _keep_if_clean(d)

    for exe in needed:
        found = shutil.which(exe)
        if not found:
            continue
        parent = Path(found).resolve().parent
        if _dir_has_agent(parent):
            dest = stub / Path(found).name
            if not dest.exists():
                try:
                    os.symlink(found, dest)
                except OSError:
                    shutil.copy2(found, dest)
        else:
            s = str(parent)
            if s not in seen:
                keep.append(s)
                seen.add(s)
    return os.pathsep.join(keep)


def write_stub_agent(stub: Path, name: str):
    if sys.platform == "win32":
        p = stub / f"{name}.cmd"
        p.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    else:
        p = stub / name
        p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        p.chmod(0o755)
    return p


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def kb_sandbox(tmp_path):
    """격리 HOME + APPDATA/LOCALAPPDATA + 키바인드 픽스처."""
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    localappdata = tmp_path / "localappdata"
    project = tmp_path / "project"
    stub = tmp_path / "stubbin"
    home.mkdir()
    appdata.mkdir()
    localappdata.mkdir()
    project.mkdir()
    stub.mkdir()

    (home / ".claude").mkdir()
    (home / ".codex").mkdir()
    (home / ".grok").mkdir()

    win_vscode = appdata / "Code" / "User" / "keybindings.json"
    win_cursor = appdata / "Cursor" / "User" / "keybindings.json"
    posix_vscode = home / ".config" / "Code" / "User" / "keybindings.json"
    posix_cursor = home / ".config" / "Cursor" / "User" / "keybindings.json"
    wt = (localappdata / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe"
          / "LocalState" / "settings.json")

    if sys.platform == "win32":
        vscode, cursor = win_vscode, win_cursor
    else:
        vscode, cursor = posix_vscode, posix_cursor

    for path in (win_vscode, posix_vscode):
        _write(path, DEFAULT_VSCODE_JSONC)
    for path in (win_cursor, posix_cursor):
        _write(path, DEFAULT_CURSOR_JSONC)
    _write(wt, DEFAULT_WT_SETTINGS)

    _write(home / ".claude" / "keybindings.json",
           json.dumps(DEFAULT_CLAUDE_KEYBINDINGS, indent=2) + "\n")
    _write(home / ".codex" / "config.toml", DEFAULT_CODEX_TOML)
    _write(home / ".grok" / "config.toml", DEFAULT_GROK_TOML)
    _write(home / ".tmux.conf", DEFAULT_TMUX)

    trash = stub / "trash-put"
    trash.write_text('#!/bin/sh\necho "[stub trash-put] $*"\n', encoding="utf-8")
    try:
        trash.chmod(0o755)
    except OSError:
        pass

    return SimpleNamespace(
        root=tmp_path,
        home=home,
        appdata=appdata,
        localappdata=localappdata,
        project=project,
        stub=stub,
        vscode=vscode,
        cursor=cursor,
        wt=wt,
        win_vscode=win_vscode,
        win_cursor=win_cursor,
        posix_vscode=posix_vscode,
        posix_cursor=posix_cursor,
    )


def sandbox_env(sb, extra=None, include_wt=True):
    # 개발자가 실제로 gnome-terminal/konsole 안에서 테스트를 돌리면 그 변수가 상속돼
    # 불가 터미널 경고가 항상 뜬다 — 주입 테스트가 상속값 덕에 통과하고, 대조군은 깨진다.
    drop = ("CI", "CONTINUOUS_INTEGRATION", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE",
            "CLAUDE_SETUP_DETECTED_TERMINAL",
            "GNOME_TERMINAL_SERVICE", "GNOME_TERMINAL_SCREEN",
            "KONSOLE_VERSION", "KONSOLE_DBUS_SESSION")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["HOME"] = str(sb.home)
    env["USERPROFILE"] = str(sb.home)
    env["APPDATA"] = str(sb.appdata)
    env["LOCALAPPDATA"] = str(sb.localappdata)
    env["CLAUDE_SETUP_VSCODE_KEYBINDINGS"] = str(sb.vscode)
    env["CLAUDE_SETUP_CURSOR_KEYBINDINGS"] = str(sb.cursor)
    if include_wt:
        env["CLAUDE_SETUP_WT_SETTINGS"] = str(sb.wt)
    else:
        env.pop("CLAUDE_SETUP_WT_SETTINGS", None)
    env["PATH"] = isolated_path(sb.stub)
    env["TERM"] = "xterm-256color"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 끄지 않으면 pwsh 가 샌드박스 HOME 에 .cache/powershell/telemetry.uuid 를 남겨
    # '아무것도 안 쓴다' 단언이 설치기와 무관하게 깨진다.
    env["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
    if extra:
        env.update(extra)
    return env


def to_ps_args(posix_args):
    mapping = {
        "--keybinds": "-Keybinds",
        "--uninstall": "-Uninstall",
        "--dry-run": "-DryRun",
        "--no-onboard": "-NoOnboard",
        "--hooks": "-Hooks",
        "--agentsmd": "-AgentsMd",
        "--help": "-Help",
        "-h": "-Help",
    }
    out = []
    i = 0
    while i < len(posix_args):
        a = posix_args[i]
        if a == "--newline" and i + 1 < len(posix_args):
            out.extend(["-Newline", posix_args[i + 1]])
            i += 2
            continue
        if a.startswith("--newline="):
            out.extend(["-Newline", a.split("=", 1)[1]])
            i += 1
            continue
        if a in mapping:
            out.append(mapping[a])
            i += 1
            continue
        out.append(a)
        i += 1
    return out


def load_apply_module():
    """res/keybinds/apply.py 를 직접 import 한다 (플랫폼 분기 같은 순수 함수 검증용)."""
    import importlib.util
    path = ROOT / "res" / "keybinds" / "apply.py"
    spec = importlib.util.spec_from_file_location("_kb_apply_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_setup(args, sb, worker=None, extra=None, include_wt=True, timeout=180):
    worker = worker or native_worker()
    skip_if_worker_missing(worker)
    env = sandbox_env(sb, extra=extra, include_wt=include_wt)
    if worker == "posix":
        cmd = ["bash", str(SETUP_SH), *args]
    else:
        cmd = [_powershell_exe(), "-NoProfile", "-File", str(SETUP_PS1), *to_ps_args(args)]
    return subprocess.run(
        cmd, cwd=str(sb.project), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def assert_ok(proc):
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def combined_output(proc) -> str:
    return f"{proc.stdout or ''}\n{proc.stderr or ''}"


def target_paths(sb):
    return {
        "vscode": sb.vscode,
        "cursor": sb.cursor,
        "wt": sb.wt,
        "claude_kb": sb.home / ".claude" / "keybindings.json",
        "codex": sb.home / ".codex" / "config.toml",
        "grok": sb.home / ".grok" / "config.toml",
        "tmux": sb.home / ".tmux.conf",
        "manifest": sb.home / ".claude" / ".claude-code-setup.json",
        "settings": sb.home / ".claude" / "settings.json",
        "agentsmd": sb.project / "AGENTS.md",
    }


def snapshot_targets(sb):
    out = {}
    for name, path in target_paths(sb).items():
        out[name] = path.read_bytes() if path.exists() else None
    return out


def snapshot_tree(root: Path):
    files = {}
    if not root.exists():
        return files
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        # pwsh -File 이 USERPROFILE 아래에 프로세스 종료 시 남기는 엔진 캐시.
        # 설치 대상이 아니며 구현이 막을 수 없다 (호스트가 스크립트 종료 후에 씀).
        if p.name.startswith("StartupProfileData"):
            continue
        # macOS 의 Apple python 은 바이트코드 캐시를 $HOME/Library/Caches/com.apple.python 에 쓴다.
        # 인터프리터가 쓰는 캐시이지 설치 대상이 아니다.
        if "/".join(p.relative_to(root).parts).startswith("home/Library/Caches/com.apple.python/"):
            continue
        files[str(p.relative_to(root))] = p.read_bytes()
    return files


def read_manifest(home: Path) -> dict:
    path = home / ".claude" / ".claude-code-setup.json"
    assert path.exists(), f"manifest is missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_chord(data: dict) -> str:
    kb = data.get("keybinds")
    assert isinstance(kb, dict), f"manifest has no keybinds section: {data!r}"
    chord = kb.get("newline_chord")
    assert chord in ("shift-enter", "ctrl-enter"), (
        f"keybinds.newline_chord value is not in the allowed list: {chord!r}"
    )
    return chord


def load_toml(path: Path):
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11 (macOS 기본 python3 는 3.9): pip install tomli
        import tomli as tomllib
    text = path.read_text(encoding="utf-8")
    return tomllib.loads(text), text


def claude_context_map(data, context: str) -> dict:
    """Claude Code 형식 {"bindings": [{"context": ..., "bindings": {...}}]} 에서 한 context 의 맵."""
    assert isinstance(data, dict), f"Claude keybindings.json must be an object: {data!r}"
    assert "Chat" not in data, f"stray top-level Chat key (not read by Claude Code): {data!r}"
    blocks = data.get("bindings")
    assert isinstance(blocks, list), f'Claude keybindings.json must have a "bindings" array: {data!r}'
    found = [b for b in blocks if isinstance(b, dict) and b.get("context") == context]
    assert len(found) == 1, f"expected exactly one {context} block: {data!r}"
    assert isinstance(found[0].get("bindings"), dict), f"{context} block has no bindings map: {data!r}"
    return found[0]["bindings"]


def claude_chat_map(data) -> dict:
    return claude_context_map(data, "Chat")


def wt_send_bindings(settings: dict):
    actions = settings.get("actions") or settings.get("keybindings") or []
    found = []
    for item in actions:
        if not isinstance(item, dict):
            continue
        keys = item.get("keys") or item.get("key") or ""
        if isinstance(keys, list):
            keys = " ".join(str(k) for k in keys)
        keys_n = norm_key(keys)
        cmd = item.get("command")
        payload = ""
        is_send = False
        if isinstance(cmd, dict):
            action = str(cmd.get("action") or "").lower()
            if action in ("sendinput", "send"):
                is_send = True
                payload = cmd.get("input") or cmd.get("text") or ""
        elif isinstance(cmd, str) and cmd.lower() in ("sendinput", "send"):
            is_send = True
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            payload = item.get("input") or args.get("input") or args.get("text") or ""
        if is_send:
            found.append((keys_n, payload, item))
    return found


def assert_ide_install(path: Path, chord: str) -> str:
    entries = load_keybindings(path)
    managed = managed_sendsequences(entries)
    assert len(managed) == 1, (
        f"{path.name}: there must be exactly one managed sendSequence (got {len(managed)}): {managed!r}"
    )
    entry = managed[0]
    assert norm_key(entry.get("key")) == vscode_key(chord), (
        f"{path.name}: managed binding key is not {vscode_key(chord)}: {entry.get('key')!r}"
    )
    when = str(entry.get("when") or "")
    assert "terminalFocus" in when
    assert not re.search(r"editor|notebook", when, re.I), (
        f"{path.name}: managed binding when has an editor/notebook clause: {when!r}"
    )
    seq = sendsequence_text(entry)
    assert not is_forbidden_sequence(seq), f"{path.name}: forbidden sequence: {seq!r}"
    assert is_allowed_sequence(seq), (
        f"{path.name}: sequence is not an SSoT candidate: {seq!r}"
    )
    # 관리 항목만 다시 도는 것은 위 단언을 되풀이할 뿐이다 (항상 참).
    # 어휘 6 은 파일 전체에 대한 조건이므로 외래 항목까지 포함해 본다.
    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get("command") != "workbench.action.terminal.sendSequence":
            continue
        if not is_allowed_sequence(sendsequence_text(e)):
            continue
        k = norm_key(e.get("key"))
        assert k != vscode_other_key(chord), (
            f"{path.name}: SSoT sequence is bound to an unchosen modified Enter: {k}"
        )
        assert k not in ("enter", "return"), (
            f"{path.name}: SSoT sequence is bound to plain enter"
        )
    return seq


def assert_foreign_preserved(path: Path):
    entries = load_keybindings(path)
    ids = {(norm_key(e.get("key")), e.get("command"), e.get("when")) for e in entries
           if isinstance(e, dict)}
    assert (FOREIGN_VSCODE_KEY, FOREIGN_VSCODE_COMMAND, "terminalFocus") in ids, (
        f"{path.name}: foreign entry {FOREIGN_VSCODE_KEY} disappeared"
    )
    editor = [e for e in entries if isinstance(e, dict)
              and e.get("command") == EDITOR_SHIFT_ENTER_COMMAND
              and norm_key(e.get("key")) == "shift+enter"]
    assert editor, f"{path.name}: editor Shift+Enter entry disappeared"
    notebook = [e for e in entries if isinstance(e, dict)
                and e.get("command") == NOTEBOOK_SHIFT_ENTER_COMMAND
                and norm_key(e.get("key")) == "shift+enter"]
    assert notebook, f"{path.name}: notebook Shift+Enter entry disappeared"
    keep = [e for e in entries if isinstance(e, dict)
            and e.get("command") == "workbench.action.terminal.sendSequence"
            and sendsequence_text(e) == "keep-me"]
    assert keep, f"{path.name}: user sendSequence (keep-me) disappeared"


def assert_no_other_emulators(home: Path):
    forbidden = [
        Path("Library") / "Application Support" / "iTerm2",
        Path("Library") / "Preferences" / "com.googlecode.iterm2.plist",
        Path(".config") / "kitty",
        Path(".config") / "wezterm",
        Path(".config") / "ghostty",
        Path(".config") / "alacritty",
        Path(".config") / "iterm2",
        Path(".iterm2"),
        Path(".warp"),
        Path(".wezterm.lua"),
    ]
    for rel in forbidden:
        p = home / rel
        assert not p.exists(), f"created an emulator path that is not a native target: {p}"


def assert_newline_install_result(
    sb,
    chord: str,
    *,
    expect_claude=True,
    expect_codex=True,
    expect_tmux=None,
    expect_wt=True,
):
    """어휘 '줄바꿈 설치 결과(C)' 일곱 항을 모두 단언한다."""
    assert chord in ("shift-enter", "ctrl-enter")
    if expect_tmux is None:
        expect_tmux = sys.platform != "win32"

    vs_seq = assert_ide_install(sb.vscode, chord)
    cur_seq = assert_ide_install(sb.cursor, chord)
    # 같은 설치의 SSoT 상수는 하나다. (예전의 "or 둘 다 SSoT" 절은 항상 참이라 지웠다 —
    #  두 값 모두 assert_ide_install 에서 이미 is_allowed_sequence 를 통과했기 때문이다.)
    assert normalize_seq(vs_seq) == normalize_seq(cur_seq), (
        f"VS Code and Cursor use different sequences: {vs_seq!r} vs {cur_seq!r}"
    )
    assert_foreign_preserved(sb.vscode)
    assert_foreign_preserved(sb.cursor)
    cursor_entries = load_keybindings(sb.cursor)
    assert any(isinstance(e, dict) and e.get("command") == FOREIGN_CURSOR_COMMAND
               for e in cursor_entries), "Cursor foreign entry disappeared"

    if expect_claude:
        claude_path = sb.home / ".claude" / "keybindings.json"
        data = json.loads(claude_path.read_text(encoding="utf-8"))
        chat = claude_chat_map(data)
        assert chat.get(vscode_key(chord)) == "chat:newline", (
            f"Claude Chat has no {vscode_key(chord)} → chat:newline: {chat!r}"
        )
        assert chat.get(CLAUDE_PRESERVE_KEY) == CLAUDE_PRESERVE_COMMAND, (
            "existing Claude Chat binding disappeared"
        )
        assert chat.get("escape") == "chat:cancel"
        assert claude_context_map(data, "Global").get("f9") == "app:toggleHelp", (
            "another Claude context disappeared"
        )
        assert chat.get(vscode_other_key(chord)) != "chat:newline", (
            f"unchosen {vscode_other_key(chord)} ended up as a managed Claude Chat newline"
        )

    if expect_codex:
        toml_path = sb.home / ".codex" / "config.toml"
        parsed, text = load_toml(toml_path)
        assert parsed.get("model") == CODEX_SENTINEL_MODEL, "another Codex key was corrupted"
        assert parsed.get("mcp", {}).get("enabled") is True
        editor = ((parsed.get("tui") or {}).get("keymap") or {}).get("editor") or {}
        insert = editor.get("insert_newline") or []
        if isinstance(insert, str):
            insert = [insert]
        assert codex_key(chord) in insert, (
            f"Codex insert_newline lacks {codex_key(chord)}: {insert!r}"
        )
        assert "ctrl-j" in insert, f"existing ctrl-j disappeared: {insert!r}"
        assert editor.get("indent") == ["tab"] or "tab" in (editor.get("indent") or []), (
            "another Codex keymap entry disappeared"
        )
        assert (parsed.get("projects") or {}).get("sentinel", {}).get("trust") is True, (
            "another Codex table disappeared"
        )
        other_codex = "ctrl-enter" if chord == "shift-enter" else "shift-enter"
        assert other_codex not in insert, (
            f"unchosen {other_codex} was added to Codex insert_newline: {insert!r}"
        )

    grok_dir = sb.home / ".grok"
    keymap_names = {
        "keybindings.json", "keymap.json", "keymap.toml", "keys.toml",
        "keys.json", "keybinds.toml", "keybindings.toml",
    }
    if grok_dir.exists():
        for p in grok_dir.rglob("*"):
            if p.is_file():
                assert p.name not in keymap_names, f"created a Grok keymap file: {p}"
        grok_cfg = grok_dir / "config.toml"
        if grok_cfg.exists():
            grok_text = grok_cfg.read_text(encoding="utf-8")
            assert GROK_SENTINEL.split(" = ")[0] in grok_text, (
                "Grok multiline keys were erased"
            )
            assert 'sentinel_multiline_key = "keep-me"' in grok_text

    if expect_wt:
        assert sb.wt.exists(), f"WT settings.json is missing: {sb.wt}"
        settings = json.loads(sb.wt.read_text(encoding="utf-8"))
        sends = wt_send_bindings(settings)
        # 어휘 5 는 *우리가 넣은* sendInput 에 대한 조건이다. 사용자가 같은 키에 걸어둔
        # 남의 sendInput 까지 없어야 한다고 단언하면, 그 삭제(결정 5 위반)를 테스트가 굳힌다.
        managed = [x for x in sends if is_allowed_sequence(x[1])]
        shift_sends = [x for x in managed if x[0] in ("shift+enter", "shift+return")]
        ctrl_sends = [x for x in managed if x[0] in ("ctrl+enter", "ctrl+return")]
        if chord == "shift-enter":
            assert not shift_sends, (
                f"C=shift-enter but WT has a managed shift+enter sendInput: {shift_sends!r}"
            )
            assert not ctrl_sends, (
                f"C=shift-enter but WT has an unchosen managed ctrl+enter sendInput: {ctrl_sends!r}"
            )
        else:
            assert ctrl_sends, "C=ctrl-enter but WT has no managed ctrl+enter sendInput"
            assert not shift_sends, (
                f"C=ctrl-enter but WT still has the old managed shift+enter sendInput: {shift_sends!r}"
            )
            payloads = [s[1] for s in ctrl_sends]
            assert any(is_allowed_sequence(p) and not is_forbidden_sequence(p)
                       for p in payloads), (
                f"WT ctrl+enter sequence is not the SSoT: {payloads!r}"
            )
            assert any(normalize_seq(p) == normalize_seq(vs_seq) for p in payloads), (
                f"WT sequence differs from the VS Code SSoT: {payloads!r} vs {vs_seq!r}"
            )
        # 외래 WT 항목 보존 — 키만 겹치는 사용자 sendInput 도 살아 있어야 한다.
        raw = sb.wt.read_text(encoding="utf-8")
        assert WT_FOREIGN_KEYS in raw, "WT foreign keys disappeared"
        assert WT_FOREIGN_SENDINPUT in raw, (
            "user's sendInput on modified Enter disappeared (key decision 5)"
        )

    if expect_tmux:
        tmux = sb.home / ".tmux.conf"
        assert tmux.exists(), "tmux.conf is missing"
        text = tmux.read_text(encoding="utf-8")
        assert "extended-keys" in text, "tmux extended-keys is missing"
        assert "extkeys" in text, "tmux terminal-features extkeys is missing"
        assert TMUX_SENTINEL in text, "tmux user line disappeared"

    assert_no_other_emulators(sb.home)

    man = read_manifest(sb.home)
    assert manifest_chord(man) == chord


def hooks_installed(sb) -> bool:
    return (sb.home / ".claude" / "hooks" / "danger_guard.py").exists()


def agentsmd_installed(sb) -> bool:
    return (sb.project / "AGENTS.md").exists()


# ── A. CLI · 온보딩 (설치 경로) ───────────────────────────────────────


@posix_only
def test_onboarding_hooks_scope_does_not_install_keybinds(kb_sandbox):
    """covers: Test-2, AI-69
    훅 범위는 jarvis 로 옮겨 갔다. --hooks 는 안내만 내고 exit 1 로 끝나며 키바인드 대상을 포함해 아무것도 쓰지 않는다.
    """
    before = snapshot_tree(kb_sandbox.root)
    proc = run_setup(["--hooks"], kb_sandbox)
    assert proc.returncode == 1, combined_output(proc)
    assert "jarvis module hooks plan --json" in combined_output(proc)
    assert snapshot_tree(kb_sandbox.root) == before


@posix_only
def test_onboarding_agentsmd_scope_does_not_install_keybinds(kb_sandbox):
    """covers: Test-2
    SCOPE=agentsmd 이면 AGENTS.md 만 설치하고 키바인드는 쓰지 않는다.
    """
    before = snapshot_targets(kb_sandbox)
    proc = run_setup(["--agentsmd"], kb_sandbox)
    assert_ok(proc)
    text = combined_output(proc)
    assert agentsmd_installed(kb_sandbox)
    assert managed_sendsequences(load_keybindings(kb_sandbox.vscode)) == []
    assert not hooks_installed(kb_sandbox)
    after = snapshot_targets(kb_sandbox)
    for name in ("vscode", "cursor", "wt", "claude_kb", "codex"):
        assert after[name] == before[name], f"agentsmd scope changed {name}"


@posix_only
def test_onboarding_ctrl_enter_choice_installs_ctrl_enter(kb_sandbox):
    """covers: Test-3
    SCOPE=all 에서 Ctrl+Enter 를 고르면 줄바꿈 설치 결과(ctrl-enter) 다.
    """
    proc = run_setup(["--keybinds", "--newline", "ctrl-enter"], kb_sandbox)
    assert_ok(proc)
    assert_newline_install_result(kb_sandbox, "ctrl-enter")


def test_ci_skips_onboarding_and_installs_all_with_shift_enter(kb_sandbox):
    """covers: Test-5
    인자 없이 실행하면 설치하지 않는다. 예전 기본 설치는 --no-onboard 다.
    jarvis 로 옮긴 뒤에는 온보딩 대신 jarvis setup 모듈을 안내하고 exit 1 로 끝낸다.
    """
    before = snapshot_tree(kb_sandbox.root)
    proc = run_setup([], kb_sandbox, extra={"CI": "true"})
    assert proc.returncode == 1, combined_output(proc)
    assert "Nothing was installed" in combined_output(proc)
    assert "jarvis module setup plan --json" in combined_output(proc)
    assert not hooks_installed(kb_sandbox)
    assert not agentsmd_installed(kb_sandbox)
    assert snapshot_tree(kb_sandbox.root) == before


def test_keybinds_only_install_leaves_hooks_and_agents_untouched(kb_sandbox):
    """covers: Test-6
    --keybinds / -Keybinds 는 키바인딩만 설치한다. 훅이 없어도 매니페스트를 만든다.
    """
    settings = kb_sandbox.home / ".claude" / "settings.json"
    assert not settings.exists()
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    assert_newline_install_result(kb_sandbox, "shift-enter")
    assert not hooks_installed(kb_sandbox)
    assert not agentsmd_installed(kb_sandbox)
    assert not settings.exists()
    man = read_manifest(kb_sandbox.home)
    assert manifest_chord(man) == "shift-enter"


@pytest.mark.parametrize("worker", ["posix", "powershell"])
@pytest.mark.parametrize(
    "args,chord,keybinds_only",
    [
        (["--keybinds", "--newline", "ctrl-enter"], "ctrl-enter", True),
        (["--keybinds", "--newline", "shift-enter"], "shift-enter", True),
        (["--newline", "shift-enter"], "shift-enter", False),
        (["--no-onboard", "--newline", "ctrl-enter"], "ctrl-enter", False),
    ],
)
def test_newline_flag_installs_result_per_chord_C(kb_sandbox, worker, args, chord, keybinds_only):
    """covers: Test-7
    --keybinds --newline ctrl-enter 와 --newline shift-enter (POSIX/PowerShell),
    --no-onboard --newline ctrl-enter 전체 설치가 각각 줄바꿈 설치 결과(C) 다.
    """
    skip_if_worker_missing(worker)
    proc = run_setup(args, kb_sandbox, worker=worker)
    assert_ok(proc)
    assert_newline_install_result(kb_sandbox, chord)
    if keybinds_only:
        assert not hooks_installed(kb_sandbox)
        assert not agentsmd_installed(kb_sandbox)
    if args[:1] == ["--no-onboard"]:
        assert not hooks_installed(kb_sandbox)    # 훅은 jarvis 가 맡는다(AI-69)
        assert agentsmd_installed(kb_sandbox)


@pytest.mark.parametrize("worker", ["posix", "powershell"])
@pytest.mark.parametrize("bad", ["enter", "shift+enter", "ctrl+enter", "foo", "Shift-Enter"])
def test_invalid_newline_exits_1_and_leaves_files_unchanged(kb_sandbox, worker, bad):
    """covers: Test-8
    --newline 허용 값이 아니면 exit 1, 대상 파일 불변.
    """
    skip_if_worker_missing(worker)
    before = snapshot_tree(kb_sandbox.root)
    proc = run_setup(["--keybinds", "--newline", bad], kb_sandbox, worker=worker)
    assert proc.returncode == 1, combined_output(proc)
    assert snapshot_tree(kb_sandbox.root) == before
    text = combined_output(proc)
    # 플래그 미구현(알 수 없는 옵션) 이 아니라, 값은 받았지만 허용 목록이 아닌 거절이어야 한다.
    assert "Unknown option" not in text, text
    assert re.search(r"newline|shift-enter|ctrl-enter", text, re.I), text


def test_hooks_and_agentsmd_do_not_write_keybind_files(kb_sandbox):
    """covers: Test-9, AI-69
    --hooks, --agentsmd, --hooks --newline ctrl-enter 는 키바인드 대상 파일을 쓰지 않는다.
    --hooks 는 jarvis 로 옮겨 가 안내만 내고 exit 1 로 끝난다.
    """
    before = snapshot_targets(kb_sandbox)

    before_tree = snapshot_tree(kb_sandbox.root)
    proc = run_setup(["--hooks"], kb_sandbox)
    assert proc.returncode == 1, combined_output(proc)
    assert not hooks_installed(kb_sandbox)
    assert snapshot_tree(kb_sandbox.root) == before_tree

    proc = run_setup(["--agentsmd"], kb_sandbox)
    assert_ok(proc)
    assert agentsmd_installed(kb_sandbox)
    after_agents = snapshot_targets(kb_sandbox)
    assert after_agents["vscode"] == before["vscode"]
    assert after_agents["cursor"] == before["cursor"]

    # 시드를 다시 깔고 hooks + newline 이 키바인드를 쓰는지 본다.
    _write(kb_sandbox.vscode, DEFAULT_VSCODE_JSONC)
    _write(kb_sandbox.cursor, DEFAULT_CURSOR_JSONC)
    before_combo = snapshot_targets(kb_sandbox)
    proc = run_setup(["--hooks", "--newline", "ctrl-enter"], kb_sandbox)
    assert proc.returncode == 1, combined_output(proc)
    after_combo = snapshot_targets(kb_sandbox)
    for name in ("vscode", "cursor", "wt", "claude_kb", "codex", "tmux", "grok"):
        assert after_combo[name] == before_combo[name], (
            f"--hooks --newline changed {name}"
        )
    assert managed_sendsequences(load_keybindings(kb_sandbox.vscode)) == []
    assert managed_sendsequences(load_keybindings(kb_sandbox.cursor)) == []


@pytest.mark.parametrize("worker", ["posix", "powershell"])
def test_keybinds_dry_run_writes_nothing_and_shows_paths_in_plan(kb_sandbox, worker):
    """covers: Test-10
    --keybinds --dry-run 은 쓰지 않고, 계획에 키바인드 대상 경로가 보인다.
    """
    skip_if_worker_missing(worker)
    before = snapshot_tree(kb_sandbox.root)
    proc = run_setup(["--keybinds", "--dry-run"], kb_sandbox, worker=worker)
    assert_ok(proc)
    assert snapshot_tree(kb_sandbox.root) == before
    text = combined_output(proc)
    assert "keybindings.json" in text, text
    assert ("config.toml" in text) or ("codex" in text.lower()), text


def test_skipped_onboarding_full_install_does_agents_shift_enter_without_hooks(kb_sandbox):
    """covers: Test-11, AI-69
    온보딩 스킵 전체 설치 = AGENTS.md + 줄바꿈 설치 결과(shift-enter). 훅은 jarvis 가 맡는다.
    """
    proc = run_setup(["--no-onboard"], kb_sandbox)
    assert_ok(proc)
    assert not hooks_installed(kb_sandbox)
    assert agentsmd_installed(kb_sandbox)
    assert_newline_install_result(kb_sandbox, "shift-enter")


def test_uninstall_removes_managed_keybinds_and_keeps_hooks_and_user_entries(kb_sandbox):
    """covers: Test-12, AI-69
    setup --uninstall 은 관리 키바인드를 제거하고, jarvis 가 맡는 훅과 그 영수증 기록은 남긴다.
    --keybinds --uninstall 은 키바인드만. 사용자 원래 항목은 남긴다.
    """
    proc = run_setup(["--no-onboard"], kb_sandbox)
    assert_ok(proc)
    assert_newline_install_result(kb_sandbox, "shift-enter")
    # 예전 ai-setup(지금은 jarvis)이 깐 훅과 그 영수증 기록을 심어 둔다.
    guard = kb_sandbox.home / ".claude" / "hooks" / "danger_guard.py"
    guard.parent.mkdir(parents=True)
    guard.write_text("# installed hook\n", encoding="utf-8")
    man_path = kb_sandbox.home / ".claude" / ".claude-code-setup.json"
    seeded = read_manifest(kb_sandbox.home)
    seeded.update({"hook_files": ["danger_guard.py"], "added_allow": ["Bash"]})
    man_path.write_text(json.dumps(seeded), encoding="utf-8")

    proc = run_setup(["--keybinds", "--uninstall"], kb_sandbox)
    assert_ok(proc)
    assert hooks_installed(kb_sandbox), "keybinds uninstall also removed the hooks"
    assert managed_sendsequences(load_keybindings(kb_sandbox.vscode)) == []
    assert managed_sendsequences(load_keybindings(kb_sandbox.cursor)) == []
    assert_foreign_preserved(kb_sandbox.vscode)
    claude = json.loads(
        (kb_sandbox.home / ".claude" / "keybindings.json").read_text(encoding="utf-8")
    )
    chat = claude_chat_map(claude)
    assert chat.get("shift+enter") != "chat:newline"
    assert chat.get(CLAUDE_PRESERVE_KEY) == CLAUDE_PRESERVE_COMMAND
    parsed, _ = load_toml(kb_sandbox.home / ".codex" / "config.toml")
    insert = ((parsed.get("tui") or {}).get("keymap") or {}).get("editor", {}).get(
        "insert_newline") or []
    if isinstance(insert, str):
        insert = [insert]
    assert "shift-enter" not in insert
    assert "ctrl-j" in insert
    if sys.platform != "win32":
        tmux = (kb_sandbox.home / ".tmux.conf").read_text(encoding="utf-8")
        assert TMUX_SENTINEL in tmux
        assert "extended-keys" not in tmux
        assert "extkeys" not in tmux

    # 다시 설치한 뒤 전체 uninstall.
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    proc = run_setup(["--uninstall"], kb_sandbox)
    assert_ok(proc)
    assert hooks_installed(kb_sandbox)
    left = read_manifest(kb_sandbox.home)
    assert left.get("hook_files") == ["danger_guard.py"] and left.get("added_allow") == ["Bash"]
    assert managed_sendsequences(load_keybindings(kb_sandbox.vscode)) == []
    assert_foreign_preserved(kb_sandbox.vscode)
    assert_foreign_preserved(kb_sandbox.cursor)


def test_installing_twice_does_not_grow_managed_bindings_or_empty_manifest(kb_sandbox):
    """covers: Test-13
    두 번 설치해도 관리 바인딩이 늘지 않는다. 설치 전에 동일 바인딩이 있어도
    재추가하지 않는다. 영수증이 빈 값으로 덮이지 않는다.
    """
    # 훅 영수증 기록(jarvis 훅 모듈이 이어 읽는다)을 심어 두고, 키바인드 워커가 그 섹션을 남기는지 본다.
    man_path = kb_sandbox.home / ".claude" / ".claude-code-setup.json"
    man_path.write_text(json.dumps({"hook_files": ["danger_guard.py"], "added_allow": ["Bash"]}), encoding="utf-8")
    proc = run_setup(["--keybinds", "--newline", "shift-enter"], kb_sandbox)
    assert_ok(proc)
    first = read_manifest(kb_sandbox.home)
    assert first.get("added_allow") == ["Bash"], "hooks manifest added_allow is missing"
    assert manifest_chord(first) == "shift-enter"
    n1 = len(managed_sendsequences(load_keybindings(kb_sandbox.vscode)))
    c1 = len(managed_sendsequences(load_keybindings(kb_sandbox.cursor)))

    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    second = read_manifest(kb_sandbox.home)
    assert second.get("added_allow") == first.get("added_allow"), (
        "rerun erased the hooks manifest"
    )
    assert manifest_chord(second) == "shift-enter"
    n2 = len(managed_sendsequences(load_keybindings(kb_sandbox.vscode)))
    c2 = len(managed_sendsequences(load_keybindings(kb_sandbox.cursor)))
    assert n1 == n2 == 1
    assert c1 == c2 == 1

    # 이미 같은 관리 바인딩이 있는 픽스처에 설치해도 하나.
    seeded = parse_jsonc(DEFAULT_VSCODE_JSONC)
    seeded.append({
        "key": "shift+enter",
        "command": "workbench.action.terminal.sendSequence",
        "args": {"text": "\u001b\r"},
        "when": "terminalFocus",
    })
    _write(kb_sandbox.vscode, json.dumps(seeded, indent=2) + "\n")
    proc = run_setup(["--keybinds", "--newline", "shift-enter"], kb_sandbox)
    assert_ok(proc)
    assert len(managed_sendsequences(load_keybindings(kb_sandbox.vscode))) == 1
    assert read_manifest(kb_sandbox.home).get("keybinds"), "manifest keybinds is empty"


def test_rerun_without_newline_reuses_manifest_choice(kb_sandbox):
    """covers: Test-14
    재실행에 --newline 이 없으면 매니페스트의 이전 선택.
    --keybinds 만으로도 선택이 기록된다. 기록 없으면 Shift+Enter.
    """
    proc = run_setup(["--keybinds", "--newline", "ctrl-enter"], kb_sandbox)
    assert_ok(proc)
    assert manifest_chord(read_manifest(kb_sandbox.home)) == "ctrl-enter"
    assert_newline_install_result(kb_sandbox, "ctrl-enter")

    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    assert manifest_chord(read_manifest(kb_sandbox.home)) == "ctrl-enter"
    assert_newline_install_result(kb_sandbox, "ctrl-enter")

    # 기록 없음: 매니페스트와 관리 대상을 시드로 되돌린 뒤 --keybinds 만 → shift-enter.
    man_path = kb_sandbox.home / ".claude" / ".claude-code-setup.json"
    man_path.unlink()
    _write(kb_sandbox.vscode, DEFAULT_VSCODE_JSONC)
    _write(kb_sandbox.cursor, DEFAULT_CURSOR_JSONC)
    _write(kb_sandbox.home / ".claude" / "keybindings.json",
           json.dumps(DEFAULT_CLAUDE_KEYBINDINGS, indent=2) + "\n")
    _write(kb_sandbox.home / ".codex" / "config.toml", DEFAULT_CODEX_TOML)
    _write(kb_sandbox.wt, DEFAULT_WT_SETTINGS)
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    assert manifest_chord(read_manifest(kb_sandbox.home)) == "shift-enter"
    assert_newline_install_result(kb_sandbox, "shift-enter")


@pytest.mark.parametrize("worker", ["posix", "powershell"])
def test_help_shows_keybinds_and_newline(kb_sandbox, worker):
    """covers: Test-15
    setup.sh --help 와 setup.ps1 -Help 에 --keybinds/-Keybinds 와 --newline 이 보인다.
    """
    skip_if_worker_missing(worker)
    proc = run_setup(["--help"], kb_sandbox, worker=worker)
    assert_ok(proc)
    text = combined_output(proc)
    if worker == "posix":
        assert "--keybinds" in text, text
        assert "--newline" in text, text
    else:
        assert "-Keybinds" in text or "Keybinds" in text, text
        assert "-Newline" in text or "Newline" in text, text


def test_rerun_with_different_newline_replaces_managed_binding(kb_sandbox):
    """covers: Test-16
    한 코드로 설치한 뒤 다른 --newline 으로 재실행하면 관리 바인딩이 교체된다.
    이전 코드의 관리 항목이 남지 않는다.
    """
    proc = run_setup(["--keybinds", "--newline", "shift-enter"], kb_sandbox)
    assert_ok(proc)
    assert_newline_install_result(kb_sandbox, "shift-enter")

    proc = run_setup(["--keybinds", "--newline", "ctrl-enter"], kb_sandbox)
    assert_ok(proc)
    assert_newline_install_result(kb_sandbox, "ctrl-enter")
    for path in (kb_sandbox.vscode, kb_sandbox.cursor):
        for e in managed_sendsequences(load_keybindings(path)):
            assert norm_key(e.get("key")) != "shift+enter", (
                f"{path.name}: old managed shift+enter entry remained"
            )


# ── B. 파일 · 스킵 ───────────────────────────────────────────────────


def test_vscode_cursor_existing_other_keybindings_are_preserved(kb_sandbox):
    """covers: Test-18
    VS Code/Cursor 픽스처의 기존 다른 키바인딩은 설치 후에도 그대로다.
    """
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    assert_foreign_preserved(kb_sandbox.vscode)
    assert_foreign_preserved(kb_sandbox.cursor)
    cursor = load_keybindings(kb_sandbox.cursor)
    assert any(isinstance(e, dict) and e.get("command") == FOREIGN_CURSOR_COMMAND
               for e in cursor)


def test_existing_claude_keybindings_merge_into_chat_only(kb_sandbox):
    """covers: Test-19
    기존 Claude keybindings.json 이면 Chat 에 고른 키 → chat:newline 만 합치고
    나머지 context/bindings 는 보존한다.
    """
    proc = run_setup(["--keybinds", "--newline", "shift-enter"], kb_sandbox)
    assert_ok(proc)
    data = json.loads(
        (kb_sandbox.home / ".claude" / "keybindings.json").read_text(encoding="utf-8")
    )
    chat = claude_chat_map(data)
    assert chat["shift+enter"] == "chat:newline"
    assert chat.get("ctrl+enter") != "chat:newline"
    assert chat[CLAUDE_PRESERVE_KEY] == CLAUDE_PRESERVE_COMMAND
    assert chat["escape"] == "chat:cancel"
    assert claude_context_map(data, "Global")["f9"] == "app:toggleHelp"
    assert data["$schema"] == DEFAULT_CLAUDE_KEYBINDINGS["$schema"]


def test_codex_preserves_other_keys_and_tables_and_unions_insert_newline(kb_sandbox):
    """covers: Test-20
    Codex 다른 키/테이블 보존, insert_newline 에 고른 키 합집합, 기존 ctrl-j 등 삭제 금지.
    """
    proc = run_setup(["--keybinds", "--newline", "shift-enter"], kb_sandbox)
    assert_ok(proc)
    parsed, _ = load_toml(kb_sandbox.home / ".codex" / "config.toml")
    assert parsed["model"] == CODEX_SENTINEL_MODEL
    assert parsed["mcp"]["enabled"] is True
    editor = parsed["tui"]["keymap"]["editor"]
    insert = editor["insert_newline"]
    if isinstance(insert, str):
        insert = [insert]
    assert "shift-enter" in insert
    assert "ctrl-j" in insert
    assert editor["indent"] == ["tab"] or "tab" in editor["indent"]
    assert parsed["projects"]["sentinel"]["trust"] is True


def test_grok_config_present_still_creates_no_keymap(kb_sandbox):
    """covers: Test-21
    Grok 설정 디렉터리가 있어도 키맵 파일을 만들지 않는다.
    multiline 관련 키를 쓰거나 지우지 않는다.
    """
    grok_cfg = kb_sandbox.home / ".grok" / "config.toml"
    original = grok_cfg.read_bytes()
    before_names = {p.name for p in (kb_sandbox.home / ".grok").rglob("*") if p.is_file()}
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    assert grok_cfg.read_bytes() == original
    after_names = {p.name for p in (kb_sandbox.home / ".grok").rglob("*") if p.is_file()}
    assert after_names == before_names
    for p in (kb_sandbox.home / ".grok").rglob("*"):
        if p.is_file():
            assert p.name not in {
                "keybindings.json", "keymap.json", "keymap.toml",
                "keys.toml", "keys.json", "keybinds.toml", "keybindings.toml",
            }


def test_agent_skipped_when_both_binary_and_config_missing(kb_sandbox):
    """covers: Test-22
    바이너리와 설정 디렉터리가 둘 다 없으면 그 에이전트 skip, exit 0.
    """
    shutil.rmtree(kb_sandbox.home / ".claude", ignore_errors=True)
    shutil.rmtree(kb_sandbox.home / ".codex", ignore_errors=True)
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    assert not (kb_sandbox.home / ".claude" / "keybindings.json").exists()
    assert not (kb_sandbox.home / ".codex" / "config.toml").exists()
    # 매니페스트는 --keybinds 만으로도 만든다.
    assert (kb_sandbox.home / ".claude" / ".claude-code-setup.json").exists()
    # 터미널 층은 에이전트 skip 과 무관하게 설치된다.
    assert len(managed_sendsequences(load_keybindings(kb_sandbox.vscode))) == 1


def test_keymap_written_when_only_one_present(kb_sandbox):
    """covers: Test-22
    바이너리와 설정 디렉터리 중 하나만 있으면 skip 하지 않고 키맵을 쓴다.
    """
    # 설정 디렉터리만 (기본 픽스처). PATH 에는 에이전트 스텁 없음.
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    assert (kb_sandbox.home / ".claude" / "keybindings.json").exists()
    chat = claude_chat_map(json.loads(
        (kb_sandbox.home / ".claude" / "keybindings.json").read_text(encoding="utf-8")
    ))
    assert chat.get("shift+enter") == "chat:newline"
    parsed, _ = load_toml(kb_sandbox.home / ".codex" / "config.toml")
    insert = parsed["tui"]["keymap"]["editor"]["insert_newline"]
    if isinstance(insert, str):
        insert = [insert]
    assert "shift-enter" in insert

    # 바이너리만: 설정 디렉터리를 지우고 스텁 바이너리를 둔다.
    shutil.rmtree(kb_sandbox.home / ".claude", ignore_errors=True)
    shutil.rmtree(kb_sandbox.home / ".codex", ignore_errors=True)
    write_stub_agent(kb_sandbox.stub, "claude")
    write_stub_agent(kb_sandbox.stub, "codex")
    _write(kb_sandbox.vscode, DEFAULT_VSCODE_JSONC)
    _write(kb_sandbox.cursor, DEFAULT_CURSOR_JSONC)
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    assert (kb_sandbox.home / ".claude" / "keybindings.json").exists()
    assert (kb_sandbox.home / ".codex" / "config.toml").exists()
    chat = claude_chat_map(json.loads(
        (kb_sandbox.home / ".claude" / "keybindings.json").read_text(encoding="utf-8")
    ))
    assert chat.get("shift+enter") == "chat:newline"


@tmux_only
def test_tmux_conf_adds_extended_keys_only_when_missing(kb_sandbox):
    """covers: Test-23
    tmux.conf 에 extended-keys / extkeys terminal-features 가 없으면 추가,
    있으면 중복하지 않는다.
    """
    tmux = kb_sandbox.home / ".tmux.conf"
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    first = tmux.read_text(encoding="utf-8")
    assert "extended-keys" in first
    assert "extkeys" in first
    n_ext = first.count("extended-keys")
    n_feat = first.count("extkeys")

    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    second = tmux.read_text(encoding="utf-8")
    assert second.count("extended-keys") == n_ext
    assert second.count("extkeys") == n_feat

    # 이미 있는 파일에는 추가하지 않는다.
    seeded = TMUX_SENTINEL + "\nset -g extended-keys on\nset -as terminal-features 'xterm*:extkeys'\n"
    _write(tmux, seeded)
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    after = tmux.read_text(encoding="utf-8")
    assert after.count("extended-keys") == 1
    assert after.count("extkeys") == 1
    assert TMUX_SENTINEL in after


@pytest.mark.parametrize("injected", [
    # 감지 결과를 그대로 주입 (테스트 전용 통로)
    {"CLAUDE_SETUP_DETECTED_TERMINAL": "gnome-terminal"},
    # gnome-terminal 이 스스로 남기는 환경변수 — 실사용 경로가 죽어 있지 않은지 본다
    {"GNOME_TERMINAL_SERVICE": ":1.42"},
    # konsole 짝
    {"KONSOLE_VERSION": "230804"},
])
def test_unsupported_terminal_warns_without_failing(kb_sandbox, injected):
    """covers: Test-24
    불가 터미널 감지는 픽스처로 주입. 설치는 실패하지 않고 SSH/외부 터미널 경고.
    실제 gnome-terminal 불필요.
    """
    proc = run_setup(["--keybinds"], kb_sandbox, extra=injected)
    assert_ok(proc)
    text = combined_output(proc)
    assert ("SSH" in text) or ("external terminal" in text), text
    assert_newline_install_result(kb_sandbox, "shift-enter")


def test_managed_binding_when_is_terminal_focus_and_editor_entries_untouched(kb_sandbox):
    """covers: Test-25
    관리 바인딩 when 은 terminalFocus 를 포함한다. 에디터/노트북 Shift+Enter 는 그대로다.
    """
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    for path in (kb_sandbox.vscode, kb_sandbox.cursor):
        managed = managed_sendsequences(load_keybindings(path))
        assert managed
        for e in managed:
            assert "terminalFocus" in str(e.get("when") or "")
            assert not re.search(r"editor|notebook", str(e.get("when") or ""), re.I)
        assert_foreign_preserved(path)


def test_wt_fixture_shift_has_no_sendinput_and_ctrl_follows_ssot(kb_sandbox):
    """covers: Test-26
    격리 WT settings.json: Shift+Enter 면 shift+enter sendInput 없음.
    Ctrl+Enter 면 ctrl+enter → SSoT 시퀀스.
    WT·VS Code·Cursor·tmux·에이전트 키맵 외에 에뮬레이터 설정 파일을 만들지 않는다.
    """
    proc = run_setup(["--keybinds", "--newline", "shift-enter"], kb_sandbox)
    assert_ok(proc)
    assert_newline_install_result(kb_sandbox, "shift-enter")
    assert_no_other_emulators(kb_sandbox.home)

    proc = run_setup(["--keybinds", "--newline", "ctrl-enter"], kb_sandbox)
    assert_ok(proc)
    assert_newline_install_result(kb_sandbox, "ctrl-enter")
    assert_no_other_emulators(kb_sandbox.home)


def test_wt_without_target_is_skipped_with_exit0(kb_sandbox):
    """covers: Test-27
    WT settings.json 대상 파일이 없으면 WT skip, exit 0.
    """
    if kb_sandbox.wt.exists():
        kb_sandbox.wt.unlink()
    # env 는 여전히 그 경로를 가리킨다. 파일이 없을 때 skip 이지, env 를 빼는 분기가 아니다.
    proc = run_setup(["--keybinds"], kb_sandbox, include_wt=True)
    assert_ok(proc)
    assert not kb_sandbox.wt.exists(), "created a WT settings.json that did not exist"
    # 나머지 설치는 성공해야 한다.
    assert len(managed_sendsequences(load_keybindings(kb_sandbox.vscode))) == 1


# ── R4 회귀 — docs/reviews/newline-keybind-static-review.md 의 확정 결함 ────────
#
# 새 인수 기준이 아니다. Test-1..27 이 이미 요구하지만 픽스처가 그 분기를 밟지 않아
# 통과해 버린 지점들을, 그 분기를 실제로 시드해서 다시 고정한다.


def test_default_ide_paths_follow_each_of_three_oses():
    """covers: R4 회귀 (어휘 1 — macOS 설치가 무효였다)

    경로가 env 주입으로만 검증되면 default_*_path 는 한 번도 실행되지 않는 죽은 코드가
    된다. 실제로 darwin 분기가 없어 mac 은 아무도 읽지 않는 ~/.config 에 파일을 만들었다.
    """
    apply = load_apply_module()
    home = Path("/home/u")

    mac = apply.default_vscode_path(home, platform="darwin", appdata=None)
    assert mac.parts[-5:] == ("Library", "Application Support", "Code", "User",
                              "keybindings.json"), mac
    mac_cur = apply.default_cursor_path(home, platform="darwin", appdata=None)
    assert mac_cur.parts[-5:] == ("Library", "Application Support", "Cursor", "User",
                                  "keybindings.json"), mac_cur

    lin = apply.default_vscode_path(home, platform="linux", appdata=None)
    assert lin.parts[-4:] == (".config", "Code", "User", "keybindings.json"), lin

    win = apply.default_cursor_path(home, platform="win32", appdata="X:/AppData")
    assert win.parts[-4:] == ("AppData", "Cursor", "User", "keybindings.json"), win


def test_trailing_comma_keybindings_are_readable():
    """covers: R4 회귀 (어휘 1)

    VS Code 는 `[{...},]` 를 정상으로 받아들인다. 우리가 못 읽으면 그 사용자에게는
    관리 sendSequence 가 하나도 안 들어간 채 '설치 완료' 만 보인다.
    """
    apply = load_apply_module()
    assert apply.parse_jsonc('[\n  {"key": "a", "command": "b"},\n]\n') == [
        {"key": "a", "command": "b"}
    ]
    # 문자열 안의 콤마는 건드리지 않는다.
    assert apply.parse_jsonc('[{"key": "a,]"}]') == [{"key": "a,]"}]


def test_does_not_claim_success_when_a_target_was_unreadable(kb_sandbox):
    """covers: R4 회귀 (핵심 결정 9 는 '에이전트가 없을 때' 의 skip 만 허용한다)"""
    kb_sandbox.vscode.write_text("{ not json at all", encoding="utf-8")
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert proc.returncode != 0, f"succeeded although a target could not be read:\n{combined_output(proc)}"
    assert "could not be read" in combined_output(proc), combined_output(proc)


@tmux_only
def test_users_own_extended_keys_survive_full_uninstall(kb_sandbox):
    """covers: Test-12 (tmux 는 우리가 넣은 줄만 지운다)

    사용자가 SSoT 상수와 글자까지 같은 두 줄을 이미 갖고 있으면 설치는 아무것도 넣지
    않는다. 그때 uninstall 이 '문자열이 같으니 우리 것' 이라고 지우면 남의 설정을 지운다.
    """
    apply = load_apply_module()
    tmux = kb_sandbox.home / ".tmux.conf"
    tmux.write_text(
        TMUX_SENTINEL + "\n" + apply.TMUX_EXTENDED_LINE + "\n"
        + apply.TMUX_EXTKEYS_LINE + "\n", encoding="utf-8")

    assert_ok(run_setup(["--no-onboard"], kb_sandbox))
    assert_ok(run_setup(["--uninstall"], kb_sandbox))

    after = tmux.read_text(encoding="utf-8")
    assert apply.TMUX_EXTENDED_LINE in after, "user's own extended-keys line disappeared"
    assert apply.TMUX_EXTKEYS_LINE in after, "user's own extkeys line disappeared"
    assert TMUX_SENTINEL in after


@tmux_only
def test_our_extended_keys_are_removed_by_full_uninstall(kb_sandbox):
    """covers: Test-12 (순서 회귀)

    훅 uninstall 이 매니페스트를 먼저 지우면 키바인드 제거가 영수증을 잃고, 우리가
    넣은 줄이 그대로 남는다. 위 테스트와 짝이다 — 한쪽만 보면 반대 방향으로 틀린다.
    """
    tmux = kb_sandbox.home / ".tmux.conf"
    tmux.write_text(TMUX_SENTINEL + "\n", encoding="utf-8")

    assert_ok(run_setup(["--no-onboard"], kb_sandbox))
    assert "extended-keys" in tmux.read_text(encoding="utf-8")

    assert_ok(run_setup(["--uninstall"], kb_sandbox))
    after = tmux.read_text(encoding="utf-8")
    assert "extended-keys" not in after, f"the line we added remained:\n{after}"
    assert TMUX_SENTINEL in after, "also removed the user's line"


def test_hooks_install_does_not_overwrite_keybinds_manifest(kb_sandbox):
    """covers: Test-13/14 (영수증이 빈 값으로 덮이지 않는다), AI-69

    훅이 keybinds 섹션을 날리면 재실행에서 이전 선택이 사라져 조용히 shift-enter 로 되돌아간다.
    훅은 jarvis 로 옮겨 가 --hooks 는 영수증을 건드리지 않고 exit 1 로 끝난다.
    """
    manifest = kb_sandbox.home / ".claude" / ".claude-code-setup.json"
    assert_ok(run_setup(["--keybinds", "--newline", "ctrl-enter"], kb_sandbox))
    before = manifest.read_bytes()
    assert run_setup(["--hooks"], kb_sandbox).returncode == 1
    assert manifest.read_bytes() == before

    kb = json.loads(manifest.read_text(encoding="utf-8")).get("keybinds") or {}
    assert kb.get("newline_chord") == "ctrl-enter", f"manifest disappeared: {kb!r}"

    assert_ok(run_setup(["--keybinds"], kb_sandbox))
    assert_newline_install_result(kb_sandbox, "ctrl-enter")


def test_install_does_not_remove_users_own_unchosen_key(kb_sandbox):
    """covers: R4 회귀 (핵심 결정 5 · CONTEXT.md '고르지 않은 키')

    영수증이 없다는 것은 우리가 넣지 않았다는 뜻이다. 그런데도 지우면 사용자가 직접
    건 Ctrl+Enter 줄바꿈이 첫 설치에서 조용히 사라진다.
    """
    claude_kb = kb_sandbox.home / ".claude" / "keybindings.json"
    claude_kb.write_text(json.dumps({"bindings": [
        {"context": "Chat", "bindings": {"ctrl+enter": "chat:newline"}},
    ]}, indent=2), encoding="utf-8")
    codex = kb_sandbox.home / ".codex" / "config.toml"
    codex.write_text('[tui.keymap.editor]\ninsert_newline = ["ctrl-enter", "ctrl-j"]\n',
                     encoding="utf-8")

    assert_ok(run_setup(["--keybinds", "--newline", "shift-enter"], kb_sandbox))

    chat = claude_chat_map(json.loads(claude_kb.read_text(encoding="utf-8")))
    assert chat.get("shift+enter") == "chat:newline"
    assert chat.get("ctrl+enter") == "chat:newline", (
        f"user's own ctrl+enter disappeared: {chat!r}"
    )

    parsed, text = load_toml(codex)
    insert = (((parsed.get("tui") or {}).get("keymap") or {}).get("editor") or {}
              ).get("insert_newline") or []
    for key in ("shift-enter", "ctrl-enter", "ctrl-j"):
        assert key in insert, f"{key} is not in insert_newline: {insert!r}\n{text}"


def test_codex_toml_without_trailing_newline_is_not_broken(kb_sandbox):
    """covers: Test-20 (다른 키/테이블 보존)

    파일이 개행 없이 끝나고 [tui.keymap.editor] 가 마지막 섹션이면, 보정이 없을 때
    `indent = ["tab"]insert_newline = [...]` 가 만들어져 TOML 전체가 파싱 불가가 된다.
    """
    codex = kb_sandbox.home / ".codex" / "config.toml"
    codex.write_text('model = "%s"\n[tui.keymap.editor]\nindent = ["tab"]'
                     % CODEX_SENTINEL_MODEL, encoding="utf-8")

    assert_ok(run_setup(["--keybinds"], kb_sandbox))

    parsed, text = load_toml(codex)
    assert parsed.get("model") == CODEX_SENTINEL_MODEL, text
    editor = ((parsed.get("tui") or {}).get("keymap") or {}).get("editor") or {}
    assert editor.get("indent") == ["tab"], text
    assert "shift-enter" in (editor.get("insert_newline") or []), text


def test_codex_same_line_comment_is_not_read_as_entry(kb_sandbox):
    """covers: Test-20 (다른 키 보존)

    정규식이 주석 안의 따옴표 문자열까지 항목으로 흡수하면 없는 키가 생기고 주석은 사라진다.
    """
    codex = kb_sandbox.home / ".codex" / "config.toml"
    codex.write_text(
        '[tui.keymap.editor]\n'
        'insert_newline = ["ctrl-j"]  # "alt-enter" 는 안 먹음\n',
        encoding="utf-8")

    assert_ok(run_setup(["--keybinds"], kb_sandbox))

    parsed, text = load_toml(codex)
    insert = (((parsed.get("tui") or {}).get("keymap") or {}).get("editor") or {}
              ).get("insert_newline") or []
    assert "alt-enter" not in insert, f"read a string inside a comment as an entry: {insert!r}"
    assert set(insert) == {"ctrl-j", "shift-enter"}, insert
    assert "안 먹음" in text, f"same-line comment disappeared:\n{text}"


def test_codex_uninstall_leaves_no_empty_insert_newline(kb_sandbox):
    """covers: Test-12 (사용자 원래 항목은 남기고 우리 것만 되돌린다)

    빈 배열을 남기면 설치 전에 없던 '줄바꿈 전면 언바인드' 라는 새 설정을 만드는 것이다.
    """
    codex = kb_sandbox.home / ".codex" / "config.toml"
    codex.write_text('model = "%s"\n' % CODEX_SENTINEL_MODEL, encoding="utf-8")

    assert_ok(run_setup(["--keybinds"], kb_sandbox))
    assert "shift-enter" in codex.read_text(encoding="utf-8")

    assert_ok(run_setup(["--keybinds", "--uninstall"], kb_sandbox))

    text = codex.read_text(encoding="utf-8")
    assert "insert_newline" not in text, f"empty insert_newline remained:\n{text}"
    assert "[tui.keymap.editor]" not in text, f"empty table remained:\n{text}"
    parsed, _ = load_toml(codex)
    assert parsed.get("model") == CODEX_SENTINEL_MODEL, text


def test_backs_up_original_before_change_and_leaves_no_temp_file(kb_sandbox):
    """covers: R4 회귀 (핵심 결정 8 — 훅 설치와 같은 철학)"""
    assert_ok(run_setup(["--keybinds"], kb_sandbox))

    backups = sorted(kb_sandbox.vscode.parent.glob(kb_sandbox.vscode.name + ".bak.*"))
    assert backups, "changed VS Code keybindings.json without leaving a backup"
    assert FOREIGN_VSCODE_COMMAND in backups[0].read_text(encoding="utf-8"), "backup is not the original"
    assert not list(kb_sandbox.vscode.parent.glob("*.tmp-claude-code-setup")), "temp file remained"


def test_rerun_does_not_pile_up_backups(kb_sandbox):
    """covers: Test-13 (두 번 설치해도 늘어나지 않는다)

    백업은 '바꿀 때만' 남겨야 한다. 내용이 같은데도 다시 쓰면 재실행마다 .bak 이 쌓여
    사용자 디렉터리를 어지럽힌다 — 멱등의 의미는 파일을 건드리지 않는 것이다.
    """
    def backup_count():
        n = 0
        for d in (kb_sandbox.vscode.parent, kb_sandbox.cursor.parent,
                  kb_sandbox.home / ".claude", kb_sandbox.home / ".codex"):
            n += len(list(d.glob("*.bak.*"))) if d.is_dir() else 0
        return n

    assert_ok(run_setup(["--keybinds"], kb_sandbox))
    first = backup_count()
    assert_ok(run_setup(["--keybinds"], kb_sandbox))
    assert backup_count() == first, "idempotent rerun left more backups"
    assert_newline_install_result(kb_sandbox, "shift-enter")


def test_no_warning_without_unsupported_terminal_injection(kb_sandbox):
    """covers: Test-24 대조군

    감지가 무조건 참이면 주입 테스트는 아무것도 검증하지 못한다. 무주입 대조군이 필요하다.
    """
    proc = run_setup(["--keybinds"], kb_sandbox)
    assert_ok(proc)
    assert "cannot distinguish" not in combined_output(proc), combined_output(proc)


# ── Codex 리뷰 회귀 — docs/reviews/newline-keybind-codex-review.md ──────────────
#
# 앞선 정적 검토와 같은 패턴이 반복됐다: 확정 결함 아홉 개가 전부 어떤 픽스처도
# 시드하지 않는 분기에 있었다. 아래는 그 분기를 직접 만들어 고정한다.


def test_uninstall_restores_claude_value_overwritten_by_install(kb_sandbox):
    """covers: Test-12 (사용자 원래 항목은 남긴다)

    고른 키에 사용자가 다른 명령을 걸어두었으면 설치는 그것을 덮어쓴다. 되돌릴 기록을
    남기지 않으면 uninstall 이 원상 복구가 아니라 삭제가 된다.
    """
    claude_kb = kb_sandbox.home / ".claude" / "keybindings.json"
    claude_kb.write_text(json.dumps({"bindings": [{"context": "Chat", "bindings": {
        "shift+enter": "chat:submit",
        "ctrl+enter": "chat:newline",
    }}]}, indent=2), encoding="utf-8")

    assert_ok(run_setup(["--keybinds", "--newline", "shift-enter"], kb_sandbox))
    assert_ok(run_setup(["--keybinds", "--uninstall"], kb_sandbox))

    chat = claude_chat_map(json.loads(claude_kb.read_text(encoding="utf-8")))
    assert chat.get("shift+enter") == "chat:submit", (
        f"did not restore the value the install overwrote: {chat!r}"
    )
    assert chat.get("ctrl+enter") == "chat:newline", (
        f"removed the user's own unchosen key: {chat!r}"
    )


def test_uninstall_does_not_remove_codex_chords_absent_from_manifest(kb_sandbox):
    """covers: Test-12 (사용자 원래 항목은 남긴다)

    insert_newline 의 "shift-enter" 는 사용자가 직접 쓰는 값과 글자까지 같다.
    영수증 없이 값만 보고 지우면 남의 설정을 지운다.
    """
    codex = kb_sandbox.home / ".codex" / "config.toml"
    codex.write_text(
        '[tui.keymap.editor]\ninsert_newline = ["shift-enter", "ctrl-enter", "ctrl-j"]\n',
        encoding="utf-8")

    assert_ok(run_setup(["--keybinds", "--newline", "shift-enter"], kb_sandbox))
    assert_ok(run_setup(["--keybinds", "--uninstall"], kb_sandbox))

    parsed, text = load_toml(codex)
    insert = (((parsed.get("tui") or {}).get("keymap") or {}).get("editor") or {}
              ).get("insert_newline") or []
    for key in ("shift-enter", "ctrl-enter", "ctrl-j"):
        assert key in insert, f"user's existing {key} disappeared: {insert!r}\n{text}"


@pytest.mark.parametrize("source", [
    'tui.keymap.editor.insert_newline = ["ctrl-j"]\n',
    '[tui]\nkeymap.editor.insert_newline = ["ctrl-j"]\n',
    '[tui.keymap]\neditor.insert_newline = ["ctrl-j"]\n',
    'tui.keymap.editor.indent = ["tab"]\n',
])
def test_dotted_codex_toml_stays_valid(kb_sandbox, source):
    """covers: Test-20 (다른 키/테이블 보존)

    TOML 에서 `a.b.c.key = ...` 는 그 테이블을 만든다. 헤더 형태만 알아보면 이미 만들어진
    테이블에 [tui.keymap.editor] 헤더를 덧붙이게 되고, 같은 테이블 이중 선언이라 파서가
    파일 전체를 거부한다.
    """
    codex = kb_sandbox.home / ".codex" / "config.toml"
    codex.write_text(source, encoding="utf-8")

    assert_ok(run_setup(["--keybinds", "--newline", "shift-enter"], kb_sandbox))

    parsed, text = load_toml(codex)
    assert parsed is not None, f"TOML is broken:\n{text}"
    insert = (((parsed.get("tui") or {}).get("keymap") or {}).get("editor") or {}
              ).get("insert_newline") or []
    assert "shift-enter" in insert, f"{insert!r}\n{text}"


def test_wt_user_entries_for_unchosen_key_are_kept(kb_sandbox):
    """covers: Test-26 / 핵심 결정 5

    시퀀스만 보면 사용자가 Ctrl+Enter 에 직접 건 항목도 '우리 것' 으로 읽힌다.
    지금 쓰는 키도 아니고 영수증에도 없으면 우리 것이 아니다.
    """
    kb_sandbox.wt.write_text(json.dumps({"actions": [
        {"command": {"action": "sendInput", "input": "\u001b\r"}, "keys": "ctrl+enter"},
        {"command": "copy", "keys": WT_FOREIGN_KEYS},
    ]}, indent=2), encoding="utf-8")

    assert_ok(run_setup(["--keybinds", "--newline", "shift-enter"], kb_sandbox))

    raw = kb_sandbox.wt.read_text(encoding="utf-8")
    settings = json.loads(raw)
    sends = wt_send_bindings(settings)
    assert any(entry[0] in ("ctrl+enter", "ctrl+return") for entry in sends), (
        f"user's own ctrl+enter sendInput disappeared:\n{raw}"
    )
    assert WT_FOREIGN_KEYS in raw


def test_wt_default_paths_also_find_non_stable():
    """covers: R4 회귀 (Test-26/27 은 env 주입으로만 돌아 실제 해석기를 못 본다)"""
    apply = load_apply_module()
    # LOCALAPPDATA 를 인자로 준다 — 플랫폼과 무관하게 해석기 자체를 본다.
    names = [str(p) for p in apply.default_wt_paths(local="X:/Local")]
    assert any("WindowsTerminalPreview" in n for n in names), names
    assert any("WindowsTerminalCanary" in n for n in names), names
    assert any(n.endswith(str(Path("Microsoft") / "Windows Terminal" / "settings.json"))
               for n in names), names


def test_tmux_comments_do_not_count_as_config(kb_sandbox):
    """covers: Test-23 (없으면 추가, 있으면 중복하지 않는다)

    문자열 검사가 주석까지 세면 `# extended-keys disabled` 한 줄이 설치를 통째로 막는다.
    """
    if sys.platform == "win32":
        pytest.skip("tmux.conf is a POSIX install target")
    apply = load_apply_module()
    tmux = kb_sandbox.home / ".tmux.conf"
    tmux.write_text("# extended-keys disabled\n# extkeys experiment\nset -g mouse on\n",
                    encoding="utf-8")

    assert_ok(run_setup(["--keybinds"], kb_sandbox))

    text = tmux.read_text(encoding="utf-8")
    assert apply.TMUX_EXTENDED_LINE in text, f"a comment blocked the install:\n{text}"
    assert apply.TMUX_EXTKEYS_LINE in text, f"a comment blocked the install:\n{text}"


@tmux_only
def test_manifest_remembers_both_lines_after_partial_repair(kb_sandbox):
    """covers: Test-12/13 (영수증이 빈 값으로 덮이지 않는다)

    사용자가 우리 줄 하나만 지운 뒤 재실행하면 그 한 줄만 다시 넣는다. 영수증을 덮어쓰면
    나머지 한 줄의 소유권 기록을 잃고, uninstall 이 우리 줄을 남긴다.
    """
    apply = load_apply_module()
    tmux = kb_sandbox.home / ".tmux.conf"
    tmux.write_text(TMUX_SENTINEL + "\n", encoding="utf-8")

    assert_ok(run_setup(["--keybinds"], kb_sandbox))
    kept = [ln for ln in tmux.read_text(encoding="utf-8").splitlines()
            if ln != apply.TMUX_EXTKEYS_LINE]
    tmux.write_text("\n".join(kept) + "\n", encoding="utf-8")

    assert_ok(run_setup(["--keybinds"], kb_sandbox))
    assert_ok(run_setup(["--keybinds", "--uninstall"], kb_sandbox))

    text = tmux.read_text(encoding="utf-8")
    assert apply.TMUX_EXTENDED_LINE not in text, f"the line we added remained:\n{text}"
    assert apply.TMUX_EXTKEYS_LINE not in text, f"the line we added remained:\n{text}"
    assert TMUX_SENTINEL in text, "also removed the user's line"


@tmux_only
def test_tmux_also_leaves_a_backup(kb_sandbox):
    """covers: R4 회귀 (핵심 결정 8 — 백업은 tmux.conf 에도 적용된다)"""
    tmux = kb_sandbox.home / ".tmux.conf"
    tmux.write_text(TMUX_SENTINEL + "\n", encoding="utf-8")

    assert_ok(run_setup(["--keybinds"], kb_sandbox))

    backups = list(tmux.parent.glob(".tmux.conf.bak.*"))
    assert backups, "changed tmux.conf without leaving a backup"
    assert TMUX_SENTINEL in backups[0].read_text(encoding="utf-8")


@posix_only
def test_keeps_symlink_intact_and_preserves_permissions(kb_sandbox):
    """covers: R4 회귀 (핵심 결정 8)

    dotfile 관리자는 설정을 링크로 건다. 링크를 os.replace 로 갈아치우면 일반 파일이 되어
    관리에서 떨어져 나가고, 새 파일의 기본 권한이 0600 설정을 0644 로 넓힌다.
    """
    codex = kb_sandbox.home / ".codex" / "config.toml"
    real = kb_sandbox.home / ".codex" / "real-config.toml"
    if codex.exists():
        codex.unlink()
    real.write_text('model = "%s"\n' % CODEX_SENTINEL_MODEL, encoding="utf-8")
    os.chmod(real, 0o600)
    os.symlink(str(real), str(codex))

    assert_ok(run_setup(["--keybinds"], kb_sandbox))

    assert codex.is_symlink(), "symlink was replaced by a regular file"
    assert "shift-enter" in real.read_text(encoding="utf-8"), "the link target was not changed"
    assert oct(real.stat().st_mode & 0o777) == "0o600", (
        f"permissions were widened: {oct(real.stat().st_mode & 0o777)}"
    )


def test_hooks_uninstall_leaves_keybindings_untouched(kb_sandbox):
    """covers: Test-9 / 핵심 결정 4, AI-69

    --hooks 는 키바인딩을 설치하지 않는다. 제거 쪽에서만 그 경계를 무시하면 안 된다.
    훅 제거는 jarvis 로 옮겨 가 --hooks --uninstall 은 아무것도 지우지 않고 exit 1 로 끝난다.
    """
    assert_ok(run_setup(["--keybinds"], kb_sandbox))
    before = len(managed_sendsequences(load_keybindings(kb_sandbox.vscode)))
    assert before == 1

    tree = snapshot_tree(kb_sandbox.root)
    assert run_setup(["--hooks", "--uninstall"], kb_sandbox).returncode == 1
    assert snapshot_tree(kb_sandbox.root) == tree

    after = len(managed_sendsequences(load_keybindings(kb_sandbox.vscode)))
    assert after == before, "--hooks --uninstall removed the managed keybindings"


def _isolated_apply(monkeypatch, tmp_path):
    apply = load_apply_module()
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir()
    for name in (apply.VSCODE_ENV, apply.CURSOR_ENV, apply.WT_ENV, "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    run = lambda *flags: apply.run(apply.parse_args(["--home", str(home), *flags]))  # noqa: E731
    return home, run


@tmux_only
def test_keybind_uninstall_removes_the_files_it_created_once_they_are_empty(monkeypatch, tmp_path):
    home, run = _isolated_apply(monkeypatch, tmp_path)
    created = (home / ".claude" / "keybindings.json", home / ".codex" / "config.toml", home / ".tmux.conf")
    assert run() == 0
    assert all(path.is_file() for path in created)
    assert run("--uninstall") == 0
    assert [path for path in created if path.exists()] == []


@tmux_only
def test_keybind_uninstall_keeps_files_that_existed_or_gained_user_content(monkeypatch, tmp_path):
    home, run = _isolated_apply(monkeypatch, tmp_path)
    tmux, codex, claude_kb = home / ".tmux.conf", home / ".codex" / "config.toml", home / ".claude" / "keybindings.json"
    tmux.write_text("", encoding="utf-8")
    user_kb = {"bindings": [{"context": "Global", "bindings": {"ctrl+k": "app:toggleTodos"}}]}
    claude_kb.write_text(json.dumps(user_kb), encoding="utf-8")
    assert run() == 0
    with codex.open("a", encoding="utf-8") as handle:
        handle.write('model = "o3"\n')
    assert run("--uninstall") == 0
    assert tmux.read_text(encoding="utf-8") == ""
    assert 'model = "o3"' in codex.read_text(encoding="utf-8")
    # 설치가 만든 빈 Chat 블록을 남기지 않는다.
    assert json.loads(claude_kb.read_text(encoding="utf-8")) == user_kb


@tmux_only
def test_keybind_uninstall_restores_a_replaced_chat_binding(monkeypatch, tmp_path):
    """빈 Chat 을 빼는 정리가 설치가 덮어쓴 사용자 값을 되돌리기 전에 돌면 그 값을 잃는다."""
    home, run = _isolated_apply(monkeypatch, tmp_path)
    claude_kb = home / ".claude" / "keybindings.json"
    user_kb = {"bindings": [{"context": "Chat", "bindings": {"shift+enter": "chat:submit"}}]}
    claude_kb.write_text(json.dumps(user_kb), encoding="utf-8")
    assert run() == 0
    assert run("--uninstall") == 0
    assert json.loads(claude_kb.read_text(encoding="utf-8")) == user_kb


# ── Claude Code keybindings.json 형식 · VS Code 주석 보존 ─────────────────────
#
# 예전 설치는 {"Chat": {...}} 를 썼다. Claude Code 는 'keybindings.json must have a "bindings"
# array' 로 그 파일을 통째로 무시하므로 줄바꿈 키가 한 번도 적용되지 않았다.


def _claude_apply(tmp_path, content=None):
    apply = load_apply_module()
    path = tmp_path / "keybindings.json"
    if content is not None:
        path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return apply, path


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_new_claude_keybindings_file_uses_claude_code_format(tmp_path):
    apply, path = _claude_apply(tmp_path)
    added, overwrote = apply.apply_claude_keybindings(path, "shift-enter", False, False)
    assert (added, overwrote) == (["shift+enter"], {})
    assert _read_json(path) == {
        "$schema": "https://www.schemastore.org/claude-code-keybindings.json",
        "$docs": "https://code.claude.com/docs/en/keybindings",
        "bindings": [{"context": "Chat", "bindings": {"shift+enter": "chat:newline"}}],
    }


def test_valid_claude_keybindings_get_no_stray_top_level_key(tmp_path):
    user = {"$schema": "https://www.schemastore.org/claude-code-keybindings.json",
            "x-user-note": "keep",
            "bindings": [{"context": "Global", "bindings": {"ctrl+k": "app:toggleTodos"}}]}
    apply, path = _claude_apply(tmp_path, user)
    apply.apply_claude_keybindings(path, "ctrl-enter", False, False)
    data = _read_json(path)
    assert set(data) == set(user), f"top-level keys changed: {data!r}"
    assert data["x-user-note"] == "keep"
    assert data["bindings"][0] == user["bindings"][0], "another context block changed"
    assert claude_chat_map(data) == {"ctrl+enter": "chat:newline"}


@pytest.mark.parametrize("has_bindings", [False, True])
def test_legacy_top_level_chat_is_migrated_into_bindings(tmp_path, has_bindings):
    """예전 설치가 남긴 최상위 Chat 은 재실행 때 bindings 배열로 옮긴다. 있는 키는 덮어쓰지 않는다."""
    legacy = {"Chat": {"shift+enter": "chat:newline", "ctrl+g": "chat:compact",
                       "escape": "chat:legacy"},
              "Global": {"f9": "app:toggleHelp"}}
    if has_bindings:
        legacy["bindings"] = [{"context": "Chat", "bindings": {"escape": "chat:cancel"}}]
    apply, path = _claude_apply(tmp_path, legacy)
    added, _ = apply.apply_claude_keybindings(path, "shift-enter", False, False,
                                              prev_keys=["shift+enter"])
    assert added == ["shift+enter"], "lost the receipt for the key the old install added"
    data = _read_json(path)
    assert claude_chat_map(data) == {
        "shift+enter": "chat:newline",
        "ctrl+g": "chat:compact",
        "escape": "chat:cancel" if has_bindings else "chat:legacy",
    }
    assert data["Global"] == {"f9": "app:toggleHelp"}, "another top-level key changed"
    assert "$docs" not in data, "added $docs to an existing file"


@pytest.mark.parametrize("before, after", [
    # 예전 형식: 옮긴 뒤 우리 키만 뺀다.
    ({"Chat": {"shift+enter": "chat:newline", "ctrl+g": "chat:compact"}},
     {"bindings": [{"context": "Chat", "bindings": {"ctrl+g": "chat:compact"}}]}),
    # 예전 형식에 우리 키만 있었다: 빈 Chat 블록은 빼고, 옮기며 생긴 bindings 배열은 남는다.
    ({"Chat": {"shift+enter": "chat:newline"}, "Global": {"f9": "app:toggleHelp"}},
     {"Global": {"f9": "app:toggleHelp"}, "bindings": []}),
    # 새 형식.
    ({"bindings": [{"context": "Global", "bindings": {"f9": "app:toggleHelp"}},
                   {"context": "Chat", "bindings": {"shift+enter": "chat:newline",
                                                    "ctrl+g": "chat:compact"}}]},
     {"bindings": [{"context": "Global", "bindings": {"f9": "app:toggleHelp"}},
                   {"context": "Chat", "bindings": {"ctrl+g": "chat:compact"}}]}),
    ({"bindings": [{"context": "Global", "bindings": {"f9": "app:toggleHelp"}},
                   {"context": "Chat", "bindings": {"shift+enter": "chat:newline"}}]},
     {"bindings": [{"context": "Global", "bindings": {"f9": "app:toggleHelp"}}]}),
])
def test_claude_uninstall_handles_legacy_and_current_shapes(tmp_path, before, after):
    apply, path = _claude_apply(tmp_path, before)
    apply.apply_claude_keybindings(path, "shift-enter", True, False, prev_keys=["shift+enter"])
    assert _read_json(path) == after
    assert apply.SKIPPED_TARGETS == []


def test_claude_uninstall_restores_overwritten_value_in_legacy_file(tmp_path):
    apply, path = _claude_apply(tmp_path, {"Chat": {"shift+enter": "chat:newline"}})
    apply.apply_claude_keybindings(path, "shift-enter", True, False, prev_keys=["shift+enter"],
                                   prev_overwrote={"shift+enter": "chat:submit"})
    assert _read_json(path) == {
        "bindings": [{"context": "Chat", "bindings": {"shift+enter": "chat:submit"}}]}


@pytest.mark.parametrize("uninstall", [False, True])
def test_claude_bindings_that_is_not_an_array_is_skipped(tmp_path, uninstall):
    content = {"bindings": {"Chat": {"shift+enter": "chat:submit"}}}
    apply, path = _claude_apply(tmp_path, content)
    before = path.read_text(encoding="utf-8")
    result = apply.apply_claude_keybindings(path, "shift-enter", uninstall, False,
                                            prev_keys=["shift+enter"])
    assert result == ([], {})
    assert path.read_text(encoding="utf-8") == before, "rewrote a file it could not understand"
    assert [p for p, _ in apply.SKIPPED_TARGETS] == [str(path)]


VSCODE_WITH_COMMENTS = """\
// Place your key bindings in this file to override the defaults
[
    // user: clear terminal
    {
        "key": "ctrl+alt+f9",
        "command": "workbench.action.terminal.clear", // trailing note
        "when": "terminalFocus"
    },
    /* block comment with "quotes" and ] [ */
    {
        "key": "ctrl+alt+k",
        "command": "workbench.action.terminal.sendSequence",
        "args": {"text": "keep-me // not a comment"},
        "when": "terminalFocus"
    }
]
"""


def test_ide_keybindings_keep_user_comments_through_install_switch_and_uninstall(tmp_path):
    apply = load_apply_module()
    path = tmp_path / "keybindings.json"
    path.write_text(VSCODE_WITH_COMMENTS, encoding="utf-8")
    comments = ("// Place your key bindings", "// user: clear terminal", "// trailing note",
                '/* block comment with "quotes" and ] [ */')

    for chord in ("shift-enter", "ctrl-enter"):
        assert apply.apply_ide_keybindings(path, chord, False, False) is True
        text = path.read_text(encoding="utf-8")
        for comment in comments:
            assert comment in text, f"user comment {comment!r} disappeared:\n{text}"
        assert_ide_install(path, chord)
        entries = load_keybindings(path)
        assert entries[:2] == parse_jsonc(VSCODE_WITH_COMMENTS), "user entries changed"
        assert len(entries) == 3

    assert apply.apply_ide_keybindings(path, "shift-enter", True, False) is True
    assert path.read_text(encoding="utf-8") == VSCODE_WITH_COMMENTS, (
        "install + uninstall did not restore the original text"
    )


def test_installed_vscode_keybindings_keep_the_leading_comment(kb_sandbox):
    assert_ok(run_setup(["--keybinds"], kb_sandbox))
    text = kb_sandbox.vscode.read_text(encoding="utf-8")
    assert text.startswith("// Place your key bindings in this file"), text
    assert_ide_install(kb_sandbox.vscode, "shift-enter")
    assert_foreign_preserved(kb_sandbox.vscode)


# ── 공유 영수증을 읽지 못하면 아무것도 쓰지 않는다 ─────────────────────────────
#
# ~/.claude/.claude-code-setup.json 은 훅·하네스 설치와 같이 쓰는 영수증이다. 읽지 못한 것을
# 없는 것처럼 다루면 마지막에 keybinds 만 담아 덮어써 harness_installs 등 다른 기록을 잃는다.


@tmux_only
@pytest.mark.parametrize("receipt", ['{"harness_installs": [', ''])
@pytest.mark.parametrize("flags", [(), ("--uninstall",)])
def test_unreadable_shared_receipt_aborts_without_writing(monkeypatch, tmp_path, capsys,
                                                          receipt, flags):
    home, run = _isolated_apply(monkeypatch, tmp_path)
    manifest = home / ".claude" / ".claude-code-setup.json"
    manifest.write_text(receipt, encoding="utf-8")
    before = snapshot_tree(home)
    assert run(*flags) != 0
    assert snapshot_tree(home) == before, "wrote files although the receipt was unreadable"
    assert str(manifest) in capsys.readouterr().err


def test_claude_uninstall_keeps_users_own_empty_bindings_array(tmp_path):
    user = {"$schema": "https://www.schemastore.org/claude-code-keybindings.json", "bindings": []}
    apply, path = _claude_apply(tmp_path, user)
    added, _ = apply.apply_claude_keybindings(path, "shift-enter", False, False)
    apply.apply_claude_keybindings(path, "shift-enter", True, False, prev_keys=added)
    assert _read_json(path) == user


@tmux_only
@pytest.mark.parametrize("receipt", ['["not", "an", "object"]', 'null'])
def test_shared_receipt_that_is_not_an_object_is_treated_as_missing(monkeypatch, tmp_path, receipt):
    """jarvis 훅 모듈의 merge_and_diff.py 와 같다: 객체가 아닌 JSON 은 기록을 담을 수 없으므로 빈 영수증으로 보고 계속한다."""
    home, run = _isolated_apply(monkeypatch, tmp_path)
    manifest = home / ".claude" / ".claude-code-setup.json"
    manifest.write_text(receipt, encoding="utf-8")
    assert run() == 0
    assert isinstance(json.loads(manifest.read_text(encoding="utf-8")), dict)


load_tests = collect(globals())
