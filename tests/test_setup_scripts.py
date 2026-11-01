"""setup.sh 의 '실제 설치' 경로 테스트 (dry-run 이 아닌 쪽). install.sh 부트스트랩은 jarvis 로 옮기지 않았다.

기존 테스트는 --dry-run 과 --help 만 밟았다. 그 두 경로는 macOS 기본 bash 3.2 에서
멀쩡히 통과하는데, 정작 실제 설치는 전부 죽었다:

  * setup.sh 는 인자를 배열로 넘긴다. bash < 4.4 는 `set -u` 에서 빈 배열의 "${arr[@]}"
    확장을 unbound variable 로 취급한다. --dry-run 일 때는 배열이 비어있지 않아 통과하고,
    실제 설치일 때만 죽는다.
  * install.sh 의 EXIT 트랩은 성공해도 exit 1 을 반환했다.
  * 설치를 두 번 하면 매니페스트(설치 영수증)가 빈 값으로 덮어써져 --uninstall 이
    defaultMode 와 권한을 되돌리지 못했다(훅과 함께 jarvis 로 옮긴 회귀 테스트).

그래서 여기서는 항상 non-dry-run 경로를 격리된 HOME 에 대고 실행한다.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "jarvis" / "modules" / "setup"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="POSIX shell script tests (bash required)",
)


@pytest.fixture
def stub_bin(tmp_path):
    """PATH 앞에 끼울 스텁 디렉터리. 실제 휴지통 도구/네트워크를 쓰지 않게 한다."""
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    trash = bin_dir / "trash-put"
    trash.write_text('#!/bin/sh\necho "[stub trash-put] $*"\n', encoding="utf-8")
    trash.chmod(0o755)
    return bin_dir


@pytest.fixture
def sandbox_home(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    return home


def run_script(args, home, stub_bin, cwd=None):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{stub_bin}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        args, cwd=str(cwd or ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )


def settings(home):
    return json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))


def manifest(home):
    return json.loads((home / ".claude" / ".claude-code-setup.json").read_text(encoding="utf-8"))


# --- setup.sh: 실제 설치 경로 (빈 배열 확장 회귀) ---

def test_setup_help_lists_keybinds_and_newline(sandbox_home, stub_bin):
    """covers: Test-15
    setup.sh --help 에 --keybinds 와 --newline 이 보여야 한다.
    """
    proc = run_script(["bash", str(ROOT / "scripts" / "setup.sh"), "--help"], sandbox_home, stub_bin)
    assert proc.returncode == 0, proc.stderr
    assert "--keybinds" in proc.stdout, proc.stdout
    assert "--newline" in proc.stdout, proc.stdout


def test_setup_no_onboard_installs_agentsmd_without_hooks(sandbox_home, stub_bin, tmp_path):
    # 인자 없음은 이제 아무것도 설치하지 않는다(D4, AI-42). 예전 전체 설치는 --no-onboard 다.
    # 훅은 jarvis 가 맡으므로 전체 설치에 들지 않는다(AI-69).
    project = tmp_path / "project"
    project.mkdir()
    proc = run_script(["bash", str(ROOT / "scripts" / "setup.sh"), "--no-onboard"], sandbox_home, stub_bin, cwd=project)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert (project / "AGENTS.md").exists()
    assert not (sandbox_home / ".claude" / "hooks").exists()


def test_setup_uninstall_leaves_the_hooks_and_their_receipt_records_to_jarvis(sandbox_home, stub_bin):
    """covers: AI-69 — 전체 --uninstall 은 훅 파일·settings 의 훅 등록·영수증의 훅 기록을 jarvis 를 위해 그대로 둔다."""
    claude = sandbox_home / ".claude"
    (claude / "hooks" / "shims").mkdir(parents=True)
    (claude / "hooks" / "danger_guard.py").write_text("# installed hook\n", encoding="utf-8")
    (claude / "hooks" / "shims" / "rm").write_text("#!/bin/sh\n", encoding="utf-8")
    hook_settings = {
        "permissions": {"allow": ["Bash"], "defaultMode": "auto"},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3 -X utf8 \"$HOME/.claude/hooks/danger_guard.py\""}]}]},
    }
    (claude / "settings.json").write_text(json.dumps(hook_settings), encoding="utf-8")
    hook_records = {
        "version": 1, "hooks_dir": "~/.claude/hooks", "hook_files": ["danger_guard.py", "shims/rm"],
        "added_allow": ["Bash"], "default_mode_before": None, "default_mode_after": "auto",
        "default_mode_changed": True,
    }
    (claude / ".claude-code-setup.json").write_text(json.dumps(hook_records), encoding="utf-8")
    proc = run_script(["bash", str(ROOT / "scripts" / "setup.sh"), "--uninstall"], sandbox_home, stub_bin)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert (claude / "hooks" / "danger_guard.py").read_text(encoding="utf-8") == "# installed hook\n"
    assert (claude / "hooks" / "shims" / "rm").exists()
    assert settings(sandbox_home) == hook_settings
    assert manifest(sandbox_home) == hook_records


def test_setup_dry_run_writes_nothing(sandbox_home, stub_bin):
    proc = run_script(["bash", str(ROOT / "scripts" / "setup.sh"), "--skills", "--dry-run"], sandbox_home, stub_bin)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (sandbox_home / ".claude" / "settings.json").exists()
    assert not (sandbox_home / ".claude" / "skills").exists()


load_tests = collect(globals())
