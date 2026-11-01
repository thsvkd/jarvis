#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""줄바꿈 키바인딩을 VS Code / Cursor / Claude / Codex / WT / tmux 에 외과적으로 설치한다.

훅 병합(jarvis 훅 모듈의 merge_and_diff.py)과 같은 철학: 합집합, 없을 때만 추가, 관리 항목만 되돌림.
settings.json 은 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

NEWLINE_SEQUENCE = "\u001b\r"
# 관리 바인딩 식별용. 설치는 NEWLINE_SEQUENCE 만 쓰지만, 이전 후보 시퀀스도 우리 것으로 본다.
SSOT_SEQUENCES = (
    "\u001b[13;2u",
    "\u001b\r",
    "\u001b[27;2;13~",
)
VALID_CHORDS = ("shift-enter", "ctrl-enter")
SEND_SEQUENCE_CMD = "workbench.action.terminal.sendSequence"
MANIFEST_NAME = ".claude-code-setup.json"

TMUX_EXTENDED_LINE = "set -s extended-keys on"
TMUX_EXTKEYS_LINE = "set -as terminal-features 'xterm*:extkeys'"

VSCODE_ENV = "CLAUDE_SETUP_VSCODE_KEYBINDINGS"
CURSOR_ENV = "CLAUDE_SETUP_CURSOR_KEYBINDINGS"
WT_ENV = "CLAUDE_SETUP_WT_SETTINGS"
DETECTED_TERM_ENV = "CLAUDE_SETUP_DETECTED_TERMINAL"


def vscode_key(chord: str) -> str:
    return "shift+enter" if chord == "shift-enter" else "ctrl+enter"


def vscode_other_key(chord: str) -> str:
    return "ctrl+enter" if chord == "shift-enter" else "shift+enter"


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
    stripped = strip_trailing_commas("".join(out)).strip()
    if not stripped:
        return []
    return json.loads(stripped)


def strip_trailing_commas(text: str) -> str:
    """VS Code 는 `[{...},]` 같은 트레일링 콤마를 정상으로 받아들인다.

    json.loads 는 거부하므로, 문자열 밖의 콤마 중 뒤따르는 것이 ] 또는 } 뿐인 것만 지운다.
    """
    out = []
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == chr(34):
                in_str = False
            continue
        if ch == chr(34):
            in_str = True
            out.append(ch)
            continue
        if ch == ",":
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in "]}":
                continue
        out.append(ch)
    return "".join(out)


BACKUP_SUFFIX = ".bak."
TMP_SUFFIX = ".tmp-claude-code-setup"


def real_target(path: Path) -> Path:
    """심볼릭 링크면 그 실체를 돌려준다.

    dotfile 관리자(chezmoi, stow, dotbot …)는 설정을 링크로 걸어둔다. 링크 자체를
    os.replace 로 갈아치우면 일반 파일이 되어 관리에서 떨어져 나간다 — 링크는 남기고
    가리키는 실체를 고쳐야 한다.
    """
    try:
        if path.is_symlink():
            return Path(os.path.realpath(str(path)))
    except OSError:
        pass
    return path


def backup_file(path: Path) -> None:
    """바꾸기 전 원본을 .bak.<타임스탬프> 로 남긴다 (훅 설치와 같은 철학)."""
    if not path.is_file():
        return
    stamp = time.strftime("%Y%m%d%H%M%S")
    target = path.with_name(path.name + BACKUP_SUFFIX + stamp)
    n = 1
    while target.exists():
        target = path.with_name(path.name + BACKUP_SUFFIX + stamp + "-" + str(n))
        n += 1
    target.write_bytes(path.read_bytes())


def write_text_atomic(path: Path, text: str) -> None:
    """임시 파일에 쓰고 교체한다 — 쓰기 도중 끊겨도 원본이 반쯤 잘리지 않는다.

    바꾸기 전 백업을 남긴다. 사용자가 직접 관리하던 설정 파일이므로 되돌릴 수단이 있어야 한다.
    """
    target = real_target(path)
    # 내용이 같으면 아무것도 하지 않는다. 이 검사가 없으면 멱등 재실행마다 백업이 쌓인다.
    if target.is_file():
        try:
            if target.read_text(encoding="utf-8") == text:
                return
        except OSError:
            pass
    target.parent.mkdir(parents=True, exist_ok=True)
    backup_file(target)
    tmp = target.with_name(target.name + TMP_SUFFIX)
    try:
        tmp.write_text(text, encoding="utf-8")
        # 새로 만든 임시 파일은 기본 권한(대개 0644)을 갖는다. 0600 설정 파일을
        # 교체하면 권한이 넓어져 내용이 노출된다 — 있던 권한을 그대로 옮긴다.
        if target.exists():
            try:
                shutil.copymode(str(target), str(tmp))
            except OSError:
                pass
        os.replace(str(tmp), str(target))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def write_json(path: Path, data) -> None:
    text = json.dumps(data, ensure_ascii=True, indent=4)
    write_text_atomic(path, text + "\n")


def resolve_home(cli_home: str | None) -> Path:
    if cli_home:
        return Path(cli_home)
    raw = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "."
    return Path(raw)


def env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    return Path(value)


def is_windows() -> bool:
    return os.name == "nt" or sys.platform == "win32"


_UNSET = object()


def current_platform() -> str:
    return "win32" if is_windows() else sys.platform


