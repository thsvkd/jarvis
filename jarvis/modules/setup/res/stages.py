"""선택(stages, harnesses, tool roles)에서 순서 있는 액션 목록을 만든다.

설치기는 이 목록 뒤에서만 돈다. dry-run 은 같은 목록을 출력하고 홈을 쓰지 않는다.
컨텍스트 맵 도구는 CodeGraph 패키지 npm 설치와 `codegraph serve --mcp` 등록만 낸다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


# Orca 관리는 jarvis 로 옮겨 갔다(jarvis 결정 D11·D16, ORCA-12). 예전 진입점은 이 안내만 내고 exit 1 로 끝난다.
# Windows 의 python3 은 Store alias 일 수 있어 py -3 을 안내한다.
def orca_moved(windows: bool = os.name == "nt") -> str:
    return (
        "Orca is now managed by jarvis; ai-setup no longer installs, updates or removes it. "
        f"From a jarvis checkout run: {'py -3' if windows else 'python3'} -m jarvis module orca plan --json "
        "(uninstall: module orca uninstall --json --dry-run)."
    )


ORCA_MOVED = orca_moved()


# doctor 도 jarvis 로 옮겨 갔다(jarvis 결정 D11, DOCTOR-1~7). --doctor·-Doctor 는 이 안내만 내고 exit 1 로 끝난다.
def doctor_moved(windows: bool = os.name == "nt") -> str:
    return (
        "Doctor is now run by jarvis; ai-setup no longer runs it. "
        f"From a jarvis checkout run: {'py -3' if windows else 'python3'} -m jarvis module doctor status --json."
    )


# 훅(가드 훅·권한 병합·rm 셰임)도 jarvis 로 옮겨 갔다(jarvis HOOKS-1~4). --hooks·-Hooks·--stages hooks 는
# 이 안내만 내고 exit 1 로 끝난다. 영수증의 훅 기록(hook_files·권한 기록)은 jarvis 훅 모듈이 이어 읽는다.
def hooks_moved(windows: bool = os.name == "nt") -> str:
    return (
        "Hooks are now managed by jarvis; ai-setup no longer installs or removes them. "
        f"From a jarvis checkout run: {'py -3' if windows else 'python3'} -m jarvis module hooks plan --json "
        "(uninstall: module hooks uninstall --json --dry-run)."
    )


HOOKS_MOVED = hooks_moved()


STAGE_ORDER = (
    "base",
    "harness",
    "plugins",
    "skills",
    "codegraph",
    "mcp",
    "newline",
    "claude_special",
    "tools",
    "agentsmd",
)

HARNESS_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "grok": "Grok",
    "agy": "Antigravity",
    "opencode": "OpenCode",
    "gemini": "Gemini",
    "copilot": "Copilot",
}

# Claude Code 플러그인은 res/plugins/plugins.json 이 정한다(SPEC). 마켓플레이스를 먼저 등록해야
# `claude plugin install name@marketplace` 가 찾는다.

BUNDLED_SKILLS = ("design-lab", "tailnet-report")

NVM_INSTALL_URL = "https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh"

# CodeGraph 는 npm 전용이라 버전별 배치(npm-release)로 깐다. `npm install -g` 는 돌고 있는
# `codegraph serve --mcp` 의 파일을 그 자리에서 바꾼다(SPEC npm-release).
NPM_RELEASE = str(Path(__file__).resolve().parent / "tools" / "npm_release.py")
CODEGRAPH_ARGV = ("python3", NPM_RELEASE, "install", "codegraph")

MCP_ADD = {
    "claude": ("claude", "mcp", "add", "-s", "user", "codegraph", "--", "codegraph", "serve", "--mcp"),
    "codex": ("codex", "mcp", "add", "codegraph", "--", "codegraph", "serve", "--mcp"),
    "gemini": ("gemini", "mcp", "add", "-s", "user", "codegraph", "codegraph", "serve", "--mcp"),
    "grok": ("grok", "mcp", "add", "codegraph", "--", "codegraph", "serve", "--mcp"),
    "copilot": ("copilot", "mcp", "add", "codegraph", "--", "codegraph", "serve", "--mcp"),
    "agy": ("agy", "mcp", "add", "codegraph", "codegraph", "serve", "--mcp"),
}

MCP_REMOVE = {
    "claude": ("claude", "mcp", "remove", "codegraph", "-s", "user"),
    "codex": ("codex", "mcp", "remove", "codegraph"),
    "gemini": ("gemini", "mcp", "remove", "-s", "user", "codegraph"),
    "grok": ("grok", "mcp", "remove", "codegraph"),
    "copilot": ("copilot", "mcp", "remove", "codegraph"),
    "agy": ("agy", "mcp", "remove", "codegraph"),
}

_LIST_KEYS = (
    "bins",
    "receipt_tools",
    "receipt_harnesses",
    "receipt_plugins",
    "receipt_skills",
    "mcp_registered",
    "mcp_present",
    "marketplaces",
    "receipt_marketplaces",
    "plugin_ids",
    "plugin_ids_other_scope",
)

_FLAG_KEYS = (
    "python_ok",
    "git",
    "npm_global_readonly",
    "psutil_ok",
    "jarvis_checkout",
    "jarvis_unit_owned",
    "agentsmd_exists",
    "newline_done",
    "output_style",
    "mods",
    "omc_hud",
    "agentsmd",
    "agentsmd_written",
    "ss_caps_done",
)


@dataclass(frozen=True)
class Action:
    stage: str
    detail: str
    writes: bool
    argv: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()


def render(actions) -> str:
    return "\n".join(
        f"{index} {action.stage}: {action.detail}" for index, action in enumerate(actions, 1)
    )


def statusline_after(world, actions) -> str:
    """statusLine 문자열은 액션이 바꿔 쓰지 않는다."""
    del actions
    return (world or {}).get("statusline") or ""


def plan_actions(selection, world) -> list[Action]:
    chosen = _selection(selection)
    snap = _world(world)
    if chosen["uninstall"]:
        return _uninstall(chosen, snap)
    actions: list[Action] = []
    selected = set(chosen["stages"])
    for stage in STAGE_ORDER:
        if stage not in selected:
            continue
        actions.extend(_BUILDERS[stage](chosen, snap))
    return actions


def world_after(world, actions) -> dict:
    snap = _world(world)
    for action in actions:
        if not action.writes:
            continue
        for effect in action.effects:
            _apply(snap, effect)
    return snap


def _selection(selection) -> dict:
    source = selection or {}
    stages = source.get("stages")
    if stages is None or stages == "all" or stages == ["all"]:
        stages = list(STAGE_ORDER)
    harnesses = source.get("harnesses")
    if harnesses is None:
        harnesses = ["claude", "codex"]
    elif "all" in harnesses:
        harnesses = list(HARNESS_LABELS)
    return {
        "stages": list(stages),
        "harnesses": list(harnesses),
        "jarvis_role": source.get("jarvis_role"),
        "herdr": bool(source.get("herdr")),
        "ss_caps": bool(source.get("ss_caps")),
        "dry_run": bool(source.get("dry_run")),
        "uninstall": bool(source.get("uninstall")),
        "newline": source.get("newline") or "shift-enter",
        # 명시한 값만 기록된 값을 바꾼다. 기본값으로 재실행해 사용자가 고른 값을 되돌리지 않는다.
        "newline_given": bool(source.get("newline")),
        "overwrite_agentsmd": bool(source.get("overwrite_agentsmd")),
        # 키가 없으면 예전 --stages all 과 같이 세 특별 설정을 모두 계획한다.
        # 온보딩이 No 를 넘긴 경우에만 False 다.
        "include_output_style": source.get("include_output_style", True),
        "include_mods": source.get("include_mods", True),
        "include_omc": source.get("include_omc", True),
    }


def _world(world) -> dict:
    source = dict(world or {})
    snap = {key: list(source.get(key) or []) for key in _LIST_KEYS}
    for key in _FLAG_KEYS:
        snap[key] = bool(source.get(key))
    node = source.get("node_major")
    snap["node_major"] = None if node is None else int(node)
    snap["statusline"] = source.get("statusline") or ""
    snap["newline_chord"] = source.get("newline_chord") or ""
    snap["platform"] = source.get("platform") or "linux"
    return snap


def _apply(world, effect: str) -> None:
    kind, _, rest = effect.partition(":")
    if kind == "flag":
        world[rest] = True
        return
    if kind == "clear":
        world[rest] = False
        return
    if kind == "node":
        world["node_major"] = int(rest)
        return
    if kind == "set":
        key, _, value = rest.partition(":")
        world[key] = value
        return
    bucket, _, item = rest.partition(":")
    if kind == "add":
        if item not in world[bucket]:
            world[bucket].append(item)
        return
    if kind == "del":
        world[bucket] = [value for value in world[bucket] if value != item]


def _node_major(selection) -> int | None:
    majors = []
    stages = set(selection["stages"])
    if "codegraph" in stages:
        majors.append(20)
    if "harness" in stages:
        if "gemini" in selection["harnesses"]:
            majors.append(20)
        if "copilot" in selection["harnesses"]:
            majors.append(22)
    return max(majors) if majors else None


def _linux_package(package: str) -> tuple[str, ...]:
    # 새 이미지(컨테이너, 클라우드)는 apt 목록이 비어 있어 바로 설치하면 패키지를 못 찾는다.
    # 설치가 실패할 때만 목록을 받고 한 번 더 시도한다.
    script = (
        'pkg="$1"; '
        'if command -v apt-get >/dev/null 2>&1; then sudo apt-get install -y "$pkg" || { sudo apt-get update && sudo apt-get install -y "$pkg"; }; '
        'elif command -v dnf >/dev/null 2>&1; then sudo dnf install -y "$pkg"; '
        'elif command -v yum >/dev/null 2>&1; then sudo yum install -y "$pkg"; '
        'elif command -v pacman >/dev/null 2>&1; then sudo pacman -S --needed --noconfirm "$pkg"; '
        'elif command -v zypper >/dev/null 2>&1; then sudo zypper install -y "$pkg"; '
        'elif command -v apk >/dev/null 2>&1; then sudo apk add "$pkg"; '
        'else echo "no package manager for $pkg" >&2; exit 1; fi'
    )
    return ("sh", "-c", script, "sh", package)


def _git_install_argv(platform: str) -> tuple[str, ...]:
    if platform == "macos":
        return ("brew", "install", "git")
    if platform == "windows":
        return ("winget", "install", "-e", "--id", "Git.Git")
    return _linux_package("git")


def _python_install_argv(platform: str) -> tuple[str, ...]:
    if platform == "macos":
        return ("brew", "install", "python")
    if platform == "windows":
        return ("winget", "install", "-e", "--id", "Python.Python.3.12")
    return _linux_package("python3")


def _node_install_argv(platform: str, major: int) -> tuple[str, ...]:
    if platform == "windows":
        # OpenJS.NodeJS.<major> is the published winget package. A bare major is not a version.
        return ("winget", "install", "-e", "--id", f"OpenJS.NodeJS.{major}")
    # nvm 은 Node 를 사용자 홈에 깐다. apt 판 Node 는 전역 경로가 root 소유라 이어지는
    # `npm install -g`(CodeGraph, Gemini, Copilot)와 update-ai 가 sudo 없이는 실패한다.
    # macOS 의 brew 는 다른 사용자 소유면 쓸 수 없고, node@<major> 는 keg-only 라 PATH 에도 없다.
    # 새 macOS 계정에는 셸 프로필이 하나도 없어 nvm 설치기가 PATH 줄을 쓰지 않는다. 그러면 새 터미널에서
    # node 도, 그 위의 codegraph 도 없다. 로그인 셸이 읽는 파일(Homebrew·Codex 와 같은 선택)을 먼저 만든다.
    # 검사는 nvm 의 셸별 탐지(nvm_detect_profile)와 같게 한다. SHELL 이 없으면 macOS 기본인 zsh 로 본다.
    # ponytail: 만든 빈 프로필은 영수증에 남기지 않는다. 비어 있어 해가 없고, nvm 이 그 안에 줄을 쓴다.
    profile = (
        'z="${ZDOTDIR:-$HOME}"; case "${SHELL:-/bin/zsh}" in '
        '*/zsh) [ -f "$z/.zshrc" ] || [ -f "$z/.zprofile" ] || touch "$z/.zprofile";; '
        '*) [ -f "$HOME/.bashrc" ] || [ -f "$HOME/.bash_profile" ] || touch "$HOME/.bash_profile";; esac; '
        if platform == "macos" else ""
    )
    return (
        "sh",
        "-c",
        'export NVM_DIR="$HOME/.nvm"; '
        f'[ -s "$NVM_DIR/nvm.sh" ] || {{ {profile}curl -fsSL {NVM_INSTALL_URL} | bash; }}; '
        f'. "$NVM_DIR/nvm.sh" && nvm install {major} && nvm alias default {major}',
    )


def _base(selection, world) -> list[Action]:
    actions = []
    if not world["git"]:
        actions.append(
            Action("base", "ensure git", True, _git_install_argv(world["platform"]), ("flag:git",))
        )
    if not world["python_ok"]:
        actions.append(
            Action(
                "base",
                "ensure Python 3.9+",
                True,
                _python_install_argv(world["platform"]),
                ("flag:python_ok",),
            )
        )
    major = _node_major(selection)
    current = world["node_major"]
    # 버전이 충분해도 전역 경로가 root 소유(apt 판 Node)면 이어지는 `npm install -g` 가 실패한다.
    readonly = world["npm_global_readonly"] and world["platform"] != "windows"
    if major is not None and (current is None or current < major or readonly):
        actions.append(
            Action(
                "base",
                f"install Node {major}" + (" for this user (global npm is not writable)" if readonly else ""),
                True,
                _node_install_argv(world["platform"], major),
                (f"node:{major}",),
            )
        )
    return actions


def _harness(selection, world) -> list[Action]:
    actions = []
    for harness in selection["harnesses"]:
        if harness in world["bins"] or harness in world["receipt_harnesses"]:
            continue
        label = HARNESS_LABELS.get(harness, harness)
        actions.append(
            Action(
                "harness",
                f"install harness {label}",
                True,
                ("install-harness", harness),
                (f"add:bins:{harness}", f"add:receipt_harnesses:{harness}"),
            )
        )
    return actions


def _plugin_entries() -> list[dict]:
    path = _repo_root() / "res" / "plugins" / "plugins.json"
    return [entry for entry in json.loads(path.read_text(encoding="utf-8")) if entry.get("default")]


def _plugins(selection, world) -> list[Action]:
    if "claude" not in selection["harnesses"]:
        return []
    actions = []
    known = set(world["marketplaces"])
    for entry in _plugin_entries():
        name, _, market = entry["plugin"].partition("@")
        # 이미 있던 플러그인은 기록하지 않는다. 기록하면 uninstall 이 남의 플러그인을 지운다.
        if name in world["receipt_plugins"] or entry["plugin"] in world["plugin_ids"]:
            continue
        if market not in known:
            known.add(market)
            argv = ("claude", "plugin", "marketplace", "add", entry["marketplace"])
            effects = (f"add:marketplaces:{market}", f"add:receipt_marketplaces:{market}")
            actions.append(Action("plugins", " ".join(argv), True, argv, effects))
        argv = ("claude", "plugin", "install", entry["plugin"], "--scope", "user")
        actions.append(
            Action("plugins", " ".join(argv), True, argv, (f"add:receipt_plugins:{name}",))
        )
    return actions


def _skills(selection, world) -> list[Action]:
    if "claude" not in selection["harnesses"]:
        return []
    missing = [name for name in BUNDLED_SKILLS if name not in world["receipt_skills"]]
    if not missing:
        return []
    return [
        Action(
            "skills",
            "install bundled skills " + " ".join(missing),
            True,
            ("install-skills", *missing),
            tuple(f"add:receipt_skills:{name}" for name in missing),
        )
    ]


def _codegraph(selection, world) -> list[Action]:
    del selection
    owned = "codegraph" in world["receipt_tools"]
    present = "codegraph" in world["bins"]
    if owned and present:
        return [Action("codegraph", "CodeGraph already recorded", False, ())]
    if present and not owned:
        return [Action("codegraph", "CodeGraph present, not claimed", False, ())]
    return [
        Action(
            "codegraph",
            "install CodeGraph package: " + " ".join(CODEGRAPH_ARGV),
            True,
            CODEGRAPH_ARGV,
            ("add:bins:codegraph", "add:receipt_tools:codegraph"),
        )
    ]


def _mcp_detail(harness: str, remove: bool) -> tuple[str, tuple[str, ...]]:
    if harness == "opencode":
        if remove:
            detail = "remove mcp.codegraph from opencode config"
            return detail, ("merge", "mcp.codegraph", "remove")
        detail = "merge mcp.codegraph command codegraph serve --mcp into opencode config"
        return detail, ("merge", "mcp.codegraph", "codegraph", "serve", "--mcp")
    argv = (MCP_REMOVE if remove else MCP_ADD)[harness]
    return " ".join(argv), argv


def _mcp(selection, world) -> list[Action]:
    actions = []
    for harness in selection["harnesses"]:
        # 같은 이름의 서버가 이미 있으면 덮어쓰지도 기록하지도 않는다(AI-29). 기록하면 uninstall 이 남의 등록을 지운다.
        if harness in world["mcp_registered"] or harness in world["mcp_present"]:
            continue
        detail, argv = _mcp_detail(harness, remove=False)
        actions.append(Action("mcp", detail, True, argv, (f"add:mcp_registered:{harness}",)))
    return actions


def _newline(selection, world) -> list[Action]:
    chord = selection["newline"]
    if world["newline_done"] and not (selection["newline_given"] and world["newline_chord"] != chord):
        return []
    return [
        Action(
            "newline",
            f"configure newline chord {chord}",
            True,
            ("newline", chord),
            ("flag:newline_done", f"set:newline_chord:{chord}"),
        )
    ]


def _claude_special(selection, world) -> list[Action]:
    if "claude" not in selection["harnesses"]:
        return []
    actions = []
    if selection["include_output_style"] and not world["output_style"]:
        actions.append(
            Action(
                "claude_special",
                "set output style fluent-korean",
                True,
                ("output-style", "fluent-korean"),
                ("flag:output_style",),
            )
        )
    if selection["include_mods"] and not world["mods"]:
        actions.append(
            Action(
                "claude_special",
                "install mods bash-diff-filter cache-ttl-status korean-reply-rewrite requiring Claude Code >= 2.1.290",
                True,
                ("mods", "bash-diff-filter", "cache-ttl-status", "korean-reply-rewrite"),
                ("flag:mods",),
            )
        )
    statusline = world["statusline"]
    if selection["include_omc"] and "omc-hud" in statusline and not world["omc_hud"]:
        actions.append(
            Action(
                "claude_special",
                f"prepend the cache TTL wrapper to the OMC HUD statusLine: {statusline}",
                True,
                ("omc-hud-wrapper",),
                ("flag:omc_hud",),
            )
        )
    return actions


def _package_action(world, tool: str, present_detail: str, install_detail: str, argv: tuple[str, ...]) -> list[Action]:
    if tool in world["receipt_tools"]:
        return []
    if tool in world["bins"]:
        return [Action("tools", present_detail, False, ())]
    return [
        Action(
            "tools",
            install_detail,
            True,
            argv,
            (f"add:bins:{tool}", f"add:receipt_tools:{tool}"),
        )
    ]


def _tailscale_install(platform: str) -> tuple[str, tuple[str, ...]]:
    if platform == "macos":
        argv = ("brew", "install", "tailscale")
    elif platform == "windows":
        argv = ("winget", "install", "tailscale.tailscale")
    else:
        argv = ("sh", "-c", "curl -fsSL https://tailscale.com/install.sh | sh")
    return "install tailscale package: " + " ".join(argv), argv


def _jarvis_script(config: str) -> str:
    # README 첫 설치. git 는 ~ 를 펼치지 않으므로 $HOME 을 쓴다. 코드는 jarvis 1.8.0 배치(launch.ROOT/app)에 둔다.
    # linger 를 clone 보다 먼저 한다. polkit 이 없거나 SSH 세션이면 본인 linger 도 거절되는데,
    # 그때 체크아웃이 남으면 다음 실행이 "present, not claimed" 로 보고 다시 시도하지 않는다.
    return (
        '{ loginctl enable-linger 2>/dev/null || sudo loginctl enable-linger "$(id -un)"; }'
        " && git clone https://github.com/thsvkd/jarvis.git \"$HOME/.local/share/jarvis/app\""
        f" && install -Dm644 \"$HOME/.local/share/jarvis/app/{config}\" \"$HOME/.config/jarvis/config.toml\""
        " && install -Dm644 \"$HOME/.local/share/jarvis/app/deploy/jarvis.service\""
        " \"$HOME/.config/systemd/user/jarvis.service\""
        " && systemctl --user daemon-reload"
        " && systemctl --user enable --now jarvis"
    )


def _setcap_argv() -> tuple[str, ...]:
    return (
        "sh",
        "-c",
        "sudo install -d /usr/local/libexec/jarvis"
        ' && sudo install -o root -g "$(id -gn)" -m 0750 /usr/bin/ss /usr/local/libexec/jarvis/ss'
        " && sudo setcap cap_sys_ptrace,cap_dac_read_search+ep /usr/local/libexec/jarvis/ss",
    )


def _jarvis(selection, world) -> list[Action]:
    role = selection["jarvis_role"]
    if role not in ("gateway", "core"):
        return []
    if "jarvis" in world["receipt_tools"]:
        return []
    if world["jarvis_checkout"] or "jarvis" in world["bins"]:
        return [Action("tools", "Jarvis present, not claimed", False, ())]
    config = "deploy/gateway.toml" if role == "gateway" else "deploy/s2.toml"
    actions = []
    if world["platform"] == "linux" and not world["psutil_ok"]:
        # 유닛은 /usr/bin/python3 -m jarvis 로 돈다. psutil 이 없으면 시작하자마자 죽는다.
        # 시스템 Python 은 pip 를 막으므로(PEP 668) 배포판 패키지로 깐다.
        actions.append(
            Action("tools", "install python3-psutil for Jarvis", True,
                   _linux_package("python3-psutil"), ("flag:psutil_ok",))
        )
    detail = (
        "clone https://github.com/thsvkd/jarvis.git to ~/.local/share/jarvis/app; "
        f"install {config} to ~/.config/jarvis/config.toml; "
        "install deploy/jarvis.service to ~/.config/systemd/user/jarvis.service and enable"
    )
    actions += [
        Action(
            "tools",
            detail,
            True,
            ("sh", "-c", _jarvis_script(config)),
            ("flag:jarvis_checkout", "flag:jarvis_unit_owned", "add:receipt_tools:jarvis"),
        )
    ]
    if selection["ss_caps"] and not world["ss_caps_done"]:
        actions.append(
            Action(
                "tools",
                "setcap cap_sys_ptrace,cap_dac_read_search+ep on the ss copy",
                True,
                _setcap_argv(),
                ("flag:ss_caps_done",),
            )
        )
    return actions


def _tools(selection, world) -> list[Action]:
    actions = []
    detail, argv = _tailscale_install(world["platform"])
    actions.extend(
        _package_action(world, "tailscale", "Tailscale present, not claimed", detail, argv)
    )
    actions.extend(_jarvis(selection, world))
    if selection["herdr"]:
        actions.extend(
            _package_action(
                world,
                "herdr",
                "herdr present, not claimed",
                "install herdr package: curl -fsSL https://herdr.dev/install.sh | sh",
                ("sh", "-c", "curl -fsSL https://herdr.dev/install.sh | sh"),
            )
        )
    return actions


def _agentsmd(selection, world) -> list[Action]:
    labels = [HARNESS_LABELS.get(harness, harness) for harness in selection["harnesses"]]
    listed = ", ".join(labels)
    if world["agentsmd_written"]:    # 같은 계획이 이미 썼다. 덮어쓰기를 골랐어도 한 번이다.
        return []
    if world["agentsmd_exists"] and not selection["overwrite_agentsmd"]:
        return [Action("agentsmd", f"AGENTS.md exists; leave it for {listed}", False, ())]
    return [
        Action(
            "agentsmd",
            f"write AGENTS.md for {listed} in the current directory",
            True,
            ("agentsmd", *selection["harnesses"]),
            ("flag:agentsmd", "flag:agentsmd_exists", "flag:agentsmd_written"),
        )
    ]


_BUILDERS = {
    "base": _base,
    "harness": _harness,
    "plugins": _plugins,
    "skills": _skills,
    "codegraph": _codegraph,
    "mcp": _mcp,
    "newline": _newline,
    "claude_special": _claude_special,
    "tools": _tools,
    "agentsmd": _agentsmd,
}


def _uninstall(selection, world) -> list[Action]:
    selected = set(selection["stages"])
    actions: list[Action] = []
    # AGENTS.md 는 지우지 않는다. 사용자가 고쳐 쓰는 프로젝트 파일이고 setup_agentsmd 에 제거 모드가 없다.
    # 기록만 지운다. 남기면 다음 설치가 AGENTS.md 단계를 영영 건너뛴다.
    if "agentsmd" in selected and world["agentsmd"]:
        actions.append(Action("agentsmd", "forget the AGENTS.md record (the file is kept)", True, (), ("clear:agentsmd",)))
    if "claude_special" in selected:
        if world["omc_hud"]:
            actions.append(
                Action(
                    "claude_special",
                    "remove OMC HUD cache wrapper recorded by this setup",
                    True,
                    ("omc-hud-wrapper", "--uninstall"),
                    ("clear:omc_hud",),
                )
            )
        if world["mods"]:
            actions.append(
                Action(
                    "claude_special",
                    "remove mods recorded by this setup",
                    True,
                    ("mods", "--uninstall"),
                    ("clear:mods",),
                )
            )
        if world["output_style"]:
            actions.append(
                Action(
                    "claude_special",
                    "remove output style fluent-korean recorded by this setup",
                    True,
                    ("output-style", "--uninstall"),
                    ("clear:output_style",),
                )
            )
    if "newline" in selected and world["newline_done"]:
        actions.append(
            Action(
                "newline",
                "remove newline chord recorded by this setup",
                True,
                ("newline", "--uninstall"),
                ("clear:newline_done",),
            )
        )
    if "mcp" in selected:
        for harness in world["mcp_registered"]:
            detail, argv = _mcp_detail(harness, remove=True)
            actions.append(
                Action("mcp", detail, True, argv, (f"del:mcp_registered:{harness}",))
            )
    if "codegraph" in selected and "codegraph" in world["receipt_tools"]:
        argv = ("python3", NPM_RELEASE, "uninstall", "codegraph")
        actions.append(
            Action(
                "codegraph",
                "remove CodeGraph package recorded by this setup: " + " ".join(argv),
                True,
                argv,
                ("del:bins:codegraph", "del:receipt_tools:codegraph"),
            )
        )
    if "plugins" in selected and "claude" in selection["harnesses"]:
        for plugin in world["receipt_plugins"]:
            argv = ("claude", "plugin", "uninstall", plugin, "--scope", "user")
            actions.append(
                Action("plugins", " ".join(argv), True, argv, (f"del:receipt_plugins:{plugin}",))
            )
        # 마켓플레이스를 지우면 거기서 깐 플러그인도 사라진다. 남이 쓰는 마켓플레이스는 남긴다.
        ours = {
            entry["plugin"] for entry in _plugin_entries()
            if entry["plugin"].partition("@")[0] in world["receipt_plugins"]
        }
        # 위 uninstall 은 user 범위만 지운다. 다른 범위의 설치는 남으므로 그 마켓플레이스도 쓰인다.
        still_used = [pid for pid in world["plugin_ids"] if pid not in ours] + world["plugin_ids_other_scope"]
        in_use = {plugin_id.partition("@")[2] for plugin_id in still_used}
        for market in world["receipt_marketplaces"]:
            if market in in_use:
                continue
            argv = ("claude", "plugin", "marketplace", "remove", market)
            effects = (f"del:marketplaces:{market}", f"del:receipt_marketplaces:{market}")
            actions.append(Action("plugins", " ".join(argv), True, argv, effects))
    if "skills" in selected and world["receipt_skills"]:
        names = " ".join(world["receipt_skills"])
        actions.append(
            Action(
                "skills",
                f"remove bundled skills {names} recorded by this setup",
                True,
                ("install-skills", "--uninstall"),
                tuple(f"del:receipt_skills:{name}" for name in world["receipt_skills"]),
            )
        )
    if "tools" in selected:
        actions.extend(_uninstall_tools(world))
    if "harness" in selected:
        for harness in world["receipt_harnesses"]:
            label = HARNESS_LABELS.get(harness, harness)
            actions.append(
                Action(
                    "harness",
                    f"remove harness {label} recorded by this setup",
                    True,
                    ("install-harness", "--uninstall", harness),
                    (f"del:bins:{harness}", f"del:receipt_harnesses:{harness}"),
                )
            )
    return actions


def _tailscale_uninstall(platform: str) -> tuple[str, ...]:
    if platform == "macos":
        return ("brew", "uninstall", "tailscale")
    if platform == "windows":
        return ("winget", "uninstall", "-e", "--id", "tailscale.tailscale")
    return ("sh", "-c", "sudo apt-get remove -y tailscale")


def _herdr_uninstall(platform: str) -> tuple[str, ...]:
    if platform == "macos":
        return ("brew", "uninstall", "herdr")
    if platform == "windows":
        return (
            "powershell",
            "-NoProfile",
            "-Command",
            "if (Get-Command herdr -ErrorAction SilentlyContinue) { Remove-Item -Force (Get-Command herdr).Source }",
        )
    return ("sh", "-c", 'bin=$(command -v herdr || true); if [ -n "$bin" ]; then rm -f "$bin"; fi')


def _uninstall_tools(world) -> list[Action]:
    actions = []
    if "herdr" in world["receipt_tools"]:
        actions.append(
            Action(
                "tools",
                "remove herdr package recorded by this setup",
                True,
                _herdr_uninstall(world["platform"]),
                ("del:bins:herdr", "del:receipt_tools:herdr"),
            )
        )
    if world["jarvis_unit_owned"]:
        actions.append(
            Action(
                "tools",
                "disable and remove user unit jarvis.service recorded by this setup",
                True,
                ("sh", "-c", "systemctl --user disable --now jarvis"
                 ' && rm -f "$HOME/.config/systemd/user/jarvis.service"'
                 " && systemctl --user daemon-reload"),
                ("clear:jarvis_unit_owned", "del:receipt_tools:jarvis"),
            )
        )
    elif "jarvis" in world["receipt_tools"]:
        actions.append(
            Action(
                "tools",
                "drop jarvis receipt recorded by this setup",
                True,
                (),
                ("del:receipt_tools:jarvis",),
            )
        )
    if "tailscale" in world["receipt_tools"]:
        actions.append(
            Action(
                "tools",
                "remove tailscale package recorded by this setup",
                True,
                _tailscale_uninstall(world["platform"]),
                ("del:bins:tailscale", "del:receipt_tools:tailscale"),
            )
        )
    return actions


def render_plan(selection, actions) -> str:
    """고른 단계 이름을 순서대로 남기고, 그 아래 액션을 번호로 붙인다."""
    chosen = _selection(selection)
    grouped: dict[str, list[Action]] = {}
    for action in actions:
        grouped.setdefault(action.stage, []).append(action)
    lines = []
    number = 1
    for stage in STAGE_ORDER:
        if stage not in chosen["stages"]:
            continue
        lines.append(stage)
        for action in grouped.get(stage, []):
            lines.append(f"{number} {stage}: {action.detail}")
            number += 1
    return "\n".join(lines)


def _home() -> Path:
    raw = os.environ.get("USERPROFILE") if os.name == "nt" else os.environ.get("HOME")
    return Path(raw or Path.home())


def _which_bins(names) -> list[str]:
    return [name for name in names if shutil.which(name)]


# macOS 앱은 CLI 를 앱 번들 안에 두어 PATH 에 없을 수 있다(jarvis config.TAILSCALE_BIN 과 같은 위치).
# 앱이 있는데 brew 판을 또 깔고 영수증에 남기지 않도록 설치된 것으로 본다.
_APP_BINS = {"tailscale": "/Applications/Tailscale.app/Contents/MacOS/Tailscale"}


def _app_bins(names) -> list[str]:
    if sys.platform != "darwin":
        return []
    return [name for name in names if name in _APP_BINS and not shutil.which(name) and os.access(_APP_BINS[name], os.X_OK)]


def _node_major_now() -> int | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+)", (out.stdout or "") + (out.stderr or ""))
    return int(match.group(1)) if match else None


def _npm_global_readonly_now() -> bool:
    """지금 npm 의 전역 node_modules 에 이 사용자가 쓸 수 없으면 True. npm 이 없으면 False."""
    npm = shutil.which("npm")
    if not npm:
        return False
    try:
        # 읽기 전용 점검이다. npm 은 명령마다 ~/.npm/_logs 에 로그를, 업데이트 알림 캐시를 쓰므로 끈다.
        env = dict(os.environ, npm_config_logs_max="0", npm_config_update_notifier="false")
        out = subprocess.run([npm, "prefix", "-g"], capture_output=True, text=True, timeout=30, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return False
    prefix = (out.stdout or "").strip()
    if out.returncode != 0 or not prefix:
        return False
    target = Path(prefix) / ("node_modules" if os.name == "nt" else "lib/node_modules")
    while not target.exists() and target != target.parent:
        target = target.parent
    return not os.access(target, os.W_OK)


def _psutil_ok_now() -> bool:
    """Jarvis 유닛이 쓰는 시스템 Python 에서 psutil 을 import 할 수 있는가."""
    try:
        return subprocess.run(["/usr/bin/python3", "-c", "import psutil"],
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _python_ok_now() -> bool:
    for name in ("python3", "python"):
        binary = shutil.which(name)
        if not binary:
            continue
        try:
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            continue
        match = re.search(r"(\d+)\.(\d+)", (out.stdout or "") + (out.stderr or ""))
        if not match:
            continue
        return (int(match.group(1)), int(match.group(2))) >= (3, 9)
    return False


def _read_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _plugin_ids(installed, wanted) -> list[str]:
    """installed_plugins.json 의 플러그인 id 중 wanted(scope) 를 만족하는 설치가 있는 것."""
    if not isinstance(installed, dict):
        return []
    return [
        plugin_id for plugin_id, installs in installed.items()
        # 예전 형식은 플러그인마다 설치 기록 하나(dict)다.
        if any(isinstance(item, dict) and wanted(item.get("scope", "user"))
               for item in ([installs] if isinstance(installs, dict) else installs or []))
    ]


def _mcp_present_now(home: Path) -> list[str]:
    """codegraph MCP 서버가 이미 등록된 하네스. 설정 파일을 읽기만 한다(AI-29).

    grok·copilot·agy 는 설정 위치가 문서에 없어 보지 않는다(TEST_SPEC 미검증 절).
    """
    found = []
    claude = _read_json(home / ".claude.json") or {}
    if isinstance(claude.get("mcpServers"), dict) and "codegraph" in claude["mcpServers"]:
        found.append("claude")
    try:
        codex = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    except OSError:
        codex = ""
    if re.search(r'^\s*\[mcp_servers\.(codegraph|"codegraph")\]', codex, re.M):
        found.append("codex")
    gemini = _read_json(home / ".gemini" / "settings.json") or {}
    if isinstance(gemini.get("mcpServers"), dict) and "codegraph" in gemini["mcpServers"]:
        found.append("gemini")
    opencode = _read_json(_opencode_config_path()) or {}
    if isinstance(opencode.get("mcp"), dict) and "codegraph" in opencode["mcp"]:
        found.append("opencode")
    return found


def probe_world(project_dir: str | None = None) -> dict:
    """지금 환경의 읽기 전용 스냅샷. 파일을 쓰지 않는다."""
    home = _home()
    receipt = _read_json(home / ".claude" / ".claude-code-setup.json") or {}
    settings = _read_json(home / ".claude" / "settings.json") or {}
    status = settings.get("statusLine")
    command = ""
    if isinstance(status, dict) and isinstance(status.get("command"), str):
        command = status["command"]
    tool_installs = receipt.get("tool_installs") if isinstance(receipt.get("tool_installs"), dict) else {}
    keybinds = receipt.get("keybinds") if isinstance(receipt.get("keybinds"), dict) else {}
    harness_installs = receipt.get("harness_installs") if isinstance(receipt.get("harness_installs"), dict) else {}
    plugins = receipt.get("plugins") if isinstance(receipt.get("plugins"), dict) else {}
    skills = receipt.get("skills") if isinstance(receipt.get("skills"), dict) else {}
    # 번들 스킬 파일의 소유는 스킬 워커의 영수증이 정한다. 일반 --uninstall 이 파일을 지웠으면
    # 단계 영수증에 이름이 남아 있어도 다시 설치해야 한다.
    skill_files = (_read_json(home / ".claude" / ".claude-code-setup-skills.json") or {}).get("files")
    owned_skills = {name.split("/")[0] for name in skill_files} if isinstance(skill_files, dict) else set()
    mcp = receipt.get("mcp") if isinstance(receipt.get("mcp"), dict) else {}
    markets = receipt.get("marketplaces") if isinstance(receipt.get("marketplaces"), dict) else {}
    installed = (_read_json(home / ".claude" / "plugins" / "installed_plugins.json") or {}).get("plugins")
    project = Path(project_dir or os.getcwd())
    names = list(HARNESS_LABELS) + ["codegraph", "tailscale", "herdr", "jarvis"]
    marketplaces = _read_json(home / ".claude" / "plugins" / "known_marketplaces.json") or {}
    return {
        "bins": _which_bins(names) + _app_bins(names),
        "receipt_tools": list(tool_installs),
        "receipt_harnesses": list(harness_installs),
        "receipt_plugins": list(plugins),
        "receipt_skills": [name for name in skills if name in owned_skills],
        "mcp_registered": list(mcp),
        "mcp_present": _mcp_present_now(home),
        "marketplaces": list(marketplaces),
        "receipt_marketplaces": list(markets),
        "plugin_ids": _plugin_ids(installed, lambda scope: scope == "user"),
        "plugin_ids_other_scope": _plugin_ids(installed, lambda scope: scope != "user"),
        "statusline": command,
        "node_major": _node_major_now(),
        "npm_global_readonly": _npm_global_readonly_now(),
        "python_ok": _python_ok_now(),
        "git": shutil.which("git") is not None,
        "platform": _platform_name(),
        # 1.8.0 배치(app) 또는 옛 배치·개발 체크아웃(~/Workspace/jarvis)
        "jarvis_checkout": any((home / p).is_dir() for p in (".local/share/jarvis/app", "Workspace/jarvis")),
        "psutil_ok": _psutil_ok_now() if sys.platform.startswith("linux") else True,
        "jarvis_unit_owned": bool(receipt.get("jarvis_unit")),
        "agentsmd_exists": (project / "AGENTS.md").is_file(),
        "newline_done": bool(receipt.get("keybinds")),
        "newline_chord": keybinds.get("newline_chord") if isinstance(keybinds.get("newline_chord"), str) else "",
        "output_style": bool(receipt.get("output_style")),
        "mods": bool(receipt.get("mods")),
        "omc_hud": bool(receipt.get("omc_hud")),
        "agentsmd": bool(receipt.get("agentsmd")),
    }


_DIRECT = {
    "npm", "git", "python3", "python", "brew", "winget", "sh", "systemctl",
    "claude", "codex", "gemini", "grok", "copilot", "agy", "powershell",
}

_RECEIPT_DICTS = {
    "receipt_tools": "tool_installs",
    "receipt_harnesses": "harness_installs",
    "receipt_plugins": "plugins",
    "receipt_skills": "skills",
    "receipt_marketplaces": "marketplaces",
    "mcp_registered": "mcp",
}

# 워커가 이미 dict 로 남긴 omc_hud / output_style / mods 는 True 로 덮지 않는다.
_RECEIPT_FLAGS = {
    "newline_done": "keybinds",
    "output_style": "output_style",
    "mods": "mods",
    "omc_hud": "omc_hud",
    "agentsmd": "agentsmd",
}
_BANNED_COMMANDS = (
    "graft", "trail", "tailscale up", "tailscale logout",
    "codegraph install", "codegraph upgrade",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _script(name: str) -> list[str]:
    root = _repo_root() / "scripts"
    if os.name == "nt":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / f"{name}.ps1")]
    return ["bash", str(root / f"{name}.sh")]


def _worker(name: str, *args: str) -> list[str]:
    """워커 스크립트 호출. Windows 에서는 --newline 같은 플래그를 -Newline 으로 바꿔 넘긴다.

    PowerShell 은 예전 훅 워커의 --default-mode 를 -DefaultMode 에 묶지 못하고 조용히 버렸다(고른 값이 사라졌다).
    다른 플래그도 셸 버전마다 바인딩이 다를 수 있으므로 모두 PowerShell 고유 이름으로 넘긴다.
    """
    if os.name == "nt":
        args = tuple(
            "-" + "".join(part.capitalize() for part in arg[2:].split("-")) if arg.startswith("--") else arg
            for arg in args
        )
    return _script(name) + list(args)


def _run(argv, cwd=None, stdin=None, env=None) -> int:
    joined = " ".join(argv).lower()
    for banned in _BANNED_COMMANDS:
        if banned in joined:
            print(f"refusing forbidden command: {banned}", file=sys.stderr)
            return 1
    try:
        try:
            return subprocess.run(argv, cwd=cwd, stdin=stdin, env=env).returncode or 0
        except FileNotFoundError:
            # Windows 의 CreateProcess 는 .exe 만 찾는다. npm.cmd 같은 셔임은 PATH 로 찾은 경로로 다시 실행한다.
            found = shutil.which(argv[0])
            if not found or found == argv[0]:
                raise
            return subprocess.run([found, *argv[1:]], cwd=cwd, stdin=stdin, env=env).returncode or 0
    except OSError as exc:
        print(f"could not run {' '.join(argv)}: {exc}", file=sys.stderr)
        return 1


def _platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _catalog_command(harness: str, remove: bool) -> str | None:
    path = _repo_root() / "res" / "catalog.json"
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = next(
        (item for item in catalog if item.get("id") == harness and item.get("kind") == "harness"),
        None,
    )
    if not entry:
        return None
    block = entry.get("uninstall" if remove else "install") or {}
    commands = block.get(_platform_name()) or []
    if not commands or not isinstance(commands[0], dict):
        return None
    cmd = commands[0].get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    return cmd


def _run_harness(argv) -> int:
    remove = "--uninstall" in argv
    harness = argv[-1]
    if harness.startswith("--"):
        print("harness id missing", file=sys.stderr)
        return 1
    cmd = _catalog_command(harness, remove)
    if not cmd:
        kind = "uninstall" if remove else "install"
        print(f"no catalog {kind} command for {harness}", file=sys.stderr)
        return 1
    head, _, rest = cmd.partition(" ")
    if head in ("python3", "python"):
        # npm-release 명령. Windows 의 python 은 실행되지 않는 Store 별칭일 수 있어 지금 인터프리터를 쓴다.
        cmd = (f'& "{sys.executable}" ' if os.name == "nt" else f"'{sys.executable}' ") + rest
    # 공식 설치기는 끝에 "지금 실행할까?"를 묻기도 한다(Codex). stdin 을 닫아 기본값(아니오)으로 끝낸다.
    # Codex 설치기는 /dev/tty 를 직접 열어 묻기 때문에 문서화된 CODEX_NON_INTERACTIVE 로 끈다.
    # 이 프로세스가 앞에 붙인 ~/.local/bin 은 빼고 넘긴다. 보이면 설치기가 "이미 PATH 에 있다"며
    # 셸 프로필에 PATH 를 쓰지 않아, 새 터미널에서 claude·codex 를 찾지 못한다.
    # PATH 를 숨기는 이유는 설치기가 셸 프로필을 쓰게 하려는 것뿐이다. 제거에는 그대로 넘긴다.
    path = os.environ.get("PATH", "") if remove else _path_without_added_user_bin()
    env = dict(os.environ, CODEX_NON_INTERACTIVE="1", PATH=path)
    if os.name == "nt":
        # Remove-Item 실패는 멈추지 않고 마지막 문장의 결과만 종료 코드가 된다. 제거는 첫 실패에서 멈춘다.
        if remove:
            cmd = "$ErrorActionPreference = 'Stop'; " + cmd
        return _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                    cwd=str(_repo_root()), stdin=subprocess.DEVNULL, env=env)
    # npm-release 명령은 저장소 기준 상대 경로(res/tools/...)다.
    return _run(["bash", "-c", cmd], cwd=str(_repo_root()), stdin=subprocess.DEVNULL, env=env)


def _opencode_config_path() -> Path:
    base = _home() / ".config" / "opencode"
    jsonc = base / "opencode.jsonc"
    if jsonc.is_file():
        return jsonc
    return base / "opencode.json"


def _merge_opencode(remove: bool) -> int:
    path = _opencode_config_path()
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"refusing to edit unparsable {path}", file=sys.stderr)
            return 1
        if not isinstance(loaded, dict):
            print(f"refusing to edit non-object {path}", file=sys.stderr)
            return 1
        data = loaded
    mcp = data.get("mcp")
    if mcp is None:
        mcp = {}
        data["mcp"] = mcp
    if not isinstance(mcp, dict):
        print(f"refusing to edit non-object mcp in {path}", file=sys.stderr)
        return 1
    if remove:
        mcp.pop("codegraph", None)
    else:
        mcp["codegraph"] = {"type": "local", "command": ["codegraph", "serve", "--mcp"]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)
        return 1
    return 0


def _receipt_path() -> Path:
    return _home() / ".claude" / ".claude-code-setup.json"


def _effect_touches_receipt(effect: str) -> bool:
    kind, _, rest = effect.partition(":")
    if kind == "flag" and (rest == "jarvis_unit_owned" or rest in _RECEIPT_FLAGS):
        return True
    if kind == "clear" and (rest == "jarvis_unit_owned" or rest in _RECEIPT_FLAGS):
        return True
    if kind in ("add", "del"):
        bucket, _, item = rest.partition(":")
        return bool(item) and bucket in _RECEIPT_DICTS
    return False


def _persist_effects(effects) -> int:
    """성공한 액션의 효과를 영수증에 남긴다. probe_world 가 다음 실행에서 읽는다."""
    if not any(_effect_touches_receipt(effect) for effect in effects):
        return 0
    path = _receipt_path()
    if path.exists():
        data = _read_json(path)
        if data is None:
            print(f"refusing to edit unparsable {path}", file=sys.stderr)
            return 1
    else:
        data = {}
    changed = False
    for effect in effects:
        if not _effect_touches_receipt(effect):
            continue
        kind, _, rest = effect.partition(":")
        if kind == "flag" and rest == "jarvis_unit_owned":
            if data.get("jarvis_unit") is not True:
                data["jarvis_unit"] = True
                changed = True
            continue
        if kind == "clear" and rest == "jarvis_unit_owned":
            if "jarvis_unit" in data:
                data.pop("jarvis_unit", None)
                changed = True
            continue
        if kind == "clear" and rest in _RECEIPT_FLAGS:
            key = _RECEIPT_FLAGS[rest]
            if key in data:
                data.pop(key, None)
                changed = True
            continue
        if kind == "flag" and rest in _RECEIPT_FLAGS:
            key = _RECEIPT_FLAGS[rest]
            if key not in data:
                data[key] = True
                changed = True
            continue
        bucket, _, item = rest.partition(":")
        key = _RECEIPT_DICTS[bucket]
        current = data.get(key)
        if current is None:
            current = {}
            data[key] = current
        elif not isinstance(current, dict):
            print(f"refusing to edit non-object {key} in {path}", file=sys.stderr)
            return 1
        if kind == "add":
            if item not in current:
                current[item] = True
                changed = True
        elif item in current:
            current.pop(item, None)
            changed = True
    if not changed:
        return 0
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)
        return 1
    return 0


def execute(actions, selection, project_dir=None) -> int:
    """dry-run 이 아닐 때만. 패키지 명령은 그대로 실행하고, 성공 뒤에 영수증을 남긴다."""
    chosen = _selection(selection)
    project = project_dir or None
    done = set()
    for action in actions:
        if not action.writes:
            continue
        status = 0
        # 스크립트 워커는 같은 스크립트의 제거 모드로 되돌린다. 설치 인자는 넘기지 않는다.
        # 제거 액션은 argv 둘째 자리에 --uninstall 을 둔다. 사용자 값(모드·키)이 우연히 같아도 뒤집히지 않는다.
        remove = ["--uninstall"] if action.argv[1:2] == ("--uninstall",) else []
        if action.argv:
            head = action.argv[0]
            if head in _DIRECT:
                argv = list(action.argv)
                if head in ("python3", "python"):
                    # Windows 의 python3 는 실행되지 않는 Store 별칭일 수 있다. 지금 인터프리터를 쓴다.
                    argv[0] = sys.executable
                status = _run(argv)
            elif head == "install-harness":
                status = _run_harness(action.argv)
            elif head == "merge":
                status = _merge_opencode(remove="remove" in action.argv)
            elif action.stage in done:
                continue
            elif action.stage == "newline":
                done.add("newline")
                status = _run(_worker("setup_keybinds", *(remove or ["--newline", chosen["newline"]])))
            elif action.stage == "agentsmd":
                done.add("agentsmd")
                args = []
                if chosen["harnesses"]:
                    args += ["--harness", ",".join(chosen["harnesses"])]
                if chosen["overwrite_agentsmd"]:
                    args.append("--force")
                status = _run(_worker("setup_agentsmd", *args), cwd=project)
            elif action.stage == "skills":
                done.add("skills")
                status = _run(_worker("setup_skills", *remove))
            elif head == "output-style":
                status = _run(_worker("setup_output_style", *remove))
            elif head == "mods":
                status = _run(_worker("setup_mods", *remove))
            elif head == "omc-hud-wrapper":
                status = _run(_worker("setup_omc", *remove))
            else:
                print(f"no executor for: {action.detail}", file=sys.stderr)
                status = 1
        if status != 0:
            return status
        _ensure_user_bin_on_path()
        status = _persist_effects(action.effects)
        if status != 0:
            return status
    return 0


def _cli(argv) -> dict:
    opts = {
        "stages": None,
        "harnesses": None,
        "jarvis_role": None,
        "herdr": False,
        "ss_caps": False,
        "dry_run": False,
        "uninstall": False,
        "newline": None,
        "overwrite_agentsmd": False,
        "project_dir": None,
        "include_output_style": None,
        "include_mods": None,
        "include_omc": None,
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--plan":
            i += 1
        elif arg == "--stages" and i + 1 < len(argv):
            opts["stages"] = argv[i + 1]
            i += 2
        elif arg == "--harness" and i + 1 < len(argv):
            opts["harnesses"] = argv[i + 1]
            i += 2
        elif arg == "--jarvis-role" and i + 1 < len(argv):
            opts["jarvis_role"] = argv[i + 1]
            i += 2
        elif arg == "--newline" and i + 1 < len(argv):
            opts["newline"] = argv[i + 1]
            i += 2
        elif arg == "--project-dir" and i + 1 < len(argv):
            opts["project_dir"] = argv[i + 1]
            i += 2
        elif arg == "--herdr":
            opts["herdr"] = True
            i += 1
        elif arg == "--ss-caps":
            opts["ss_caps"] = True
            i += 1
        elif arg == "--dry-run":
            opts["dry_run"] = True
            i += 1
        elif arg == "--uninstall":
            opts["uninstall"] = True
            i += 1
        elif arg == "--overwrite-agentsmd":
            opts["overwrite_agentsmd"] = True
            i += 1
        elif arg == "--include-output-style" and i + 1 < len(argv):
            opts["include_output_style"] = argv[i + 1]
            i += 2
        elif arg == "--include-mods" and i + 1 < len(argv):
            opts["include_mods"] = argv[i + 1]
            i += 2
        elif arg == "--include-omc" and i + 1 < len(argv):
            opts["include_omc"] = argv[i + 1]
            i += 2
        else:
            print(f"Unknown option: {arg}", file=sys.stderr)
            return None
    return opts


def _nvm_node_bin() -> str | None:
    """nvm 이 깐 Node 중 가장 높은 버전의 bin. nvm 이 없으면 None."""
    root = _home() / ".nvm" / "versions" / "node"
    found = []
    for path in root.glob("v*"):
        match = re.match(r"v(\d+)\.(\d+)\.(\d+)$", path.name)
        if match and (path / "bin").is_dir():
            found.append((tuple(int(part) for part in match.groups()), path / "bin"))
    return str(max(found)[1]) if found else None


_STARTUP_PATH = os.environ.get("PATH", "")


def _harness_bin_dirs() -> list[str]:
    """공식 설치기가 바이너리를 두는 사용자 디렉터리. claude·codex·agy 는 ~/.local/bin 이다."""
    home = _home()
    dirs = [
        str(home / ".local" / "bin"),
        os.environ.get("GROK_BIN_DIR") or str(home / ".grok" / "bin"),
        str(home / ".opencode" / "bin"),
    ]
    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        dirs.append(os.environ.get("CODEX_INSTALL_DIR") or str(Path(local) / "Programs" / "OpenAI" / "Codex" / "bin"))
        dirs.append(str(Path(local) / "agy" / "bin"))
        dirs.append(str(Path(local) / "ai-setup" / "bin"))  # npm-release 셔임
    return dirs


def _path_without_added_user_bin() -> str:
    startup = _STARTUP_PATH.split(os.pathsep)
    added = {directory for directory in _harness_bin_dirs() if directory not in startup}
    return os.pathsep.join(part for part in os.environ.get("PATH", "").split(os.pathsep) if part not in added)


def _ensure_user_bin_on_path() -> None:
    """하네스 공식 설치기는 ~/.local/bin 에, nvm 은 ~/.nvm 에 깔고 PATH 는 다음 셸부터 잡힌다.

    같은 실행 안에서 바로 이어지는 `claude plugin install`·`npm install -g` 와 재실행 때의
    probe 가 방금 깐 바이너리를 찾도록 이 프로세스의 PATH 앞에 둔다.
    """
    for directory in (_nvm_node_bin(), *reversed(_harness_bin_dirs())):
        if directory and directory not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")


def main(argv) -> int:
    if "--orca-desktop" in argv:
        print(ORCA_MOVED, file=sys.stderr)
        return 1
    _ensure_user_bin_on_path()
    opts = _cli(argv)
    if opts is None:
        return 2
    if not opts.get("stages"):
        print("--stages is required", file=sys.stderr)
        return 2
    if opts["jarvis_role"] not in (None, "none", "gateway", "core"):
        print("--jarvis-role must be gateway or core", file=sys.stderr)
        return 2
    stages = list(STAGE_ORDER) if opts["stages"] == "all" else [part for part in opts["stages"].split(",") if part]
    if "hooks" in stages:
        print(HOOKS_MOVED, file=sys.stderr)
        return 1
    selection = {
        "stages": stages,
        "jarvis_role": None if opts["jarvis_role"] in (None, "none") else opts["jarvis_role"],
        "herdr": opts["herdr"],
        "ss_caps": opts["ss_caps"],
        "dry_run": opts["dry_run"],
        "uninstall": opts["uninstall"],
        "overwrite_agentsmd": opts["overwrite_agentsmd"],
    }
    if opts["harnesses"] is not None:
        selection["harnesses"] = [part for part in opts["harnesses"].split(",") if part]
    unknown = [name for name in stages if name not in STAGE_ORDER]
    unknown += [name for name in selection.get("harnesses", []) if name != "all" and name not in HARNESS_LABELS]
    if unknown:
        print(f"unknown stage or harness: {', '.join(unknown)}", file=sys.stderr)
        return 1
    if opts["newline"]:
        selection["newline"] = opts["newline"]
    for name in ("include_output_style", "include_mods", "include_omc"):
        raw = opts[name]
        if raw is None:
            continue
        selection[name] = str(raw).strip().lower() not in {"0", "no", "false"}
    actions = plan_actions(selection, probe_world(opts["project_dir"]))
    print(render_plan(selection, actions))
    if opts["dry_run"]:
        return 0
    return execute(actions, selection, opts["project_dir"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
