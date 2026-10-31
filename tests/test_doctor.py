"""doctor: read-only checks in isolated homes with stub executables on PATH."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "jarvis" / "modules" / "doctor" / "doctor.py"
CATALOG = json.loads((DOCTOR.parent / "catalog.json").read_text(encoding="utf-8"))
WINDOWS = os.name == "nt"
PLATFORM = "windows" if WINDOWS else ("macos" if sys.platform == "darwin" else "linux")
SECRET = "sk-test-0123456789abcdef"


def make_stub(bin_dir, name, version_line):
    """PATH 앞에 둘 가짜 실행 파일. version_line 을 찍는다.

    실제 gemini·opencode 처럼 --version 만 불러도 홈에 파일을 쓰는 하네스를 흉내 낸다.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        path = bin_dir / (name + ".cmd")
        path.write_text('@type nul > "%USERPROFILE%\\.probe-wrote-here"\r\n@echo ' + version_line + "\r\n", encoding="utf-8")
    else:
        path = bin_dir / name
        path.write_text("#!/bin/sh\n: > \"$HOME/.probe-wrote-here\"\necho '" + version_line + "'\n", encoding="utf-8")
        path.chmod(0o755)
    return path


def run_doctor(home, path_dirs):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    # 시스템 PATH 를 섞으면 실제 설치된 하네스가 보여 결과가 장비마다 달라진다.
    # Windows 는 .cmd 스텁을 돌릴 cmd.exe 가 있어야 하므로 System32 만 남긴다.
    keep = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")] if WINDOWS else []
    env["PATH"] = os.pathsep.join([str(d) for d in path_dirs] + keep)
    return subprocess.run(
        # setup.sh/ps1 과 같이 -B 로 돌린다(macOS 의 Xcode python 은 바이트코드를 ~/Library/Caches 에 쓴다).
        [sys.executable, "-B", "-X", "utf8", str(DOCTOR)],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
    )


def snapshot(root):
    return {
        str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }


def harness(entry_id):
    return next(e for e in CATALOG if e["id"] == entry_id)


def test_doctor_writes_nothing_under_home(tmp_path):
    """covers: AI-1"""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"hooks": {}}', encoding="utf-8")
    (home / ".agents" / "skills" / "stray").mkdir(parents=True)
    (home / ".agents" / ".skill-lock.json").write_text('{"version": 3, "skills": {}}', encoding="utf-8")
    bin_dir = tmp_path / "bin"
    make_stub(bin_dir, "claude", "9.9.9 (Claude Code)")
    before = snapshot(home)
    result = run_doctor(home, [bin_dir])
    assert result.returncode in (0, 1), result.stderr
    assert snapshot(home) == before


def test_reports_installed_harness_version_and_install_hint_for_missing(tmp_path):
    """covers: AI-2"""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    make_stub(bin_dir, "claude", "9.9.9 (Claude Code)")
    result = run_doctor(home, [bin_dir])
    assert result.returncode in (0, 1), result.stderr
    assert any("claude" in line and "9.9.9" in line for line in result.stdout.splitlines()), result.stdout
    codex_hint = harness("codex")["install"][PLATFORM][0]["cmd"]
    assert codex_hint in result.stdout


def test_duplicate_harness_on_path_is_reported_with_the_winner(tmp_path):
    """covers: AI-3"""
    home = tmp_path / "home"
    home.mkdir()
    first = make_stub(tmp_path / "first", "claude", "2.0.0 (Claude Code)")
    second = make_stub(tmp_path / "second", "claude", "1.0.0 (Claude Code)")
    result = run_doctor(home, [tmp_path / "first", tmp_path / "second"])
    assert result.returncode == 1
    dup = [line for line in result.stdout.splitlines() if "duplicate" in line.lower()]
    assert dup, result.stdout
    text = "\n".join(dup)
    assert str(first.parent) in text and str(second.parent) in text
    assert text.index(str(first.parent)) < text.index(str(second.parent))