def default_ide_path(home: Path, app: str, platform=_UNSET, appdata=_UNSET) -> Path:
    """VS Code/Cursor 사용자 keybindings.json 의 기본 경로.

    platform/appdata 를 인자로 받는다 — 테스트가 env 주입 없이 세 OS 를 단언할 수 있어야
    이 함수가 "실행되지 않는 죽은 코드" 가 되지 않는다.
    """
    plat = current_platform() if platform is _UNSET else platform
    data = os.environ.get("APPDATA") if appdata is _UNSET else appdata
    if plat == "win32" and data:
        return Path(data) / app / "User" / "keybindings.json"
    if plat == "darwin":
        # macOS 의 VS Code/Cursor 는 XDG 가 아니라 Application Support 를 쓴다.
        return home / "Library" / "Application Support" / app / "User" / "keybindings.json"
    return home / ".config" / app / "User" / "keybindings.json"


def default_vscode_path(home: Path, platform=_UNSET, appdata=_UNSET) -> Path:
    return default_ide_path(home, "Code", platform, appdata)


def default_cursor_path(home: Path, platform=_UNSET, appdata=_UNSET) -> Path:
    return default_ide_path(home, "Cursor", platform, appdata)


WT_PACKAGES = (
    "Microsoft.WindowsTerminal_8wekyb3d8bbwe",          # Stable
    "Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe",   # Preview
    "Microsoft.WindowsTerminalCanary_8wekyb3d8bbwe",    # Canary
)


def default_wt_paths(local=_UNSET) -> list:
    """설치돼 있을 수 있는 Windows Terminal settings.json 을 전부 돌려준다.

    Stable 패키지 하나만 보면 Preview/Canary 나 비패키지(unpackaged) 설치에서는
    아무 일도 일어나지 않은 채 성공으로 끝난다. 포터블 모드는 실행 파일 옆에 설정을
    두어 위치를 알 수 없으므로 대상이 아니다.
    """
    local = os.environ.get("LOCALAPPDATA") if local is _UNSET else local
    if not local:
        return []
    base = Path(local)
    paths = [base / "Packages" / pkg / "LocalState" / "settings.json" for pkg in WT_PACKAGES]
    paths.append(base / "Microsoft" / "Windows Terminal" / "settings.json")
    return paths


def default_wt_path() -> Path | None:
    for path in default_wt_paths():
        if path.is_file():
            return path
    return None


def binary_on_path(name: str) -> bool:
    if shutil.which(name):
        return True
    if is_windows():
        for ext in (".exe", ".cmd", ".bat", ".ps1"):
            if shutil.which(name + ext):
                return True
    return False


def load_manifest(home: Path) -> dict:
    """없으면 빈 영수증. 읽지 못하면 ValueError.

    훅·하네스 설치와 같이 쓰는 파일이다. 없는 것처럼 다루면 나중에 keybinds 만 담아
    덮어써 다른 설치의 기록을 잃는다. 객체가 아닌 JSON 은 기록을 담을 수 없으므로
    jarvis 훅 모듈의 merge_and_diff.py 처럼 빈 영수증으로 본다.
    """
    path = home / ".claude" / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read the setup receipt {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def write_manifest(home: Path, data: dict) -> None:
    path = home / ".claude" / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + TMP_SUFFIX)
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def merge_manifest_keybinds(home: Path, keybinds: dict) -> None:
    data = load_manifest(home)
    data["keybinds"] = keybinds
    write_manifest(home, data)


def strip_keybinds_from_manifest(home: Path) -> None:
    path = home / ".claude" / MANIFEST_NAME
    if not path.is_file():
        return
    data = load_manifest(home)
    if "keybinds" not in data:
        return
    data.pop("keybinds", None)
    write_manifest(home, data)


def remove_created_if_empty(own_files: dict, created) -> None:
    """설치가 새로 만든 파일이 제거 뒤 비었으면 지운다. 원래 있던 파일·사용자가 내용을 쓴 파일은 남긴다."""
    for name in created or []:
        path = own_files.get(name)
        if path is None or not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        try:
            data = json.loads(text) if text.startswith("{") else None
        except ValueError:
            data = None
        # 설치가 새로 만든 Claude keybindings.json 은 제거 뒤 $schema/$docs 와 빈 bindings 만 남는다.
        if text == "" or (isinstance(data, dict) and not data.get("bindings")
                          and set(data) <= {"$schema", "$docs", "bindings"}):
            path.unlink()
            print(f"  {path} — removed the empty file the installer created")


def invalid_newline_message(value: str) -> str:
    return (
        f"--newline must be shift-enter or ctrl-enter "
        f"(got: {value!s})."
    )


SKIPPED_TARGETS = []


def note_skipped(path, reason: str) -> None:
    """읽지 못해 건너뛴 대상. run() 이 마지막에 요약하고 종료 코드를 바꾼다."""
    SKIPPED_TARGETS.append((str(path), reason))
    print(f"  ⚠️ {reason}: {path}")


def desired_ide_binding(chord: str) -> dict:
    return {
        "key": vscode_key(chord),
        "command": SEND_SEQUENCE_CMD,
        "args": {"text": NEWLINE_SEQUENCE},
        "when": "terminalFocus",
    }


def sendsequence_text(entry: dict) -> str:
    args = entry.get("args") or {}
    if isinstance(args, dict):
        return args.get("text") or args.get("sequence") or ""
    return ""


def is_ssot_sequence(text: str) -> bool:
    if not isinstance(text, str) or not text:
        return False
    if text in SSOT_SEQUENCES:
        return True
    try:
        decoded = text.encode("utf-8").decode("unicode_escape")
    except Exception:
        decoded = text
    return decoded in SSOT_SEQUENCES


def is_managed_sendsequence(entry) -> bool:
    """우리가 넣은 터미널 줄바꿈 sendSequence. 사용자 sendSequence(keep-me 등)는 제외."""
    if not isinstance(entry, dict):
        return False
    if entry.get("command") != SEND_SEQUENCE_CMD:
        return False
    when = str(entry.get("when") or "")
    if "terminalFocus" not in when:
        return False
    if re.search(r"editor|notebook", when, re.I):
        return False
    return is_ssot_sequence(sendsequence_text(entry))


