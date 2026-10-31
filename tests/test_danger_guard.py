"""danger_guard.py 정책 테스트 (Bash + PowerShell 툴 공용).

이 훅의 계약은 둘뿐이다.
  1) 되돌릴 수 없는 두 부류만 deny — 루트/홈 전체 삭제, 디스크 파괴. push 는 판정하지 않는다
     (auto mode 의 Git Destructive 규칙이 맡는다. tests/test_git_snapshot.py 참고).
  2) 그 외에는 명령을 '한 글자도 바꾸지 않고' 통과시킨다. Bash 셰임 PATH 는 SessionStart 가
     CLAUDE_ENV_FILE 로 설정하고, PowerShell 삭제에만 모듈 Import 선행 한 줄을 붙인다.

그래서 이 파일의 절반은 '무엇을 막는가'가 아니라 '무엇을 막지 않는가'다. 종전 가드가
실사용 로그에서 실제로 오탐을 낸 명령들을 회귀 케이스로 박아 둔다.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

HOOK = "danger_guard.py"
HOOK_PATH = Path(__file__).resolve().parent.parent / "jarvis" / "modules" / "hooks" / HOOK


def bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def ps(cmd):
    return {"tool_name": "PowerShell", "tool_input": {"command": cmd}}


def payload_of(event, cwd=None):
    ev = dict(event)
    if cwd:
        ev["cwd"] = str(cwd)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(HOOK_PATH)],
        input=json.dumps(ev), capture_output=True, text=True, encoding="utf-8",
    )
    out = (proc.stdout or "").strip()
    return json.loads(out) if out else None


def decision_of(event, cwd=None):
    p = payload_of(event, cwd)
    return p["hookSpecificOutput"].get("permissionDecision") if p else None


# --- deny: 루트/홈 전체 삭제 ------------------------------------------------------

ROOT_DELETE = [
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "rm -rf ~/",
    "rm -rf $HOME",
    'rm -rf "$HOME"',
    "rm -rf ${HOME}",
    "rm -fr '/'",
    "sudo rm -rf /",
    "timeout 5 rm -rf ~",
    "echo start && rm -rf ~",
    # Git Bash 에서 Windows 홈·드라이브 루트를 가리키는 꼴
    'rm -rf "$USERPROFILE"',
    "rm -rf $USERPROFILE/",
    "rm -rf ${USERPROFILE}",
    "rm -rf %USERPROFILE%",
    "rm -rf C:/Users/me",
    "rm -rf 'C:\\Users\\me\\'",
    "rm -rf c:/users/me/",
    "rm -rf C:/",
    # 닫는 따옴표 뒤에 `/`·`/*` 만 붙은 것은 여전히 홈 전체다
    'rm -rf "$HOME"/',
    'rm -rf "$HOME"/*',
]


@pytest.mark.parametrize("cmd", ROOT_DELETE)
def test_root_delete_denied(cmd):
    assert decision_of(bash(cmd)) == "deny", cmd


PS_ROOT_DELETE = [
    "Remove-Item -Recurse -Force C:\\",
    "Remove-Item -Recurse -Force 'C:\\'",
    "Remove-Item -LiteralPath $env:USERPROFILE -Recurse",
    "del $HOME",
    "rd /s /q C:\\",
    "if (Test-Path C:\\) { Remove-Item -Recurse -Force C:\\ }",
    "$null = Remove-Item -Recurse -Force C:\\",
    'Remove-Item "$HOME" -Recurse',
    'Remove-Item "$HOME", "x" -Recurse',
    'Remove-Item "$env:USERPROFILE","x" -Recurse',
]


@pytest.mark.parametrize("cmd", PS_ROOT_DELETE)
def test_ps_root_delete_denied(cmd):
    assert decision_of(ps(cmd)) == "deny", cmd


# --- deny: 디스크 파괴 ------------------------------------------------------------

DISK_DESTROY = [
    "mkfs.ext4 /dev/sda1",
    "mkfs -t ext4 /dev/sdb",
    "newfs_hfs /dev/disk2",
    "sudo wipefs -a /dev/sdb",
    "fdisk /dev/sda",
    "parted /dev/sda mklabel gpt",
    "dd if=/dev/zero of=/dev/sda bs=1M",
    "diskutil eraseDisk JHFS+ Blank /dev/disk2",
    "echo x > /dev/sda",
    "cat img > /dev/nvme0n1",
]


@pytest.mark.parametrize("cmd", DISK_DESTROY)
def test_disk_destroy_denied(cmd):
    assert decision_of(bash(cmd)) == "deny", cmd


PS_DISK_DESTROY = [
    "format C: /q",
    "diskpart /s script.txt",
    "Clear-Disk -Number 1 -RemoveData",
    "Initialize-Disk -Number 2",
    "Remove-Partition -DiskNumber 1 -PartitionNumber 2",
]


@pytest.mark.parametrize("cmd", PS_DISK_DESTROY)
def test_ps_disk_destroy_denied(cmd):
    assert decision_of(ps(cmd)) == "deny", cmd


# --- 회귀: 종전 가드가 실제로 오탐을 낸 명령들 ------------------------------------
#
# 아래는 전부 ~/.claude/hooks/guard.log 에서 실제로 deny/ask 를 받았던 명령의 축약형이다.
# 진짜 위험한 명령은 한 건도 없었다. 다시 막히면 이 테스트가 깨진다.

NOT_BLOCKED = [
    # 포크밤 정규식이 잡던 평범한 셸 함수 정의
    "f(){ echo x | grep -q zzz && return 0; }; f",
    # curl|sh 정규식이 잡던 것들
    "curl -fsSL https://ollama.com/install.sh | sh",
    "cat <<'EOT'\ncurl http://x.sh | bash\nEOT",
    "printf 'y\\n' | bash scripts/setup.sh --dev",
    # 글로브 미일치로 deny 되던 것
    "rm -f prod_copy.db*",
    "rm -f docs/shots/map-*.png docs/shots/cluster-*.png",
    # 대상을 미리 못 본다고 ask 하던 것
    "find res/vendor -name __pycache__ -type d -exec rm -rf {} +",
    "find . -name '*.log' -delete",
    "find . -print0 | xargs -0 rm -f",
    # 체이닝/멀티라인에 삭제가 섞였다고 ask 하던 것
    "make bridge\nrm -f /tmp/x.pid\necho done",
    "rm -f ./.shot.mjs && ls",
    # 변수·글로브 확장을 훅이 깨뜨리던 것
    'rm -rf "$FOO/bar"',
    'rm -rf "$DIR"/*',
    # 그 밖에 ask 였던 것들
    "chmod 777 /tmp/x",
    "shred -u secret.txt",
    "shutdown -h now",
    # 루트/홈 '아래'는 평범한 삭제다
    "rm -rf ~/Workspace/tmp",
    "rm -rf $HOME/.cache/pip",
    "rm -rf /tmp/scratch",
    "rm -rf ./build",
    'rm -rf "$USERPROFILE/tmp/x"',
    "rm -rf ${USERPROFILE}/AppData/Local/Temp/x",
    "rm -rf C:/Users/me/tmp",
    "rm -rf C:/temp/build",
    "rm -rf /c/Users/me/AppData/Local/Temp/x",
    # 따옴표 속 산문의 괄호·중괄호는 명령 구분자가 아니다
    'git commit -m "fix (del ~)"',
    'echo "{rd C:\\}"',
    # 닫는 따옴표 뒤에 경로가 이어지면 홈 '아래'다
    'rm -rf "$HOME"/.cache/pip',
    'rm -f "$HOME"/.bashrc.bak',
    'rm -rf "$USERPROFILE"/tmp',
    "rm -rf '~'/x",
]


@pytest.mark.parametrize("cmd", NOT_BLOCKED)
def test_not_blocked(cmd):
    assert decision_of(bash(cmd)) in (None, "allow"), cmd


PS_NOT_BLOCKED = [
    "Remove-Item C:\\temp\\build -Recurse",
    "Remove-Item $env:USERPROFILE\\Downloads\\x.zip",
    "Get-ChildItem *.log | Remove-Item",
    'Remove-Item "$HOME"\\foo',
    "Remove-Item 'C:'\\temp -Recurse",
    "Format-Table -AutoSize",
    "Get-Content x | Format-List",
]


@pytest.mark.parametrize("cmd", PS_NOT_BLOCKED)
def test_ps_not_blocked(cmd):
    assert decision_of(ps(cmd)) in (None, "allow"), cmd


def _guard_module():
    sys.path.insert(0, str(HOOK_PATH.parent))
    import danger_guard
    return danger_guard


@pytest.mark.parametrize("cmd", ["rm -rf /c", "rm -rf /c/", "rm -rf '/d/'", "rm -rf /c/*",
                                 "rm -rf /c/Users/me", "rm -rf /d/users/bob/"])
def test_git_bash_drive_root_denied_on_windows(monkeypatch, cmd):
    """Git Bash 의 `/c` 는 C: 드라이브 루트, `/c/Users/<이름>` 은 Windows 홈이다."""
    guard = _guard_module()
    monkeypatch.setattr(guard, "IS_WINDOWS", True)
    assert guard.root_delete_reason(cmd), cmd


@pytest.mark.parametrize("cmd", ["rm -rf /c", "rm -rf /c/", "rm -rf /c/*", "rm -rf /d/users/bob"])
def test_one_letter_top_level_dir_is_not_root_on_posix(monkeypatch, cmd):
    """Linux·macOS 의 `/c`·`/d/users/bob` 은 평범한 디렉터리일 뿐이다."""
    guard = _guard_module()
    monkeypatch.setattr(guard, "IS_WINDOWS", False)
    assert guard.root_delete_reason(cmd) is None, cmd


def test_git_bash_drive_subdir_is_not_root_on_windows(monkeypatch):
    guard = _guard_module()
    monkeypatch.setattr(guard, "IS_WINDOWS", True)
    assert guard.root_delete_reason("rm -rf /c/tmp/x") is None


def test_prose_mentioning_disk_command_is_not_blocked():
    assert decision_of(bash('echo "mkfs should not be used"')) is None


def test_grep_for_dangerous_string_is_not_blocked():
    """위험한 문자열을 '데이터로' 다루는 명령은 막지 않는다.

    셰임 선행 줄은 붙을 수 있다 — PATH 한 줄일 뿐이고 grep 동작을 바꾸지 않는다.
    """
    assert decision_of(bash('grep -rn "rm -rf /" docs/')) != "deny"


# --- 셰임 선행 줄 ----------------------------------------------------------------


def test_bash_rm_is_not_rewritten():
    """Bash 명령은 건드리지 않는다. 명령을 바꾸면 권한 규칙이 바뀐 명령에 대해 평가돼
    `Bash(rm *)` 같은 사용자 허용 규칙이 맞지 않게 되고(실측), 그렇다고 allow 를 붙이면
    권한 체계를 통째로 우회한다. 셰임 PATH 는 SessionStart 가 맡는다."""
    assert payload_of(bash('rm -rf "$FOO/bar" && echo done')) is None


def _session_start(env_file):
    ev = {"hook_event_name": "SessionStart", "source": "startup"}
    env = dict(os.environ, CLAUDE_ENV_FILE=str(env_file))
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(HOOK_PATH)], input=json.dumps(ev),
        capture_output=True, text=True, encoding="utf-8", env=env)


def test_session_start_puts_shims_first_on_path_for_later_bash_commands(tmp_path):
    env_file = tmp_path / "env.sh"
    proc = _session_start(env_file)
    assert proc.returncode == 0
    # SessionStart 의 stdout 은 모델 컨텍스트에 들어간다 — 아무것도 내지 않는다.
    assert not proc.stdout.strip()
    # startup/resume/clear/compact 마다 다시 불려 같은 줄이 여러 번 쌓여도 PATH 는 한 번만 는다.
    _session_start(env_file)
    # Git Bash 는 PATH 를 `:` 로 구분한 /c/... 형식으로 보여준다. cygpath 가 있으면 Windows 형식으로 돌려 비교한다.
    out = subprocess.run(
        [shutil.which("bash") or "bash", "-c", '. "$1"; . "$1"; r=$(command -v rm); p=$PATH; '
         'if command -v cygpath >/dev/null; then r=$(cygpath -w "$r"); p=$(cygpath -wp "$p"); fi; '
         'echo "$r"; echo "$p"', "_", str(env_file)],
        capture_output=True, text=True, encoding="utf-8").stdout.splitlines()
    shim_dir = str(HOOK_PATH.parent / "shims")
    assert out[0] == os.path.join(shim_dir, "rm")
    assert out[1].split(os.pathsep).count(shim_dir) == 1


def test_session_start_without_env_file_is_harmless():
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(HOOK_PATH)],
        input=json.dumps({"hook_event_name": "SessionStart"}),
        capture_output=True, text=True, encoding="utf-8",
        env={k: v for k, v in os.environ.items() if k != "CLAUDE_ENV_FILE"})
    assert proc.returncode == 0 and not proc.stdout.strip()


def test_shim_prefix_keeps_other_tool_input_fields():
    """updatedInput 은 입력 객체 전체를 대체한다. command 만 넘기면 timeout·
    run_in_background 같은 필드가 조용히 사라진다."""
    ev = {"tool_name": "PowerShell", "tool_input": {
        "command": "Remove-Item x", "timeout": 600000, "run_in_background": True}}
    new = payload_of(ev)["hookSpecificOutput"]["updatedInput"]
    assert new["timeout"] == 600000
    assert new["run_in_background"] is True


def test_powershell_delete_gets_module_import_prefix():
    original = "Remove-Item -Recurse -Force .\\build"
    p = payload_of(ps(original))
    assert "permissionDecision" not in p["hookSpecificOutput"]
    new = p["hookSpecificOutput"]["updatedInput"]["command"]
    assert new.endswith(original)
    assert "Import-Module" in new and "RmShim" in new


@pytest.mark.parametrize("original", [
    "Get-ChildItem x | Remove-Item",
    "cd x; Remove-Item y",
    "if (Test-Path y) { Remove-Item y -Recurse }",
    "Get-ChildItem *.tmp | ForEach-Object { del $_ }",
    "$null = $(Remove-Item x)",
    "$null = Remove-Item x",
])
def test_powershell_delete_anywhere_in_command_gets_module_import_prefix(original):
    """삭제 동사가 명령 맨 앞이 아니어도(파이프·구분자·블록 안) 셰임이 붙어야 한다.
    붙지 않으면 그 삭제는 휴지통을 거치지 않고 영구 삭제된다."""
    p = payload_of(ps(original))
    assert "permissionDecision" not in p["hookSpecificOutput"]
    new = p["hookSpecificOutput"]["updatedInput"]["command"]
    assert new.endswith(original)
    assert "Import-Module" in new and "RmShim" in new


@pytest.mark.parametrize("cmd", ["Get-ChildItem -Recurse", "git rm --cached x"])
def test_powershell_without_delete_is_untouched(cmd):
    assert payload_of(ps(cmd)) is None


# --- 그 외 계약 -------------------------------------------------------------------


def test_other_tools_are_ignored():
    assert payload_of({"tool_name": "Read", "tool_input": {"file_path": "/x"}}) is None


def test_empty_command_is_ignored():
    assert payload_of(bash("   ")) is None


def test_malformed_event_does_not_crash():
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(HOOK_PATH)],
        input="not json", capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0
    assert not (proc.stdout or "").strip()


load_tests = collect(globals())
