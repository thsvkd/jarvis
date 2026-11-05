"""선택에서 설치 액션 목록을 만드는 순수 함수 계약.

covers: stage order, CodeGraph before MCP, no graft/Trail command,
default harnesses, skipped stages, OMC HUD statusLine.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "jarvis" / "modules" / "setup"
STAGES_PATH = ROOT / "res" / "stages.py"
spec = importlib.util.spec_from_file_location("ai_setup_stages", STAGES_PATH)
stages = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stages
spec.loader.exec_module(stages)

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
PLUGINS = (
    "oh-my-claudecode",
    "fluent-korean",
    "ponytail",
    "codex",
    "commit-commands",
    "context7",
    "typescript-lsp",
    "pyright-lsp",
)
# 새 머신의 Claude Code 는 이 마켓플레이스들을 모른다. 설치 전에 등록해야 한다.
PLUGIN_SOURCES = {
    "oh-my-claudecode": ("omc", "Yeachan-Heo/oh-my-claudecode"),
    "fluent-korean": ("fluent-korean", "snflkd/fluent-korean"),
    "ponytail": ("ponytail", "DietrichGebert/ponytail"),
    "codex": ("openai-codex", "openai/codex-plugin-cc"),
    "commit-commands": ("claude-plugins-official", "anthropics/claude-plugins-official"),
    "context7": ("claude-plugins-official", "anthropics/claude-plugins-official"),
    "typescript-lsp": ("claude-plugins-official", "anthropics/claude-plugins-official"),
    "pyright-lsp": ("claude-plugins-official", "anthropics/claude-plugins-official"),
}
BANNED = (
    "graft",
    "trail",
    "codegraph install",
    "codegraph upgrade",
    "tailscale up",
    "tailscale logout",
    "setcap",
)


def _full(**overrides):
    selection = {
        "stages": list(STAGE_ORDER),
        "harnesses": ["claude", "codex"],
        "jarvis_role": None,
        "herdr": False,
        "ss_caps": False,
        "dry_run": False,
        "uninstall": False,
        "newline": "shift-enter",
        "overwrite_agentsmd": False,
    }
    selection.update(overrides)
    return selection


def _empty(**overrides):
    world = {
        "bins": [],
        "receipt_tools": [],
        "receipt_harnesses": [],
        "receipt_plugins": [],
        "receipt_skills": [],
        "mcp_registered": [],
        "statusline": "",
        "node_major": None,
        "python_ok": False,
        "git": False,
        "platform": "linux",
        "jarvis_checkout": False,
        "jarvis_unit_owned": False,
        "agentsmd_exists": False,
        "newline_done": False,
        "output_style": False,
        "mods": False,
        "omc_hud": False,
        "agentsmd": False,
    }
    world.update(overrides)
    return world


def _blob(actions):
    rendered = stages.render(actions)
    argv = "\n".join(" ".join(action.argv) for action in actions)
    return rendered + "\n" + argv


def _stage_sequence(actions):
    seen = []
    for action in actions:
        if not seen or seen[-1] != action.stage:
            seen.append(action.stage)
    return seen


def test_module_exports_stage_order():
    assert tuple(stages.STAGE_ORDER) == STAGE_ORDER


def test_full_selection_orders_codegraph_package_before_mcp():
    actions = stages.plan_actions(_full(), _empty())
    sequence = _stage_sequence(actions)
    assert sequence == [stage for stage in STAGE_ORDER if stage in sequence]
    codegraph_at = next(i for i, action in enumerate(actions) if action.stage == "codegraph" and action.writes)
    mcp_at = next(i for i, action in enumerate(actions) if action.stage == "mcp")
    assert codegraph_at < mcp_at
    blob = _blob(actions)
    assert "npm_release.py install codegraph" in blob
    assert "CodeGraph" in stages.render(actions)
    assert "codegraph serve --mcp" in blob
    assert "npm install -g" not in blob
    lowered = blob.lower()
    for banned in BANNED:
        assert banned not in lowered
    assert stages.render(actions).splitlines()[0].startswith("1 ")


def test_default_harnesses_are_claude_code_and_codex_only():
    actions = stages.plan_actions(_full(), _empty())
    installs = [action.detail for action in actions if action.stage == "harness" and action.writes]
    blob = "\n".join(installs)
    assert "Claude Code" in blob
    assert "Codex" in blob
    for label in ("Grok", "Antigravity", "OpenCode", "Gemini", "Copilot"):
        assert label not in blob
    assert [action.stage for action in actions if action.stage == "harness"]


def test_skipped_stage_is_absent():
    selection = _full(stages=[stage for stage in STAGE_ORDER if stage != "plugins"])
    actions = stages.plan_actions(selection, _empty())
    assert all(action.stage != "plugins" for action in actions)
    assert any(action.stage == "skills" for action in actions)


def test_generic_skills_are_bundled_names_without_orca_skills():
    actions = stages.plan_actions(_full(), _empty())
    skills = [action for action in actions if action.stage == "skills"]
    assert len(skills) == 1
    detail = skills[0].detail.lower()
    assert "design-lab" in detail
    assert "tailnet-report" in detail
    assert "orca" not in detail


def test_skills_stage_writes_nothing_without_claude():
    actions = stages.plan_actions(_full(harnesses=["codex"]), _empty())
    assert [action for action in actions if action.stage == "skills"] == []


def test_plugins_are_claude_scoped_and_never_vendor_plugin_installs():
    actions = stages.plan_actions(_full(harnesses=["claude", "codex", "grok"]), _empty())
    blob = _blob(actions)
    for plugin in PLUGINS:
        market, repo = PLUGIN_SOURCES[plugin]
        install = f"claude plugin install {plugin}@{market} --scope user"
        add = f"claude plugin marketplace add {repo}"
        assert install in blob
        assert blob.index(add) < blob.index(install)
    # 같은 마켓플레이스는 한 번만 등록하고, 이미 아는 마켓플레이스는 다시 등록하지 않는다.
    adds = [a for a in actions if a.argv[:4] == ("claude", "plugin", "marketplace", "add")]
    assert [a.argv[4] for a in adds].count("anthropics/claude-plugins-official") == 1
    known = _blob(stages.plan_actions(_full(), _empty(marketplaces=["omc"])))
    assert "marketplace add Yeachan-Heo/oh-my-claudecode" not in known
    assert "claude plugin install oh-my-claudecode@omc --scope user" in known
    assert "codex plugin" not in blob
    assert "grok plugin" not in blob
    codex_only = stages.plan_actions(_full(harnesses=["codex"]), _empty())
    assert "claude plugin install" not in _blob(codex_only)
    assert "codex plugin" not in _blob(codex_only)


def test_omc_hud_statusline_is_wrapped_without_replacement():
    command = 'sh "$HOME/.claude/hud/omc-hud-ttl.mjs" omc-hud.mjs'
    world = _empty(statusline=command)
    actions = stages.plan_actions(_full(), world)
    wrappers = [action for action in actions if action.stage == "claude_special" and "omc-hud" in action.detail]
    assert len(wrappers) == 1
    assert command in wrappers[0].detail
    assert stages.statusline_after(world, actions) == command
    custom = "echo custom-status"
    custom_world = _empty(statusline=custom)
    custom_actions = stages.plan_actions(_full(), custom_world)
    assert not any("omc-hud" in action.detail for action in custom_actions)
    assert stages.statusline_after(custom_world, custom_actions) == custom


def test_existing_codegraph_binary_is_not_claimed():
    actions = stages.plan_actions(
        _full(stages=["codegraph"]),
        _empty(bins=["codegraph"], git=True, python_ok=True, node_major=20),
    )
    codegraph = [action for action in actions if action.stage == "codegraph"]
    assert len(codegraph) == 1
    assert codegraph[0].writes is False
    assert "not claimed" in codegraph[0].detail.lower()
    assert "npm install" not in codegraph[0].detail


def test_second_plan_against_post_state_does_not_write():
    world = _empty()
    selection = _full(jarvis_role="gateway")
    first = stages.plan_actions(selection, world)
    assert any(action.writes for action in first)
    second = stages.plan_actions(selection, stages.world_after(world, first))
    assert not any(action.writes for action in second)


def test_agentsmd_is_planned_for_a_new_project_after_another_was_set_up():
    """영수증의 agentsmd 기록은 머신 전체다. 그 기록이 있어도 이 프로젝트에는 AGENTS.md 가 아직 없다."""
    actions = stages.plan_actions(_full(stages=["agentsmd"]), _empty(agentsmd=True))
    assert [action.writes for action in actions] == [True]


def test_overwriting_agentsmd_is_planned_once():
    """covers: AI-43 (덮어쓰기를 고른 경우)"""
    selection = _full(stages=["agentsmd"], overwrite_agentsmd=True)
    first = stages.plan_actions(selection, _empty(agentsmd_exists=True))
    assert [action.writes for action in first] == [True]
    second = stages.plan_actions(selection, stages.world_after(_empty(agentsmd_exists=True), first))
    assert not any(action.writes for action in second)


def test_explicit_newline_change_is_planned_but_the_default_keeps_the_recorded_value():
    world = _empty(newline_done=True, newline_chord="ctrl-enter")
    picked = ["newline"]
    defaults = stages.plan_actions(_full(stages=picked, newline=None), world)
    assert not any(action.writes for action in defaults)
    same = stages.plan_actions(_full(stages=picked, newline="ctrl-enter"), world)
    assert not any(action.writes for action in same)
    changed = stages.plan_actions(_full(stages=picked, newline="shift-enter"), world)
    assert [action.stage for action in changed if action.writes] == ["newline"]
    again = stages.plan_actions(_full(stages=picked, newline="shift-enter"), stages.world_after(world, changed))
    assert not any(action.writes for action in again)


def test_probe_finds_jarvis_in_the_app_folder_or_the_old_checkout(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    assert stages.probe_world(str(tmp_path))["jarvis_checkout"] is False
    for rel in (".local/share/jarvis/app", "Workspace/jarvis"):
        (home / rel).mkdir(parents=True)
        assert stages.probe_world(str(tmp_path))["jarvis_checkout"] is True
        shutil.rmtree(home / rel)


def test_probe_reads_the_recorded_newline_chord(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    (home / ".claude").mkdir()
    (home / ".claude" / ".claude-code-setup.json").write_text(json.dumps({
        "keybinds": {"newline_chord": "ctrl-enter"},
    }), encoding="utf-8")
    world = stages.probe_world(str(tmp_path))
    assert world["newline_done"] is True
    assert world["newline_chord"] == "ctrl-enter"


def test_jarvis_herdr_and_setcap_follow_explicit_choice():
    # jarvis 로 옮긴 뒤에는 CodeGraph 액션의 npm_release.py 경로에 "jarvis" 가 들어간다. 그 경로만 빼고 본다.
    plain = stages.render(stages.plan_actions(_full(), _empty())).replace(stages.NPM_RELEASE, "").lower()
    assert "jarvis" not in plain
    assert "herdr" not in plain
    assert "setcap" not in plain
    gateway = stages.plan_actions(_full(jarvis_role="gateway"), _empty())
    gateway_blob = stages.render(gateway)
    assert "https://github.com/thsvkd/jarvis.git" in gateway_blob
    assert "~/.local/share/jarvis/app" in gateway_blob
    assert "deploy/gateway.toml" in gateway_blob
    assert "jarvis.service" in gateway_blob
    assert "setcap" not in gateway_blob.lower()
    core = stages.render(stages.plan_actions(_full(jarvis_role="core", ss_caps=True), _empty()))
    assert "deploy/s2.toml" in core
    assert "gateway.toml" not in core
    assert "setcap" in core.lower()
    herdr = stages.render(stages.plan_actions(_full(herdr=True), _empty())).lower()
    assert "herdr" in herdr


def test_uninstall_removes_only_recorded_units_and_packages():
    owned = _empty(
        receipt_tools=["tailscale", "jarvis", "codegraph"],
        jarvis_unit_owned=True,
        jarvis_checkout=True,
        bins=["tailscale", "codegraph"],
        mcp_registered=["claude"],
        receipt_harnesses=["claude"],
        agentsmd=True,
    )
    actions = stages.plan_actions(_full(uninstall=True), owned)
    blob = _blob(actions).replace(stages.NPM_RELEASE, "").lower()  # 옮긴 경로의 "jarvis" 는 빼고 본다
    for banned in ("graft", "trail", "tailscale logout", "tailscale up", "codegraph install", "codegraph upgrade", "setcap"):
        assert banned not in blob
    assert "workspace/jarvis" not in blob
    assert ".local/state/jarvis" not in blob
    assert any("jarvis.service" in action.detail for action in actions)
    unowned = dict(owned)
    unowned["jarvis_unit_owned"] = False
    quiet = stages.plan_actions(_full(uninstall=True), unowned)
    assert not any("jarvis.service" in action.detail for action in quiet)
    assert "logout" not in _blob(quiet).lower()


def test_node_is_installed_only_at_the_max_required_major():
    claude_only = stages.plan_actions(_full(stages=["base", "harness"], harnesses=["claude"]), _empty())
    assert not any("node" in action.detail.lower() for action in claude_only)
    codegraph = stages.render(stages.plan_actions(_full(stages=["base", "codegraph"], harnesses=[]), _empty()))
    assert "Node 20" in codegraph
    copilot = stages.render(
        stages.plan_actions(_full(stages=["base", "harness", "codegraph"], harnesses=["copilot", "gemini"]), _empty())
    )
    assert "Node 22" in copilot
    assert "Node 20" not in copilot
    enough = stages.plan_actions(
        _full(stages=["base", "codegraph"], harnesses=[]),
        _empty(node_major=20, git=True, python_ok=True),
    )
    assert not any("Node" in action.detail for action in enough)


def test_tailscale_install_is_package_only():
    expected = {
        "linux": "https://tailscale.com/install.sh",
        "macos": "brew install tailscale",
        "windows": "winget install tailscale.tailscale",
    }
    for platform, snippet in expected.items():
        actions = stages.plan_actions(_full(stages=["tools"], harnesses=[]), _empty(platform=platform))
        blob = _blob(actions)
        assert snippet in blob
        assert "tailscale up" not in blob.lower()
        assert "tailscale logout" not in blob.lower()
    present = stages.plan_actions(
        _full(stages=["tools"], harnesses=[]),
        _empty(bins=["tailscale"]),
    )
    tailscale = [action for action in present if "tailscale" in action.detail.lower()]
    assert len(tailscale) == 1
    assert tailscale[0].writes is False
    assert "not claimed" in tailscale[0].detail.lower()


def test_existing_harness_is_not_reinstalled_or_claimed():
    actions = stages.plan_actions(_full(stages=["harness"]), _empty(bins=["claude"]))
    writes = [action for action in actions if action.stage == "harness" and action.writes]
    assert len(writes) == 1
    assert "Codex" in writes[0].detail
    assert "Claude Code" not in writes[0].detail
    after = stages.world_after(_empty(bins=["claude"]), actions)
    assert "claude" not in after["receipt_harnesses"]
    assert "codex" in after["receipt_harnesses"]


def test_base_prerequisites_and_agentsmd_without_hooks():
    actions = stages.plan_actions(_full(), _empty())
    base = [action.detail for action in actions if action.stage == "base"]
    base_blob = "\n".join(base)
    assert "git" in base_blob.lower()
    assert "GitHub" not in base_blob
    assert "Python 3.9+" in base_blob
    assert not [action for action in actions if action.stage == "hooks"]    # 훅은 jarvis 가 맡는다
    agents = [action for action in actions if action.stage == "agentsmd"]
    assert len(agents) == 1
    assert "Claude Code" in agents[0].detail
    assert "Codex" in agents[0].detail
    newline = [action for action in actions if action.stage == "newline"]
    assert len(newline) == 1
    assert "shift-enter" in newline[0].detail


def test_claude_special_names_output_style_and_mods_only_for_claude():
    actions = stages.plan_actions(_full(), _empty())
    special = [action.detail for action in actions if action.stage == "claude_special"]
    blob = "\n".join(special)
    assert "fluent-korean" in blob
    for name in ("bash-diff-filter", "cache-ttl-status", "korean-reply-rewrite"):
        assert name in blob
    assert "2.1.290" in blob
    without = stages.plan_actions(_full(harnesses=["codex"]), _empty())
    assert [action for action in without if action.stage == "claude_special"] == []


MCP_ARGV = {
    "claude": ("claude", "mcp", "add", "-s", "user", "codegraph", "--", "codegraph", "serve", "--mcp"),
    "codex": ("codex", "mcp", "add", "codegraph", "--", "codegraph", "serve", "--mcp"),
    "gemini": ("gemini", "mcp", "add", "-s", "user", "codegraph", "codegraph", "serve", "--mcp"),
    "grok": ("grok", "mcp", "add", "codegraph", "--", "codegraph", "serve", "--mcp"),
    "copilot": ("copilot", "mcp", "add", "codegraph", "--", "codegraph", "serve", "--mcp"),
    "agy": ("agy", "mcp", "add", "codegraph", "codegraph", "serve", "--mcp"),
}


def test_mcp_registration_uses_codegraph_serve_for_each_selected_harness():
    actions = stages.plan_actions(
        _full(harnesses=["claude", "codex", "opencode", "gemini", "grok", "copilot", "agy"]),
        _empty(),
    )
    mcp = [action for action in actions if action.stage == "mcp" and action.argv[0] != "merge"]
    by_head = {action.argv[0]: action.argv for action in mcp}
    for harness, argv in MCP_ARGV.items():
        assert by_head[harness] == argv
    opencode = [action for action in actions if action.stage == "mcp" and action.argv[:2] == ("merge", "mcp.codegraph")]
    assert opencode
    assert opencode[0].argv == ("merge", "mcp.codegraph", "codegraph", "serve", "--mcp")
    assert all("graft" not in action.detail.lower() for action in actions if action.stage == "mcp")


def test_dry_run_renders_the_same_plan_as_apply():
    world = _empty()
    dry = stages.plan_actions(_full(dry_run=True), world)
    live = stages.plan_actions(_full(), world)
    assert stages.render(dry) == stages.render(live)
    assert [(action.stage, action.writes, action.detail, action.argv) for action in dry] == [
        (action.stage, action.writes, action.detail, action.argv) for action in live
    ]


def test_agentsmd_existing_file_is_left_unless_overwrite_is_set():
    world = _empty(agentsmd_exists=True, git=True, python_ok=True, node_major=22)
    left = stages.plan_actions(_full(stages=["agentsmd"]), world)
    assert len(left) == 1
    assert left[0].writes is False
    replaced = stages.plan_actions(_full(stages=["agentsmd"], overwrite_agentsmd=True), world)
    assert replaced[0].writes is True


@pytest.mark.parametrize(
    "platform",
    ["linux", "macos", "windows"],
)
def test_codegraph_package_command_is_npm_release_on_every_platform(platform):
    actions = stages.plan_actions(_full(stages=["codegraph"], harnesses=[]), _empty(platform=platform))
    assert any(
        action.argv[1:] == (stages.NPM_RELEASE, "install", "codegraph") for action in actions if action.writes
    )


def test_full_apply_invokes_harness_plugins_and_codegraph_mcp(monkeypatch, tmp_path):
    """비 dry-run 전체 선택은 하네스 설치, Claude 플러그인, CodeGraph MCP 를 실제로 호출한다."""
    calls = []

    class _Done:
        returncode = 0

    def fake_run(argv, *args, **kwargs):
        calls.append(tuple(argv))
        return _Done()

    monkeypatch.setattr(stages.subprocess, "run", fake_run)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Windows 의 하네스 bin(Codex·agy·ai-setup 셔임)은 LOCALAPPDATA 아래다. 실제 사용자 설치가 보이면 안 된다.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    harnesses = "claude,codex,grok,opencode,gemini,agy,copilot"
    status = _run_as_platform("linux", lambda: stages.main(
        ["--plan", "--stages", "all", "--harness", harnesses, "--project-dir", str(tmp_path)]
    ))
    assert status == 0
    blob = "\n".join(" ".join(argv) for argv in calls)
    assert "https://claude.ai/install.sh" in blob
    assert "https://chatgpt.com/codex/install.sh" in blob
    assert "claude plugin install oh-my-claudecode@omc --scope user" in blob
    assert "npm_release.py install codegraph" in blob
    assert "claude mcp add -s user codegraph -- codegraph serve --mcp" in blob
    assert "codex mcp add codegraph -- codegraph serve --mcp" in blob
    assert "gemini mcp add -s user codegraph codegraph serve --mcp" in blob
    assert "agy mcp add codegraph codegraph serve --mcp" in blob
    assert "copilot mcp add codegraph -- codegraph serve --mcp" in blob
    for argv in MCP_ARGV.values():
        assert argv in calls
    for banned in ("graft", "trail", "codegraph install", "codegraph upgrade"):
        assert banned not in blob.lower()
    install_at = blob.index("https://claude.ai/install.sh")
    plugin_at = blob.index("claude plugin install oh-my-claudecode")
    package_at = blob.index("npm_release.py install codegraph")
    mcp_at = blob.index("claude mcp add -s user codegraph")
    assert install_at < plugin_at < package_at < mcp_at
    config = json.loads((tmp_path / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert config["mcp"]["codegraph"]["command"] == ["codegraph", "serve", "--mcp"]
    agents = [argv for argv in calls if "setup_agentsmd." in " ".join(argv)]    # Windows 는 .ps1
    assert agents
    forwarded = " ".join(agents[-1])
    assert "agy" in forwarded and "gemini" in forwarded and "copilot" in forwarded


def test_unknown_executor_fails_closed(monkeypatch):
    def fail_run(*args, **kwargs):
        raise AssertionError("an unknown action must not be launched")

    monkeypatch.setattr(stages.subprocess, "run", fail_run)
    missing = stages.Action("tools", "mystery", True, ("not-a-command",), ())
    assert stages.execute([missing], _full()) == 1
    unknown = stages.Action("harness", "install harness Cursor", True, ("install-harness", "cursor"), ())
    assert stages.execute([unknown], _full()) == 1


def _host_platform() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _record_runs(monkeypatch, passthrough=None):
    recorded = []
    real_run = stages.subprocess.run

    def runner(argv, *args, **kwargs):
        if passthrough is not None and passthrough(argv):
            return real_run(argv, *args, **kwargs)
        recorded.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(stages.subprocess, "run", runner)
    return recorded


@pytest.mark.parametrize("argv,name", [
    (["--stages", "hook"], "hook"),
    (["--stages", "mcp", "--harness", "claude-code"], "claude-code"),
])
def test_unknown_stage_or_harness_exits_1_before_planning(argv, name, capsys, monkeypatch):
    monkeypatch.setattr(stages, "probe_world", lambda *a, **k: pytest.fail("planned with an unknown name"))
    assert stages.main([*argv, "--dry-run"]) == 1
    assert name in capsys.readouterr().err


def test_run_retries_a_shim_through_its_resolved_path(monkeypatch):
    """Windows 의 CreateProcess 는 .exe 만 찾아 npm.cmd 를 이름으로 실행하지 못한다(실측)."""
    calls = []

    def runner(argv, *args, **kwargs):
        calls.append(argv[0])
        if argv[0] == "npm":
            raise FileNotFoundError(2, "not found")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(stages.subprocess, "run", runner)
    monkeypatch.setattr(stages.shutil, "which", lambda name: r"C:\nodejs\npm.CMD" if name == "npm" else None)
    assert stages._run(["npm", "install", "-g", "x"]) == 0
    assert calls == ["npm", r"C:\nodejs\npm.CMD"]


def _home(monkeypatch, tmp_path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _path_hiding(names) -> str:
    blocked = set()
    for name in names:
        found = shutil.which(name)
        if found:
            blocked.add(Path(found).resolve().parent)
    kept = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).resolve() in blocked:
                continue
        except OSError:
            pass
        kept.append(entry)
    return os.pathsep.join(kept)


TAILSCALE_UNINSTALL = {
    "linux": ("sh", "-c", "sudo apt-get remove -y tailscale"),
    "macos": ("brew", "uninstall", "tailscale"),
    "windows": ("winget", "uninstall", "-e", "--id", "tailscale.tailscale"),
}

HERDR_UNINSTALL = {
    "linux": ("sh", "-c", 'bin=$(command -v herdr || true); if [ -n "$bin" ]; then rm -f "$bin"; fi'),
    "macos": ("brew", "uninstall", "herdr"),
    "windows": (
        "powershell",
        "-NoProfile",
        "-Command",
        "if (Get-Command herdr -ErrorAction SilentlyContinue) { Remove-Item -Force (Get-Command herdr).Source }",
    ),
}

JARVIS_GATEWAY = (
    '{ loginctl enable-linger 2>/dev/null || sudo loginctl enable-linger "$(id -un)"; }'
    ' && git clone https://github.com/thsvkd/jarvis.git "$HOME/.local/share/jarvis/app"'
    ' && install -Dm644 "$HOME/.local/share/jarvis/app/deploy/gateway.toml" "$HOME/.config/jarvis/config.toml"'
    ' && install -Dm644 "$HOME/.local/share/jarvis/app/deploy/jarvis.service"'
    ' "$HOME/.config/systemd/user/jarvis.service"'
    " && systemctl --user daemon-reload"
    " && systemctl --user enable --now jarvis"
)

JARVIS_CORE = JARVIS_GATEWAY.replace("deploy/gateway.toml", "deploy/s2.toml")

_LINUX_PKG = (
    'pkg="$1"; '
    'if command -v apt-get >/dev/null 2>&1; then sudo apt-get install -y "$pkg" || { sudo apt-get update && sudo apt-get install -y "$pkg"; }; '
    'elif command -v dnf >/dev/null 2>&1; then sudo dnf install -y "$pkg"; '
    'elif command -v yum >/dev/null 2>&1; then sudo yum install -y "$pkg"; '
    'elif command -v pacman >/dev/null 2>&1; then sudo pacman -S --needed --noconfirm "$pkg"; '
    'elif command -v zypper >/dev/null 2>&1; then sudo zypper install -y "$pkg"; '
    'elif command -v apk >/dev/null 2>&1; then sudo apk add "$pkg"; '
    'else echo "no package manager for $pkg" >&2; exit 1; fi'
)

GIT_INSTALL = {
    "linux": ("sh", "-c", _LINUX_PKG, "sh", "git"),
    "macos": ("brew", "install", "git"),
    "windows": ("winget", "install", "-e", "--id", "Git.Git"),
}
PYTHON_INSTALL = {
    "linux": ("sh", "-c", _LINUX_PKG, "sh", "python3"),
    "macos": ("brew", "install", "python"),
    "windows": ("winget", "install", "-e", "--id", "Python.Python.3.12"),
}


def test_base_install_commands_match_platform():
    for platform in ("linux", "macos", "windows"):
        actions = stages.plan_actions(_full(stages=["base"], harnesses=[]), _empty(platform=platform))
        argv = [action.argv for action in actions]
        assert GIT_INSTALL[platform] in argv
        assert PYTHON_INSTALL[platform] in argv
        assert ("git", "--version") not in argv
        assert ("python3", "--version") not in argv
        # 공개 레포라 GitHub CLI 도 로그인도 요구하지 않는다.
        assert not any("gh" in action_argv for action_argv in argv)
    ready = stages.plan_actions(
        _full(stages=["base"], harnesses=[]),
        _empty(git=True, python_ok=True),
    )
    assert [action for action in ready if action.stage == "base"] == []


def test_base_install_commands_run_through_execute(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    python3 = bindir / "python3"
    python3.write_text("#!/bin/sh\nprintf '%s\\n' 'Python 3.8.10' >&2\n", encoding="utf-8")
    python3.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    recorded = _record_runs(
        monkeypatch,
        passthrough=lambda argv: bool(argv) and Path(str(argv[0])).name == "python3" and "--version" in argv,
    )
    project = tmp_path / "proj"
    project.mkdir()
    status = stages.main(["--plan", "--stages", "base", "--harness", "claude", "--project-dir", str(project)])
    assert status == 0
    host = _host_platform()
    assert GIT_INSTALL[host] in recorded
    assert PYTHON_INSTALL[host] in recorded
    assert ("git", "--version") not in recorded
    assert not any("gh" in argv for argv in recorded)


def _exe(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    # win32 shutil.which only matches PATHEXT names such as name.exe.
    linked = directory / f"{name}.exe"
    if not linked.exists():
        linked.symlink_to(path.name)


def _node_stubs(directory: Path) -> None:
    """PATH 스텁. curl 이 받은 nvm 설치기 본문이 bash 의 stdin 으로 가는지 본다."""
    _exe(directory, "node", "printf '%s\\n' 'v18.19.0'\n")
    _exe(directory, "codegraph", "exit 0\n")
    _exe(directory, "copilot", "exit 0\n")
    _exe(directory, "git", "exit 0\n")
    _exe(directory, "python3", "printf '%s\\n' 'Python 3.11.0'\n")
    _exe(directory, "python", "printf '%s\\n' 'Python 3.11.0'\n")
    _exe(
        directory,
        "curl",
        "url=\n"
        'for arg in "$@"; do url=$arg; done\n'
        'printf \'%s\\n\' "$url" > "$AI_SETUP_EVIDENCE/curl.url"\n'
        'printf \'nvm-installer %s\\n\' "$url"\n',
    )
    # nvm 공식 설치기 자리. 받은 본문을 남기고, nvm 명령을 기록하는 nvm.sh 를 만든다.
    _exe(
        directory,
        "bash",
        'cat > "$AI_SETUP_EVIDENCE/bash.stdin"\n'
        'mkdir -p "$HOME/.nvm"\n'
        "printf '%s\\n' 'nvm() { printf \"%s\\n\" \"$*\" >> \"$AI_SETUP_EVIDENCE/nvm.log\"; }'"
        ' > "$HOME/.nvm/nvm.sh"\n',
    )
    _exe(
        directory,
        "apt-get",
        'printf \'%s\\n\' "$*" >> "$AI_SETUP_EVIDENCE/apt-get.log"\nexit 0\n',
    )
    _exe(
        directory,
        "winget",
        'printf \'%s\\n\' "$*" >> "$AI_SETUP_EVIDENCE/winget.log"\nexit 0\n',
    )
    _exe(
        directory,
        "brew",
        'printf \'%s\\n\' "$*" >> "$AI_SETUP_EVIDENCE/brew.log"\nexit 0\n',
    )


def test_root_owned_global_npm_gets_a_user_node_even_when_the_version_is_enough():
    """apt 판 Node 20 이 있어도 전역 npm 에 못 쓰면 CodeGraph 설치가 EACCES 로 실패한다. 사용자 Node 를 더한다."""
    world = _empty(git=True, python_ok=True, node_major=20, npm_global_readonly=True)
    base = [a for a in stages.plan_actions(_full(stages=["base", "codegraph"]), world) if a.stage == "base"]
    assert [a.detail for a in base] == ["install Node 20 for this user (global npm is not writable)"]
    assert stages.NVM_INSTALL_URL in " ".join(base[0].argv)
    writable = dict(world, npm_global_readonly=False)
    assert [a for a in stages.plan_actions(_full(stages=["base", "codegraph"]), writable) if a.stage == "base"] == []


def test_jarvis_installs_psutil_first_when_the_system_python_lacks_it():
    """Jarvis 유닛은 /usr/bin/python3 로 돈다. psutil 이 없으면 유닛이 시작하자마자 죽는다."""
    selection = _full(stages=["tools"], jarvis_role="gateway")
    tools = [a for a in stages.plan_actions(selection, _empty()) if a.stage == "tools"]
    psutil = [a.detail for a in tools].index("install python3-psutil for Jarvis")
    clone = next(i for i, a in enumerate(tools) if a.detail.startswith("clone https://github.com/thsvkd/jarvis.git"))
    assert psutil < clone
    ready = [a.detail for a in stages.plan_actions(selection, _empty(psutil_ok=True))]
    assert "install python3-psutil for Jarvis" not in ready


def test_user_bin_goes_on_path_so_fresh_harness_installs_are_found(monkeypatch, tmp_path):
    """Claude Code 설치기는 ~/.local/bin 에 깔지만 PATH 는 다음 셸부터다. 같은 실행의 다음 명령이 찾아야 한다."""
    home = _home(monkeypatch, tmp_path)
    monkeypatch.delenv("GROK_BIN_DIR", raising=False)
    monkeypatch.delenv("CODEX_INSTALL_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("PATH", "/usr/bin")
    stages._ensure_user_bin_on_path()
    stages._ensure_user_bin_on_path()
    expected = [home / ".local" / "bin", home / ".grok" / "bin", home / ".opencode" / "bin"]
    if os.name == "nt":
        local = home / "AppData" / "Local"
        expected += [local / "Programs" / "OpenAI" / "Codex" / "bin", local / "agy" / "bin", local / "ai-setup" / "bin"]
    assert os.environ["PATH"] == os.pathsep.join([*map(str, expected), "/usr/bin"])


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh not found")
def test_apt_install_refreshes_package_lists_when_the_package_is_missing(tmp_path):
    """새 Debian 은 apt 목록이 비어 있다. 첫 설치가 실패하면 update 뒤 한 번 더 설치한다."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "apt.log"
    _exe(bindir, "sudo", 'exec "$@"\n')
    _exe(
        bindir,
        "apt-get",
        f'printf \'%s\\n\' "$*" >> "{log}"\n'
        f'if [ "$1" = update ]; then touch "{tmp_path}/lists"; exit 0; fi\n'
        f'[ -e "{tmp_path}/lists" ] || {{ echo "E: Unable to locate package $3" >&2; exit 100; }}\n',
    )
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"])
    proc = subprocess.run(list(stages._linux_package("git")), env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert log.read_text(encoding="utf-8").splitlines() == ["install -y git", "update", "install -y git"]


def _run_as_platform(platform: str, fn):
    # sys.platform=win32 breaks shutil.which on Linux because _winapi is absent.
    # The shipped planner still picks the install argv from _platform_name.
    original = stages._platform_name
    stages._platform_name = lambda: platform
    try:
        return fn()
    finally:
        stages._platform_name = original


@pytest.mark.skipif(os.name == "nt", reason="shell-script stubs cannot be started by CreateProcess")
def test_node_install_runs_through_execute(monkeypatch, tmp_path):
    """stages.main 이 고른 Node 명령을 PATH 스텁이 실제로 받는다."""
    _home(monkeypatch, tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _node_stubs(bindir)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    project = tmp_path / "proj"
    project.mkdir()

    def run(platform, evidence_name, args):
        evidence = tmp_path / evidence_name
        evidence.mkdir()
        monkeypatch.setenv("AI_SETUP_EVIDENCE", str(evidence))
        status = _run_as_platform(
            platform,
            lambda: stages.main(["--plan", "--project-dir", str(project), *args]),
        )
        assert status == 0
        return evidence

    linux20 = run("linux", "linux20", ["--stages", "base,codegraph", "--harness", "claude"])
    url = (linux20 / "curl.url").read_text(encoding="utf-8").strip()
    assert url == stages.NVM_INSTALL_URL
    assert (linux20 / "bash.stdin").read_text(encoding="utf-8") == f"nvm-installer {url}\n"
    assert (linux20 / "nvm.log").read_text(encoding="utf-8").splitlines() == ["install 20", "alias default 20"]
    assert not (linux20 / "sudo.log").exists()

    # nvm 이 이미 있으면 설치기를 다시 받지 않고 필요한 major 만 깐다.
    linux22 = run("linux", "linux22", ["--stages", "base,harness", "--harness", "copilot"])
    assert not (linux22 / "curl.url").exists()
    assert (linux22 / "nvm.log").read_text(encoding="utf-8").splitlines() == ["install 22", "alias default 22"]

    # macOS 도 nvm 이다. brew 는 다른 사용자 소유일 수 있고 node@<major> 는 keg-only 다.
    macos20 = run("macos", "macos20", ["--stages", "base,codegraph", "--harness", "claude"])
    assert (macos20 / "nvm.log").read_text(encoding="utf-8").splitlines() == ["install 20", "alias default 20"]
    assert not (macos20 / "brew.log").exists()

    windows20 = run("windows", "windows20", ["--stages", "base,codegraph", "--harness", "claude"])
    winget20 = (windows20 / "winget.log").read_text(encoding="utf-8")
    assert "OpenJS.NodeJS.20" in winget20
    assert "--version" not in winget20

    windows22 = run("windows", "windows22", ["--stages", "base,harness", "--harness", "copilot"])
    winget22 = (windows22 / "winget.log").read_text(encoding="utf-8")
    assert "OpenJS.NodeJS.22" in winget22
    assert "--version" not in winget22


def test_jarvis_execute_clones_into_home_and_enables_the_unit(monkeypatch, tmp_path, capsys):
    home = _home(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", _path_hiding(["jarvis"]))
    recorded = _record_runs(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    status = stages.main(
        [
            "--plan",
            "--stages",
            "tools",
            "--harness",
            "claude",
            "--jarvis-role",
            "gateway",
            "--project-dir",
            str(project),
        ]
    )
    assert status == 0
    assert ("sh", "-c", JARVIS_GATEWAY) in recorded
    assert ("git", "clone", "https://github.com/thsvkd/jarvis.git") not in recorded
    receipt = json.loads((home / ".claude" / ".claude-code-setup.json").read_text(encoding="utf-8"))
    assert receipt["jarvis_unit"] is True
    assert "jarvis" in receipt["tool_installs"]
    capsys.readouterr()
    again = stages.main(
        [
            "--plan",
            "--dry-run",
            "--stages",
            "tools",
            "--harness",
            "claude",
            "--jarvis-role",
            "gateway",
            "--project-dir",
            str(project),
        ]
    )
    assert again == 0
    assert "thsvkd/jarvis.git" not in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="shell-script stubs cannot be started by CreateProcess")
def test_setcap_execute_uses_the_ss_copy_capabilities(monkeypatch, tmp_path):
    """install -g 는 id -gn 이 출력한 그룹을 받는다."""
    _home(monkeypatch, tmp_path)
    # 이 테스트는 setcap 만 본다. 호스트 psutil 유무에 따라 apt 액션이 끼면 안 된다.
    monkeypatch.setattr(stages, "_psutil_ok_now", lambda: True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    group = "invoker-group"
    monkeypatch.setenv("AI_SETUP_EVIDENCE", str(evidence))
    monkeypatch.setenv("AI_SETUP_GROUP", group)
    for name in ("git", "loginctl", "systemctl", "tailscale"):
        _exe(bindir, name, "exit 0\n")
    _exe(
        bindir,
        "id",
        'printf \'%s\\n\' "$*" >> "$AI_SETUP_EVIDENCE/id.log"\n'
        'if [ "$1" = "-gn" ]; then printf \'%s\\n\' "$AI_SETUP_GROUP"; exit 0; fi\n'
        "exit 1\n",
    )
    _exe(
        bindir,
        "install",
        'printf \'%s\\n\' "$*" >> "$AI_SETUP_EVIDENCE/install.log"\nexit 0\n',
    )
    _exe(
        bindir,
        "setcap",
        'printf \'%s\\n\' "$*" >> "$AI_SETUP_EVIDENCE/setcap.log"\nexit 0\n',
    )
    _exe(
        bindir,
        "sudo",
        'printf \'%s\\n\' "$*" >> "$AI_SETUP_EVIDENCE/sudo.log"\n'
        'cmd=$1\n'
        "shift\n"
        'exec "$cmd" "$@"\n',
    )
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + _path_hiding(["jarvis"]))
    project = tmp_path / "proj"
    project.mkdir()
    status = stages.main(
        [
            "--plan",
            "--stages",
            "tools",
            "--harness",
            "claude",
            "--jarvis-role",
            "core",
            "--ss-caps",
            "--project-dir",
            str(project),
        ]
    )
    assert status == 0
    assert "-gn" in (evidence / "id.log").read_text(encoding="utf-8")
    granted = [
        line.split()
        for line in (evidence / "install.log").read_text(encoding="utf-8").splitlines()
        if "-g" in line.split()
    ]
    assert granted
    assert all(parts[parts.index("-g") + 1] == group for parts in granted)
    caps = (evidence / "setcap.log").read_text(encoding="utf-8")
    assert "cap_sys_ptrace,cap_dac_read_search+ep" in caps
    assert "cap_net_admin" not in caps


def test_package_uninstall_commands_follow_platform():
    for platform in ("linux", "macos", "windows"):
        actions = stages.plan_actions(
            _full(stages=["tools"], harnesses=[], uninstall=True),
            _empty(platform=platform, receipt_tools=["tailscale", "herdr"]),
        )
        argv = [action.argv for action in actions]
        assert TAILSCALE_UNINSTALL[platform] in argv
        assert HERDR_UNINSTALL[platform] in argv
        blob = _blob(actions).lower()
        assert "tailscale logout" not in blob
        assert "tailscale up" not in blob
        assert "setcap" not in blob
        assert "workspace/jarvis" not in blob


def test_package_uninstall_runs_through_execute(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    claude = home / ".claude"
    claude.mkdir()
    (claude / ".claude-code-setup.json").write_text(
        json.dumps({"tool_installs": {"tailscale": True, "herdr": True}}),
        encoding="utf-8",
    )
    recorded = _record_runs(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    status = stages.main(
        ["--plan", "--stages", "tools", "--uninstall", "--harness", "claude", "--project-dir", str(project)]
    )
    assert status == 0
    assert TAILSCALE_UNINSTALL[_host_platform()] in recorded
    assert HERDR_UNINSTALL[_host_platform()] in recorded
    blob = "\n".join(" ".join(argv) for argv in recorded).lower()
    assert "tailscale logout" not in blob
    assert "tailscale up" not in blob
    receipt = json.loads((claude / ".claude-code-setup.json").read_text(encoding="utf-8"))
    assert "tailscale" not in receipt["tool_installs"]
    assert "herdr" not in receipt["tool_installs"]


def test_main_writes_agentsmd_when_only_claude_md_exists(monkeypatch, tmp_path, capsys):
    """CLAUDE.md만 있는 디렉터리는 probe 가 끝난 것으로 보지 않고, execute 가 AGENTS.md 를 쓴다."""
    home = _home(monkeypatch, tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "CLAUDE.md").write_text("notes\n", encoding="utf-8")
    args = ["--plan", "--stages", "agentsmd", "--harness", "claude", "--project-dir", str(project)]
    assert stages.main(args) == 0
    assert (project / "AGENTS.md").is_file()
    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == "notes\n"
    assert json.loads((home / ".claude" / ".claude-code-setup.json").read_text(encoding="utf-8"))["agentsmd"] is True
    capsys.readouterr()
    assert stages.main(args + ["--dry-run"]) == 0
    assert "write AGENTS.md" not in capsys.readouterr().out


def test_declined_claude_specials_are_omitted(monkeypatch, tmp_path, capsys):
    home = _home(monkeypatch, tmp_path)
    claude = home / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": "omc-hud"}}),
        encoding="utf-8",
    )
    project = tmp_path / "proj"
    project.mkdir()
    status = stages.main(
        [
            "--plan",
            "--dry-run",
            "--stages",
            "claude_special",
            "--harness",
            "claude",
            "--project-dir",
            str(project),
            "--include-output-style",
            "0",
            "--include-mods",
            "no",
            "--include-omc",
            "false",
        ]
    )
    assert status == 0
    out = capsys.readouterr().out
    assert "fluent-korean" not in out
    assert "bash-diff-filter" not in out
    assert "cache TTL wrapper to the OMC HUD" not in out


def test_apply_receipt_makes_the_next_probe_skip_plugins_and_mcp(monkeypatch, tmp_path, capsys):
    home = _home(monkeypatch, tmp_path)
    recorded = _record_runs(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    args = ["--plan", "--stages", "plugins,mcp", "--harness", "claude", "--project-dir", str(project)]
    assert stages.main(args) == 0
    capsys.readouterr()
    receipt_path = home / ".claude" / ".claude-code-setup.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert set(receipt["plugins"]) == set(PLUGINS)
    assert "claude" in receipt["mcp"]
    recorded.clear()
    assert stages.main(args + ["--dry-run"]) == 0
    second = capsys.readouterr().out
    assert "plugin install" not in second
    assert "mcp add" not in second
    assert not any("plugin" in argv and "install" in argv for argv in recorded)
    assert not any(len(argv) > 2 and argv[1] == "mcp" and "add" in argv for argv in recorded)
    recorded.clear()
    assert stages.main(args + ["--uninstall"]) == 0
    blob = "\n".join(" ".join(argv) for argv in recorded)
    for plugin in PLUGINS:
        assert f"claude plugin uninstall {plugin} --scope user" in blob
    assert "claude mcp remove codegraph -s user" in blob
    after = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert after.get("plugins") == {}
    assert "claude" not in after.get("mcp", {})


def _default_markets():
    entries = json.loads((ROOT / "res" / "plugins" / "plugins.json").read_text(encoding="utf-8"))
    return {entry["plugin"].partition("@")[2] for entry in entries if entry.get("default")}


def test_marketplaces_this_setup_added_are_removed_on_uninstall(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    recorded = _record_runs(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    args = ["--plan", "--stages", "plugins", "--harness", "claude", "--project-dir", str(project)]
    assert stages.main(args) == 0
    receipt_path = home / ".claude" / ".claude-code-setup.json"
    assert set(json.loads(receipt_path.read_text(encoding="utf-8"))["marketplaces"]) == _default_markets()
    recorded.clear()
    assert stages.main(args + ["--uninstall"]) == 0
    blob = "\n".join(" ".join(argv) for argv in recorded)
    for market in _default_markets():
        assert f"claude plugin marketplace remove {market}" in blob
    # 플러그인을 먼저 지운다. 마켓플레이스를 먼저 지우면 그 플러그인이 함께 사라져 uninstall 이 실패한다.
    assert blob.index("plugin uninstall") < blob.index("marketplace remove")
    assert not json.loads(receipt_path.read_text(encoding="utf-8")).get("marketplaces")


def test_uninstall_keeps_a_marketplace_that_other_plugins_still_use(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    recorded = _record_runs(monkeypatch)
    _installed_plugins(home, {"context7@claude-plugins-official": ["user"],
                              "firecrawl@claude-plugins-official": ["user"], "ponytail@ponytail": ["user"]})
    (home / ".claude" / ".claude-code-setup.json").write_text(json.dumps({
        "plugins": {"context7": True, "ponytail": True},
        "marketplaces": {"claude-plugins-official": True, "ponytail": True}}))
    project = tmp_path / "proj"
    project.mkdir()
    assert stages.main(["--plan", "--stages", "plugins", "--harness", "claude", "--uninstall",
                        "--project-dir", str(project)]) == 0
    blob = "\n".join(" ".join(argv) for argv in recorded)
    assert "claude plugin marketplace remove ponytail" in blob
    assert "marketplace remove claude-plugins-official" not in blob


def _installed_plugins(home, plugins):
    plugins_dir = home / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    (plugins_dir / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        plugin_id: [{"scope": scope} for scope in scopes] for plugin_id, scopes in plugins.items()}}))


def test_plugins_already_installed_are_not_claimed_or_removed(monkeypatch, tmp_path):
    """SPEC: 이미 있던 플러그인은 영수증에 기록하지 않고 uninstall 이 제거하지 않는다."""
    home = _home(monkeypatch, tmp_path)
    recorded = _record_runs(monkeypatch)
    _installed_plugins(home, {"ponytail@ponytail": ["user"]})
    project = tmp_path / "proj"
    project.mkdir()
    args = ["--plan", "--stages", "plugins", "--harness", "claude", "--project-dir", str(project)]
    assert stages.main(args) == 0
    blob = "\n".join(" ".join(argv) for argv in recorded)
    assert "plugin install ponytail@ponytail" not in blob
    receipt = json.loads((home / ".claude" / ".claude-code-setup.json").read_text(encoding="utf-8"))
    assert "ponytail" not in receipt["plugins"]
    # 설치가 ponytail 마켓플레이스를 추가했더라도 남의 플러그인이 쓰므로 남긴다.
    recorded.clear()
    assert stages.main(args + ["--uninstall"]) == 0
    blob = "\n".join(" ".join(argv) for argv in recorded)
    assert "plugin uninstall ponytail" not in blob
    assert "marketplace remove ponytail" not in blob


def test_uninstall_keeps_a_marketplace_our_plugin_still_uses_at_project_scope(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    recorded = _record_runs(monkeypatch)
    _installed_plugins(home, {"ponytail@ponytail": ["user", "project"]})
    (home / ".claude" / ".claude-code-setup.json").write_text(json.dumps({
        "plugins": {"ponytail": True}, "marketplaces": {"ponytail": True}}))
    project = tmp_path / "proj"
    project.mkdir()
    assert stages.main(["--plan", "--stages", "plugins", "--harness", "claude", "--uninstall",
                        "--project-dir", str(project)]) == 0
    blob = "\n".join(" ".join(argv) for argv in recorded)
    assert "claude plugin uninstall ponytail --scope user" in blob
    assert "marketplace remove ponytail" not in blob


def test_execute_does_not_replace_a_structured_omc_receipt(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    claude = home / ".claude"
    claude.mkdir()
    original = {"omc_hud": {"command": "omc-hud"}, "kept": {"ok": True}}
    path = claude / ".claude-code-setup.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    _record_runs(monkeypatch)
    action = stages.Action("claude_special", "wrap", True, ("omc-hud-wrapper",), ("flag:omc_hud",))
    assert stages.execute([action], _full()) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_harness_installers_run_without_their_launch_prompt(monkeypatch):
    """Codex 설치기는 stdin 이 아니라 /dev/tty 로 "Start Codex now?" 를 묻는다. 공식 변수로 끈다."""
    seen = []

    def runner(argv, *args, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(stages.subprocess, "run", runner)
    assert stages._run_harness(("harness", "codex")) == 0
    assert seen[0]["stdin"] is subprocess.DEVNULL
    assert seen[0]["env"]["CODEX_NON_INTERACTIVE"] == "1"


def test_harness_installers_do_not_see_the_user_bin_this_process_added(monkeypatch, tmp_path):
    """보이면 공식 설치기가 셸 프로필에 PATH 를 쓰지 않아 새 터미널에서 claude·codex 가 없다."""
    home = _home(monkeypatch, tmp_path)
    nvm_bin = home / ".nvm" / "versions" / "node" / "v20.0.0" / "bin"
    nvm_bin.mkdir(parents=True)
    monkeypatch.setattr(stages, "_STARTUP_PATH", "/usr/bin")
    monkeypatch.setenv("PATH", "/usr/bin")
    stages._ensure_user_bin_on_path()
    seen = []

    def runner(argv, *args, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(stages.subprocess, "run", runner)
    assert str(home / ".local" / "bin") in os.environ["PATH"].split(os.pathsep)
    assert stages._run_harness(("harness", "codex")) == 0
    passed = seen[0]["env"]["PATH"].split(os.pathsep)
    assert str(home / ".local" / "bin") not in passed
    assert str(nvm_bin) in passed                   # npm 으로 까는 하네스는 방금 깐 Node 가 필요하다
    # 사용자가 원래 PATH 에 두었다면 그대로 둔다.
    monkeypatch.setattr(stages, "_STARTUP_PATH", str(home / ".local" / "bin"))
    assert stages._run_harness(("harness", "codex")) == 0
    assert str(home / ".local" / "bin") in seen[1]["env"]["PATH"].split(os.pathsep)


@pytest.mark.skipif(os.name == "nt", reason="POSIX install directories")
def test_harnesses_outside_the_user_bin_are_found_in_the_same_run(monkeypatch, tmp_path):
    """Grok·OpenCode 는 자기 bin 에 깔고 PATH 는 다음 셸부터 잡힌다. 같은 실행의 MCP 등록이 그 바이너리를 찾아야 한다."""
    home = _home(monkeypatch, tmp_path)
    monkeypatch.delenv("GROK_BIN_DIR", raising=False)
    monkeypatch.setattr(stages, "_STARTUP_PATH", "/usr/bin")
    monkeypatch.setenv("PATH", "/usr/bin")
    stages._ensure_user_bin_on_path()
    added = [str(home / ".grok" / "bin"), str(home / ".opencode" / "bin")]
    path = os.environ["PATH"].split(os.pathsep)
    assert all(directory in path for directory in added)
    # 설치기에 보이면 셸 프로필에 PATH 를 쓰지 않는다(M2 와 같은 이유).
    passed = stages._path_without_added_user_bin().split(os.pathsep)
    assert not any(directory in passed for directory in added)
    # 제거 명령은 방금 깐 바이너리(opencode uninstall)를 불러야 하므로 전체 PATH 를 본다.
    seen = []
    monkeypatch.setattr(stages, "_run", lambda argv, **kwargs: seen.append(kwargs["env"]["PATH"]) or 0)
    assert stages._run_harness(("harness", "--uninstall", "opencode")) == 0
    assert all(directory in seen[0].split(os.pathsep) for directory in added)


def test_macos_node_install_creates_a_login_profile_only_when_none_exists():
    """새 macOS 계정은 프로필이 없어 nvm 이 PATH 줄을 쓰지 못한다. Linux 는 /etc/skel 의 .bashrc 가 있다."""
    world = _empty(git=True, python_ok=True)
    def node_script(platform):
        actions = stages.plan_actions(_full(stages=["base", "codegraph"]), dict(world, platform=platform))
        return [a for a in actions if a.stage == "base"][0].argv[-1]
    assert '.zprofile' in node_script("macos")
    assert 'touch' not in node_script("linux")


@pytest.mark.skipif(os.name == "nt", reason="POSIX uninstall command")
def test_grok_opencode_and_agy_uninstall_remove_binaries_and_keep_settings(monkeypatch, tmp_path):
    """SPEC D5. opencode 자체 uninstall 은 버전에 따라 설정·인증(Data)까지 지워 쓰지 않는다."""
    home = _home(monkeypatch, tmp_path)
    monkeypatch.delenv("GROK_BIN_DIR", raising=False)
    user_bin, grok_bin = home / ".local" / "bin", home / ".grok" / "bin"
    download = home / ".grok" / "downloads" / "grok-linux-arm64"
    download.parent.mkdir(parents=True)
    download.write_text("binary", encoding="utf-8")
    for directory in (user_bin, grok_bin, home / ".opencode" / "bin"):
        directory.mkdir(parents=True, exist_ok=True)
    for name in ("grok", "agent"):
        (grok_bin / name).symlink_to(download)
        (user_bin / name).symlink_to(grok_bin / name)
    (user_bin / "agent-other").symlink_to(download)   # 남의 링크
    removed = [grok_bin / "grok", grok_bin / "agent", user_bin / "grok", user_bin / "agent", download,
               home / ".opencode" / "bin" / "opencode", user_bin / "agy"]
    for path in removed[5:]:
        path.write_text("binary", encoding="utf-8")
    kept = [home / ".grok" / "config.toml", home / ".grok" / "auth.json", home / ".config" / "opencode" / "opencode.json",
            home / ".local" / "share" / "opencode" / "auth.json", home / ".gemini" / "antigravity" / "settings.json"]
    for path in kept:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    for harness in ("grok", "opencode", "agy"):
        assert stages._run_harness(("harness", "--uninstall", harness)) == 0, harness
    assert [path for path in removed if os.path.lexists(path)] == []
    assert all(path.is_file() for path in kept)
    assert (user_bin / "agent-other").is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX uninstall command")
def test_grok_uninstall_leaves_files_in_a_changed_bin_dir_that_are_not_grok(monkeypatch, tmp_path):
    """설치 때와 다른 GROK_BIN_DIR 이면 거기 있는 agent(예: Cursor CLI)는 Grok 의 것이 아니다."""
    home = _home(monkeypatch, tmp_path)
    other = home / ".local" / "bin"
    other.mkdir(parents=True)
    (other / "agent").write_text("cursor agent", encoding="utf-8")
    monkeypatch.setenv("GROK_BIN_DIR", str(other))
    assert stages._run_harness(("harness", "--uninstall", "grok")) == 0
    assert (other / "agent").read_text(encoding="utf-8") == "cursor agent"


@pytest.mark.skipif(os.name == "nt", reason="POSIX uninstall command")
def test_claude_and_codex_uninstall_remove_binaries_and_keep_settings(monkeypatch, tmp_path):
    """SPEC D5: 하네스 제거는 패키지·바이너리만 지우고 설정·인증 디렉터리는 남긴다."""
    home = _home(monkeypatch, tmp_path)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_INSTALL_DIR", raising=False)
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    claude_version = home / ".local" / "share" / "claude" / "versions" / "2.1.310"
    claude_version.parent.mkdir(parents=True)
    claude_version.write_text("binary", encoding="utf-8")
    (bin_dir / "claude").symlink_to(claude_version)
    codex_bin = home / ".codex" / "packages" / "standalone" / "current" / "bin"
    codex_bin.mkdir(parents=True)
    for name in ("codex", "codex-code-mode-host"):
        (codex_bin / name).write_text("binary", encoding="utf-8")
        (bin_dir / name).symlink_to(codex_bin / name)
    kept = [home / ".claude" / "settings.json", home / ".claude.json",
            home / ".codex" / "config.toml", home / ".codex" / "auth.json"]
    for path in kept:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    assert stages._run_harness(("harness", "--uninstall", "claude")) == 0
    assert stages._run_harness(("harness", "--uninstall", "codex")) == 0

    assert sorted(os.listdir(bin_dir)) == []
    assert not (home / ".local" / "share" / "claude").exists()
    assert not (home / ".codex" / "packages" / "standalone").exists()
    assert all(path.is_file() for path in kept)


def test_every_platform_has_a_claude_and_codex_uninstall_command():
    for platform in ("linux", "macos", "windows"):
        for harness in ("claude", "codex"):
            assert _run_as_platform(platform, lambda: stages._catalog_command(harness, True)), (platform, harness)


def test_every_installable_harness_has_a_matching_uninstall_command():
    """설치 명령이 있는 플랫폼마다 같은 방식의 제거 명령이 있어야 --stages all 제거가 실패하지 않는다."""
    catalog = json.loads((ROOT / "res" / "catalog.json").read_text(encoding="utf-8"))
    for entry in (item for item in catalog if item.get("kind") == "harness"):
        for platform, commands in (entry.get("install") or {}).items():
            removal = ((entry.get("uninstall") or {}).get(platform) or [{}])[0]
            assert removal.get("cmd"), (entry["id"], platform)
            assert removal.get("method") == commands[0]["method"], (entry["id"], platform)


def test_skills_count_as_owned_only_while_the_skills_receipt_lists_them(monkeypatch, tmp_path):
    """일반 --uninstall 이 번들 스킬을 지운 뒤에는 단계 영수증의 이름만으로 '설치됨' 이 되지 않는다."""
    home = _home(monkeypatch, tmp_path)
    claude = home / ".claude"
    claude.mkdir()
    (claude / ".claude-code-setup.json").write_text(
        json.dumps({"skills": {"design-lab": True, "tailnet-report": True}}), encoding="utf-8")
    (claude / ".claude-code-setup-skills.json").write_text(
        json.dumps({"version": 1, "files": {"design-lab/SKILL.md": "0" * 64}}), encoding="utf-8")
    assert stages.probe_world(str(tmp_path))["receipt_skills"] == ["design-lab"]
    (claude / ".claude-code-setup-skills.json").unlink()
    assert stages.probe_world(str(tmp_path))["receipt_skills"] == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell one-liner")
@pytest.mark.parametrize(
    ("shell", "existing", "created"),
    [
        ("/bin/zsh", [], ".zprofile"),
        ("/bin/zsh", [".zshrc"], None),
        ("/bin/zsh", [".bashrc"], ".zprofile"),       # zsh 는 .bashrc 를 읽지 않는다
        ("", [], ".zprofile"),                        # SHELL 이 없으면 macOS 기본 zsh
        ("/bin/bash", [], ".bash_profile"),
        ("/bin/bash", [".bashrc"], None),
    ],
)
def test_macos_node_script_creates_the_profile_nvm_will_read(tmp_path, shell, existing, created):
    home = tmp_path / "home"
    home.mkdir()
    for name in existing:
        (home / name).write_text("", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _exe(bindir, "curl", "echo true\n")               # 설치기 본문 대신 아무 일도 하지 않는 스크립트
    env = {"HOME": str(home), "PATH": f"{bindir}{os.pathsep}/usr/bin:/bin"}
    if shell:
        env["SHELL"] = shell
    script = stages._node_install_argv("macos", 20)[-1]
    subprocess.run(["sh", "-c", script], env=env, capture_output=True, timeout=30)
    after = sorted(path.name for path in home.iterdir())
    assert after == sorted(existing + ([created] if created else [])), after


def test_stage_uninstall_runs_each_worker_in_uninstall_mode(monkeypatch, tmp_path):
    """--uninstall --stages 는 워커를 제거 모드로 부른다. 설치 모드로 부르면 되돌리는 대신 다시 깐다."""
    home = _home(monkeypatch, tmp_path)
    (home / ".claude").mkdir()
    (home / ".claude" / ".claude-code-setup.json").write_text(json.dumps({
        "keybinds": {"claude": True}, "agentsmd": True, "skills": {"design-lab": True},
        "output_style": {"set": True}, "mods": {"installed": True}, "omc_hud": {"wrapped": True},
    }), encoding="utf-8")
    (home / ".claude" / ".claude-code-setup-skills.json").write_text(
        json.dumps({"version": 1, "files": {"design-lab/SKILL.md": "0" * 64}}), encoding="utf-8")
    monkeypatch.setenv("PATH", os.environ["PATH"])    # main 이 ~/.local/bin 을 앞에 붙인다. 테스트 뒤 되돌린다.
    recorded = _record_runs(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENTS.md").write_text("mine\n", encoding="utf-8")
    status = stages.main(["--plan", "--uninstall", "--stages", "skills,newline,claude_special,agentsmd",
                          "--harness", "claude", "--project-dir", str(project)])
    assert status == 0
    scripts = [argv for argv in recorded if any(str(part).endswith((".sh", ".ps1")) for part in argv)]
    names = sorted(Path(str(next(p for p in argv if str(p).endswith((".sh", ".ps1"))))).stem for argv in scripts)
    assert names == ["setup_keybinds", "setup_mods", "setup_omc", "setup_output_style", "setup_skills"]
    flag = "-Uninstall" if os.name == "nt" else "--uninstall"    # _worker 가 PowerShell 이름으로 바꾼다
    assert all(flag in argv for argv in scripts), scripts
    # AGENTS.md 는 사용자가 고쳐 쓰는 프로젝트 파일이다. 제거 계약이 없으므로 파일은 두고 기록만 지운다.
    receipt = json.loads((home / ".claude" / ".claude-code-setup.json").read_text(encoding="utf-8"))
    assert "agentsmd" not in receipt
    assert not any(key in receipt for key in ("keybinds", "output_style", "mods", "omc_hud"))


def test_windows_workers_get_powershell_parameter_names(monkeypatch):
    """PowerShell 은 긴 표기를 파라미터에 묶지 못할 수 있다. Windows 에서는 고유 이름으로 넘긴다."""
    monkeypatch.setattr(stages, "_script", lambda name: [name])
    # os.name 을 nt 로 두는 동안 Path 를 만들면 WindowsPath 가 되어 Linux 에서 실패한다. PATH 갱신은 뺀다.
    monkeypatch.setattr(stages, "_ensure_user_bin_on_path", lambda: None)
    recorded = _record_runs(monkeypatch)
    selection = {"harnesses": ["claude", "codex"], "newline": "ctrl-enter", "overwrite_agentsmd": True}
    actions = [
        stages.Action("newline", "", True, ("newline", "ctrl-enter")),
        stages.Action("agentsmd", "", True, ("agentsmd",)),
        stages.Action("skills", "", True, ("install-skills", "--uninstall")),
    ]
    monkeypatch.setattr(stages.os, "name", "nt")
    try:
        status = stages.execute(actions, selection)
    finally:
        monkeypatch.undo()
    assert status == 0
    assert recorded[:3] == [
        ("setup_keybinds", "-Newline", "ctrl-enter"),
        ("setup_agentsmd", "-Harness", "claude,codex", "-Force"),
        ("setup_skills", "-Uninstall"),
    ]


def test_a_user_value_equal_to_uninstall_does_not_flip_an_install(monkeypatch):
    monkeypatch.setattr(stages, "_script", lambda name: [name])
    recorded = _record_runs(monkeypatch)
    selection = {"harnesses": ["claude"], "newline": "--uninstall"}
    assert stages.execute([stages.Action("newline", "", True, ("newline", "--uninstall-typo", "--uninstall"))], selection) == 0
    assert recorded[0] == ("setup_keybinds", "--newline", "--uninstall")


def test_harness_all_expands_to_every_harness_instead_of_crashing():
    """help 가 허용하는 --harness all 은 하네스 이름 하나로 취급되면 MCP 단계가 KeyError 로 죽는다."""
    actions = stages.plan_actions(_full(harnesses=["all"]), _empty())
    blob = _blob(actions)
    for harness in stages.HARNESS_LABELS:
        assert f"install-harness {harness}" in blob, harness


def test_windows_native_uninstall_succeeds_when_files_are_already_gone(tmp_path):
    """제거는 Stop 으로 첫 실패에서 멈춘다. 이미 없는 경로까지 실패로 보면 영수증이 영영 남는다."""
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    catalog = json.loads((ROOT / "res" / "catalog.json").read_text(encoding="utf-8"))
    env = dict(os.environ, USERPROFILE=str(tmp_path), LOCALAPPDATA=str(tmp_path / "local"), HOME=str(tmp_path))
    for name in ("GROK_BIN_DIR", "CODEX_HOME", "CODEX_INSTALL_DIR"):
        env.pop(name, None)
    for entry in catalog:
        removal = ((entry.get("uninstall") or {}).get("windows") or [{}])[0]
        if entry.get("kind") != "harness" or removal.get("method") != "native":
            continue
        script = "$ErrorActionPreference = 'Stop'; " + removal["cmd"]
        proc = subprocess.run([pwsh, "-NoProfile", "-Command", script], env=env,
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, (entry["id"], proc.stderr)


def test_single_record_installed_plugins_still_count_as_installed(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    plugins_dir = home / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "installed_plugins.json").write_text(json.dumps({"plugins": {"ponytail@ponytail": {"version": "1"}}}))
    assert stages.probe_world(str(tmp_path))["plugin_ids"] == ["ponytail@ponytail"]


def test_windows_native_uninstall_removes_the_binaries_it_names(tmp_path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    local = tmp_path / "local"
    owned = {
        "claude": [tmp_path / ".local" / "bin" / "claude.exe", tmp_path / ".local" / "share" / "claude" / "v" / "x"],
        "codex": [tmp_path / ".codex" / "packages" / "standalone" / "current" / "codex.exe"],
        "grok": [tmp_path / ".grok" / "bin" / "grok.exe", tmp_path / ".grok" / "bin" / "agent.exe",
                 tmp_path / ".grok" / "downloads" / "grok-windows-x64.exe"],
        "agy": [local / "agy" / "bin" / "agy.exe", local / "antigravity" / "staging" / "agy.zip"],
    }
    kept = [tmp_path / ".codex" / "config.toml", tmp_path / ".grok" / "config.toml"]
    for path in [*kept, *(p for paths in owned.values() for p in paths)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    env = dict(os.environ, USERPROFILE=str(tmp_path), LOCALAPPDATA=str(local), HOME=str(tmp_path))
    for name in ("GROK_BIN_DIR", "CODEX_HOME", "CODEX_INSTALL_DIR"):
        env.pop(name, None)
    catalog = {entry["id"]: entry for entry in json.loads((ROOT / "res" / "catalog.json").read_text(encoding="utf-8"))}
    for harness, paths in owned.items():
        script = "$ErrorActionPreference = 'Stop'; " + catalog[harness]["uninstall"]["windows"][0]["cmd"]
        proc = subprocess.run([pwsh, "-NoProfile", "-Command", script], env=env,
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, (harness, proc.stderr)
        assert [path for path in paths if path.exists()] == [], harness
    assert all(path.is_file() for path in kept)


def test_orca_desktop_points_to_jarvis_and_changes_nothing(monkeypatch, tmp_path, capsys):
    """covers: AI-66 — Orca 는 jarvis 가 관리한다. 계획을 세우기 전에 안내만 내고 exit 1 로 끝난다."""
    home = _home(monkeypatch, tmp_path)
    monkeypatch.setattr(stages, "probe_world", lambda *a, **k: pytest.fail("planned after --orca-desktop"))
    recorded = _record_runs(monkeypatch)
    assert stages.main(["--plan", "--stages", "tools", "--orca-desktop", "--project-dir", str(tmp_path)]) == 1
    assert stages.ORCA_MOVED in capsys.readouterr().err
    assert stages.orca_moved(windows=False) == (
        "Orca is now managed by jarvis; ai-setup no longer installs, updates or removes it. "
        "From a jarvis checkout run: python3 -m jarvis module orca plan --json "
        "(uninstall: module orca uninstall --json --dry-run)."
    )
    # Windows 의 python3 은 Store alias 일 수 있다: setup.ps1·install.ps1 과 같이 py -3 을 안내한다
    assert "run: py -3 -m jarvis module orca plan --json" in stages.orca_moved(windows=True)
    assert recorded == []
    assert list(home.iterdir()) == []


def test_doctor_pointer_names_the_jarvis_doctor_module():
    """covers: AI-68 — doctor 는 jarvis 로 옮겨 갔다. setup·install 스크립트의 --doctor·-Doctor 안내가 이 문구를 쓴다."""
    assert stages.doctor_moved(windows=False) == (
        "Doctor is now run by jarvis; ai-setup no longer runs it. "
        "From a jarvis checkout run: python3 -m jarvis module doctor status --json."
    )
    assert "run: py -3 -m jarvis module doctor status --json." in stages.doctor_moved(windows=True)


@pytest.mark.parametrize("args, recorded_before", [
    (["--stages", "tools"], False),
    (["--stages", "tools"], True),
    (["--stages", "all", "--uninstall"], True),
])
def test_tools_and_full_uninstall_leave_orca_and_its_receipt_records_to_jarvis(
        monkeypatch, tmp_path, args, recorded_before):
    """covers: AI-67 — jarvis 의 Orca 모듈이 같은 영수증 키를 읽고 제거하므로 ai-setup 은 손대지 않는다."""
    home = _home(monkeypatch, tmp_path)
    claude = home / ".claude"
    claude.mkdir()
    orca_records = {
        "orca": {"method": "deb", "package": "orca-ide"},
        "orca_server": {"service_manager": "systemd-user", "state": "configured"},
    } if recorded_before else {}
    receipt_path = claude / ".claude-code-setup.json"
    receipt_path.write_text(json.dumps({"tool_installs": dict(orca_records, tailscale=True)}), encoding="utf-8")
    # 호스트에 Orca 가 깔려 있어도 결과가 같아야 한다. 예전 tools 단계는 orca 가 PATH 에 없으면 설치했다.
    monkeypatch.setattr(stages, "_which_bins", lambda names: [])
    recorded = _record_runs(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    assert stages.main(["--plan", *args, "--harness", "claude", "--project-dir", str(project)]) == 0
    assert not [argv for argv in recorded if "orca" in " ".join(argv).lower()]
    tools = json.loads(receipt_path.read_text(encoding="utf-8"))["tool_installs"]
    assert {key: value for key, value in tools.items() if key.startswith("orca")} == orca_records


def test_hooks_pointer_names_the_jarvis_hooks_module():
    """covers: AI-69 — 훅은 jarvis 로 옮겨 갔다. setup·install 스크립트의 --hooks·-Hooks 안내가 이 문구를 쓴다."""
    assert stages.hooks_moved(windows=False) == (
        "Hooks are now managed by jarvis; ai-setup no longer installs or removes them. "
        "From a jarvis checkout run: python3 -m jarvis module hooks plan --json "
        "(uninstall: module hooks uninstall --json --dry-run)."
    )
    assert "run: py -3 -m jarvis module hooks plan --json" in stages.hooks_moved(windows=True)
    assert "hooks" not in stages.STAGE_ORDER


@pytest.mark.parametrize("args", [
    ["--stages", "hooks"],
    ["--stages", "newline,hooks", "--dry-run"],
    ["--stages", "hooks", "--uninstall"],
])
def test_hooks_stage_points_to_jarvis_and_writes_nothing(monkeypatch, tmp_path, capsys, args):
    """covers: AI-69 — 계획을 세우기 전에 안내만 내고 exit 1 로 끝난다. 영수증의 훅 기록은 그대로다."""
    home = _home(monkeypatch, tmp_path)
    claude = home / ".claude"
    claude.mkdir()
    receipt = claude / ".claude-code-setup.json"
    receipt.write_text(json.dumps({"hooks": True, "hook_files": ["danger_guard.py"]}), encoding="utf-8")
    before = sorted(path.relative_to(home) for path in home.rglob("*"))
    monkeypatch.setattr(stages, "probe_world", lambda *a, **k: pytest.fail("planned after --stages hooks"))
    recorded = _record_runs(monkeypatch)
    monkeypatch.setenv("PATH", os.environ["PATH"])    # main 이 ~/.local/bin 을 앞에 붙인다. 테스트 뒤 되돌린다.
    assert stages.main(["--plan", *args, "--project-dir", str(tmp_path)]) == 1
    assert stages.HOOKS_MOVED in capsys.readouterr().err
    assert recorded == []
    assert sorted(path.relative_to(home) for path in home.rglob("*")) == before
    assert json.loads(receipt.read_text(encoding="utf-8")) == {"hooks": True, "hook_files": ["danger_guard.py"]}


@pytest.mark.parametrize("args", [
    ["--stages", "all"],
    ["--stages", "all", "--uninstall"],
])
def test_full_run_and_full_uninstall_leave_hooks_and_their_receipt_records_to_jarvis(
        monkeypatch, tmp_path, capsys, args):
    """covers: AI-69 — 전체 단계는 hooks 단계 없이 돌고, jarvis 훅 모듈이 이어 읽는 영수증 기록을 남긴다."""
    home = _home(monkeypatch, tmp_path)
    claude = home / ".claude"
    (claude / "hooks").mkdir(parents=True)
    guard = claude / "hooks" / "danger_guard.py"
    guard.write_text("# installed hook\n", encoding="utf-8")
    hook_records = {
        "version": 1,
        "hooks_dir": "~/.claude/hooks",
        "hook_files": ["danger_guard.py", "shims/rm"],
        "added_allow": ["Bash"],
        "default_mode_before": "acceptEdits",
        "default_mode_after": "auto",
        "default_mode_changed": True,
        "hooks": True,
    }
    receipt_path = claude / ".claude-code-setup.json"
    receipt_path.write_text(json.dumps(hook_records), encoding="utf-8")
    monkeypatch.setattr(stages, "_which_bins", lambda names: [])
    monkeypatch.setenv("PATH", os.environ["PATH"])    # main 이 ~/.local/bin 을 앞에 붙인다. 테스트 뒤 되돌린다.
    recorded = _record_runs(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    assert stages.main(["--plan", *args, "--harness", "claude", "--project-dir", str(project)]) == 0
    plan = capsys.readouterr().out
    assert "hooks" not in plan.split()
    assert "guard hooks" not in plan
    assert not [argv for argv in recorded if any("setup_hooks" in str(part) for part in argv)]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert {key: receipt.get(key) for key in hook_records} == hook_records
    assert guard.read_text(encoding="utf-8") == "# installed hook\n"


load_tests = collect(globals())
