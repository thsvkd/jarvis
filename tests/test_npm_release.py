"""npm-release: 격리된 홈과, 버전별 설치를 흉내 내는 가짜 npm 으로 배치·교체·정리를 확인한다."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "jarvis" / "modules" / "setup"
sys.path.insert(0, str(ROOT / "res" / "tools"))
sys.path.insert(0, str(ROOT / "res" / "update"))
import npm_release  # noqa: E402
import update_ai  # noqa: E402

WINDOWS = os.name == "nt"
ENTRY = next(e for e in json.loads(npm_release.CATALOG.read_text(encoding="utf-8")) if e["id"] == "gemini")
PACKAGE = ENTRY["packages"]["npm"]

# 가짜 npm. install --prefix 는 <prefix>/node_modules/<pkg>/cli 와 .bin/gemini 를 만들고,
# 그 cli 는 --version 이면 설치한 버전을 찍고 --sleep 이면 30초 기다린다(실행 중인 세션 흉내).
FAKE_NPM = r'''
import os, shutil, sys
args = sys.argv[1:]
with open(os.environ["NPM_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")
broken = bool(os.environ.get("NPM_BROKEN"))
def cli(package_dir, bin_dir, version):
    os.makedirs(package_dir, exist_ok=True)
    os.makedirs(bin_dir, exist_ok=True)
    if os.name == "nt":
        # 실제 npm 셔임처럼 .bin\\gemini.cmd 가 인터프리터로 버전 디렉터리 안의 스크립트를 띄운다.
        # 세션은 그 인터프리터이고, 명령줄에 버전 경로가 남는다(배치만으로 기다리면 남지 않는다).
        script = os.path.join(package_dir, "cli.py")
        open(script, "w").write('import sys, time\nif sys.argv[1:2] == ["--sleep"]: time.sleep(30); sys.exit(0)\n'
                                + ('sys.exit(1)\n' if broken else '') + 'print("gemini ' + version + '")\n')
        open(os.path.join(bin_dir, "gemini.cmd"), "w").write('@"' + sys.executable + '" "' + script + '" %*\r\n')
    else:
        script = os.path.join(package_dir, "cli")
        open(script, "w").write('#!/bin/sh\nif [ "$1" = "--sleep" ]; then /bin/sleep 30; exit 0; fi\n'
                                + ('exit 1\n' if broken else '') + 'echo "gemini ' + version + '"\n')
        os.chmod(script, 0o755)
        os.symlink(os.path.relpath(script, bin_dir), os.path.join(bin_dir, "gemini"))
if args[:2] == ["view", "@google/gemini-cli"]:
    print(os.environ["NPM_LATEST"])
elif args[:2] == ["root", "-g"]:
    print(os.environ["NPM_GLOBAL_ROOT"])
elif args[:2] == ["uninstall", "-g"]:
    shutil.rmtree(os.path.join(os.environ["NPM_GLOBAL_ROOT"], *args[2].split("/")))
    # 실제 npm(arborist)처럼 <prefix>/bin 의 링크 이름은 무엇을 가리키든 지운다.
    link = os.path.join(os.path.dirname(os.path.dirname(os.environ["NPM_GLOBAL_ROOT"])), "bin", "gemini")
    if os.path.lexists(link):
        os.remove(link)
elif args[:1] == ["install"]:
    if os.environ.get("NPM_FAIL"):
        sys.exit(1)
    prefix = args[args.index("--prefix") + 1]
    version = args[-1].rsplit("@", 1)[1]
    modules = os.path.join(prefix, "node_modules")
    cli(os.path.join(modules, "@google", "gemini-cli"), os.path.join(modules, ".bin"), version)
'''


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "npm.py").write_text(FAKE_NPM, encoding="utf-8")
    if WINDOWS:
        (tools / "npm.cmd").write_text('@"' + sys.executable + '" "%~dp0npm.py" %*\r\n', encoding="utf-8")
    else:
        (tools / "npm").write_text("#!" + sys.executable + "\n" + FAKE_NPM, encoding="utf-8")
        (tools / "npm").chmod(0o755)
    keep = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")] if WINDOWS else ["/bin", "/usr/bin"]
    monkeypatch.setenv("PATH", os.pathsep.join([str(tools)] + keep))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("NPM_LOG", str(tmp_path / "npm.log"))
    # npm 접두가 ~/.local 인 경우: 전역 bin 이 셔임 디렉터리와 같다.
    monkeypatch.setenv("NPM_GLOBAL_ROOT", str(home / ".local" / "lib" / "node_modules"))
    monkeypatch.setenv("NPM_LATEST", "1.0.0")
    # 실제 사용자 PATH(레지스트리)는 테스트 대상이 아니다.
    monkeypatch.setattr(npm_release, "ensure_user_path", lambda directory: None)
    return tmp_path


def npm_calls(world):
    log = world / "npm.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def run_shim(*args):
    shim = npm_release.shim_path(ENTRY)
    argv = ["cmd", "/c", str(shim)] if WINDOWS else [str(shim)]
    return subprocess.run(argv + list(args), capture_output=True, text=True, timeout=30)


def start_session():
    """셔임으로 띄운 세션. 그 순간의 current 버전 경로로 계속 돈다."""
    shim = npm_release.shim_path(ENTRY)
    argv = ["cmd", "/c", str(shim), "--sleep"] if WINDOWS else [str(shim), "--sleep"]
    session = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    return session


def stop(session):
    if WINDOWS:
        # cmd /c 셔임만 끝내면 손자인 세션 인터프리터가 남는다.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(session.pid)], capture_output=True, timeout=30)
    session.kill()
    session.wait()
    time.sleep(0.5)


def test_update_switches_current_only_after_the_new_version_is_complete_and_never_rewrites_the_shim(world, monkeypatch):
    """covers: AI-57"""
    root = npm_release.store(ENTRY)
    assert npm_release.install(ENTRY, update_ai.process_table) == "ok"
    shim = npm_release.shim_path(ENTRY)
    assert npm_release.read_pointer(root, "current") == "1.0.0"
    assert run_shim("--version").stdout.strip() == "gemini 1.0.0"
    # npm 12 는 허용하지 않은 install 스크립트를 막는다. 설치하는 그 패키지만 허용한다.
    assert (root / "1.0.0" / ".npmrc").read_text(encoding="utf-8") == "allow-scripts=@google/gemini-cli\n"
    before = (shim.read_bytes(), shim.stat().st_ino, shim.stat().st_mtime_ns)

    monkeypatch.setenv("NPM_LATEST", "2.0.0")
    assert npm_release.install(ENTRY, update_ai.process_table) == "ok"
    assert npm_release.read_pointer(root, "current") == "2.0.0"
    assert npm_release.read_pointer(root, "previous") == "1.0.0"
    assert run_shim("--version").stdout.strip() == "gemini 2.0.0"
    assert (shim.read_bytes(), shim.stat().st_ino, shim.stat().st_mtime_ns) == before

    # 설치가 실패하면 current 는 그대로이고, 반쯤 만든 버전 디렉터리도 남지 않는다.
    monkeypatch.setenv("NPM_LATEST", "3.0.0")
    monkeypatch.setenv("NPM_FAIL", "1")
    assert npm_release.install(ENTRY, update_ai.process_table) == "failed"
    assert npm_release.read_pointer(root, "current") == "2.0.0"
    assert not (root / "3.0.0").exists()
    assert run_shim("--version").stdout.strip() == "gemini 2.0.0"

    # 설치는 됐지만 실행되지 않는 버전(postinstall 이 바이너리를 못 놓은 경우)도 current 가 되지 않는다.
    monkeypatch.delenv("NPM_FAIL")
    monkeypatch.setenv("NPM_BROKEN", "1")
    assert npm_release.install(ENTRY, update_ai.process_table) == "failed"
    assert npm_release.read_pointer(root, "current") == "2.0.0"
    assert not (root / "3.0.0").exists()


def test_prune_keeps_current_previous_and_versions_a_running_session_uses(world, monkeypatch):
    """covers: AI-58"""
    root = npm_release.store(ENTRY)
    assert npm_release.install(ENTRY, update_ai.process_table) == "ok"
    session = start_session()
    try:
        for version in ("2.0.0", "3.0.0"):
            monkeypatch.setenv("NPM_LATEST", version)
            assert npm_release.install(ENTRY, update_ai.process_table) == "ok"
        # 1.0.0 은 current 도 previous 도 아니지만 세션이 쓰고 있다.
        assert sorted(p.name for p in root.iterdir() if p.is_dir()) == ["1.0.0", "2.0.0", "3.0.0"]
        assert session.poll() is None
    finally:
        stop(session)
    assert npm_release.install(ENTRY, update_ai.process_table) == "ok"
    assert sorted(p.name for p in root.iterdir() if p.is_dir()) == ["2.0.0", "3.0.0"]

    # 프로세스 목록을 못 읽으면 아무것도 지우지 않는다.
    (root / "0.9.0").mkdir()
    assert npm_release.prune(ENTRY, root, None) == [("0.9.0", ["unknown"])]
    assert (root / "0.9.0").is_dir()


@pytest.mark.skipif(WINDOWS, reason="the legacy link lives in the POSIX shim directory only")
def test_old_global_copy_is_removed_only_when_no_process_uses_it(world):
    """covers: AI-59"""
    legacy = world / "home" / ".local" / "lib" / "node_modules" / "@google" / "gemini-cli"
    legacy.mkdir(parents=True)
    script = legacy / "cli"
    script.write_text('#!/bin/sh\nif [ "$1" = "--sleep" ]; then /bin/sleep 30; exit 0; fi\necho "gemini 0.1.0"\n',
                      encoding="utf-8")
    script.chmod(0o755)
    link = npm_release.shim_path(ENTRY)
    link.parent.mkdir(parents=True)
    link.symlink_to(script)
    # PATH 의 링크로 띄운 세션: 명령줄에는 링크 경로만 남는다.
    session = subprocess.Popen([str(link), "--sleep"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    try:
        assert npm_release.install(ENTRY, update_ai.process_table) == "partial"
        assert legacy.is_dir()
        assert not any(c.startswith("uninstall") for c in npm_calls(world))
        assert npm_release.owns(link) and run_shim("--version").stdout.strip() == "gemini 1.0.0"
    finally:
        stop(session)
    assert npm_release.install(ENTRY, update_ai.process_table) == "ok"
    assert "uninstall -g @google/gemini-cli" in npm_calls(world)
    assert not legacy.exists()
    # npm uninstall -g 가 같은 bin 의 셔임 이름을 지워도 셔임은 다시 있어야 한다.
    assert npm_release.owns(link) and run_shim("--version").stdout.strip() == "gemini 1.0.0"


@pytest.mark.skipif(WINDOWS, reason="the legacy link lives in the POSIX shim directory only")
def test_uninstall_of_an_old_npm_global_receipt_removes_that_copy(world):
    """covers: AI-61 — npm-release 배치가 없는 예전 npm -g 영수증도 같은 규칙으로 지운다."""
    legacy = world / "home" / ".local" / "lib" / "node_modules" / "@google" / "gemini-cli"
    legacy.mkdir(parents=True)
    assert npm_release.uninstall(ENTRY, update_ai.process_table) == "ok"
    assert "uninstall -g @google/gemini-cli" in npm_calls(world)
    assert not legacy.exists()


def test_a_file_ai_setup_did_not_write_at_the_shim_path_is_never_overwritten(world):
    """covers: AI-60"""
    shim = npm_release.shim_path(ENTRY)
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    assert npm_release.install(ENTRY, update_ai.process_table) == "failed"
    assert shim.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    assert not any(c.startswith("install") for c in npm_calls(world))
    assert not npm_release.store(ENTRY).exists()


def test_a_command_of_the_same_name_from_another_install_is_left_alone(world, monkeypatch):
    """covers: AI-60 — 다른 방식(brew·winget·다른 npm 패키지)으로 깔린 같은 이름의 명령 옆에 사본을 만들지 않는다."""
    other = world / "other-bin"
    other.mkdir()
    name = "gemini.cmd" if WINDOWS else "gemini"
    (other / name).write_text("@echo off\r\n" if WINDOWS else "#!/bin/sh\n", encoding="utf-8")
    if not WINDOWS:
        (other / name).chmod(0o755)
    monkeypatch.setenv("PATH", os.environ["PATH"] + os.pathsep + str(other))
    assert npm_release.install(ENTRY, update_ai.process_table) == "failed"
    assert not any(c.startswith("install") for c in npm_calls(world))
    assert not os.path.lexists(npm_release.shim_path(ENTRY))

    # 같은 패키지의 npm -g 사본이 있어도, PATH 의 명령이 그것이 아니면 여전히 다른 설치다.
    (world / "home" / ".local" / "lib" / "node_modules" / "@google" / "gemini-cli").mkdir(parents=True)
    assert npm_release.install(ENTRY, update_ai.process_table) == "failed"
    assert not any(c.startswith("install") for c in npm_calls(world))


def test_uninstall_removes_the_shim_and_keeps_only_a_version_still_in_use(world, monkeypatch):
    """covers: AI-61"""
    root = npm_release.store(ENTRY)
    assert npm_release.install(ENTRY, update_ai.process_table) == "ok"
    session = start_session()
    try:
        monkeypatch.setenv("NPM_LATEST", "2.0.0")
        assert npm_release.install(ENTRY, update_ai.process_table) == "ok"
        assert npm_release.uninstall(ENTRY, update_ai.process_table) == "failed"
        assert not os.path.lexists(npm_release.shim_path(ENTRY))
        assert sorted(p.name for p in root.iterdir() if p.is_dir()) == ["1.0.0"]
    finally:
        stop(session)
    assert npm_release.uninstall(ENTRY, update_ai.process_table) == "ok"
    assert not root.exists()


def test_update_ai_updates_an_npm_release_install_while_its_session_runs(world, monkeypatch):
    """covers: AI-9 — update-ai 가 npm-release 셔임을 알아보고 세션이 돌아도 올린다."""
    assert npm_release.install(ENTRY, update_ai.process_table) == "ok"
    shim = npm_release.shim_path(ENTRY)
    assert update_ai.method_for(ENTRY, str(shim))[0] == "npm-release"
    session = start_session()
    try:
        monkeypatch.setattr(update_ai, "locations", lambda name: [str(shim)] if name == "gemini" else [])
        args = type("A", (), {"check": False, "allow_interrupt": False})()
        for version in ("2.0.0", "3.0.0"):
            monkeypatch.setenv("NPM_LATEST", version)
            assert update_ai.run_all([ENTRY], ["gemini"], args) == 0
        assert npm_release.read_pointer(npm_release.store(ENTRY), "current") == "3.0.0"
        # 1.0.0 은 current 도 previous 도 아니다. 세션이 쓰고 있어서 남아야 한다.
        assert session.poll() is None and (npm_release.store(ENTRY) / "1.0.0").is_dir()
    finally:
        stop(session)


load_tests = collect(globals())
