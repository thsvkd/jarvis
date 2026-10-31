"""훅 모듈의 계약 층: 임시 홈에서 status·plan·apply·uninstall. 실제 홈·pip·apt 는 쓰지 않는다."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis import hooks_module as hooks


class HooksModuleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.claude = self.home / ".claude"
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        for p in (mock.patch.object(hooks, "python_invoke", return_value=sys.executable),
                  mock.patch.object(hooks, "_trash", return_value=(True, []))):
            p.start()
            self.addCleanup(p.stop)

    def settings(self):
        return json.loads((self.claude / "settings.json").read_text())

    def receipt(self):
        return json.loads((self.claude / ".claude-code-setup.json").read_text())

    def snapshot(self):
        return {str(p): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}

    def install(self):
        plan = hooks.plan()
        return plan, hooks.apply(plan["plan_id"])

    def test_status_and_plan_only_read(self):
        """covers: HOOKS-1"""
        (self.claude).mkdir()
        (self.claude / "settings.json").write_text('{"model": "sonnet"}')
        before = self.snapshot()
        st = hooks.status()
        plan = hooks.plan()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((st["installed"], st["health"]), (False, "down"))
        self.assertEqual([s["id"] for s in plan["steps"]], ["files", "settings", "selftest"])
        self.assertEqual(plan["action"], "install")

    def test_install_writes_files_merges_settings_and_reaches_current(self):
        """covers: HOOKS-2"""
        self.claude.mkdir()
        (self.claude / "settings.json").write_text('{"model": "sonnet", "statusLine": {"type": "command", "command": "x"}}')
        plan, out = self.install()
        self.assertEqual(out["status"], "done", out)
        self.assertEqual([s["id"] for s in out["steps"]], ["files", "settings", "selftest"])
        for name in hooks.FILES:
            self.assertEqual(hooks._digest(self.claude / "hooks" / name), hooks._digest(os.path.join(hooks.HOOKS, name)))
        s = self.settings()
        self.assertEqual((s["model"], s["statusLine"]["command"]), ("sonnet", "x"))  # 다른 최상위 키는 그대로
        self.assertIn("danger_guard.py", json.dumps(s["hooks"]))
        self.assertEqual(sorted(self.receipt()["hook_files"]), sorted(hooks.FILES))
        self.assertEqual(hooks.plan()["steps"], [])
        self.assertEqual(hooks.status()["health"], "ok")

    def test_a_file_the_receipt_does_not_own_is_never_overwritten(self):
        """covers: HOOKS-3"""
        (self.claude / "hooks").mkdir(parents=True)
        mine = self.claude / "hooks" / "danger_guard.py"
        mine.write_text("# 사용자 훅\n")
        plan, out = self.install()
        self.assertTrue(any(b["step"] == "files" for b in plan["blocked"]))
        self.assertEqual(out["status"], "failed")
        self.assertEqual(mine.read_text(), "# 사용자 훅\n")

    def test_a_changed_owned_file_is_updated(self):
        """covers: HOOKS-2"""
        self.install()
        guard = self.claude / "hooks" / "danger_guard.py"
        guard.write_text("# 옛 판\n")
        plan = hooks.plan()
        self.assertIn("danger_guard.py", plan["steps"][0]["summary"])
        self.assertEqual(hooks.apply(plan["plan_id"])["status"], "done")
        self.assertEqual(hooks._digest(guard), hooks._digest(os.path.join(hooks.HOOKS, "danger_guard.py")))

    def test_the_users_permission_mode_is_kept(self):
        """covers: HOOKS-2"""
        self.claude.mkdir()
        (self.claude / "settings.json").write_text('{"permissions": {"defaultMode": "default"}}')
        self.install()
        self.assertEqual(self.settings()["permissions"]["defaultMode"], "default")

    def test_a_failing_self_test_fails_the_apply(self):
        """covers: HOOKS-2"""
        with mock.patch.object(hooks, "selftest", return_value=(1, "rm -rf ~: 판정이 없습니다")):
            plan, out = self.install()
        self.assertEqual((out["status"], out["steps"][-1]["id"], out["steps"][-1]["result"]), ("failed", "selftest", "failed"))

    def test_apply_with_an_old_plan_runs_nothing(self):
        """covers: HOOKS-2"""
        plan = hooks.plan()
        (self.claude / "hooks").mkdir(parents=True)
        (self.claude / "hooks" / "danger_guard.py").write_text("# 그사이 생긴 파일\n")
        out = hooks.apply(plan["plan_id"])
        self.assertEqual(out["status"], "stale")
        self.assertFalse((self.claude / "settings.json").exists())

    def test_uninstall_removes_only_what_the_install_added(self):
        """covers: HOOKS-4"""
        self.claude.mkdir()
        (self.claude / "settings.json").write_text('{"model": "sonnet", "permissions": {"allow": ["Bash(ls:*)"]}}')
        self.install()
        r = self.receipt()
        r["tool_installs"] = {"orca": {"version": "1"}}
        (self.claude / ".claude-code-setup.json").write_text(json.dumps(r))
        dry = hooks.uninstall(dry_run=True)
        self.assertTrue(all((self.claude / "hooks" / n).exists() for n in hooks.FILES))
        out = hooks.uninstall(plan_id=dry["plan_id"])
        self.assertEqual(out["status"], "done", out)
        self.assertFalse(any((self.claude / "hooks" / n).exists() for n in hooks.FILES))
        s = self.settings()
        self.assertEqual(s["model"], "sonnet")
        self.assertIn("Bash(ls:*)", s["permissions"]["allow"])
        self.assertNotIn("danger_guard.py", json.dumps(s.get("hooks", {})))
        self.assertEqual(self.receipt(), {"tool_installs": {"orca": {"version": "1"}}})

    def test_uninstall_without_the_dry_run_plan_is_stale(self):
        """covers: HOOKS-4"""
        self.install()
        self.assertEqual(hooks.uninstall(plan_id="0" * 64)["status"], "stale")
        self.assertTrue((self.claude / "hooks" / "danger_guard.py").exists())


if __name__ == "__main__":
    unittest.main()
