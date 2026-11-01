"""fluent-korean 출력 스타일 설치 (res/output_style/, scripts/setup_output_style.sh).

실제 홈·claude CLI 를 건드리지 않도록 임시 HOME 과 PATH 앞의 claude 스텁으로만 검증한다.
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
APPLY = ROOT / "res" / "output_style" / "apply.py"
STYLE = "fluent-korean:fluent-korean"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    return h


def apply(home, *args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(APPLY), "--home", str(home), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def settings_path(home):
    return home / ".claude" / "settings.json"


def manifest_path(home):
    return home / ".claude" / ".claude-code-setup.json"


def settings(home):
    return json.loads(settings_path(home).read_text(encoding="utf-8"))


def write_settings(home, data):
    settings_path(home).write_text(json.dumps(data), encoding="utf-8")


def test_sets_output_style_and_uninstall_restores_original_value(home):
    write_settings(home, {"model": "opus", "outputStyle": "Explanatory"})

    proc = apply(home)
    assert proc.returncode == 0, proc.stderr
    assert settings(home) == {"model": "opus", "outputStyle": STYLE}

    proc = apply(home, "--uninstall")
    assert proc.returncode == 0, proc.stderr
    assert settings(home) == {"model": "opus", "outputStyle": "Explanatory"}
    assert not manifest_path(home).exists()


def test_uninstall_removes_key_if_output_style_was_absent(home):
    write_settings(home, {"model": "opus"})
    assert apply(home).returncode == 0
    assert apply(home, "--uninstall").returncode == 0
    assert settings(home) == {"model": "opus"}


def test_uninstall_removes_file_it_created_if_settings_was_absent(home):
    assert apply(home).returncode == 0
    assert settings(home) == {"outputStyle": STYLE}
    assert apply(home, "--uninstall").returncode == 0
    assert not settings_path(home).exists()
    assert not manifest_path(home).exists()


def test_reinstall_restores_value_user_chose_after_install_on_uninstall(home):
    write_settings(home, {"outputStyle": "Concise"})
    assert apply(home).returncode == 0
    # 사용자가 /config 로 Learning 을 고른 뒤 셋업을 다시 돌렸다. 마지막 선택은 Learning 이다.
    write_settings(home, {"outputStyle": "Learning"})
    assert apply(home).returncode == 0
    assert settings(home)["outputStyle"] == STYLE
    assert apply(home, "--uninstall").returncode == 0
    assert settings(home)["outputStyle"] == "Learning"


def test_reinstall_rerun_does_not_lose_original_value(home):
    write_settings(home, {"outputStyle": "Concise"})
    assert apply(home).returncode == 0
    assert apply(home).returncode == 0  # 이미 지정됨 — 영수증을 건드리지 않는다
    assert apply(home, "--uninstall").returncode == 0
    assert settings(home)["outputStyle"] == "Concise"


def test_uninstall_keeps_output_style_user_changed_after_install(home):
    assert apply(home).returncode == 0
    write_settings(home, {"outputStyle": "Learning"})
    proc = apply(home, "--uninstall")
    assert proc.returncode == 0, proc.stderr
    assert settings(home) == {"outputStyle": "Learning"}
    assert not manifest_path(home).exists()


def test_writes_nothing_when_already_set(home):
    write_settings(home, {"outputStyle": STYLE})
    proc = apply(home)
    assert proc.returncode == 0, proc.stderr
    assert "already set" in proc.stdout
    assert not manifest_path(home).exists()
    assert list((home / ".claude").iterdir()) == [settings_path(home)]


def test_dry_run_writes_nothing(home):
    write_settings(home, {"outputStyle": "Concise"})
    before = settings_path(home).read_bytes()
    proc = apply(home, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "DRY RUN" in proc.stdout
    assert settings_path(home).read_bytes() == before
    assert not manifest_path(home).exists()

    assert apply(home).returncode == 0
    after = settings_path(home).read_bytes()
    assert apply(home, "--uninstall", "--dry-run").returncode == 0
    assert settings_path(home).read_bytes() == after
    assert manifest_path(home).exists()


def test_unparseable_settings_stops_without_writing(home):
    settings_path(home).write_text("{ broken", encoding="utf-8")
    proc = apply(home)
    assert proc.returncode == 1
    assert settings_path(home).read_text(encoding="utf-8") == "{ broken"
    assert not manifest_path(home).exists()


def test_manifest_preserves_other_sections(home):
    manifest_path(home).write_text(json.dumps({"omc_hud": {"refresh_interval_added": True}}),
                                   encoding="utf-8")
    assert apply(home).returncode == 0
    assert apply(home, "--uninstall").returncode == 0
    assert json.loads(manifest_path(home).read_text(encoding="utf-8")) == {
        "omc_hud": {"refresh_interval_added": True}}


posix_only = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="POSIX shell script test (bash required)",
)


def stub_claude(tmp_path, installed):
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    log = tmp_path / "claude.log"
    listed = "  ❯ fluent-korean@fluent-korean" if installed else "  ❯ other@x"
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
def test_setup_output_style_installs_plugin_and_sets_style(tmp_path, home):
    bin_dir, log = stub_claude(tmp_path, installed=False)
    proc = run_setup(["--output-style"], home, bin_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = log.read_text(encoding="utf-8")
    assert "plugin marketplace add snflkd/fluent-korean" in calls
    assert "plugin install fluent-korean@fluent-korean" in calls
    # --output-style 만 줬으므로 훅은 설치되지 않는다
    assert settings(home) == {"outputStyle": STYLE}


@posix_only
def test_already_installed_plugin_is_not_reinstalled(tmp_path, home):
    bin_dir, log = stub_claude(tmp_path, installed=True)
    proc = run_setup(["--output-style"], home, bin_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "install" not in log.read_text(encoding="utf-8")
    assert settings(home)["outputStyle"] == STYLE


@posix_only
def test_setup_output_style_dry_run_does_not_run_claude(tmp_path, home):
    bin_dir, log = stub_claude(tmp_path, installed=False)
    proc = run_setup(["--output-style", "--dry-run"], home, bin_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "(dry-run) claude plugin install fluent-korean@fluent-korean" in proc.stdout
    assert "install" not in log.read_text(encoding="utf-8")
    assert not settings_path(home).exists()


@posix_only
def test_setup_uninstall_also_restores_output_style(tmp_path, home):
    bin_dir, _ = stub_claude(tmp_path, installed=True)
    write_settings(home, {"outputStyle": "Concise"})
    assert run_setup(["--output-style"], home, bin_dir).returncode == 0
    proc = run_setup(["--uninstall"], home, bin_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert settings(home)["outputStyle"] == "Concise"


load_tests = collect(globals())