def bindings_equivalent(entry: dict, desired: dict) -> bool:
    if str(entry.get("key") or "").lower().replace(" ", "") != desired["key"]:
        return False
    if entry.get("command") != desired["command"]:
        return False
    if str(entry.get("when") or "") != desired["when"]:
        return False
    return sendsequence_text(entry) == desired["args"]["text"]


def load_ide_entries(path: Path):
    if not path.is_file():
        return []
    try:
        data = parse_jsonc(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, list):
        return data
    return None


def jsonc_array_layout(text: str):
    """최상위 배열 각 원소의 (시작, 끝) 과 닫는 ] 위치. 문자열·주석 안은 건너뛴다."""
    spans = []
    depth = 0
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            if i < 0:
                break
        elif text.startswith("/*", i):
            i = text.find("*/", i + 2)
            if i < 0:
                break
            i += 1
        elif ch in "[{":
            if depth == 1:
                start = i
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 1:
                spans.append((start, i + 1))
            elif depth == 0:
                return spans, i
        i += 1
    return spans, None


def edit_ide_text(text: str, entries: list, keep: list, add) -> str | None:
    """entries 중 keep 이 거짓인 원소를 지우고 add 를 끝에 붙인 텍스트. 사용자 주석·서식은 그대로.

    keybindings.json 을 통째로 다시 직렬화하면 // 주석이 전부 사라진다 (기본 파일도 주석으로
    시작한다). 배열 모양을 확신할 수 없으면 None — 호출자가 재직렬화로 폴백한다.
    """
    spans, close = jsonc_array_layout(text)
    if close is None or len(spans) != len(entries):
        return None
    ws = " \t\r\n"
    for (s, e), kept in reversed(list(zip(spans, keep))):
        if kept:
            continue
        while s > 0 and text[s - 1] in ws:
            s -= 1
        j = e
        while j < len(text) and text[j] in ws:
            j += 1
        if j < len(text) and text[j] == ",":
            e = j + 1
        elif s > 0 and text[s - 1] == ",":
            s -= 1
        text = text[:s] + text[e:]
    if add is not None:
        block = "\n".join("    " + line for line in json.dumps(add, ensure_ascii=True, indent=4).splitlines())
        spans, close = jsonc_array_layout(text)
        if spans:
            at = spans[-1][1]
            text = text[:at] + ",\n" + block + text[at:]
        else:
            lead = "" if text[:close].endswith("\n") else "\n"
            text = text[:close] + lead + block + "\n" + text[close:]
    return text


def write_ide_entries(path: Path, entries: list, keep: list, add=None) -> None:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    edited = edit_ide_text(text, entries, keep, add)
    if edited is None:
        new_entries = [e for e, k in zip(entries, keep) if k] + ([add] if add is not None else [])
        write_json(path, new_entries)
    else:
        write_text_atomic(path, edited)


def apply_ide_keybindings(path: Path, chord: str, uninstall: bool, dry_run: bool) -> bool:
    """관리 sendSequence 하나를 넣거나 뺀다. 외래 항목은 보존. 변경했으면 True."""
    existed = path.is_file()
    entries = load_ide_entries(path)
    if entries is None:
        note_skipped(path, "cannot read keybindings.json, skipping")
        return False

    desired = desired_ide_binding(chord)
    keep = [not is_managed_sendsequence(e) for e in entries]
    if uninstall:
        changed = not all(keep)
        if dry_run:
            print(f"  {path} (keybindings.json) — remove managed sendSequence")
            return False
        if changed:
            write_ide_entries(path, entries, keep)
        return changed

    managed = [e for e, k in zip(entries, keep) if not k]
    if len(managed) == 1 and bindings_equivalent(managed[0], desired):
        if dry_run:
            print(f"  {path} (keybindings.json) — already {desired['key']}")
        return False
    if dry_run:
        print(f"  {path} (keybindings.json) — {desired['key']} sendSequence")
        return False
    if not existed:
        path.parent.mkdir(parents=True, exist_ok=True)
    write_ide_entries(path, entries, keep, desired)
    return True


CLAUDE_KB_SCHEMA = "https://www.schemastore.org/claude-code-keybindings.json"
CLAUDE_KB_DOCS = "https://code.claude.com/docs/en/keybindings"


def claude_chat_entry(data: dict):
    """bindings 배열의 Chat 블록을 찾거나 붙인다. 반환: (블록, 형식을 고쳤는지) 또는 (None, False).

    Claude Code 는 {"bindings": [{"context": "Chat", "bindings": {...}}]} 만 읽는다 —
    예전 설치가 쓰던 최상위 "Chat" 객체는 'must have a "bindings" array' 로 파일째 무시된다.
    그 최상위 "Chat" 은 여기로 옮긴다. 이미 Chat 블록에 있는 키는 덮어쓰지 않는다.
    """
    blocks = data.setdefault("bindings", [])
    if not isinstance(blocks, list):
        return None, False
    entry = next((b for b in blocks if isinstance(b, dict) and b.get("context") == "Chat"), None)
    if entry is None:
        entry = {"context": "Chat", "bindings": {}}
        blocks.append(entry)
    chat = entry.setdefault("bindings", {})
    if not isinstance(chat, dict):
        return None, False
    legacy = data.get("Chat")
    if not isinstance(legacy, dict):
        return entry, False
    for key, value in legacy.items():
        chat.setdefault(key, value)
    del data["Chat"]
    return entry, True


