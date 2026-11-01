"""Bundled skills: real installation in isolated homes, ownership and CLI scopes."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "jarvis" / "modules" / "setup"
WORKER = ROOT / "res" / "skills" / "apply.py"
SOURCE = ROOT / "res" / "skills" / "design-lab" / "SKILL.md"
RELATIVE = "design-lab/SKILL.md"
REPORT_RELATIVE = "tailnet-report/SKILL.md"


def installed(home):
    return home / ".claude" / "skills" / RELATIVE


def receipt(home):
    return home / ".claude" / ".claude-code-setup-skills.json"


def claims(home):
    # 영수증의 files 맵을 돌려준다(없으면 빈 맵). 번들 스킬이 여럿이므로 호출부가 항목을 고른다.
    path = receipt(home)
    return json.loads(path.read_text(encoding="utf-8"))["files"] if path.exists() else {}


def run_worker(home, *args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(WORKER), "--home", str(home), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


def test_install_reinstall_uninstall_and_user_files(tmp_path):
    home = tmp_path / "home"
    assert run_worker(home).returncode == 0
    assert installed(home).read_bytes() == SOURCE.read_bytes()
    report = home / ".claude" / "skills" / REPORT_RELATIVE
    assert report.read_bytes() == (ROOT / "res" / "skills" / REPORT_RELATIVE).read_bytes()
    initial_receipt = receipt(home).read_bytes()
    assert run_worker(home).returncode == 0
    assert receipt(home).read_bytes() == initial_receipt
    note = installed(home).with_name("notes.txt")
    note.write_text("user notes", encoding="utf-8")
    other = home / ".claude" / "skills" / "user-skill" / "SKILL.md"
    other.parent.mkdir()
    other.write_text("user skill", encoding="utf-8")
    assert run_worker(home, "--uninstall").returncode == 0
    assert not installed(home).exists()
    assert not report.exists()
    assert not receipt(home).exists()
    assert note.read_text() == "user notes"
    assert other.read_text() == "user skill"


def test_dry_run_does_not_create_home(tmp_path):
    home = tmp_path / "absent"
    result = run_worker(home, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "design-lab" in result.stdout
    assert not home.exists()


def test_uninstall_dry_run_preserves_installed_bytes(tmp_path):
    assert run_worker(tmp_path).returncode == 0
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert run_worker(tmp_path, "--uninstall", "--dry-run").returncode == 0
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("same_content", [False, True])
def test_preexisting_skill_is_not_claimed_or_removed(tmp_path, same_content):
    target = installed(tmp_path)
    target.parent.mkdir(parents=True)
    content = SOURCE.read_bytes() if same_content else b"user skill"
    target.write_bytes(content)
    result = run_worker(tmp_path)
    assert result.returncode == (0 if same_content else 1)
    assert target.read_bytes() == content
    assert RELATIVE not in claims(tmp_path)
    assert run_worker(tmp_path, "--uninstall").returncode == 0
    assert target.read_bytes() == content


def test_edited_managed_skill_is_preserved_on_install_and_uninstall(tmp_path):
    assert run_worker(tmp_path).returncode == 0
    target = installed(tmp_path)
    target.write_text("user edit", encoding="utf-8")
    before = claims(tmp_path)
    manifest = before[RELATIVE]
    # 재설치는 영수증을 그대로 두고, 제거는 손대지 않은 스킬만 빼고 편집된 스킬의 항목을 남긴다.
    for args, expected in [((), before), (("--uninstall",), {RELATIVE: manifest})]:
        result = run_worker(tmp_path, *args)
        assert result.returncode == 1
        assert "preserv" in result.stdout.lower()
        assert target.read_bytes() == b"user edit"
        assert claims(tmp_path) == expected


def test_upgrade_of_unmodified_managed_skill(tmp_path):
    target = installed(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous bundled version")
    receipt(tmp_path).write_text(json.dumps({
        "version": 1, "files": {RELATIVE: hashlib.sha256(target.read_bytes()).hexdigest()},
    }), encoding="utf-8")
    assert run_worker(tmp_path).returncode == 0
    assert target.read_bytes() == SOURCE.read_bytes()
    assert json.loads(receipt(tmp_path).read_text())["files"][RELATIVE] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()


@pytest.mark.parametrize("value", ["{", '{"version": 1, "files": {"../../victim": "x"}}', '{"version": 1, "files": []}'])
def test_invalid_receipt_is_not_overwritten(tmp_path, value):
    receipt(tmp_path).parent.mkdir()
    receipt(tmp_path).write_text(value, encoding="utf-8")
    for args in [(), ("--uninstall",)]:
        assert run_worker(tmp_path, *args).returncode == 1
        assert receipt(tmp_path).read_text() == value
        assert not installed(tmp_path).exists()


def test_linked_skill_directory_is_not_changed(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    target = installed(tmp_path)
    target.parent.parent.mkdir(parents=True)
    if sys.platform == "win32":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(target.parent), str(external)], capture_output=True)
        assert result.returncode == 0
    else:
        target.parent.symlink_to(external, target_is_directory=True)
    result = run_worker(tmp_path)
    assert result.returncode == 1
    assert not list(external.iterdir())
    assert not receipt(tmp_path).exists()


def test_home_reached_through_a_link_still_installs(tmp_path):
    """macOS 의 /var -> /private/var 처럼 홈 위쪽의 링크는 사용자가 만든 링크가 아니다.
    mktemp 홈에서 설치 전체가 'Preserved linked path' 로 실패했다."""
    real = tmp_path / "real"
    real.mkdir()
    home = tmp_path / "home"
    if sys.platform == "win32":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(home), str(real)], capture_output=True)
        assert result.returncode == 0
    else:
        home.symlink_to(real, target_is_directory=True)
    result = run_worker(home)
    assert result.returncode == 0, result.stdout
    assert installed(real).read_bytes() == SOURCE.read_bytes()


def run_setup(home, *args):
    env = dict(os.environ)
    env.update(USERPROFILE=str(home), HOME=str(home), CI="1")
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    # All writable editor/terminal paths live in the same sandbox.
    env.update(APPDATA=str(home / "appdata"), LOCALAPPDATA=str(home / "local"),
               CLAUDE_SETUP_VSCODE_KEYBINDINGS=str(home / "vscode.json"),
               CLAUDE_SETUP_CURSOR_KEYBINDINGS=str(home / "cursor.json"),
               CLAUDE_SETUP_WT_SETTINGS=str(home / "wt.json"))
    home.mkdir(exist_ok=True)
    if sys.platform == "win32":
        command = ["pwsh", "-NoProfile", "-File", str(ROOT / "scripts" / "setup.ps1"), *args]
    else:
        command = ["bash", str(ROOT / "scripts" / "setup.sh"), *args]
    return subprocess.run(command, env=env, cwd=home, capture_output=True, text=True, encoding="utf-8", timeout=180)


def test_dispatcher_skills_scope_and_long_option(tmp_path):
    home = tmp_path / "home"
    for _ in range(2):
        result = run_setup(home, "--skills")
        assert result.returncode == 0, result.stdout + result.stderr
    assert installed(home).read_bytes() == SOURCE.read_bytes()
    assert not (home / ".claude" / "settings.json").exists()
    assert not (home / "AGENTS.md").exists()
    result = run_setup(home, "--skills", "--uninstall")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not installed(home).exists()


@pytest.mark.parametrize("scope", ["--agentsmd", "--keybinds"])
def test_other_scopes_do_not_install_skills(tmp_path, scope):
    home = tmp_path / "home"
    result = run_setup(home, scope, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "design-lab" not in result.stdout
    assert not installed(home).exists()


def test_default_dry_run_includes_skills_without_writes(tmp_path):
    home = tmp_path / "home"
    result = run_setup(home, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "design-lab" in result.stdout
    assert not installed(home).exists()
    assert not receipt(home).exists()



def test_default_real_install_includes_skills(tmp_path):
    result = run_setup(tmp_path / "home", "--no-onboard")
    assert result.returncode == 0, result.stdout + result.stderr
    assert installed(tmp_path / "home").read_bytes() == SOURCE.read_bytes()


@pytest.mark.parametrize("upgrade", [False, True])
def test_receipt_failure_rolls_back_skill_content(tmp_path, monkeypatch, upgrade):
    import runpy

    apply = runpy.run_path(str(WORKER))["apply"]
    namespace = apply.__globals__
    write = namespace["atomic_write"]
    target = installed(tmp_path)
    original = b"previous bundled version" if upgrade else None
    if upgrade:
        target.parent.mkdir(parents=True)
        target.write_bytes(original)
        receipt(tmp_path).write_text(json.dumps({
            "version": 1, "files": {RELATIVE: hashlib.sha256(original).hexdigest()},
        }), encoding="utf-8")
    old_receipt = receipt(tmp_path).read_bytes() if upgrade else None

    def fail_receipt(path, data):
        if path == receipt(tmp_path):
            raise PermissionError("receipt is locked")
        write(path, data)

    monkeypatch.setitem(namespace, "atomic_write", fail_receipt)
    with pytest.raises(PermissionError, match="receipt is locked"):
        apply(tmp_path)
    assert (target.read_bytes() if target.exists() else None) == original
    assert (receipt(tmp_path).read_bytes() if receipt(tmp_path).exists() else None) == old_receipt
    assert not (tmp_path / ".claude" / "skills" / REPORT_RELATIVE).exists()
    monkeypatch.setitem(namespace, "atomic_write", write)
    assert apply(tmp_path) == 0
    assert apply(tmp_path, uninstall=True) == 0
    assert not target.exists()


load_tests = collect(globals())
