"""Windows S4U Orca 서버가 있는 기기의 갱신(ai-setup tests/test_update_ai.py 에서 옮김, 본문은 그대로).

옮기지 않은 것: update_orca_deb(jarvis 는 orca_update 가 deb 를 갱신한다), runs_target·method_for(update-ai 의 세션 판정·설치 방식 판별).
"""
import sys as _sys
from pathlib import Path

_sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

UPDATE_WINDOWS = Path(__file__).resolve().parents[1] / "jarvis" / "modules" / "orca" / "orca_update_windows.py"


def _update_ai_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("update_ai_under_test", UPDATE_WINDOWS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _orca_windows_release(mod, monkeypatch, tmp_path, payload, server_state):
    import base64
    import hashlib
    import io

    good = b"official"
    meta = {"name": "orca-windows-setup.exe", "size": len(good),
            "sha512": base64.b64encode(hashlib.sha512(good).digest()).decode("ascii")}
    installed = {"version": "1.0.0"}
    steps = []
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(mod.orca_release, "fetch_manifest", lambda plan: ("9.9.9", meta))
    monkeypatch.setattr(mod.orca_release, "open_release", lambda url: io.BytesIO(payload))
    monkeypatch.setattr(mod.orca_release, "installed_version", lambda plan: installed["version"])
    monkeypatch.setattr(mod.orca_release, "read_receipt", lambda path: {"tool_installs": {"orca_server": {
        "service_manager": "task-scheduler", "service_root": str(tmp_path / "service"),
        "server_profile": str(tmp_path / "orca"), "cli": "orca.exe"}}})
    monkeypatch.setattr(mod.orca_server, "runtime_is_active", lambda profile: True)
    monkeypatch.setattr(mod.orca_server, "running_runner_state", lambda root: server_state.get("state"))
    monkeypatch.setattr(mod.orca_server, "app_identity", lambda root: "build-new")
    monkeypatch.setattr(mod.orca_server, "live_terminal_count", lambda *args: server_state.get("terminals", 0))
    monkeypatch.setattr(mod, "ORCA_SERVER_SWITCH_SECONDS", 0)

    def run_step(label, argv, **kwargs):
        steps.append(argv)
        installed["version"] = "9.9.9"
        server_state["state"] = server_state.get("after", server_state.get("state"))
        return True

    monkeypatch.setattr(mod, "run_step", run_step)
    return steps, good


def test_orca_windows_update_runs_only_a_verified_installer_as_an_in_app_update(tmp_path, monkeypatch, capsys):
    """covers: ORCA-6 (AI-55a, AI-55c) — CLI 만으로 Orca 데스크톱·서버를 올린다. --updated 가 없으면 이전 언인스톨러가 터미널 호스트를 지운다."""
    mod = _update_ai_module()
    state = {"state": {"pid": 1, "job": True, "app": str(tmp_path / "service" / "app" / "build-old")},
             "after": {"pid": 1, "job": True, "app": str(tmp_path / "service" / "app" / "build-new")}}
    steps, good = _orca_windows_release(mod, monkeypatch, tmp_path, b"tampered", state)
    assert mod.update_orca_windows() == "failed" and steps == []

    monkeypatch.setattr(mod.orca_release, "open_release", lambda url: __import__("io").BytesIO(good))
    assert mod.update_orca_windows() == "ok"
    assert len(steps) == 1 and steps[0][0].endswith("orca-windows-setup.exe") and steps[0][1:] == ["/S", "--updated"]
    assert "now runs the new version" in capsys.readouterr().out


def test_orca_windows_update_reports_a_server_that_waits_for_its_terminals(tmp_path, monkeypatch, capsys):
    """covers: ORCA-6 (AI-55c) — 서버에 터미널이 있으면 패키지만 올리고 서버는 터미널이 닫힌 뒤 스스로 옮긴다."""
    mod = _update_ai_module()
    state = {"state": {"pid": 1, "job": True, "app": str(tmp_path / "service" / "app" / "build-old")}, "terminals": 2}
    steps, good = _orca_windows_release(mod, monkeypatch, tmp_path, b"official", state)
    assert mod.update_orca_windows() == "ok" and len(steps) == 1
    assert "2 live terminals" in capsys.readouterr().out


# (jarvis) 셀 수 없으면 시간까지 기다리므로 None 을 두 번 센다
@pytest.mark.parametrize("counts, reason", [([None, None], "could not be counted"), ([0, 0], "did not switch within"), ([3], "3 live terminals")])
def test_orca_windows_update_names_why_the_server_switch_was_deferred(tmp_path, monkeypatch, capsys, counts, reason):
    """covers: ORCA-6 (AI-55c) — 미룬 이유를 터미널 수·셀 수 없음·시간 초과로 구분해 알린다."""
    mod = _update_ai_module()
    clock = iter([0, 0, 1, 1, 2, 2, 3])
    monkeypatch.setattr(mod, "ORCA_SERVER_SWITCH_SECONDS", 1)
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(mod.orca_server, "app_identity", lambda root: "build-new")
    monkeypatch.setattr(mod.orca_server, "running_runner_state", lambda root: {"app": str(tmp_path / "build-old")})
    monkeypatch.setattr(mod.orca_server, "server_query_cli", lambda server: "orca.exe")
    monkeypatch.setattr(mod.orca_server, "live_terminal_count", lambda cli, profile: counts.pop(0))
    server = {"service_root": str(tmp_path / "service"), "server_profile": str(tmp_path / "orca")}
    assert mod.wait_for_orca_server_switch(server, tmp_path / "install") is False
    assert reason in capsys.readouterr().out


@pytest.mark.parametrize("payload", [b"tampered", b"official but longer"])
def test_orca_windows_update_rejects_a_tampered_installer_of_any_size(tmp_path, monkeypatch, payload):
    """covers: ORCA-6 (AI-55a) — 크기가 같아도(SHA-512), 달라도(크기) 설치기를 띄우지 않는다."""
    mod = _update_ai_module()
    state = {"state": {"pid": 1, "job": True, "app": str(tmp_path / "service" / "app" / "build-old")}}
    steps, _ = _orca_windows_release(mod, monkeypatch, tmp_path, payload, state)
    assert mod.update_orca_windows() == "failed" and steps == []


def test_orca_windows_update_fails_when_the_installer_fails(tmp_path, monkeypatch):
    """covers: ORCA-6 (AI-55a) — 설치기 종료 코드가 실패면 결과도 실패다."""
    mod = _update_ai_module()
    state = {"state": {"pid": 1, "job": True, "app": str(tmp_path / "service" / "app" / "build-old")}}
    steps, _ = _orca_windows_release(mod, monkeypatch, tmp_path, b"official", state)
    monkeypatch.setattr(mod, "run_step", lambda label, argv, **kwargs: steps.append(argv) and False)
    assert mod.update_orca_windows() == "failed" and len(steps) == 1


def test_orca_windows_update_is_not_refused_when_the_server_is_down_or_already_latest(tmp_path, monkeypatch):
    """covers: ORCA-6 (AI-55b) — 거부는 설치 폴더에서 서버가 실제로 돌 때만이다."""
    mod = _update_ai_module()
    steps, _ = _orca_windows_release(mod, monkeypatch, tmp_path, b"official", {"state": None})
    monkeypatch.setattr(mod.orca_server, "runtime_is_active", lambda profile: False)
    assert mod.update_orca_windows() == "ok" and len(steps) == 1
    monkeypatch.setattr(mod.orca_server, "runtime_is_active", lambda profile: True)
    assert mod.update_orca_windows() == "ok" and len(steps) == 1  # 이미 최신이라 설치기도 거부도 없다


def test_orca_windows_update_refuses_while_the_server_runs_from_the_install_directory(tmp_path, monkeypatch, capsys):
    """covers: ORCA-6 (AI-55b) — 예전 구조의 서버는 설치기가 끝내지 못해 'cannot be closed' 로 멈춘다. 설치기를 띄우지 않는다."""
    mod = _update_ai_module()
    steps, _ = _orca_windows_release(mod, monkeypatch, tmp_path, b"official", {"state": None})
    assert mod.update_orca_windows() == "failed" and steps == []
    assert "setup --tools orca" in capsys.readouterr().out


def test_an_install_newer_than_the_release_manifest_is_not_downgraded(tmp_path, monkeypatch):
    """covers: ORCA-7 (AI-65) — 앱 안에서 받은 prerelease 나 늦게 갱신된 매니페스트 때문에 설치본이 더 새로우면 이전 판 설치기를 돌리지 않는다."""
    mod = _update_ai_module()
    assert mod.is_current("9.9.10", "9.9.9") and mod.is_current("9.9.9", "9.9.9")
    assert not mod.is_current("9.9.8", "9.9.9") and not mod.is_current("", "9.9.9")
    # (jarvis) deb 부분은 jarvis orca_update.check()·execute 가 맡는다. 여기서는 Windows 설치기와 is_current 만 본다.
    steps, _ = _orca_windows_release(mod, monkeypatch, tmp_path, b"official", {"state": None})
    monkeypatch.setattr(mod.orca_release, "installed_version", lambda plan: "9.9.10")
    assert mod.update_orca_windows() == "ok" and steps == []



def test_orca_windows_update_refuses_a_release_other_than_the_approved_target(tmp_path, monkeypatch, capsys):
    """covers: ORCA-13 (jarvis) — 승인한 계획의 목표 버전과 지금 최신이 다르면 받지도 설치기를 띄우지도 않는다."""
    mod = _update_ai_module()
    steps, _ = _orca_windows_release(mod, monkeypatch, tmp_path, b"official", {"state": None})
    monkeypatch.setattr(mod.orca_release, "download", lambda *args: pytest.fail("downloaded an unapproved release"))
    assert mod.update_orca_windows(expect="9.9.8") == "failed" and steps == []
    assert "plan again" in capsys.readouterr().out

load_tests = collect(globals())