def apply_claude_keybindings(path: Path, chord: str, uninstall: bool, dry_run: bool,
                             prev_keys=None, prev_overwrote=None):
    """Chat 블록에 고른 키 → chat:newline. 다른 context/bindings/최상위 키 보존."""
    added = []
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            note_skipped(path, "cannot read Claude keybindings.json, skipping")
            return [], {}
        if not isinstance(data, dict):
            note_skipped(path, "Claude keybindings.json top level is not an object, skipping")
            return [], {}
    else:
        data = {"$schema": CLAUDE_KB_SCHEMA, "$docs": CLAUDE_KB_DOCS, "bindings": []}

    entry, migrated = claude_chat_entry(data)
    if entry is None:
        note_skipped(path, "Claude keybindings.json \"bindings\" is not an array of context blocks, skipping")
        return [], {}
    chat = entry["bindings"]

    if uninstall:
        changed = migrated
        # 영수증에 있는 키만 지운다. `chat:newline` 은 사용자도 똑같이 쓸 수 있는 값이라,
        # 값이 같다는 이유로 지우면 남의 바인딩을 지운다 (Test-12 "사용자 원래 항목은 남긴다").
        for key in sorted(set(prev_keys or [])):
            if chat.get(key) == "chat:newline":
                del chat[key]
                changed = True
        # 설치가 덮어쓴 값이 있으면 되돌린다. 안 그러면 제거가 원상 복구가 아니다.
        for key, value in (prev_overwrote or {}).items():
            if key not in chat:
                chat[key] = value
                changed = True
        # 우리 키만 들어 있던 Chat 블록이 비면 블록도 뺀다. 남기면 설치 전과 다른 파일이 된다.
        # 빈 "bindings" 배열은 사용자가 원래 둔 것일 수 있으므로 남긴다.
        if changed and not chat:
            data["bindings"].remove(entry)
        if dry_run:
            print(f"  {path} (Claude keybindings.json) — remove keys added by the installer + restore overwritten values")
            return [], {}
        if changed:
            write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return [], {}

    want = vscode_key(chord)
    other = vscode_other_key(chord)
    # 고르지 않은 수정 Enter 는 우리가 넣은 기록이 있을 때만 뺀다 (핵심 결정 5).
    # 기록이 없으면 사용자가 직접 넣은 것이므로 그대로 둔다.
    if other in set(prev_keys or []) and chat.get(other) == "chat:newline":
        del chat[other]
    # 고른 키에 사용자가 다른 명령을 걸어두었으면 그 값을 영수증에 남긴다 —
    # 기록해 두지 않으면 --uninstall 이 되돌릴 수 없다.
    overwrote = dict(prev_overwrote or {})
    prior = chat.get(want)
    if prior is not None and prior != "chat:newline" and want not in overwrote:
        overwrote[want] = prior
        print(f"  ℹ️ Replacing the existing value {prior!r} of Claude Chat[{want}] with newline "
              f"(restored on --uninstall).")
    # 이미 사용자가 같은 값을 걸어 두었다면 우리가 넣은 것이 아니다 — 영수증에 적으면
    # uninstall 이 남의 바인딩을 지운다.
    added = [] if prior == "chat:newline" and want not in set(prev_keys or []) else [want]
    chat[want] = "chat:newline"
    if dry_run:
        print(f"  {path} (Claude keybindings.json) — Chat[{want}]=chat:newline")
        return added, overwrote
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return added, overwrote


def _parse_toml_str_list(src: str) -> list:
    src = src.strip()
    if src.startswith("#"):
        return []
    strings = re.findall(r'"([^"]*)"', src)
    if strings:
        return strings
    strings = re.findall(r"'([^']*)'", src)
    if strings:
        return strings
    return []


def split_toml_comment(line: str):
    """문자열 밖의 첫 # 앞뒤로 나눈다 — (코드, 주석). 주석에는 개행이 없다."""
    in_str = False
    quote = ""
    escape = False
    for i, ch in enumerate(line):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_str = False
            continue
        if ch in (chr(34), chr(39)):
            in_str = True
            quote = ch
            continue
        if ch == "#":
            return line[:i], line[i:].rstrip("\r\n")
    return line, ""


def _consume_insert_newline(lines: list, start: int):
    """insert_newline = VALUE 를 읽어 (items, indent, next_index, 주석) 을 반환.

    주석을 먼저 떼어낸다 — 안 그러면 `# "alt-enter" 는 안 먹음` 같은 주석 속 문자열이
    항목으로 읽히고, 재작성할 때 주석이 통째로 사라진다.
    """
    line = lines[start]
    indent = re.match(r"^(\s*)", line).group(1)
    code, comment = split_toml_comment(line.split("=", 1)[1])
    buf = code
    i = start + 1
    while buf.count("[") > buf.count("]") and i < len(lines):
        code, tail = split_toml_comment(lines[i])
        buf += code
        if tail:
            comment = tail
        i += 1
    items = _parse_toml_str_list(buf)
    return items, indent, i, comment


EDITOR_PATH = ("tui", "keymap", "editor")
INSERT_KEY = "insert_newline"


def split_toml_key(raw: str) -> tuple:
    """점 표기 키를 부분으로 나눈다. 따옴표로 감싼 부분 안의 점은 구분자가 아니다."""
    parts = []
    buf = ""
    in_str = False
    quote = ""
    for ch in raw.strip():
        if in_str:
            if ch == quote:
                in_str = False
            else:
                buf += ch
            continue
        if ch in (chr(34), chr(39)):
            in_str = True
            quote = ch
            continue
        if ch == ".":
            parts.append(buf.strip())
            buf = ""
            continue
        buf += ch
    parts.append(buf.strip())
    return tuple(p for p in parts if p)


