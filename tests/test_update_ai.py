"""update-ai: isolated homes, stub executables on PATH that record how they were called."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "jarvis" / "modules" / "setup"
UPDATE_AI = ROOT / "res" / "update" / "update_ai.py"
WINDOWS = os.name == "nt"


def make_stub(bin_dir, name, version="1.0.0", fail_on=None, tag=None):
    """호출 인자를 STUB_LOG 에 한 줄씩 남기는 가짜 실행 파일.

    --version 이면 version 을 찍고, --sleep 이면 30초 기다린다(실행 중인 세션 흉내).
    fail_on 으로 준 첫 인자로 불리면 exit 1 로 실패한다. tag 를 주면 기록 줄에 이름 대신 쓴다
    (같은 이름의 실행 파일 둘 중 어느 쪽이 불렸는지 구분할 때).
    """
    tag = tag or name
    bin_dir.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        path = bin_dir / (name + ".cmd")
        lines = [
            "@echo off",
            'if "%~1"=="--sleep" (ping -n 30 127.0.0.1 >nul & exit /b 0)',
            ">>\"%STUB_LOG%\" echo " + tag + " %*",
            'if "%~1"=="--version" (echo ' + name + " " + version + " & exit /b 0)",
            'if "%~1 %~2"=="plugin list" (echo [] & exit /b 0)',
        ]
        if fail_on:
            lines.append('if "%~1"=="' + fail_on + '" exit /b 1')
        lines.append("exit /b 0")
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    else:
        path = bin_dir / name
        lines = [
            "#!/bin/sh",
            'if [ "$1" = "--sleep" ]; then /bin/sleep 30; exit 0; fi',
            'echo "' + tag + ' $*" >> "$STUB_LOG"',
            'if [ "$1" = "--version" ]; then echo "' + name + " " + version + '"; exit 0; fi',
            'if [ "$1 $2" = "plugin list" ]; then echo "[]"; exit 0; fi',
        ]
        if fail_on:
            lines.append('if [ "$1" = "' + fail_on + '" ]; then exit 1; fi')
        lines.append("exit 0")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(0o755)
    return path


def env_for(home, path_dirs, log):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["STUB_LOG"] = str(log)
    # jarvis 에서 더한 codex 최신 판 조회(GitHub 릴리스)가 네트워크에 닿지 않게 한다. 없는 파일이라 "확인 불가"다
    env["UPDATE_AI_CODEX_LATEST"] = (home / "no-codex-release.json").as_uri()
    keep = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")] if WINDOWS else []
    env["PATH"] = os.pathsep.join([str(d) for d in path_dirs] + keep)
    return env


def run_update(home, path_dirs, log, *args):
    home.mkdir(parents=True, exist_ok=True)
    bootstrap = r"""
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("update_ai_fixture", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
original = module.process_table
roots = [value.replace("\\", "/").lower().rstrip("/") + "/" for value in json.loads(sys.argv[2])]
def fixture_processes():
    table = original()
    if table is None:
        return None
    return [(pid, command) for pid, command in table
            if pid not in (os.getpid(), os.getppid())
            and any(root in command.replace("\\", "/").lower() for root in roots)]
module.process_table = fixture_processes
raise SystemExit(module.main(sys.argv[3:]))
"""
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-c", bootstrap, str(UPDATE_AI), json.dumps([str(path) for path in path_dirs]), *args],
        capture_output=True, text=True, encoding="utf-8", env=env_for(home, path_dirs, log),
        stdin=subprocess.DEVNULL, timeout=120,
    )


def calls(log):
    if not log.exists():
        return []
    return [line.strip() for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]


def updates(log):
    """--version 확인을 뺀, 실제로 무언가를 바꾸는 호출."""
    return [c for c in calls(log) if not c.endswith(" --version")]


def test_check_only_reads_versions_and_never_updates(tmp_path):
    """covers: AI-7"""
    bin_dir = tmp_path / "bin"
    for name in ("claude", "agy", "herdr"):
        make_stub(bin_dir, name)
    log = tmp_path / "calls.log"
    result = run_update(tmp_path / "home", [bin_dir], log, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert updates(log) == [], updates(log)
    assert "claude" in result.stdout and "1.0.0" in result.stdout
    assert not (tmp_path / "home" / ".cache" / "update-ai.lock").exists()


def test_missing_tools_are_skipped_without_installing(tmp_path):
    """covers: AI-8"""
    bin_dir = tmp_path / "bin"
    make_stub(bin_dir, "agy")
    make_stub(bin_dir, "npm")
    log = tmp_path / "calls.log"
    result = run_update(tmp_path / "home", [bin_dir], log, "--allow-interrupt")
    assert result.returncode == 0, result.stdout + result.stderr
    assert updates(log) == ["agy update"], updates(log)
    assert any("codex" in line and "not installed" in line for line in result.stdout.splitlines()), result.stdout


def test_running_harness_with_a_versioned_install_is_updated_without_allow_interrupt(tmp_path):
    """covers: AI-9"""
    bin_dir = tmp_path / "bin"
    stub = make_stub(bin_dir, "agy")
    log = tmp_path / "calls.log"
    env = env_for(tmp_path / "home", [bin_dir], log)
    session = subprocess.Popen([str(stub), "--sleep"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        result = run_update(tmp_path / "home", [bin_dir], log, "--only", "agy")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "agy update" in updates(log)
        assert "deferred" not in result.stdout, result.stdout
        assert session.poll() is None
    finally:
        session.kill()
        session.wait()


def test_running_session_of_an_in_place_npm_install_defers_the_update(tmp_path):
    """covers: AI-56"""
    prefix_bin = npm_layout(tmp_path / "node-prefix", "@google/gemini-cli", "gemini", "prefix-npm")
    log = tmp_path / "calls.log"
    env = env_for(tmp_path / "home", [prefix_bin], log)
    session = subprocess.Popen([str(prefix_bin / ("gemini.cmd" if WINDOWS else "gemini")), "--sleep"], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        result = run_update(tmp_path / "home", [prefix_bin], log, "--only", "gemini")
        assert result.returncode == 1, result.stdout + result.stderr
        assert not any(" install -g " in c for c in updates(log)), updates(log)
        # 진짜로 그 세션을 찾았는지 PID 로 확인하고, 실시간 갱신되는 카탈로그 설치 방법을 알려 준다.
        deferred = [line for line in result.stdout.splitlines() if "deferred" in line and "gemini" in line]
        assert deferred and str(session.pid) in deferred[0], result.stdout
        hint = [line for line in result.stdout.splitlines() if "npm_release.py" in line]
        assert hint and hint[0].rstrip().endswith("install gemini"), result.stdout
        result = run_update(tmp_path / "home", [prefix_bin], log, "--only", "gemini", "--allow-interrupt")
        assert "prefix-npm install -g @google/gemini-cli@latest" in updates(log), result.stdout
    finally:
        session.kill()
        session.wait()


def test_live_lock_stops_the_run_and_dead_lock_is_reclaimed(tmp_path):
    """covers: AI-10"""
    bin_dir = tmp_path / "bin"
    make_stub(bin_dir, "agy")
    log = tmp_path / "calls.log"
    home = tmp_path / "home"
    lock = home / ".cache" / "update-ai.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")
    result = run_update(home, [bin_dir], log, "--allow-interrupt")
    assert result.returncode == 1
    assert updates(log) == []
    assert "already running" in (result.stdout + result.stderr)

    finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    (lock / "pid").write_text(finished.stdout.strip(), encoding="utf-8")
    result = run_update(home, [bin_dir], log, "--allow-interrupt")
    assert result.returncode == 0, result.stdout + result.stderr
    assert updates(log) == ["agy update"]
    assert not lock.exists()


@pytest.mark.parametrize("only", [["--only", "claude,agy"], ["--only", "claude", "--only", "agy"], ["claude", "agy"]])
def test_only_updates_the_chosen_targets(tmp_path, only):
    """covers: AI-11"""
    bin_dir = tmp_path / "bin"
    for name in ("claude", "agy", "herdr"):
        make_stub(bin_dir, name)
    log = tmp_path / "calls.log"
    result = run_update(tmp_path / "home", [bin_dir], log, "--allow-interrupt", *only)
    assert result.returncode == 0, result.stdout + result.stderr
    done = updates(log)
    assert "claude update" in done and "agy update" in done
    assert not any(c.startswith("herdr") for c in done), done


def test_unknown_only_target_is_rejected(tmp_path):
    """covers: AI-11"""
    log = tmp_path / "calls.log"
    result = run_update(tmp_path / "home", [], log, "--only", "nope")
    assert result.returncode == 2
    assert updates(log) == []


def test_one_failure_does_not_stop_the_rest_and_exits_1(tmp_path):
    """covers: AI-12"""
    bin_dir = tmp_path / "bin"
    make_stub(bin_dir, "claude", fail_on="update")
    make_stub(bin_dir, "agy")
    log = tmp_path / "calls.log"
    result = run_update(tmp_path / "home", [bin_dir], log, "--allow-interrupt")
    assert result.returncode == 1
    assert "agy update" in updates(log)
    table = result.stdout[result.stdout.rindex("Results"):]
    assert any("claude" in line and "failed" in line for line in table.splitlines()), table
    assert any("agy" in line and "failed" not in line for line in table.splitlines()), table


def npm_layout(root, pkg, name, npm_tag):
    """실제 npm 전역 설치 모양: POSIX 는 <prefix>/bin/<name> -> ../lib/node_modules/<pkg>/cli.js,
    Windows 는 <prefix>\\<name>.cmd 셔임 옆에 node_modules\\<pkg>. npm 스텁은 PATH 의 첫 항목도 남긴다."""
    if WINDOWS:
        bin_dir = root
        (root / "node_modules" / pkg).mkdir(parents=True)
        make_stub(root, name)
        (root / "npm.cmd").write_text(
            '@echo off\r\nfor /f "delims=;" %%a in ("%PATH%") do >>"%STUB_LOG%" echo ' + npm_tag + ' path-head %%a\r\n'
            '>>"%STUB_LOG%" echo ' + npm_tag + ' %*\r\nexit /b 0\r\n', encoding="utf-8")
    else:
        bin_dir = root / "bin"
        package = root / "lib" / "node_modules" / pkg
        make_stub(package, "cli.js")
        bin_dir.mkdir(parents=True)
        (bin_dir / name).symlink_to(os.path.relpath(package / "cli.js", bin_dir))
        npm = bin_dir / "npm"
        npm.write_text('#!/bin/sh\necho "' + npm_tag + ' path-head ${PATH%%:*}" >> "$STUB_LOG"\n'
                       'echo "' + npm_tag + ' $*" >> "$STUB_LOG"\n', encoding="utf-8")
        npm.chmod(0o755)
    return bin_dir


def test_npm_harness_is_updated_with_the_npm_that_installed_it(tmp_path):
    """covers: AI-13"""
    other = tmp_path / "other-node" / "bin"
    make_stub(other, "npm", tag="other-npm")
    prefix_bin = npm_layout(tmp_path / "node-prefix", "@google/gemini-cli", "gemini", "prefix-npm")
    log = tmp_path / "calls.log"
    # 다른 node 의 npm 이 PATH 에서 먼저 보이게 둔다.
    result = run_update(tmp_path / "home", [other, prefix_bin], log, "--allow-interrupt", "--only", "gemini")
    assert result.returncode == 0, result.stdout + result.stderr
    done = updates(log)
    assert "prefix-npm install -g @google/gemini-cli@latest" in done, done
    assert not any(c.startswith("other-npm") for c in done), done
    # npm 은 자기를 돌리는 node 의 전역 경로에 설치한다. 그 npm 의 node 가 PATH 맨 앞이어야 한다.
    heads = [c.split(" path-head ", 1)[1] for c in done if " path-head " in c]
    assert heads and all(os.path.normcase(h) == os.path.normcase(str(prefix_bin)) for h in heads), done


def test_npm_next_to_a_binary_that_npm_did_not_install_is_not_used(tmp_path):
    """covers: AI-13"""
    # brew 의 node 처럼 같은 bin 에 npm 이 있어도, 패키지가 그 npm 의 node_modules 에 없으면 npm 설치가 아니다.
    bin_dir = tmp_path / "bin"
    make_stub(bin_dir, "gemini")
    make_stub(bin_dir, "npm")
    log = tmp_path / "calls.log"
    result = run_update(tmp_path / "home", [bin_dir], log, "--allow-interrupt", "--only", "gemini")
    assert not any(c.startswith("npm install") for c in updates(log)), updates(log)
    assert "skipped" in result.stdout, result.stdout


sys.path.insert(0, str(ROOT / "res" / "update"))
import update_ai  # noqa: E402

CATALOG = {e["id"]: e for e in update_ai.json.loads(update_ai.CATALOG.read_text(encoding="utf-8"))}


@pytest.mark.parametrize("target, args, expected", [
    ("claude", "claude --resume", True),
    ("claude", "/home/u/.local/bin/claude", True),
    ("claude", "bash scripts/update-ai.sh claude", False),
    ("claude", r"C:\Users\u\AppData\Local\AnthropicClaude\app-1.0\claude.exe", False),
    ("codex", "node /n/lib/node_modules/@openai/codex/bin/codex.js", True),
    ("codex", "/h/.codex/packages/app-server-daemon/releases/x/bin/codex app-server --listen unix://", False),
    ("gemini", "node /n/lib/node_modules/@google/gemini-cli/bundle/gemini.js", True),
    ("grok", "qemu-aarch64-static -cpu max /h/.grok/downloads/grok-linux-aarch64 chat", True),
    ("herdr", "/h/.local/bin/herdr server", False),
    ("herdr", "herdr", True),
    ("codegraph", "node /n/bin/codegraph serve --mcp", False),
    ("codegraph", "codegraph", True),
    ("claude", r'C:\WINDOWS\system32\cmd.exe /c ""C:\t\bin\claude.cmd" --sleep"', True),
])
def test_session_detection(target, args, expected):
    """covers: AI-56"""
    assert update_ai.runs_target(args, CATALOG[target]) is expected


def test_brew_update_keeps_the_old_version_for_running_sessions(monkeypatch):
    """covers: AI-62"""
    seen = []
    monkeypatch.setattr(update_ai, "run_step", lambda label, argv, **kw: seen.append((argv, kw.get("env"))) or True)
    for method in ("brew", "brew_cask"):
        broke, _, skipped = update_ai.update_target(CATALOG["opencode"], "/opt/homebrew/bin/opencode", method, "opencode")
        assert broke == [] and skipped is None
    assert [argv[:2] for argv, _ in seen] == [["brew", "upgrade"], ["brew", "upgrade"]]
    assert all(env and env.get("HOMEBREW_NO_INSTALL_CLEANUP") == "1" for _, env in seen), seen


def test_an_install_newer_than_the_latest_release_counts_as_up_to_date(monkeypatch, capsys):
    """covers: AI-65 — --check 와 갱신 뒤 검사 모두 설치본이 최신보다 새로우면 실패로 보지 않는다."""
    import argparse

    assert update_ai.is_current("9.9.10", "9.9.9") and update_ai.is_current("9.9.9", "9.9.9")
    assert not update_ai.is_current("9.9.8", "9.9.9") and not update_ai.is_current("", "9.9.9")
    monkeypatch.setattr(update_ai, "locations", lambda name: ["/h/.local/bin/herdr"])
    monkeypatch.setattr(update_ai, "method_for", lambda entry, path: ("default", ["herdr", "update"]))
    monkeypatch.setattr(update_ai, "version_of", lambda path: "9.9.10")
    monkeypatch.setattr(update_ai, "latest_of", lambda *a: "9.9.9")
    monkeypatch.setattr(update_ai, "process_table", lambda: [])
    monkeypatch.setattr(update_ai, "run_step", lambda *a, **kw: True)
    args = argparse.Namespace(check=True, allow_interrupt=False)
    assert update_ai.run_all([CATALOG["herdr"]], ["herdr"], args) == 0
    assert "up to date" in capsys.readouterr().out
    args.check = False
    assert update_ai.run_all([CATALOG["herdr"]], ["herdr"], args) == 0
    assert "latest is" not in capsys.readouterr().out


@pytest.mark.parametrize("args", [["orca"], ["Orca"], ["--only", "ORCA"], ["--check", "--only", "claude,orca"]])
def test_update_ai_orca_points_to_jarvis_and_updates_nothing(tmp_path, args):
    """covers: AI-66 — Orca 는 jarvis 가 관리한다. 안내만 내고 exit 1 로 끝나며 아무것도 부르지 않는다."""
    bin_dir = tmp_path / "bin"
    make_stub(bin_dir, "orca")
    make_stub(bin_dir, "claude")
    log = tmp_path / "calls.log"
    home = tmp_path / "home"
    result = run_update(home, [bin_dir], log, *args)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Orca is now managed by jarvis; ai-setup no longer installs, updates or removes it. " \
        "From a jarvis checkout run: python3 -m jarvis module orca plan --json " \
        "(uninstall: module orca uninstall --json --dry-run)." in result.stderr
    assert calls(log) == []
    assert list(home.iterdir()) == []


def test_plain_update_ai_does_not_list_orca(tmp_path):
    """covers: AI-66 — 대상을 주지 않은 update-ai 는 Orca 를 대상으로 보지 않는다."""
    bin_dir = tmp_path / "bin"
    make_stub(bin_dir, "orca")
    log = tmp_path / "calls.log"
    result = run_update(tmp_path / "home", [bin_dir], log, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "orca" not in result.stdout.lower()
    assert calls(log) == []


def test_codegraph_catalog_updates_through_npm_without_an_upgrade_command():
    """npm 탐지가 실패해도 update 배열이 codegraph upgrade 로 떨어지면 안 된다."""
    assert "graft" not in CATALOG
    assert "trail" not in CATALOG
    raw = update_ai.CATALOG.read_text(encoding="utf-8").lower()
    assert "graft" not in raw
    assert "trail" not in raw
    entry = CATALOG["codegraph"]
    assert entry["packages"]["npm"] == "@colbymchenry/codegraph"
    assert not entry.get("update")
    ignore = entry["detect"]["sessions"]["ignore"]
    assert any("serve --mcp" in marker for marker in ignore)


# ── codex 관리형 app-server 데몬 동기화 ─────────────────────────────────
# 데몬 상태를 파일로 흉내 내는 codex 스텁. update 는 CLI 를 2.0.0 으로 올리고, 공식 경로
# (daemon update --yes)는 고정된 데몬을 옮기지 못하며, --from-cli 는 데몬을 CLI 버전에 맞춘다.
CODEX_STUB = r'''
import json, os, sys, time
state_path = os.environ["CODEX_STATE"]
state = json.load(open(state_path))
args = " ".join(sys.argv[1:])
if sys.argv[1:2] == ["--sleep-listen"]:
    time.sleep(30)
    sys.exit(0)
if args != "--version":
    with open(os.environ["STUB_LOG"], "a") as log:
        log.write("codex " + args + "\n")
if args == "--version":
    print("codex-cli " + state["cli"])
elif args == "update":
    state["cli"] = "2.0.0"
elif args == "app-server daemon version":
    print(json.dumps({"cliVersion": state["cli"], "managedCodexVersion": state["managed"],
                      "appServerVersion": state["srv"], "status": state["status"]}))
elif args == "app-server daemon update --yes" and state.get("pin_only"):
    sys.exit(2)
elif args == "app-server daemon update --from-cli --yes":
    state["managed"] = state["cli"]
    if state["status"] == "running":
        state["srv"] = state["cli"]
elif args == "app-server daemon start":
    state["status"], state["srv"] = "running", state["managed"]
json.dump(state, open(state_path, "w"))
'''


def make_codex(bin_dir, state_path, **state):
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex.py"
    script.write_text(CODEX_STUB, encoding="utf-8")
    state_path.write_text(json.dumps(dict({"cli": "1.0.0", "managed": "1.0.0", "srv": "", "status": "stopped"}, **state)), encoding="utf-8")
    if WINDOWS:
        path = bin_dir / "codex.cmd"
        path.write_text('@"' + sys.executable + '" "%~dp0codex.py" %*\r\n', encoding="utf-8")
    else:
        path = bin_dir / "codex"
        path.write_text("#!" + sys.executable + "\n" + CODEX_STUB, encoding="utf-8")
        path.chmod(0o755)
    return path


def run_codex(tmp_path, *args, **state):
    bin_dir = tmp_path / "bin"
    state_path = tmp_path / "codex-state.json"
    make_codex(bin_dir, state_path, **state)
    log = tmp_path / "calls.log"
    os.environ["CODEX_STATE"] = str(state_path)
    try:
        result = run_update(tmp_path / "home", [bin_dir], log, "--only", "codex", *args)
    finally:
        del os.environ["CODEX_STATE"]
    return result, updates(log), json.loads(state_path.read_text(encoding="utf-8"))


def test_pinned_daemon_is_moved_to_the_new_cli(tmp_path):
    result, done, state = run_codex(tmp_path, "--allow-interrupt")
    assert result.returncode == 0, result.stdout
    assert done.index("codex app-server daemon update --yes") < done.index("codex app-server daemon update --from-cli --yes"), done
    assert state["managed"] == "2.0.0"


def test_daemon_is_pinned_when_the_plain_update_is_rejected(tmp_path):
    # codex 0.166 은 고정된 데몬에 --from-cli 없는 update 를 rc=2 로 거부한다.
    result, done, state = run_codex(tmp_path, "--allow-interrupt", pin_only=True)
    assert result.returncode == 0, result.stdout
    assert "codex app-server daemon update --from-cli --yes" in done, done
    assert state["managed"] == "2.0.0"


def test_running_daemon_is_left_alone_without_allow_interrupt(tmp_path, capsys, monkeypatch):
    # 개발 장비에는 진짜 codex 세션이 돌고 있어, --allow-interrupt 없이 전체를 돌리면 세션 보호에
    # 먼저 걸린다. 데몬 단계만 직접 부른다.
    state_path = tmp_path / "codex-state.json"
    stub = make_codex(tmp_path / "bin", state_path, cli="2.0.0", status="running", srv="1.0.0")
    log = tmp_path / "calls.log"
    monkeypatch.setenv("CODEX_STATE", str(state_path))
    monkeypatch.setenv("STUB_LOG", str(log))
    broke, warnings = update_ai.codex_daemon_sync(str(stub), allow_interrupt=False)
    assert broke == [] and warnings == ["daemon-deferred"]
    assert not any("daemon update" in c for c in updates(log)), updates(log)
    assert "--allow-interrupt" in capsys.readouterr().out
    assert json.loads(state_path.read_text(encoding="utf-8"))["managed"] == "1.0.0"


def test_running_daemon_is_restarted_on_the_new_cli_with_allow_interrupt(tmp_path):
    result, done, state = run_codex(tmp_path, "--allow-interrupt", status="running", srv="1.0.0")
    assert result.returncode == 0, result.stdout
    assert state["managed"] == "2.0.0" and state["srv"] == "2.0.0" and state["status"] == "running"


def test_daemon_newer_than_the_cli_is_kept(tmp_path):
    result, done, state = run_codex(tmp_path, "--allow-interrupt", managed="3.0.0")
    assert not any("daemon update" in c for c in done), done
    assert state["managed"] == "3.0.0"
    assert result.returncode == 1  # 부분 실패로 알린다(CLI 배포가 따라오면 다시 실행)


def test_unmanaged_app_server_blocks_the_codex_update(tmp_path):
    bin_dir = tmp_path / "bin"
    state_path = tmp_path / "codex-state.json"
    stub = make_codex(bin_dir, state_path)
    log = tmp_path / "calls.log"
    env = env_for(tmp_path / "home", [bin_dir], log)
    env["CODEX_STATE"] = str(state_path)
    # 원격 세션이 직접 띄운 app-server 흉내. 명령줄에 app-server --listen 이 있고 --managed-daemon 은 없다.
    script = bin_dir / "codex.py"
    server = subprocess.Popen([sys.executable, str(script), "--sleep-listen", "app-server", "--listen", "ws://x"],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        os.environ["CODEX_STATE"] = str(state_path)
        try:
            result = run_update(tmp_path / "home", [bin_dir], log, "--only", "codex", "--allow-interrupt")
        finally:
            del os.environ["CODEX_STATE"]
        assert result.returncode == 1, result.stdout
        assert "codex update" not in updates(log)
        assert "unmanaged" in result.stdout
        assert str(server.pid) in result.stdout
    finally:
        server.kill()
        server.wait()
    assert stub.exists()


def test_check_reports_the_daemon_rows(tmp_path):
    result, done, _ = run_codex(tmp_path, "--check", managed="0.9.0", status="running", srv="0.9.0")
    assert result.returncode == 0, result.stdout
    assert not any("update" in c for c in done), done
    assert "codex-daemon" in result.stdout and "codex-server" in result.stdout


def test_codex_latest_is_read_from_its_github_release_tag(tmp_path, monkeypatch):
    """covers: HARNESS-1
    standalone codex 는 레지스트리가 없다. Codex TUI 처럼 GitHub 최신 릴리스의 rust-v<판> 태그를 읽는다."""
    release = tmp_path / "release.json"
    release.write_text(json.dumps({"tag_name": "rust-v0.166.0"}), encoding="utf-8")
    monkeypatch.setattr(update_ai, "CODEX_LATEST", release.as_uri())
    entry = {"id": "codex"}
    assert update_ai.latest_of(entry, "/bin/codex", "default", None) == "0.166.0"
    release.write_text(json.dumps({"tag_name": "nightly"}), encoding="utf-8")
    assert update_ai.latest_of(entry, "/bin/codex", "default", None) == ""
    monkeypatch.setattr(update_ai, "CODEX_LATEST", (tmp_path / "missing.json").as_uri())
    assert update_ai.latest_of(entry, "/bin/codex", "default", None) == ""


def test_a_trailing_full_stop_is_not_part_of_the_version(tmp_path):
    """covers: HARNESS-4
    copilot 은 "GitHub Copilot CLI 1.0.105." 를 낸다. 마침표가 붙으면 최신 판과 같아도 갱신 실패로 남는다."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if WINDOWS:
        stub = bin_dir / "copilot.cmd"
        stub.write_text("@echo GitHub Copilot CLI 1.0.105.\r\n", encoding="utf-8")
    else:
        stub = bin_dir / "copilot"
        stub.write_text("#!/bin/sh\necho 'GitHub Copilot CLI 1.0.105.'\n", encoding="utf-8")
        stub.chmod(0o755)
    assert update_ai.version_of(str(stub)) == "1.0.105"
    assert update_ai.is_current(update_ai.version_of(str(stub)), "1.0.105")


def test_windows_codex_runs_the_official_installer_without_the_profile(monkeypatch):
    """covers: HARNESS-4
    사용자 PowerShell 프로필이 있으면 `codex update` 가 띄운 공식 설치기가 StrictMode 에서 멈춘다(kgolf 실측)."""
    calls = []
    monkeypatch.setattr(update_ai.os, "name", "nt")
    monkeypatch.setattr(update_ai, "run_step", lambda label, argv, **kw: calls.append(argv) or True)
    monkeypatch.setattr(update_ai, "codex_daemon_sync", lambda path, allow: ([], []))
    entry = {"id": "codex", "update": ["codex", "update"]}
    update_ai.update_target(entry, r"C:\bin\codex.exe", "default", ["codex", "update"])
    assert calls[0][:2] == ["powershell", "-NoProfile"]
    assert "chatgpt.com/codex/install.ps1" in calls[0][-1]


load_tests = collect(globals())