def test_same_directory_twice_on_path_is_not_a_duplicate(tmp_path):
    """covers: AI-3"""
    home = tmp_path / "home"
    home.mkdir()
    make_stub(tmp_path / "bin", "claude", "2.0.0 (Claude Code)")
    result = run_doctor(home, [tmp_path / "bin", tmp_path / "bin"])
    assert any("claude" in line and "2.0.0" in line for line in result.stdout.splitlines()), result.stdout
    assert "duplicate" not in result.stdout.lower()


def test_retired_hook_still_wired_in_another_harness_is_drift(tmp_path):
    """covers: AI-4"""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".claude-code-setup.json").write_text(json.dumps({
        "version": 1,
        "hook_files": ["danger_guard.py", "secrets_guard.py", "shims/rm"],
        "retired_hooks": [],
    }), encoding="utf-8")
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text(
        '[[hooks.PreToolUse]]\ncommand = "python3 ~/.claude/hooks/secrets_guard.py"\n', encoding="utf-8")
    result = run_doctor(home, [])
    assert result.returncode == 1
    drift = [line for line in result.stdout.splitlines() if "secrets_guard.py" in line]
    assert drift and any("config.toml" in line for line in drift), result.stdout


def test_current_hook_is_not_drift(tmp_path):
    """covers: AI-4"""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".claude-code-setup.json").write_text(json.dumps({
        "version": 1, "hook_files": ["danger_guard.py"], "retired_hooks": [],
    }), encoding="utf-8")
    (home / ".claude" / "settings.json").write_text(
        '{"hooks": {"PreToolUse": [{"hooks": [{"command": "python3 danger_guard.py"}]}]}}', encoding="utf-8")
    result = run_doctor(home, [])
    assert "danger_guard.py" not in result.stdout


def test_plaintext_secret_reports_location_and_key_but_never_the_value(tmp_path):
    """covers: AI-5"""
    home = tmp_path / "home"
    (home / ".grok").mkdir(parents=True)
    (home / ".grok" / "config.toml").write_text(
        '[mcp_servers.ha]\nurl = "http://ha.local"\n[mcp_servers.ha.headers]\nAuthorization = "Bearer ' + SECRET + '"\n',
        encoding="utf-8")
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text(
        '[mcp_servers.x]\nenv = { TYPESAFE_API_KEY = "' + SECRET + '", FROM_ENV = "${FROM_ENV}" }\n', encoding="utf-8")
    result = run_doctor(home, [])
    assert result.returncode == 1
    assert SECRET not in result.stdout and SECRET not in result.stderr
    secret_lines = [line for line in result.stdout.splitlines() if "secret" in line.lower()]
    text = "\n".join(secret_lines)
    assert "Authorization" in text and "TYPESAFE_API_KEY" in text
    assert "FROM_ENV" not in text


def test_skill_outside_lock_is_reported_and_kept(tmp_path):
    """covers: AI-6"""
    home = tmp_path / "home"
    skills = home / ".agents" / "skills"
    (skills / "locked").mkdir(parents=True)
    (skills / "stray").mkdir()
    (skills / "stray" / "SKILL.md").write_text("x", encoding="utf-8")
    (home / ".agents" / ".skill-lock.json").write_text(
        json.dumps({"version": 3, "skills": {"locked": {"source": "o/r"}}}), encoding="utf-8")
    result = run_doctor(home, [])
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert any("stray" in line for line in lines)
    assert not any("locked" in line and "not in" in line for line in lines)
    assert (skills / "stray" / "SKILL.md").exists()