def toml_section_path(code: str):
    """`[a.b.c]` 면 경로 튜플. 테이블 헤더가 아니면 None, 배열 테이블이면 ARRAY_TABLE."""
    stripped = code.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    if stripped.startswith("[["):
        return ARRAY_TABLE
    return split_toml_key(stripped[1:-1])


ARRAY_TABLE = ("\x00array-table",)


def format_insert_newline(items: list, indent: str = "", prefix: tuple = (),
                          comment: str = "") -> str:
    name = ".".join(prefix + (INSERT_KEY,))
    inner = ", ".join('"%s"' % x for x in items)
    tail = ("  " + comment) if comment else ""
    return "%s%s = [%s]%s\n" % (indent, name, inner, tail)


def codex_insert_newline_items(text: str) -> list:
    """현재 tui.keymap.editor.insert_newline 의 항목. 없으면 빈 리스트."""
    lines = text.splitlines(keepends=True)
    section = ()
    i = 0
    while i < len(lines):
        code, _ = split_toml_comment(lines[i])
        sec = toml_section_path(code)
        if sec is not None:
            section = sec
            i += 1
            continue
        if "=" in code and section is not ARRAY_TABLE:
            full = tuple(section) + split_toml_key(code.split("=", 1)[0])
            if full == EDITOR_PATH + (INSERT_KEY,):
                items, _indent, _next, _comment = _consume_insert_newline(lines, i)
                return items
        i += 1
    return []


def edit_codex_insert_newline(text: str, add_key: str | None, remove_keys) -> str:
    """tui.keymap.editor.insert_newline 만 고친다. 다른 키/테이블은 그대로.

    TOML 에서 그 키는 여러 형태로 쓸 수 있고 전부 유효하다:

        [tui.keymap.editor]        tui.keymap.editor.insert_newline = [...]
        insert_newline = [...]

        [tui.keymap]               [tui]
        editor.insert_newline = [] keymap.editor.insert_newline = [...]

    헤더 형태만 알아보면, 점 표기로 쓴 사용자의 파일에 `[tui.keymap.editor]` 헤더를 새로
    덧붙이게 된다. 같은 테이블을 두 번 선언하는 것이라 TOML 파서가 파일 전체를 거부한다.
    """
    remove_keys = set(remove_keys or [])
    if not text.strip():
        if not add_key:
            return text
        return "[tui.keymap.editor]\ninsert_newline = [\"%s\"]\n" % add_key

    lines = text.splitlines(keepends=True)

    def updated_items(items: list) -> list:
        kept = [x for x in items if x not in remove_keys]
        if add_key and add_key not in kept:
            kept.append(add_key)
        return kept

    out = []
    i = 0
    section = ()
    found = False
    # 헤더 `[tui.keymap.editor]` 안에서 키를 못 찾은 채 섹션을 벗어날 때 쓸 위치.
    in_editor_header = False
    # 점 표기로 이미 이 테이블을 만들어 둔 스코프가 있으면 헤더를 새로 쓰면 안 된다.
    dotted_scope = None

    for raw in lines:
        code, _ = split_toml_comment(raw)
        sec = toml_section_path(code)
        if sec is not None and sec is not ARRAY_TABLE:
            if in_editor_header and not found:
                items = updated_items([])
                if add_key or items:
                    if out and not out[-1].endswith("\n"):
                        out[-1] += "\n"
                    out.append(format_insert_newline(items))
                    found = True
            section = sec
            in_editor_header = sec == EDITOR_PATH
            out.append(raw)
            i += 1
            continue
        if sec is ARRAY_TABLE:
            section = ARRAY_TABLE
            in_editor_header = False
            out.append(raw)
            i += 1
            continue
        if "=" in code and section is not ARRAY_TABLE:
            full = tuple(section) + split_toml_key(code.split("=", 1)[0])
            if full == EDITOR_PATH + (INSERT_KEY,):
                items, indent, i, comment = _consume_insert_newline(lines, i)
                items = updated_items(items)
                prefix = full[len(section):-1]
                # 빈 리스트를 남기면 "줄바꿈 전면 언바인드" 라는 새 설정을 만드는 것이다.
                if items or add_key:
                    out.append(format_insert_newline(items, indent, prefix, comment))
                found = True
                continue
            if len(full) > len(EDITOR_PATH) and full[:len(EDITOR_PATH)] == EDITOR_PATH \
                    and tuple(section) != EDITOR_PATH:
                dotted_scope = tuple(section)
        out.append(raw)
        i += 1

    if not found and in_editor_header:
        items = updated_items([])
        if add_key or items:
            # 파일이 개행 없이 끝나면 마지막 줄에 붙어버려 TOML 이 깨진다.
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n"
            out.append(format_insert_newline(items))
            found = True

    if not found and add_key:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        if dotted_scope is not None:
            # 점 표기로 이미 만들어진 테이블이다. 같은 스코프에 형제 키로 붙인다.
            prefix = EDITOR_PATH[len(dotted_scope):]
            out.append(format_insert_newline([add_key], "", prefix))
        else:
            if out and out[-1].strip():
                out.append("\n")
            out.append("[tui.keymap.editor]\n")
            out.append(format_insert_newline([add_key]))

    return "".join(out)


