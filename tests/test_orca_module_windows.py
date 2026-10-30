"""Native Windows boundaries that must hold without administrator consent."""
import base64
import json
import importlib.util
from pathlib import Path
import subprocess
import sys

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

spec = importlib.util.spec_from_file_location("orca_windows_native", Path(__file__).resolve().parents[1] / "jarvis/modules/orca/orca_windows.py")
orca_windows = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orca_windows)


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Native Windows security boundary")


def test_elevated_worker_refuses_a_different_account_before_task_access():
    payload = base64.b64encode(json.dumps({"callerSid": "S-1-5-18"}).encode()).decode()
    worker = orca_windows.WORKER.replace("__PAYLOAD__", payload)
    encoded = base64.b64encode(worker.encode("utf-16-le")).decode()
    result = subprocess.run(
        [str(orca_windows.powershell_path()), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode != 0
    assert "must match the original Windows user" in result.stderr


def test_private_pairing_file_has_an_explicit_current_user_rule(tmp_path):
    path = tmp_path / "pairing.txt"
    path.write_text("fixture only", encoding="utf-8")
    orca_windows.protect_file(path)
    script = r'''
$path = [Console]::In.ReadToEnd()
$acl = [IO.File]::GetAccessControl($path)
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$rules = @($acl.Access | Where-Object {
    $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $sid -and
    $_.AccessControlType -eq 'Allow' -and -not $_.IsInherited
})
if ($rules.Count -eq 0) { exit 1 }
'''
    result = subprocess.run(
        [str(orca_windows.powershell_path()), "-NoProfile", "-NonInteractive", "-Command", script],
        input=str(path), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert path.read_text(encoding="utf-8") == "fixture only"


def _alive(pid):
    listed = subprocess.run(["tasklist", "/FI", "PID eq {}".format(pid), "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, timeout=30)
    return '"{}"'.format(pid) in listed.stdout


def test_runner_process_tree_ends_when_the_runner_is_terminated():
    """covers: ORCA-4 (AI-50) — schtasks /End 는 러너만 TerminateProcess 한다. 자손이 남으면 안 된다."""
    import time
    runner_code = (
        "import subprocess, sys, time\n"
        "sys.path.insert(0, {!r})\n"
        "import orca_windows\n"
        "orca_windows.contain_process_tree()\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],\n"
        "                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n"
    ).format(str(Path(spec.origin).parent))
    runner = subprocess.Popen([sys.executable, "-c", runner_code], stdout=subprocess.PIPE, text=True)
    try:
        grandchild = int(runner.stdout.readline())
        assert _alive(grandchild)
        runner.kill()
        runner.wait(timeout=30)
        deadline = time.monotonic() + 10
        while _alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.5)
        assert not _alive(grandchild)
    finally:
        if runner.poll() is None:
            runner.kill()


def test_legacy_runner_cleanup_ends_only_that_runner_tree():
    """covers: ORCA-4 (AI-52b) — 승격 헬퍼의 트리 정리 함수를 승격 없이 실제 프로세스에 대고 실행한다."""
    import time
    runner_path = r"C:\ai-setup-test\service\orca-server-runner.py"
    sleeper = "import time; time.sleep(120)"
    # 러너(명령줄에 러너 경로) -> 서버 -> 터미널 호스트, 그리고 같은 이름 패턴의 무관한 python
    runner_code = (
        "import subprocess, sys, time\n"
        "server = subprocess.Popen([sys.executable, '-c', 'import subprocess, sys, time; "
        "h = subprocess.Popen([sys.executable, \\'-c\\', \\'import time; time.sleep(120)\\']); "
        "print(h.pid, flush=True); time.sleep(120)'], stdout=subprocess.PIPE, text=True)\n"
        "print(server.pid, server.stdout.readline().strip(), flush=True)\n"
        "time.sleep(120)\n"
    )
    runner = subprocess.Popen([sys.executable, "-c", runner_code, runner_path], stdout=subprocess.PIPE, text=True)
    bystander = subprocess.Popen([sys.executable, "-c", sleeper])
    try:
        server_pid, host_pid = (int(value) for value in runner.stdout.readline().split())
        assert all(_alive(pid) for pid in (runner.pid, server_pid, host_pid))
        script = orca_windows.TREE_FUNCTIONS + (
            "\n$tree = Get-OrcaRunnerTree '{}'\nStop-OrcaRunnerTree $tree\n"
            "$tree | ForEach-Object {{ $_.ProcessId }}\n"
        ).format(runner_path)
        result = subprocess.run(
            [str(orca_windows.powershell_path()), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        assert result.returncode == 0, result.stderr
        listed = {int(line) for line in result.stdout.split() if line.strip().isdigit()}
        assert {runner.pid, server_pid, host_pid} <= listed and bystander.pid not in listed
        deadline = time.monotonic() + 10
        while any(_alive(pid) for pid in (runner.pid, server_pid, host_pid)) and time.monotonic() < deadline:
            time.sleep(0.5)
        assert not any(_alive(pid) for pid in (runner.pid, server_pid, host_pid))
        assert _alive(bystander.pid)
    finally:
        for process in (runner, bystander):
            process.kill()


load_tests = collect(globals())
