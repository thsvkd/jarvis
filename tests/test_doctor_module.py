"""doctor 모듈의 계약 층: 모듈 CLI 로 격리된 홈과 가짜 실행 파일만 본다(실제 홈·하네스는 보지 않는다)."""
import json
import os
import site
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import psutil

from jarvis import doctor_module, orca_rpc

ROOT = Path(__file__).resolve().parents[1]
SECRET = "sk-test-0123456789abcdef"
# 모듈 CLI 그대로지만 PATH 보강(harness_module.user_path)을 임시 홈의 ~/.local/bin 으로 좁힌다.
# 실제 보강은 기기의 /usr/local/bin·Homebrew 도 붙여 이 기기의 하네스가 섞인다(보강 자체는 test_harness_module 이 본다)
CLI = ("import os, sys; from pathlib import Path; from jarvis import harness_module as h; "
       "h.user_path = lambda p: str(Path.home() / '.local' / 'bin') + os.pathsep + p; "
       "from jarvis.__main__ import main; sys.exit(main())")


class DoctorModuleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.bin = Path(tmp.name) / "bin"
        self.bin2 = Path(tmp.name) / "bin2"
        for d in (self.home, self.bin, self.bin2):
            d.mkdir()
        for d in (self.bin, self.bin2):
            stub = d / "claude"
            stub.write_text("#!/bin/sh\necho '9.9.9 (Claude Code)'\n")
            stub.chmod(0o755)
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text(f'api_key = "{SECRET}"\n')

    def cli(self, *args):
        env = {**os.environ, "HOME": str(self.home), "PATH": f"{self.bin}{os.pathsep}{self.bin2}{os.pathsep}/usr/bin:/bin",
               "JARVIS_CONFIG": os.devnull, "PYTHONUSERBASE": site.getuserbase()}  # 사용자 site 의 psutil
        r = subprocess.run([sys.executable, "-c", CLI, "module", "doctor", *args, "--json"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=120)
        return r.returncode, json.loads(r.stdout), r.stdout + r.stderr

    @unittest.skipIf(sys.platform == "win32", "가짜 실행 파일이 POSIX 셸 스크립트다")
    def test_status_reports_tools_and_findings_by_kind_without_secret_values(self):
        """covers: DOCTOR-7"""
        code, out, raw = self.cli("status")
        self.assertEqual(code, 0, raw)
        self.assertEqual(out["health"], "degraded")
        claude = next(t for t in out["details"]["tools"] if t["id"] == "claude")
        self.assertEqual(claude["version"], "9.9.9 (Claude Code)")
        self.assertEqual(claude["found"], [str(self.bin / "claude"), str(self.bin2 / "claude")])
        missing = next(t for t in out["details"]["tools"] if t["id"] == "codex")
        self.assertEqual(missing["found"], [])
        self.assertTrue(missing["hint"])
        kinds = [f["kind"] for f in out["details"]["findings"]]
        self.assertEqual(kinds, ["duplicate", "secret"])
        self.assertIn("~/.codex/config.toml:1 key api_key", out["details"]["findings"][1]["message"])
        self.assertNotIn(SECRET, raw)

    @unittest.skipIf(sys.platform == "win32", "가짜 실행 파일이 POSIX 셸 스크립트다")
    def test_paths_under_home_are_shown_with_a_tilde(self):
        """covers: DOCTOR-7"""
        local = self.home / ".local" / "bin"
        local.mkdir(parents=True)
        (self.bin / "claude").rename(local / "claude")
        self.bin = local
        code, out, raw = self.cli("status")
        self.assertEqual(code, 0, raw)
        claude = next(t for t in out["details"]["tools"] if t["id"] == "claude")
        self.assertEqual(claude["found"][0], "~/.local/bin/claude")
        self.assertNotIn(str(self.home), raw)

    @unittest.skipIf(sys.platform == "win32", "가짜 실행 파일이 POSIX 셸 스크립트다")
    def test_status_finds_harnesses_in_user_bin_missing_from_the_service_path(self):
        """covers: DOCTOR-7. 게이트웨이 서비스 PATH 에는 ~/.local/bin 이 없어도 설치된 하네스를 찾는다"""
        local = self.home / ".local" / "bin"
        local.mkdir(parents=True)
        stub = local / "codex"
        stub.write_text("#!/bin/sh\necho 'codex-cli 1.2.3'\n")
        stub.chmod(0o755)
        code, out, raw = self.cli("status")
        self.assertEqual(code, 0, raw)
        codex = next(t for t in out["details"]["tools"] if t["id"] == "codex")
        self.assertEqual((codex["found"], codex["version"], codex["hint"]), (["~/.local/bin/codex"], "codex-cli 1.2.3", None))

    def test_plan_has_no_steps_and_apply_is_current_or_stale(self):
        """covers: DOCTOR-7"""
        code, plan, raw = self.cli("plan")
        self.assertEqual((code, plan["steps"], plan["blocked"]), (0, [], []), raw)
        code, result, raw = self.cli("apply", "--plan-id", plan["plan_id"])
        self.assertEqual((code, result["status"]), (0, "current"), raw)
        code, result, raw = self.cli("apply", "--plan-id", "0" * 64)
        self.assertEqual((code, result["status"]), (3, "stale"), raw)

    def test_status_writes_nothing_under_home(self):
        """covers: DOCTOR-1"""
        before = sorted(p.relative_to(self.home) for p in self.home.rglob("*"))
        self.cli("status")
        self.cli("plan")
        self.assertEqual(sorted(p.relative_to(self.home) for p in self.home.rglob("*")), before)


AUTH, TOKEN = "AUTH-SECRET-1234", "DEV-TOKEN-5678"


def wait_exec(pid):
    """fork 직후 exec 전에는 부모(테스트)의 명령행이 보인다. 가짜 프로세스의 명령행이 보일 때까지 기다린다."""
    for _ in range(500):
        if "import time; time.sleep(60)" in psutil.Process(pid).cmdline():
            return
        time.sleep(0.01)
    raise AssertionError("가짜 프로세스의 명령행이 보이지 않습니다")


@unittest.skipIf(sys.platform == "win32", "가짜 런타임 경로가 POSIX 홈 기준이다")
class OrcaSplitTest(unittest.TestCase):
    """정식 서버(~/.config/orca)와 SSH 호스트 연결 orcad(~/.orca)가 함께 도는 기기. 프로세스는 테스트가 띄운 sleep 뿐이다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.official = self.spawn()
        self.orcad = self.spawn(str(self.home / ".orca-remote" / "orcad-x" / "orcad.js"))
        self.runtime(self.home / ".config" / "orca", self.official.pid)
        self.runtime(self.home / ".orca", self.orcad.pid)
        (self.home / ".orca" / "orca-devices.json").write_text(json.dumps(
            [{"deviceId": "638d1290", "name": "kgolf", "scope": "runtime", "token": TOKEN, "lastSeenAt": 5}]))
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.census = []  # 차례로 돌려줄 열린 터미널 수. 비면 0
        self.calls = []

        def call(method, params=None, timeout=12, d=None):
            self.calls.append((method, params, d))
            return {"liveSessions": self.census.pop(0) if self.census else 0, "inProcessSessions": 0}
        rpc = mock.patch.object(orca_rpc, "call", call)
        rpc.start()
        self.addCleanup(rpc.stop)

    def spawn(self, *argv):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", *argv])
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        wait_exec(p.pid)
        return p

    def runtime(self, d, pid):
        d.mkdir(parents=True)
        (d / "orca-runtime.json").write_text(json.dumps({"pid": pid, "runtimeId": "r", "authToken": AUTH, "transports": []}))

    def test_status_reports_orca_split_with_both_profiles_and_guidance_without_tokens(self):
        """covers: DOCTOR-8"""
        env = {**os.environ, "PATH": "/usr/bin:/bin", "JARVIS_CONFIG": os.devnull, "PYTHONUSERBASE": site.getuserbase()}
        r = subprocess.run([sys.executable, "-c", CLI, "module", "doctor", "status", "--json"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=120)
        raw = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, raw)
        out = json.loads(r.stdout)
        split = [f["message"] for f in out["details"]["findings"] if f["kind"] == "orca_split"]
        self.assertEqual(len(split), 1, raw)
        for part in ("~/.config/orca", f"pid {self.official.pid}", "~/.orca", f"pid {self.orcad.pid}", "kgolf",
                     "orca environment stop", "SSH 터널"):
            self.assertIn(part, split[0])
        self.assertEqual(out["health"], "degraded")
        for secret in (AUTH, TOKEN):
            self.assertNotIn(secret, raw)

    def test_no_split_when_only_one_runtime_is_alive(self):
        """covers: DOCTOR-8"""
        self.orcad.kill()
        self.orcad.wait()
        self.assertIsNone(doctor_module.split())
        self.assertEqual(doctor_module.plan()["steps"], [])

    def test_plan_has_a_stop_step_bound_to_the_process_only_at_zero_terminals(self):
        """covers: DOCTOR-9"""
        before = sorted(self.home.rglob("*"))
        plan = doctor_module.plan()
        self.assertEqual(plan["blocked"], [])
        step, = plan["steps"]
        self.assertEqual((step["id"], step["risk"], step["pid"]), ("stop_ssh_orcad", "config", self.orcad.pid))
        self.assertEqual(step["started"], round(psutil.Process(self.orcad.pid).create_time(), 2))
        self.assertEqual(self.calls, [("orcad.terminalCensus", {"activatedAt": 0}, str(self.home / ".orca"))])
        self.census = [2]
        plan = doctor_module.plan()
        self.assertEqual(plan["steps"], [])
        self.assertEqual(plan["blocked"][0]["step"], "stop_ssh_orcad")
        self.assertIn("2개", plan["blocked"][0]["reason"])
        self.assertEqual(sorted(self.home.rglob("*")), before)  # plan 은 홈에 쓰지 않는다

    def test_census_failure_blocks_the_step(self):
        """covers: DOCTOR-9"""
        with mock.patch.object(orca_rpc, "call", side_effect=RuntimeError("소켓 없음")):
            plan = doctor_module.plan()
        self.assertEqual(plan["steps"], [])
        self.assertIn("소켓 없음", plan["blocked"][0]["reason"])

    def test_apply_stops_only_the_planned_orcad(self):
        """covers: DOCTOR-9"""
        before = sorted(self.home.rglob("*"))
        plan = doctor_module.plan()
        self.assertEqual(doctor_module.apply("0" * 64)["status"], "stale")
        self.assertIsNone(self.orcad.poll())
        result = doctor_module.apply(plan["plan_id"])
        self.assertEqual(result["status"], "done", result)
        self.assertEqual([s["result"] for s in result["steps"]], ["ok"])
        self.assertIsNotNone(self.orcad.poll())
        self.assertIsNone(self.official.poll())  # 정식 서버는 그대로
        self.assertEqual(sorted(self.home.rglob("*")), before)  # 파일은 지우지 않는다

    def test_apply_is_deferred_while_terminals_are_open(self):
        """covers: DOCTOR-9"""
        self.census = [1]
        plan = doctor_module.plan()
        self.census = [1]
        result = doctor_module.apply(plan["plan_id"])
        self.assertEqual((result["status"], result["steps"][0]["result"]), ("deferred", "deferred"))
        self.census = [0, 3]  # 다시 세운 계획은 0, 멈추기 직전 다시 셀 때 3
        plan = doctor_module.plan()
        self.census = [0, 3]
        result = doctor_module.apply(plan["plan_id"])
        self.assertEqual(result["status"], "deferred", result)
        self.assertIsNone(self.orcad.poll())

    def test_a_runtime_pid_reused_by_another_process_is_not_a_split_and_is_never_stopped(self):
        """covers: DOCTOR-8, DOCTOR-9"""
        other = self.spawn()  # ~/.orca-remote 의 orcad.js 가 명령행에 없는 프로세스
        self.runtime_pid(other.pid)  # orcad 가 죽고 남은 런타임 파일의 pid 를 다른 프로세스가 재사용함
        self.assertIsNone(doctor_module.split())
        plan = doctor_module.plan()
        self.assertEqual((plan["steps"], plan["blocked"]), ([], []))
        self.assertEqual(doctor_module.apply(plan["plan_id"])["status"], "current")
        # 계획과 apply 사이에 pid 가 재사용돼도 _stop 이 신원을 다시 보고 멈추지 않는다
        started = round(psutil.Process(other.pid).create_time(), 2)
        self.assertEqual(doctor_module._stop({"pid": other.pid, "started": started})[0], "failed")
        self.assertIsNone(other.poll())
        self.assertIsNone(self.official.poll())

    def test_census_is_never_asked_of_the_official_server(self):
        """covers: DOCTOR-9"""
        with mock.patch.object(doctor_module.orca, "find_dir", lambda dirs: None if dirs == doctor_module.orca.SSH_DIRS else "x"):
            with self.assertRaises(RuntimeError):
                doctor_module._live()
        self.assertEqual(self.calls, [])

    def runtime_pid(self, pid):
        path = self.home / ".orca" / "orca-runtime.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "pid": pid}))


if __name__ == "__main__":
    unittest.main()