def drop_empty_editor_table(text: str) -> str:
    """내용이 하나도 없는 [tui.keymap.editor] 헤더를 지운다.

    설치가 이 테이블을 새로 만들 수 있으므로, 제거 후 빈 껍데기를 남기면 원상 복구가 아니다.
    """
    lines = text.splitlines(keepends=True)
    out = []
    i = 0
    while i < len(lines):
        head, _ = split_toml_comment(lines[i])
        if head.strip() == "[tui.keymap.editor]":
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            nxt = split_toml_comment(lines[j])[0].strip() if j < len(lines) else ""
            if j >= len(lines) or (nxt.startswith("[") and nxt.endswith("]")):
                i = j
                continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def apply_codex_toml(path: Path, chord: str, uninstall: bool, dry_run: bool,
                     prev_keys=None) -> list:
    existed = path.is_file()
    text = path.read_text(encoding="utf-8") if existed else ""
    if uninstall:
        # 영수증에 있는 코드만 뺀다. insert_newline 의 "shift-enter" 는 사용자가 직접
        # 쓰는 값과 글자까지 같아서, 기록 없이 지우면 남의 설정을 지운다.
        new_text = drop_empty_editor_table(
            edit_codex_insert_newline(text, None, set(prev_keys or []))
        )
        if dry_run:
            print(f"  {path} (codex config.toml) — remove managed insert_newline key")
            return []
        if new_text != text and existed:
            write_text_atomic(path, new_text)
        return []

    # 고르지 않은 코드는 우리가 넣은 기록이 있을 때만 뺀다 (핵심 결정 5).
    other = vscode_other_codex(chord)
    remove = {other} if other in set(prev_keys or []) else set()
    # 이미 있던 코드는 우리가 넣은 것이 아니다. 영수증에 적으면 uninstall 이 지운다.
    already = chord in codex_insert_newline_items(text)
    added = [] if already else [chord]
    new_text = edit_codex_insert_newline(text, chord, remove)
    if dry_run:
        print(f"  {path} (codex config.toml) — insert_newline += {chord}")
        return added
    if new_text != text or not existed:
        write_text_atomic(path, new_text)
    return added


def vscode_other_codex(chord: str) -> str:
    return "ctrl-enter" if chord == "shift-enter" else "shift-enter"


def _wt_keys(item: dict) -> str:
    keys = item.get("keys") or item.get("key") or ""
    if isinstance(keys, list):
        keys = " ".join(str(k) for k in keys)
    return str(keys or "").lower().replace(" ", "")


def is_wt_send(item) -> bool:
    if not isinstance(item, dict):
        return False
    cmd = item.get("command")
    if isinstance(cmd, dict):
        action = str(cmd.get("action") or "").lower()
        return action in ("sendinput", "send")
    if isinstance(cmd, str):
        return cmd.lower() in ("sendinput", "send")
    return False


def _wt_input(item) -> str:
    cmd = item.get("command")
    if isinstance(cmd, dict):
        return str(cmd.get("input") or cmd.get("sequence") or "")
    return ""


def is_managed_wt(item) -> bool:
    """키만 보면 사용자가 같은 키에 걸어둔 남의 sendInput 까지 지운다.

    VS Code 판정(is_managed_sendsequence)과 같이 시퀀스까지 봐야 우리 것이다.
    """
    if not is_wt_send(item):
        return False
    keys = _wt_keys(item)
    if keys not in ("shift+enter", "ctrl+enter", "shift+return", "ctrl+return"):
        return False
    return is_ssot_sequence(_wt_input(item))


def wt_key(chord: str) -> str:
    return "shift+enter" if chord == "shift-enter" else "ctrl+enter"


