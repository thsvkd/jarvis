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
                  mock.patch.object(hooks, "_has_trash", return_value=True)):
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


    def test_a_foreign_file_blocks_everything_and_is_never_adopted(self):
        """covers: HOOKS-3 — 다른 파일이 모두 최신이어도 막힌 계획은 아무것도 하지 않고, 영수증에 그 파일을 넣지 않는다."""
        (self.claude / "hooks" / "shims").mkdir(parents=True)
        mine = self.claude / "hooks" / "danger_guard.py"
        mine.write_text("# 사용자 훅\n")
        (self.claude / "hooks" / "shims" / "rm").write_bytes(Path(hooks.HOOKS, "shims", "rm").read_bytes())
        for _ in range(2):
            plan = hooks.plan()
            self.assertTrue(any(b["step"] == "files" for b in plan["blocked"]))
            self.assertEqual(hooks.apply(plan["plan_id"])["status"], "failed")
        self.assertEqual(mine.read_text(), "# 사용자 훅\n")
        self.assertFalse((self.claude / "settings.json").exists())
        self.assertFalse((self.claude / ".claude-code-setup.json").exists())

    def test_a_blocked_plan_runs_no_earlier_step(self):
        """covers: HOOKS-2"""
        with mock.patch.object(hooks, "python_invoke", return_value=None):
            plan = hooks.plan()
            out = hooks.apply(plan["plan_id"])
        self.assertEqual(out["status"], "failed")
        self.assertFalse((self.claude / "hooks").exists())

    def test_a_trash_tool_that_cannot_be_installed_does_not_stop_the_guard(self):
        """covers: HOOKS-2"""
        step = {"id": "trash", "summary": "x", "risk": "install", "argv": ["false"]}
        with mock.patch.object(hooks, "_has_trash", return_value=False), \
                mock.patch.object(hooks, "_trash_steps", return_value=[step]):
            plan, out = self.install()
        self.assertEqual(out["status"], "done", out)
        self.assertEqual(out["steps"][0]["result"], "skipped")
        self.assertTrue((self.claude / "hooks" / "danger_guard.py").exists())

    @unittest.skipIf(sys.platform == "win32", "POSIX 휴지통 도구 경로")
    def test_trash_is_found_in_the_users_bin_and_apt_is_its_own_sudo_step(self):
        """covers: HOOKS-1 — 게이트웨이 PATH 에 ~/.local/bin 이 없어도 pip --user 로 깐 trash-put 을 찾는다."""
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        real = HAS_TRASH
        with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}:/usr/bin:/bin"}), mock.patch.object(hooks, "_has_trash", real), \
                mock.patch.object(hooks.site, "getuserbase", return_value=str(self.home / "ub")), \
                mock.patch.object(hooks.sys, "platform", "linux"):
            self.assertFalse(real(sys.executable))
            apt = bin_dir / "apt-get"
            apt.write_text("#!/bin/sh\nexit 0\n")
            apt.chmod(0o755)
            steps = hooks._trash_steps(sys.executable)
            self.assertEqual([(s["id"], s["risk"]) for s in steps], [("trash", "install"), ("trash_apt", "sudo")])
            self.assertEqual(steps[1]["argv"], ["sudo", "-n", "apt-get", "install", "-y", "trash-cli"])
            local = self.home / ".local" / "bin"
            local.mkdir(parents=True)
            (local / "trash-put").write_text("#!/bin/sh\n")
            (local / "trash-put").chmod(0o755)
            self.assertTrue(real(sys.executable))
            self.assertEqual(hooks._trash_steps(sys.executable), [])

    def test_a_permission_mode_the_merger_does_not_know_is_kept(self):
        """covers: HOOKS-2"""
        self.claude.mkdir()
        (self.claude / "settings.json").write_text('{"permissions": {"defaultMode": "dontAsk"}}')
        plan, out = self.install()
        self.assertEqual(out["status"], "done", out)
        self.assertEqual(self.settings()["permissions"]["defaultMode"], "dontAsk")

    def test_the_permission_mode_change_comes_first_in_the_summary(self):
        """covers: HOOKS-1"""
        settings = next(s for s in hooks.plan()["steps"] if s["id"] == "settings")
        self.assertTrue(settings["changes"][0].startswith("permissions.defaultMode"), settings["changes"])
        self.assertIn("permissions.defaultMode", settings["summary"])

    def test_a_registration_whose_python_is_gone_blocks_and_degrades(self):
        """covers: HOOKS-1 — 같은 이름으로 등록돼 있으면 병합기가 고치지 않으므로 가드가 꺼진 채 남는다."""
        self.install()
        s = self.settings()
        for entries in s["hooks"].values():
            for e in entries:
                for h in e.get("hooks", []):
                    if "danger_guard.py" in h.get("command", ""):
                        h["command"] = 'no-such-python -X utf8 "/gone/danger_guard.py"'
        (self.claude / "settings.json").write_text(json.dumps(s))
        self.assertEqual(hooks.status()["health"], "degraded")
        self.assertTrue(any("no-such-python" in b["reason"] for b in hooks.plan()["blocked"]))

    def test_a_file_changed_after_the_plan_makes_the_plan_stale(self):
        """covers: HOOKS-2"""
        self.install()
        guard = self.claude / "hooks" / "danger_guard.py"
        guard.write_text("# 옛 판\n")
        plan = hooks.plan()
        guard.write_text("# 그사이 또 바뀜\n")
        self.assertEqual(hooks.apply(plan["plan_id"])["status"], "stale")
        self.assertEqual(guard.read_text(), "# 그사이 또 바뀜\n")


    def test_a_file_changed_while_the_trash_tool_installs_is_not_overwritten(self):
        """covers: HOOKS-2 — 계획 뒤가 아니라 apply 도중(긴 설치 단계)에 바뀐 파일도 덮어쓰지 않는다."""
        self.install()
        guard = self.claude / "hooks" / "danger_guard.py"
        guard.write_text("# 옛 판\n")
        step = {"id": "trash", "summary": "x", "risk": "install", "argv": ["false"]}

        def meanwhile(_step):
            guard.write_text("# 설치 중에 바뀜\n")
            return "skipped", "x"

        with mock.patch.object(hooks, "_has_trash", return_value=False), \
                mock.patch.object(hooks, "_trash_steps", return_value=[step]), \
                mock.patch.object(hooks, "_install_trash", side_effect=meanwhile):
            plan = hooks.plan()
            out = hooks.apply(plan["plan_id"])
        self.assertEqual((out["status"], out["steps"][-1]["id"]), ("failed", "files"))
        self.assertEqual(guard.read_text(), "# 설치 중에 바뀜\n")

HAS_TRASH = hooks._has_trash  # 패치하기 전의 원래 함수


if __name__ == "__main__":
    unittest.main()