def test_a_skill_link_that_cannot_be_followed_is_reported_without_stopping(tmp_path, monkeypatch):
    """Windows 는 신뢰하지 않는 탑재 지점을 가리키는 junction 에 WinError 448 을 낸다(실측). 보고 전체가 멈추면 안 된다."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("doctor_under_test", DOCTOR)
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)
    skills = tmp_path / ".agents" / "skills"
    (skills / "blocked").mkdir(parents=True)
    (skills / "stray").mkdir()
    real = doctor.os.path.realpath

    def realpath(path, *args, **kwargs):
        if Path(path).name == "blocked":
            raise OSError(448, "untrusted mount point")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(doctor.os.path, "realpath", realpath)
    warnings = []
    doctor.check_skills(tmp_path, warnings.append)
    assert any("blocked" in w and "cannot inspect" in w for w in warnings), warnings
    assert any("stray" in w and "not in" in w for w in warnings), warnings


def test_keys_that_only_mention_a_secret_word_are_not_reported(tmp_path):
    """covers: AI-5"""
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        '[mcp_servers.ha]\nbearer_token_env_var = "HA_TOKEN_VARIABLE"\ntokenizer = "cl100k_base"\n', encoding="utf-8")
    (home / ".claude.json").write_text('{"claudeCodeFirstTokenDate": "2026-01-01T00:00:00Z"}', encoding="utf-8")
    result = run_doctor(home, [])
    assert "plaintext secret" not in result.stdout, result.stdout


def test_bearer_values_are_reported_whatever_the_key_is_called(tmp_path):
    """covers: AI-5"""
    home = tmp_path / "home"
    (home / ".grok").mkdir(parents=True)
    (home / ".grok" / "config.toml").write_text(
        '[mcp_servers.ha]\nargs = ["mcp-remote", "--header", "Authorization: Bearer ' + SECRET + '"]\n'
        '[mcp_servers.ha.env]\nAUTH = "Bearer ' + SECRET + '"\nFROM_ENV = "Bearer ${HA_TOKEN}"\n',
        encoding="utf-8")
    result = run_doctor(home, [])
    assert result.returncode == 1
    assert SECRET not in result.stdout and SECRET not in result.stderr
    found = [line for line in result.stdout.splitlines() if "plaintext secret" in line]
    assert any(":2 " in line and "Authorization" in line for line in found), result.stdout
    assert any(":4 " in line and "AUTH" in line for line in found), result.stdout
    assert not any(":5 " in line for line in found), result.stdout


def test_hook_the_receipt_lists_as_retired_is_drift(tmp_path):
    """covers: AI-4"""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".claude-code-setup.json").write_text(json.dumps({
        "version": 1, "hook_files": [], "retired_hooks": ["old_guard.py"],
    }), encoding="utf-8")
    (home / ".gemini").mkdir()
    (home / ".gemini" / "settings.json").write_text('{"hooks": {"x": "python3 old_guard.py"}}', encoding="utf-8")
    result = run_doctor(home, [])
    assert result.returncode == 1
    assert any("old_guard.py" in line and "settings.json" in line for line in result.stdout.splitlines()), result.stdout


def test_common_secret_shapes_are_reported_without_values(tmp_path):
    """covers: AI-5"""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        '{"env": {"ANTHROPIC_AUTH_TOKEN": "' + SECRET + '"}}', encoding="utf-8")
    (home / ".claude.json").write_text("\n".join([
        '{"mcpServers": {',
        '"a": {"url": "https://x.example/sse?token=' + SECRET + '"},',
        '"b": {"args": ["--api-key=' + SECRET + '"]},',
        '"c": {"args": ["run", "-e", "GITHUB_TOKEN=' + SECRET + '"]},',
        '"d": {"env": {"AWS_SECRET_ACCESS_KEY": "' + SECRET + '"}},',
        '"e": {"env": {"DATABASE_URL": "postgres://app:' + SECRET + '@db:5432/x"}},',
        '"f": {"env": {"PASSWORD": "p4$sw0rd' + SECRET + '"}}',
        "}}",
    ]), encoding="utf-8")
    result = run_doctor(home, [])
    assert result.returncode == 1
    assert SECRET not in result.stdout and SECRET not in result.stderr
    found = [line for line in result.stdout.splitlines() if "plaintext secret" in line]
    assert any("settings.json:1 " in line and "ANTHROPIC_AUTH_TOKEN" in line for line in found), result.stdout
    for number in range(2, 8):
        assert any(".claude.json:{} ".format(number) in line for line in found), (number, result.stdout)


def test_header_mapped_to_an_environment_variable_name_is_not_reported(tmp_path):
    """covers: AI-5"""
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        '[mcp_servers.x]\nenv_http_headers = { "Authorization" = "MY_SERVICE_TOKEN_VAR" }\n', encoding="utf-8")
    result = run_doctor(home, [])
    assert "plaintext secret" not in result.stdout, result.stdout


load_tests = collect(globals())
