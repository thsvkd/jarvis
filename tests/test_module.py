"""모듈 공통 계약(docs/SPEC.md 의 모듈 계약): module.cli 의 거절·종료 코드·표준 출력과 plan_id. 가짜 모듈만 쓴다."""
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import types
import unittest
from unittest import mock

from jarvis import module

ROOT = str(pathlib.Path(__file__).resolve().parents[1])
FAKE = "jarvis_fake_module_for_test"
# 하위 프로세스 안에서 쓰는 가짜 모듈. fd 를 건드리는 동작은 실제 프로세스에서만 볼 수 있다
SUBPROCESS_FAKE = ("import subprocess, sys, types; from jarvis import module\n"
                   f"fake = types.ModuleType({FAKE!r}); sys.modules[{FAKE!r}] = fake; module.MODULES['fake'] = {FAKE!r}\n")


def run_script(script, **env):
    return subprocess.run([sys.executable, "-c", SUBPROCESS_FAKE + script], capture_output=True, timeout=60, cwd=ROOT,
                          env={**os.environ, "JARVIS_CONFIG": os.devnull, **env})


class Cli(unittest.TestCase):
    def setUp(self):
        self.fake = types.ModuleType(FAKE)
        self.fake.status = mock.Mock(return_value={"module": "fake"})
        self.fake.plan = mock.Mock(return_value={"module": "fake", "steps": []})
        self.fake.apply = mock.Mock(return_value={"status": "done", "steps": []})
        self.fake.uninstall = mock.Mock(return_value={"status": "done", "steps": []})
        for p in (mock.patch.dict(sys.modules, {FAKE: self.fake}), mock.patch.dict(module.MODULES, fake=FAKE)):
            p.start()
            self.addCleanup(p.stop)

    def cli(self, *argv):
        """(종료 코드, 표준 출력, 표준 오류)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.cli(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli_prints_one_json_document_and_sends_module_logs_to_stderr(self):
        """covers: MODULE-3"""
        self.fake.status.side_effect = lambda: (print("진행 로그"), {"module": "fake"})[1]
        code, out, err = self.cli("fake", "status", "--json")
        self.assertEqual((code, json.loads(out)), (0, {"module": "fake"}))
        self.assertIn("진행 로그", err)

    def test_cli_refuses_apply_without_a_plan_id(self):
        """covers: MODULE-1"""
        code, out, err = self.cli("fake", "apply", "--json")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("--plan-id", err)
        self.fake.apply.assert_not_called()

    def test_cli_refuses_apply_with_dry_run(self):
        """covers: MODULE-1"""
        code, out, _ = self.cli("fake", "apply", "--json", "--dry-run", "--plan-id", "x")
        self.assertEqual((code, out), (1, ""))
        self.fake.apply.assert_not_called()

    def test_apply_gets_the_plan_id(self):
        self.assertEqual(self.cli("fake", "apply", "--json", "--plan-id", "abc")[0], 0)
        self.fake.apply.assert_called_once_with("abc")

    def test_uninstall_needs_a_plan_id_unless_it_is_a_dry_run(self):
        """covers: MODULE-1"""
        code, out, err = self.cli("fake", "uninstall", "--json")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("--plan-id", err)
        self.fake.uninstall.assert_not_called()
        self.assertEqual(self.cli("fake", "uninstall", "--json", "--dry-run")[0], 0)
        self.fake.uninstall.assert_called_once_with(True, None)
        self.fake.uninstall.reset_mock()
        self.assertEqual(self.cli("fake", "uninstall", "--json", "--plan-id", "abc")[0], 0)
        self.fake.uninstall.assert_called_once_with(False, "abc")

    def test_an_update_held_for_open_terminals_is_deferred_with_exit_code_2(self):
        """covers: MODULE-2"""
        result = {"status": "deferred", "steps": [{"id": "package", "result": "deferred", "message": "열린 터미널이 있어 보류합니다"}]}
        self.fake.apply.return_value = self.fake.uninstall.return_value = result
        for action in ("apply", "uninstall"):
            code, out, _ = self.cli("fake", action, "--json", "--plan-id", "abc")
            self.assertEqual((code, json.loads(out)), (2, result), action)

    def test_a_plan_id_that_no_longer_matches_is_stale_with_exit_code_3(self):
        """covers: MODULE-2"""
        result = {"status": "stale", "plan_id": "지금", "steps": []}
        self.fake.apply.return_value = self.fake.uninstall.return_value = result
        for action in ("apply", "uninstall"):
            code, out, _ = self.cli("fake", action, "--json", "--plan-id", "0" * 64)
            self.assertEqual((code, json.loads(out)["status"]), (3, "stale"), action)

    def test_done_and_current_exit_0_and_failed_exits_1(self):
        """covers: MODULE-2"""
        for status, want in (("done", 0), ("current", 0), ("failed", 1)):
            self.fake.apply.return_value = self.fake.uninstall.return_value = {"status": status, "steps": []}
            for action in ("apply", "uninstall"):
                self.assertEqual(self.cli("fake", action, "--json", "--plan-id", "abc")[0], want, (status, action))

    def test_a_module_that_raises_answers_one_json_error_and_restores_stdout(self):
        """covers: MODULE-3"""
        self.fake.status.side_effect = RuntimeError("읽지 못함")
        before = os.fstat(1)
        code, out, _ = self.cli("fake", "status", "--json")
        self.assertEqual((code, json.loads(out)), (1, {"module": "fake", "error": "RuntimeError: 읽지 못함"}))
        self.assertTrue(os.path.samestat(before, os.fstat(1)))

    def test_an_unknown_module_or_action_prints_only_the_usage_to_stderr(self):
        for argv in (("nope", "status", "--json"), ("fake", "nope", "--json"), ("fake",), ()):
            code, out, err = self.cli(*argv)
            self.assertEqual((code, out), (1, ""), argv)
            self.assertIn("사용법", err, argv)


class Process(unittest.TestCase):
    """fd 와 콘솔 인코딩은 실제 프로세스에서만 볼 수 있다."""

    def test_apply_refuses_dry_run_and_child_output_never_reaches_stdout(self):
        """covers: MODULE-1, MODULE-3"""
        script = ("def status():\n"
                  "    subprocess.run([sys.executable, '-c', 'print(\"apt: Removing a-package\")'])\n"
                  "    return {'module': 'fake'}\n"
                  "fake.status = status\n"
                  "rc = module.cli(['fake', 'status', '--json'])\n"
                  "rc2 = module.cli(['fake', 'apply', '--json', '--dry-run', '--plan-id', 'x'])\n"
                  "sys.stderr.write(f'rc={rc},{rc2}')\n")
        p = run_script(script)
        self.assertEqual(json.loads(p.stdout), {"module": "fake"}, p.stderr)
        self.assertIn(b"apt: Removing a-package", p.stderr)
        self.assertIn(b"rc=0,1", p.stderr)

    def test_stdout_is_utf8_json_whatever_the_console_encoding(self):
        """covers: MODULE-3. Windows 콘솔(cp949)에서 한글 JSON 이 깨졌다. 표준 출력의 계약은 UTF-8 JSON 이다."""
        script = "fake.status = lambda: {'module': 'fake', 'error': '데스크톱 앱이 열려 있음'}\nmodule.cli(['fake', 'status', '--json'])\n"
        p = run_script(script, PYTHONIOENCODING="cp949")
        self.assertEqual(json.loads(p.stdout.decode("utf-8")), {"module": "fake", "error": "데스크톱 앱이 열려 있음"}, p.stderr)

    def test_every_module_imports_without_pwd(self):
        """Windows 에는 pwd·os.getuid 가 없다. 그래도 등록된 모듈을 모두 import 할 수 있다."""
        script = ("import importlib, os, sys\nsys.modules['pwd'] = None\nif hasattr(os, 'getuid'): del os.getuid\n"
                  "from jarvis import module\nfor path in module.MODULES.values(): importlib.import_module(path)\n")
        p = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, cwd=ROOT,
                           env={**os.environ, "JARVIS_CONFIG": os.devnull})
        self.assertEqual(p.returncode, 0, p.stderr)


class PlanId(unittest.TestCase):
    PLAN = {"module": "fake", "device": "here", "action": "update", "steps": [{"id": "a"}], "blocked": []}

    def test_plan_id_is_stable_and_follows_the_plan_content(self):
        sealed = module.seal(self.PLAN)
        self.assertEqual(sealed["plan_id"], module.plan_id(sealed))  # plan_id 자신은 계산에 들어가지 않는다
        self.assertEqual(sealed["plan_id"], module.seal(dict(reversed(list(self.PLAN.items()))))["plan_id"])  # 키 순서와 무관하다
        self.assertNotEqual(sealed["plan_id"], module.seal({**self.PLAN, "steps": [{"id": "b"}]})["plan_id"])
        self.assertRegex(sealed["plan_id"], r"^[0-9a-f]{64}$")

    def test_plan_id_is_bound_to_the_device(self):
        self.assertNotEqual(module.seal(self.PLAN)["plan_id"], module.seal({**self.PLAN, "device": "other"})["plan_id"])


if __name__ == "__main__":
    unittest.main()
