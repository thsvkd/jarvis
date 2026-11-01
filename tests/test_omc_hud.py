"""OMC 설치 + HUD 캐시 TTL 표시 (res/omc/, scripts/setup_omc.sh).

실제 홈·플러그인 캐시·claude CLI 를 건드리지 않도록 임시 HOME 과 가짜 플러그인 캐시,
PATH 앞의 claude 스텁으로만 검증한다.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "jarvis" / "modules" / "setup"
APPLY = ROOT / "res" / "omc" / "apply.py"
TTL = ROOT / "res" / "omc" / "omc-hud-ttl.mjs"
OMC_CMD = "sh ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hud/omc-hud-cache.sh ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hud/omc-hud.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    lib = h / ".claude" / "plugins" / "cache" / "omc" / "oh-my-claudecode" / "5.4.0" / "scripts" / "lib"
    lib.mkdir(parents=True)
    (lib / "hud-wrapper-template.txt").write_text("// omc hud template\n", encoding="utf-8")
    (lib / "config-dir.mjs").write_text("// config dir\n", encoding="utf-8")
    return h


def apply(home, *args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(APPLY), "--home", str(home), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def settings(home):
    return json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))


def write_settings(home, data):
    (home / ".claude" / "settings.json").write_text(json.dumps(data), encoding="utf-8")


def test_wraps_existing_omc_status_line_with_ttl_wrapper_and_uninstall_restores(home):
    before = {"model": "opus", "statusLine": {"type": "command", "command": OMC_CMD}}
    write_settings(home, before)

    proc = apply(home)
    assert proc.returncode == 0, proc.stderr
    status = settings(home)["statusLine"]
    assert status["command"].startswith('node "')
    assert status["command"].endswith(OMC_CMD)
    assert "omc-hud-ttl.mjs" in status["command"]
    assert status["refreshInterval"] == 10
    assert (home / ".claude" / "hud" / "omc-hud-ttl.mjs").is_file()

    # 멱등: 두 번 실행해도 접두가 겹치지 않는다
    assert apply(home).returncode == 0
    assert settings(home)["statusLine"]["command"].count("omc-hud-ttl.mjs") == 1

    proc = apply(home, "--uninstall")
    assert proc.returncode == 0, proc.stderr
    assert settings(home) == before
    assert not (home / ".claude" / "hud" / "omc-hud-ttl.mjs").exists()


def test_uninstall_keeps_refresh_interval_set_by_user(home):
    write_settings(home, {"statusLine": {"type": "command", "command": OMC_CMD, "refreshInterval": 3}})
    assert apply(home).returncode == 0
    assert settings(home)["statusLine"]["refreshInterval"] == 3
    assert apply(home, "--uninstall").returncode == 0
    assert settings(home)["statusLine"] == {"type": "command", "command": OMC_CMD, "refreshInterval": 3}


def test_copies_and_configures_omc_hud_when_status_line_is_absent(home):
    assert apply(home).returncode == 0
    hud = home / ".claude" / "hud"
    assert (hud / "omc-hud.mjs").read_text(encoding="utf-8") == "// omc hud template\n"
    assert (hud / "lib" / "config-dir.mjs").is_file()
    assert settings(home)["statusLine"]["command"].endswith('/omc-hud.mjs"')


def test_non_omc_status_line_is_left_alone(home):
    mine = {"statusLine": {"type": "command", "command": "~/my-status.sh"}}
    write_settings(home, mine)
    proc = apply(home)
    assert proc.returncode == 0
    assert settings(home) == mine
    assert not (home / ".claude" / "hud" / "omc-hud-ttl.mjs").exists()


def test_unparseable_settings_stops_without_writing(home):
    path = home / ".claude" / "settings.json"
    path.write_text("{ broken", encoding="utf-8")
    proc = apply(home)
    assert proc.returncode == 1
    assert path.read_text(encoding="utf-8") == "{ broken"


def test_dry_run_writes_nothing(home):
    write_settings(home, {"statusLine": {"type": "command", "command": OMC_CMD}})
    before = (home / ".claude" / "settings.json").read_text(encoding="utf-8")
    proc = apply(home, "--dry-run")
    assert proc.returncode == 0
    assert "DRY RUN" in proc.stdout
    assert (home / ".claude" / "settings.json").read_text(encoding="utf-8") == before
    assert not (home / ".claude" / "hud").exists()
    assert not (home / ".claude" / ".claude-code-setup.json").exists()


def test_manifest_preserves_other_sections(home):
    manifest = home / ".claude" / ".claude-code-setup.json"
    manifest.write_text(json.dumps({"keybinds": {"x": 1}}), encoding="utf-8")
    write_settings(home, {"statusLine": {"type": "command", "command": OMC_CMD}})
    assert apply(home).returncode == 0
    assert json.loads(manifest.read_text(encoding="utf-8"))["keybinds"] == {"x": 1}
    assert apply(home, "--uninstall").returncode == 0
    assert json.loads(manifest.read_text(encoding="utf-8")) == {"keybinds": {"x": 1}}


# ── 래퍼: session:NNm → cache:... ─────────────────────────────────────


def run_ttl(stdin_obj, hud_line):
    """가짜 HUD(printf)를 안쪽 명령으로 물려 래퍼 출력을 받는다."""
    return subprocess.run(
        ["node", str(TTL), sys.executable, "-c", f"import sys; sys.stdin.read(); print({hud_line!r})"],
        input=json.dumps(stdin_obj), capture_output=True, text=True, encoding="utf-8", timeout=30,
    ).stdout


HUD = "[OMC] | thinking | session:\x1b[32m45m\x1b[0m\nctx:[#---]8%"


@needs_node
def test_shows_remaining_ttl_in_minutes():
    out = run_ttl({"prompt_cache": {"warm": True, "expires_at": time.time() + 30 * 60 + 5}}, HUD)
    assert "session:" not in out
    assert "cache:\x1b[32m30m\x1b[0m" in out
    assert out.endswith("ctx:[#---]8%\n")


@needs_node
def test_two_minutes_or_less_is_yellow_under_one_minute_shows_seconds():
    out = run_ttl({"prompt_cache": {"warm": True, "expires_at": time.time() + 45}}, HUD)
    assert "cache:\x1b[33m44s\x1b[0m" in out or "cache:\x1b[33m45s\x1b[0m" in out


@needs_node
def test_shows_cold_when_expired():
    out = run_ttl({"prompt_cache": {"warm": True, "expires_at": time.time() - 10}}, HUD)
    assert "cache:\x1b[31mcold\x1b[0m" in out


@needs_node
def test_keeps_session_display_when_no_cache_info():
    """구버전 Claude Code 처럼 prompt_cache 가 없으면 쓸모 있는 세션 시간을 지우지 않는다."""
    assert run_ttl({}, HUD) == HUD + "\n"


@needs_node
def test_inner_command_failure_goes_to_stderr_and_exit_code_is_0():
    """OMC find-node.sh 처럼 statusLine 을 0 이 아닌 코드로 끝내지 않는다."""
    proc = subprocess.run(
        ["node", str(TTL), sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        input="{}", capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert proc.returncode == 0
    assert "boom" in proc.stderr


def test_uninstall_removes_everything_when_install_had_no_status_line(home):
    assert apply(home).returncode == 0
    assert apply(home, "--uninstall").returncode == 0
    assert not (home / ".claude" / "settings.json").exists()
    assert not list((home / ".claude").glob("settings.json.bak.*"))
    assert not (home / ".claude" / "hud").exists()
    assert not (home / ".claude" / ".claude-code-setup.json").exists()


def test_uninstall_keeps_hud_file_rewritten_by_omc(home):
    """omc-setup 이 HUD 파일과 statusLine 을 다시 쓰면 그 파일은 더 이상 우리 것이 아니다."""
    assert apply(home).returncode == 0
    hud = home / ".claude" / "hud"
    (hud / "omc-hud.mjs").write_text("// OMC generated\n", encoding="utf-8")
    data = settings(home)
    data["statusLine"]["command"] = data["statusLine"]["command"].rsplit('" ', 1)[0] + '" ' + OMC_CMD
    write_settings(home, data)
    assert apply(home, "--uninstall").returncode == 0
    assert (hud / "omc-hud.mjs").read_text(encoding="utf-8") == "// OMC generated\n"
    assert (hud / "lib" / "config-dir.mjs").is_file()
    assert settings(home)["statusLine"]["command"] == OMC_CMD


def test_when_only_one_hud_file_changed_both_stay_and_reinstall_fills_only_missing(home):
    assert apply(home).returncode == 0
    hud = home / ".claude" / "hud"
    (hud / "omc-hud.mjs").write_text("// hud setup rewrote me\n", encoding="utf-8")
    assert apply(home, "--uninstall").returncode == 0
    assert (hud / "omc-hud.mjs").is_file() and (hud / "lib" / "config-dir.mjs").is_file()

    (hud / "lib" / "config-dir.mjs").unlink()
    assert apply(home).returncode == 0
    assert (hud / "lib" / "config-dir.mjs").is_file()
    assert (hud / "omc-hud.mjs").read_text(encoding="utf-8") == "// hud setup rewrote me\n"


def test_unstrippable_wrapper_stops_both_install_and_uninstall(home):
    command = 'FOO=1 node "$HOME/.claude/hud/omc-hud-ttl.mjs" ' + OMC_CMD
    write_settings(home, {"statusLine": {"type": "command", "command": command}})
    ttl = home / ".claude" / "hud" / "omc-hud-ttl.mjs"
    ttl.parent.mkdir(parents=True)
    ttl.write_text("// in use\n", encoding="utf-8")
    assert apply(home).returncode == 1
    assert not (home / ".claude" / "hud" / "omc-hud.mjs").exists()
    assert not (home / ".claude" / ".claude-code-setup.json").exists()
    assert apply(home, "--uninstall").returncode == 1
    assert ttl.is_file()
    assert settings(home)["statusLine"]["command"] == command


def test_first_revision_manifest_format_is_readable(home):
    manifest = home / ".claude" / ".claude-code-setup.json"
    manifest.write_text(json.dumps({"omc_hud": {"hud_copied": True}}), encoding="utf-8")
    assert apply(home).returncode == 0
    assert isinstance(json.loads(manifest.read_text(encoding="utf-8"))["omc_hud"]["hud_copied"], dict)
    assert apply(home, "--uninstall").returncode == 0
    assert not (home / ".claude" / "hud").exists()


def test_double_wrapped_wrapper_is_collapsed_to_one_layer_on_reinstall(home):
    wrapped = 'node "$HOME/.claude/hud/omc-hud-ttl.mjs" '
    write_settings(home, {"statusLine": {"type": "command", "command": wrapped * 2 + OMC_CMD}})
    assert apply(home).returncode == 0
    assert settings(home)["statusLine"]["command"].count("omc-hud-ttl.mjs") == 1
    assert apply(home, "--uninstall").returncode == 0
    assert settings(home)["statusLine"]["command"] == OMC_CMD


def test_existing_config_dir_is_not_overwritten(home):
    lib = home / ".claude" / "hud" / "lib"
    lib.mkdir(parents=True)
    (lib / "config-dir.mjs").write_text("// mine\n", encoding="utf-8")
    assert apply(home).returncode == 0
    assert apply(home, "--uninstall").returncode == 0
    assert (lib / "config-dir.mjs").read_text(encoding="utf-8") == "// mine\n"


def test_old_version_prefix_becomes_current_notation_on_reinstall(home):
    old = 'node "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hud/omc-hud-ttl.mjs" ' + OMC_CMD
    write_settings(home, {"statusLine": {"type": "command", "command": old, "refreshInterval": 10}})
    assert apply(home).returncode == 0
    command = settings(home)["statusLine"]["command"]
    assert "CLAUDE_CONFIG_DIR:-$HOME/.claude}/hud/omc-hud-ttl" not in command
    assert command.endswith('/omc-hud-ttl.mjs" ' + OMC_CMD) and command.count("omc-hud-ttl.mjs") == 1


def test_manifest_with_bom_is_readable(home):
    """Windows PowerShell 5.1 의 Set-Content -Encoding UTF8 은 BOM 을 붙여 쓴다."""
    write_settings(home, {"statusLine": {"type": "command", "command": OMC_CMD}})
    assert apply(home).returncode == 0
    manifest = home / ".claude" / ".claude-code-setup.json"
    manifest.write_bytes(b"\xef\xbb\xbf" + manifest.read_bytes())
    assert apply(home, "--uninstall").returncode == 0
    assert settings(home)["statusLine"] == {"type": "command", "command": OMC_CMD}


def test_uninstall_keeps_user_text_before_wrapper(home):
    command = 'cd /tmp && node "$HOME/.claude/hud/omc-hud-ttl.mjs" ' + OMC_CMD
    write_settings(home, {"statusLine": {"type": "command", "command": command}})
    assert apply(home, "--uninstall").returncode == 1
    assert settings(home)["statusLine"]["command"] == command


def test_non_string_command_stops(home):
    write_settings(home, {"statusLine": {"type": "command", "command": 123}})
    assert apply(home).returncode == 1
    assert apply(home, "--uninstall").returncode == 0
    assert settings(home)["statusLine"]["command"] == 123


def test_uninstall_keeps_refresh_interval_user_changed_after_install(home):
    write_settings(home, {"statusLine": {"type": "command", "command": OMC_CMD}})
    assert apply(home).returncode == 0
    data = settings(home)
    data["statusLine"]["refreshInterval"] = 2
    write_settings(home, data)
    assert apply(home, "--uninstall").returncode == 0
    assert settings(home)["statusLine"] == {"type": "command", "command": OMC_CMD, "refreshInterval": 2}


def test_non_omc_status_line_creates_no_hud_files_either(home):
    write_settings(home, {"statusLine": {"type": "command", "command": "~/my-status.sh"}})
    assert apply(home).returncode == 0
    assert not (home / ".claude" / "hud").exists()


def test_broken_manifest_stops_without_overwriting(home):
    write_settings(home, {"statusLine": {"type": "command", "command": OMC_CMD}})
    manifest = home / ".claude" / ".claude-code-setup.json"
    manifest.write_text('{ "hook_files": [', encoding="utf-8")
    assert apply(home).returncode == 1
    assert manifest.read_text(encoding="utf-8") == '{ "hook_files": ['
    assert "omc-hud-ttl" not in settings(home)["statusLine"]["command"]


def test_rerun_does_not_rewrite_settings_so_backups_do_not_pile_up(home):
    write_settings(home, {"statusLine": {"type": "command", "command": OMC_CMD}})
    assert apply(home).returncode == 0
    backups = set((home / ".claude").glob("settings.json.bak.*"))
    assert apply(home).returncode == 0
    assert set((home / ".claude").glob("settings.json.bak.*")) == backups


@pytest.mark.skipif(os.name == "nt", reason="find-node.sh is POSIX only")
def test_uses_omc_find_node_to_locate_node_when_present(home):
    """PATH 에 node 가 없는 셸에서도 OMC HUD 가 뜨던 곳이면 래퍼도 떠야 한다."""
    (home / ".claude" / "hud").mkdir()
    (home / ".claude" / "hud" / "find-node.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    write_settings(home, {"statusLine": {"type": "command", "command": OMC_CMD}})
    assert apply(home).returncode == 0
    command = settings(home)["statusLine"]["command"]
    assert command.startswith('sh "$HOME/.claude/hud/find-node.sh" "$HOME/.claude/hud/omc-hud-ttl.mjs" ')
    assert apply(home, "--uninstall").returncode == 0
    assert settings(home)["statusLine"]["command"] == OMC_CMD


# ── setup.sh --omc: claude CLI 는 스텁 ────────────────────────────────


posix_only = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="POSIX shell script test (bash required)",
)


def stub_claude(tmp_path, installed):
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    log = tmp_path / "claude.log"
    listed = "  ❯ oh-my-claudecode@omc" if installed else "  ❯ other@x"
    (bin_dir / "claude").write_text(
        f'#!/bin/sh\necho "$*" >> "{log}"\n'
        f'case "$*" in "plugin list") echo "{listed}" ;; esac\n',
        encoding="utf-8",
    )
    (bin_dir / "claude").chmod(0o755)
    return bin_dir, log


def run_setup(args, home, bin_dir):
    env = dict(os.environ, HOME=str(home), PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return subprocess.run(["bash", str(ROOT / "scripts" / "setup.sh"), *args], env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


@posix_only
def test_setup_omc_installs_plugin_and_configures_hud(tmp_path, home):
    bin_dir, log = stub_claude(tmp_path, installed=False)
    proc = run_setup(["--omc"], home, bin_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = log.read_text(encoding="utf-8")
    assert "plugin marketplace add Yeachan-Heo/oh-my-claudecode" in calls
    assert "plugin install oh-my-claudecode@omc" in calls
    assert "omc-hud-ttl.mjs" in settings(home)["statusLine"]["command"]
    # --omc 만 줬으므로 훅은 설치되지 않는다
    assert "hooks" not in settings(home)


@posix_only
def test_setup_omc_dry_run_does_not_run_claude(tmp_path, home):
    bin_dir, log = stub_claude(tmp_path, installed=False)
    proc = run_setup(["--omc", "--dry-run"], home, bin_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "(dry-run) claude plugin install oh-my-claudecode@omc" in proc.stdout
    assert "install" not in log.read_text(encoding="utf-8")
    assert not (home / ".claude" / "settings.json").exists()


@posix_only
def test_hooks_uninstall_points_to_jarvis_and_keeps_the_omc_manifest(tmp_path, home):
    """covers: AI-69 — --uninstall --hooks 는 안내만 내고 아무것도 쓰지 않는다. 뒤의 --uninstall --omc 가 그대로 되돌린다."""
    bin_dir, _ = stub_claude(tmp_path, installed=True)
    write_settings(home, {"statusLine": {"type": "command", "command": OMC_CMD}})
    assert run_setup(["--omc"], home, bin_dir).returncode == 0
    manifest = home / ".claude" / ".claude-code-setup.json"
    before = (manifest.read_bytes(), (home / ".claude" / "settings.json").read_bytes())
    proc = run_setup(["--uninstall", "--hooks"], home, bin_dir)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "python3 -m jarvis module hooks plan --json" in proc.stderr
    assert (manifest.read_bytes(), (home / ".claude" / "settings.json").read_bytes()) == before
    proc = run_setup(["--uninstall", "--omc"], home, bin_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert settings(home)["statusLine"] == {"type": "command", "command": OMC_CMD}


@posix_only
def test_setup_uninstall_also_restores_ttl_hud(tmp_path, home):
    bin_dir, _ = stub_claude(tmp_path, installed=True)
    write_settings(home, {"statusLine": {"type": "command", "command": OMC_CMD}})
    assert run_setup(["--omc"], home, bin_dir).returncode == 0
    proc = run_setup(["--uninstall"], home, bin_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert settings(home)["statusLine"] == {"type": "command", "command": OMC_CMD}


load_tests = collect(globals())