def apply_wt_settings(path: Path | None, chord: str, uninstall: bool, dry_run: bool,
                      prev_chord=None, prev_managed=False) -> bool:
    """대상 파일이 없으면 skip. Shift+Enter 선택 시 shift+enter sendInput 을 넣지 않는다.

    지울 수 있는 것은 (a) 지금 우리가 쓰는 키와 (b) 영수증이 우리 것이라고 말하는 이전
    코드의 키뿐이다. 시퀀스가 같더라도 사용자가 직접 건 다른 키는 건드리지 않는다
    (핵심 결정 5 — 고르지 않은 키는 바인딩하지도 언바인드하지도 않는다).
    """
    if path is None or not path.is_file():
        if dry_run:
            print("  Windows Terminal settings.json — no target, skip")
        return False
    try:
        data = parse_jsonc(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        note_skipped(path, "cannot read WT settings.json, skipping")
        return False
    if not isinstance(data, dict):
        note_skipped(path, "WT settings.json top level is not an object, skipping")
        return False

    actions_key = "actions" if "actions" in data else ("keybindings" if "keybindings" in data else "actions")
    actions = list(data.get(actions_key) or [])

    # 우리가 손댈 자격이 있는 키만 모은다.
    ours = set()
    if uninstall:
        if prev_chord:
            ours.add(wt_key(prev_chord))
        if prev_managed:
            ours.update(("shift+enter", "ctrl+enter"))
    else:
        ours.add(wt_key(chord))
        if prev_chord and prev_managed:
            ours.add(wt_key(prev_chord))

    def removable(item) -> bool:
        if not is_managed_wt(item):
            return False
        keys = _wt_keys(item)
        return keys in ours or keys.replace("return", "enter") in ours

    kept = [a for a in actions if not removable(a)]
    managed = False

    if not uninstall and chord == "ctrl-enter":
        kept.append({
            "command": {"action": "sendInput", "input": NEWLINE_SEQUENCE},
            "keys": "ctrl+enter",
        })
        managed = True

    if not uninstall and chord == "shift-enter":
        # 우리는 넣지 않았지만 사용자가 직접 걸어둔 Shift+Enter sendInput 이 남아 있으면
        # WT 는 이미 이 키를 구분하므로 줄이 두 번 생긴다. 지우지는 않고 알린다.
        leftover = [a for a in kept
                    if is_managed_wt(a)
                    and _wt_keys(a).replace("return", "enter") == "shift+enter"]
        if leftover:
            print("  ⚠️ Windows Terminal has a user-defined Shift+Enter sendInput — "
                  "it may insert two newlines. It was not removed; delete it yourself if needed.")

    if dry_run:
        print(f"  {path} (Windows Terminal settings.json)")
        return False

    if kept != actions:
        data[actions_key] = kept
        write_json(path, data)
        return managed or True
    return False


def apply_tmux(path: Path, uninstall: bool, dry_run: bool, previously_added) -> list:
    """POSIX 전용. 없을 때만 extended-keys / extkeys 를 붙인다. 우리가 넣은 줄만 지운다."""
    if is_windows():
        return []
    existed = path.is_file()
    text = path.read_text(encoding="utf-8") if existed else ""
    added = list(previously_added or [])

    if uninstall:
        if not existed:
            return []
        # 영수증에 없으면 우리가 넣은 줄이 아니다. extended-keys 는 사용자가 직접 쓰는
        # 흔한 설정이라, 기록 없이 문자열만 같다고 지우면 남의 설정을 지운다.
        remove = set(added)
        if not remove:
            if dry_run:
                print(f"  {path} (tmux.conf) — no record of lines we added, leaving untouched")
            return []
        new_lines = []
        for line in text.splitlines(True):
            stripped = line.rstrip("\r\n")
            if stripped in remove:
                continue
            new_lines.append(line)
        new_text = "".join(new_lines)
        if dry_run:
            print(f"  {path} (tmux.conf) — remove extended-keys lines added by the installer")
            return []
        if new_text != text:
            write_text_atomic(path, new_text)
        return []

    # 주석까지 세면 `# extended-keys disabled` 한 줄이 설치를 통째로 막는다.
    # 실제 tmux 명령이 있는 줄만 본다.
    active = [split_toml_comment(line)[0] for line in text.splitlines()]
    active_text = "\n".join(active)
    to_add = []
    if "extended-keys" not in active_text:
        to_add.append(TMUX_EXTENDED_LINE)
    if "extkeys" not in active_text:
        to_add.append(TMUX_EXTKEYS_LINE)
    if dry_run:
        print(f"  {path} (tmux.conf)")
        return to_add
    if not to_add:
        return []
    new_text = text
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    new_text += "\n".join(to_add) + "\n"
    # 백업과 원자적 교체는 tmux.conf 에도 똑같이 필요하다 — 사용자가 직접 쓰는 파일이다.
    write_text_atomic(path, new_text)
    return to_add


def detect_terminal() -> str:
    """불가 터미널(Shift+Enter 를 Enter 와 구분하지 못하는 터미널) 이름.

    테스트는 CLAUDE_SETUP_DETECTED_TERMINAL 로 주입한다 (실제 gnome-terminal 없이 검증하려고).
    실사용에서는 터미널 에뮬레이터가 스스로 남기는 환경변수로 판단한다 — 주입이 없을 때만.
    """
    injected = (os.environ.get(DETECTED_TERM_ENV) or "").strip().lower()
    if injected:
        return injected
    env = os.environ
    if env.get("KONSOLE_VERSION") or env.get("KONSOLE_DBUS_SESSION"):
        return "konsole"
    if env.get("GNOME_TERMINAL_SERVICE") or env.get("GNOME_TERMINAL_SCREEN"):
        return "gnome-terminal"
    return ""


def print_warnings() -> None:
    detected = detect_terminal()
    if detected in ("gnome-terminal", "konsole"):
        print(
            "⚠️ This terminal cannot distinguish Shift+Enter. "
            "On Linux, use SSH or an external terminal for Shift+Enter."
        )
    print(
        "⚠️ When Grok /multiline is on, Enter and Shift+Enter have opposite meanings. "
        "The installer does not change that toggle."
    )


def resolve_chord(explicit: str | None, manifest: dict) -> str:
    if explicit:
        return explicit
    kb = manifest.get("keybinds") if isinstance(manifest.get("keybinds"), dict) else {}
    previous = kb.get("newline_chord") if isinstance(kb, dict) else None
    if previous in VALID_CHORDS:
        return previous
    return "shift-enter"


def should_apply_agent(name: str, config_dir: Path) -> bool:
    return binary_on_path(name) or config_dir.is_dir()


def union_receipt(previous, current) -> list:
    """영수증은 덮어쓰지 않고 합집합으로 쌓는다.

    사용자가 우리 줄 하나만 지운 뒤 재실행하면 그 한 줄만 다시 넣는다. 그때 영수증을
    덮어쓰면 나머지 한 줄의 소유권 기록을 잃고, uninstall 이 그 줄을 남긴다.
    """
    out = []
    for item in list(previous or []) + list(current or []):
        if item not in out:
            out.append(item)
    return out


def report_result(done_message: str) -> int:
    """건너뛴 대상이 있으면 성공이라고 말하지 않는다.

    파일을 읽지 못해 건너뛴 것은 "에이전트가 없어서 skip"(핵심 결정 9)과 다르다 —
    사용자는 줄바꿈 키가 적용된 줄 알지만 실제로는 아무것도 설치되지 않았다.
    """
    if not SKIPPED_TARGETS:
        print("✅ " + done_message)
        return 0
    print("⚠️ The following targets were skipped because the file could not be read — the newline key was not applied:")
    for target, reason in SKIPPED_TARGETS:
        print(f"  - {target} ({reason})")
    print("   Fix the JSON syntax and run again. The remaining targets were processed.")
    return 1


def run(args) -> int:
    home = resolve_home(args.home)
    explicit = args.newline
    if explicit is not None and explicit not in VALID_CHORDS:
        print(invalid_newline_message(explicit), file=sys.stderr)
        return 1

    vscode_path = env_path(VSCODE_ENV) or default_vscode_path(home)
    cursor_path = env_path(CURSOR_ENV) or default_cursor_path(home)
    wt_override = WT_ENV in os.environ
    if wt_override:
        wt_path = env_path(WT_ENV)
    else:
        wt_path = default_wt_path()

    claude_dir = home / ".claude"
    codex_dir = home / ".codex"
    # 매니페스트용 .claude 를 만들기 전에 스킵 여부를 고정한다.
    claude_apply = should_apply_agent("claude", claude_dir)
    codex_apply = should_apply_agent("codex", codex_dir)
    claude_kb = claude_dir / "keybindings.json"
    codex_toml = codex_dir / "config.toml"
    tmux_conf = home / ".tmux.conf"

    own_files = {"claude": claude_kb, "codex": codex_toml, "tmux": tmux_conf}
    existed = {name for name, path in own_files.items() if path.exists()}

    try:
        manifest = load_manifest(home)
    except ValueError as exc:
        print(f"{exc}. Fix or remove it and run again; nothing was changed.", file=sys.stderr)
        return 1
    prev_kb = manifest.get("keybinds") if isinstance(manifest.get("keybinds"), dict) else {}

    if args.uninstall:
        print("🧹 Removing newline keybindings (managed entries only)...")
        if args.dry_run:
            print("  🧪 DRY RUN — no files are actually removed.")
        apply_ide_keybindings(vscode_path, "shift-enter", True, args.dry_run)
        apply_ide_keybindings(cursor_path, "shift-enter", True, args.dry_run)
        if claude_kb.is_file() or claude_apply:
            apply_claude_keybindings(claude_kb, "shift-enter", True, args.dry_run,
                                     prev_kb.get("claude_keys_added"),
                                     prev_kb.get("claude_overwrote"))
        if codex_toml.is_file() or codex_apply:
            apply_codex_toml(codex_toml, "shift-enter", True, args.dry_run,
                             prev_kb.get("codex_keys_added"))
        apply_wt_settings(wt_path, "shift-enter", True, args.dry_run,
                          prev_kb.get("newline_chord"), bool(prev_kb.get("wt_managed")))
        apply_tmux(tmux_conf, True, args.dry_run, prev_kb.get("tmux_added"))
        if not args.dry_run:
            remove_created_if_empty(own_files, prev_kb.get("created_files"))
            strip_keybinds_from_manifest(home)
        return report_result("Keybindings removed — your original entries were preserved.")

    chord = resolve_chord(explicit, manifest)
    print(f"🔧 Installing newline keybindings ({chord})...")
    if args.dry_run:
        print("  🧪 DRY RUN — no files are actually changed.")
        print(f"  Plan: VS Code {vscode_path} (keybindings.json)")
        print(f"  Plan: Cursor {cursor_path} (keybindings.json)")
        print(f"  Plan: Claude {claude_kb} (keybindings.json)")
        print(f"  Plan: Codex {codex_toml} (config.toml)")
        if wt_path:
            print(f"  Plan: Windows Terminal {wt_path} (settings.json)")
        if not is_windows():
            print(f"  Plan: tmux {tmux_conf}")

    print_warnings()

    vscode_managed = apply_ide_keybindings(vscode_path, chord, False, args.dry_run)
    cursor_managed = apply_ide_keybindings(cursor_path, chord, False, args.dry_run)

    claude_keys = []
    claude_overwrote = dict(prev_kb.get("claude_overwrote") or {})
    if claude_apply:
        claude_keys, claude_overwrote = apply_claude_keybindings(
            claude_kb, chord, False, args.dry_run,
            prev_kb.get("claude_keys_added"), prev_kb.get("claude_overwrote"))

    codex_keys = []
    if codex_apply:
        codex_keys = apply_codex_toml(codex_toml, chord, False, args.dry_run,
                                      prev_kb.get("codex_keys_added"))

    wt_managed = apply_wt_settings(wt_path, chord, False, args.dry_run,
                                   prev_kb.get("newline_chord"),
                                   bool(prev_kb.get("wt_managed")))
    tmux_added = apply_tmux(tmux_conf, False, args.dry_run, prev_kb.get("tmux_added"))
    if not tmux_added and isinstance(prev_kb.get("tmux_added"), list) and not args.uninstall:
        # 이미 있는 줄은 다시 안 넣지만, 이전에 우리가 넣은 기록은 유지한다 (uninstall 근거).
        if not args.dry_run and "extended-keys" in (
            tmux_conf.read_text(encoding="utf-8") if tmux_conf.is_file() else ""
        ):
            tmux_added = list(prev_kb.get("tmux_added") or [])

    if args.dry_run:
        print("🧪 DRY RUN complete — keybindings.json / Codex config.toml were not written.")
        return 0

    merge_manifest_keybinds(home, {
        "newline_chord": chord,
        "vscode_managed": bool(vscode_managed) or bool(prev_kb.get("vscode_managed")),
        "cursor_managed": bool(cursor_managed) or bool(prev_kb.get("cursor_managed")),
        "wt_managed": (bool(wt_managed) or bool(prev_kb.get("wt_managed")))
        if chord == "ctrl-enter" else False,
        "tmux_added": union_receipt(prev_kb.get("tmux_added"), tmux_added),
        "claude_keys_added": union_receipt(prev_kb.get("claude_keys_added"), claude_keys)
        if chord == prev_kb.get("newline_chord") else claude_keys,
        "claude_overwrote": claude_overwrote,
        "codex_keys_added": union_receipt(prev_kb.get("codex_keys_added"), codex_keys)
        if chord == prev_kb.get("newline_chord") else codex_keys,
        "created_files": union_receipt(
            prev_kb.get("created_files"),
            [name for name, path in own_files.items() if name not in existed and path.is_file()]),
    })
    return report_result("Newline keybindings installed.")


def parse_args(argv):
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--newline", default=None)
    parser.add_argument("--home", default=None)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
