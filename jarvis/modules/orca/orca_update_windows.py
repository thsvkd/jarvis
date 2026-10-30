"""Windows S4U Orca 서버가 있는 기기의 Orca 갱신(ai-setup res/update/update_ai.py 에서 옮김, 함수 본문은 그대로).

설치기를 앱 내 업데이트와 같은 `/S --updated` 로 실행하고, 러너가 새 복사본으로 옮겨 가는지 보고한다.
run_step 은 update-ai 의 출력 흘리기·프로세스 트리 끊기 대신 시간 제한만 둔 단순판이다(jarvis 가 진행 로그를 모은다).
"""
import os
import re
import subprocess
import sys
import tempfile
import shutil
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orca as orca_release  # noqa: E402
import orca_server  # noqa: E402

STEP_TIMEOUT = 900
ORCA_SERVER_SWITCH_SECONDS = 300


def say(text=""):
    print(text, flush=True)


def run_step(label, argv):
    """성공이면 True. 표준 입력은 주지 않는다. 시간이 지나면 설치기의 프로세스 트리째 끊는다(반쯤 남은 설치와 부딪히지 않게)."""
    say("  {:<14}  {}".format(label, " ".join(argv)))
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as error:
        say("      {}".format(error.strerror or type(error).__name__))
        return False
    try:
        code = proc.wait(timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        import psutil
        try:
            tree = [psutil.Process(proc.pid)] + psutil.Process(proc.pid).children(recursive=True)
        except psutil.Error:
            tree = []
        for child in tree:
            try:
                child.kill()
            except psutil.Error:
                pass
        say("      -> {} step timed out after {} seconds".format(label, STEP_TIMEOUT))
        return False
    if code != 0:
        say("      -> {} step failed (rc={})".format(label, code))
        return False
    return True


def orca_server_record(manager="task-scheduler"):
    receipt = orca_release.read_receipt(orca_release.home_dir() / ".claude/.claude-code-setup.json")
    record = (receipt.get("tool_installs") or {}).get("orca_server")
    return record if isinstance(record, dict) and record.get("service_manager") == manager else None


def update_orca_windows(expect=None):
    """공식 설치기를 앱 내 업데이트와 같은 `/S --updated` 로 실행한다. 결과: ok·failed.

    --updated 가 없으면 이전 버전의 언인스톨러가 '제거'로 돌아 Orca 터미널 호스트를 끝내고 지운다.
    ai-setup 서버는 설치 폴더 밖 복사본에서 돌아 설치기가 건드리지 않으며, 터미널이 없을 때 새 판으로 옮긴다.
    """
    plan = orca_release.package_plan("win32", "AMD64")
    root, _ = orca_release.install_paths(plan)
    server = orca_server_record()
    try:
        version, meta = orca_release.fetch_manifest(plan)
        installed = orca_release.installed_version(plan)
    except (OSError, ValueError, KeyError, RuntimeError):
        say("  {:<14}  cannot read the latest release or the installed version".format("package"))
        return "failed"
    if expect and version != expect:  # (jarvis) 승인한 계획의 목표 버전이 아니면 설치기를 띄우지 않는다
        say("  {:<14}  the latest release is {} but the approved plan targets {}; plan again".format("package", version, expect))
        return "failed"
    if is_current(installed, version):
        say("  {:<14}  Orca {} is already the latest".format("package", installed))
        return "ok"
    if server and orca_server.runtime_is_active(server["server_profile"]) and not (
            orca_server.running_runner_state(server["service_root"]) or {}).get("app"):
        say("  {:<14}  the ai-setup Orca server runs from the install directory; the installer cannot close it".format("blocked"))
        say("  {:<14}  run setup --tools orca first (it moves the server once its terminals are closed)".format(""))
        return "failed"
    scratch = tempfile.mkdtemp(prefix="update-ai-orca-")
    try:
        say("  {:<14}  download {}".format("package", meta["name"]))
        try:
            target = orca_release.download(version, meta, Path(scratch))
        except (OSError, RuntimeError) as error:
            say("      download failed: {}".format(error if isinstance(error, RuntimeError) else type(error).__name__))
            return "failed"
        if not run_step("package", [str(target), "/S", "--updated"]):
            return "failed"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    try:
        installed = orca_release.installed_version(plan)
    except (OSError, ValueError, KeyError, RuntimeError):
        installed = ""
    if installed != version:
        say("  {:<14}  the installer finished but Orca is {} (expected {})".format("package", installed or "unreadable", version))
        return "failed"
    say("  {:<14}  reopen the Orca desktop app if it was running".format("desktop"))
    if server:
        wait_for_orca_server_switch(server, root)
    return "ok"


def wait_for_orca_server_switch(server, root):
    """러너가 새 설치본의 복사본으로 옮겨 가는지 지켜본다. 터미널이 있으면 기다리지 않고 알린다. 셀 수 없으면 시간까지 기다린다."""
    identity = orca_server.app_identity(root)
    deadline = time.monotonic() + ORCA_SERVER_SWITCH_SECONDS
    while True:
        state = orca_server.running_runner_state(server["service_root"]) or {}
        if identity and Path(state.get("app") or "").name == identity:
            say("  {:<14}  the Orca server now runs the new version".format("server"))
            return True
        terminals = orca_server.live_terminal_count(orca_server.server_query_cli(server), server["server_profile"])
        # (jarvis) 일반 권한의 jarvis 는 High 무결성 서버의 터미널을 셀 수 없다(None). 그때는 끝내지 않고 러너의 전환을 기다린다.
        if (terminals is not None and terminals != 0) or time.monotonic() >= deadline:
            reason = (orca_server.terminal_count_problem(terminals) if terminals != 0
                      else "it did not switch within {} seconds".format(ORCA_SERVER_SWITCH_SECONDS))
            say("  {:<14}  the Orca server keeps the previous version for now ({}); "
                "it switches by itself once it has no live terminals".format("server", reason))
            return False
        time.sleep(5)


def version_key(text):
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def is_current(installed, latest):
    """설치본이 최신 이상인가. 앱 안에서 받은 prerelease·늦게 갱신된 매니페스트로 설치본이 더 새로울 수 있다."""
    return installed == latest or version_key(str(installed or "")) > version_key(str(latest or ""))
