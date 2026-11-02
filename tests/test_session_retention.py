"""임시 홈에서 setup의 세션 보존 계약을 확인한다."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis import setup_module as setup


class SessionRetentionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.claude = self.home / ".claude"
        self.claude.mkdir()
        self.settings = self.claude / "settings.json"
        self.receipt = self.claude / ".claude-code-setup.json"
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.patches = [
            mock.patch.object(setup, "_snapshot", return_value={"receipt_harnesses": [], "newline_chord": "", "bins": []}),
            mock.patch.object(setup.stages(), "plan_actions", return_value=[]),
            mock.patch.object(setup, "REFRESH", ()),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_apply_preserves_settings_and_records_ownership(self):
        """covers: SETUP-36"""
        self.settings.write_text('{"model":"keep"}', encoding="utf-8")
        self.receipt.write_text('{"other":{"keep":true}}', encoding="utf-8")
        before = {p: p.read_bytes() for p in (self.settings, self.receipt)}
        plan = setup.plan()
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(setup.apply(plan["plan_id"])["status"], "done")
        self.assertEqual(json.loads(self.settings.read_text()), {"model": "keep", "cleanupPeriodDays": 36500})
        self.assertEqual(json.loads(self.receipt.read_text())["other"], {"keep": True})
        self.assertFalse(setup.plan()["steps"])
        self.assertTrue(list(self.claude.glob("settings.json.bak.*")))

    def test_user_value_is_reported_and_never_overwritten(self):
        """covers: SETUP-37"""
        self.settings.write_text('{"cleanupPeriodDays":7}', encoding="utf-8")
        before = self.settings.read_bytes()
        status = setup.status()["details"]["session_retention"]
        self.assertEqual(status["claude"]["value"], 7)
        self.assertEqual(status["claude"]["state"], "user_configured")
        self.assertEqual(status["codex"]["state"], "unknown")
        self.assertFalse(setup.plan()["steps"])
        self.assertEqual(before, self.settings.read_bytes())

    def test_changed_plan_is_stale_and_malformed_settings_are_blocked(self):
        """covers: SETUP-37"""
        plan = setup.plan()
        self.settings.write_text('{"other": true}', encoding="utf-8")
        self.assertEqual(setup.apply(plan["plan_id"])["status"], "stale")
        self.settings.write_text('{bad', encoding="utf-8")
        bad = setup.plan()
        self.assertTrue(bad["blocked"])
        self.assertEqual(bad["blocked"][0]["step"], "session-retention")
        self.assertIn("reason", bad["blocked"][0])
        self.assertTrue(any(step["id"] == "session-retention" for step in bad["steps"]))
        self.assertEqual(setup.status()["health"], "degraded")
        self.assertEqual(setup.apply(bad["plan_id"])["status"], "failed")
        self.assertEqual(self.settings.read_text(), '{bad')

    def test_uninstall_only_removes_unchanged_owned_value(self):
        """covers: SETUP-38"""
        self.settings.write_text('{"model":"keep"}', encoding="utf-8")
        self.assertEqual(setup.apply(setup.plan()["plan_id"])["status"], "done")
        before = self.settings.read_bytes(), self.receipt.read_bytes()
        dry = setup.uninstall(dry_run=True)
        self.assertEqual(before, (self.settings.read_bytes(), self.receipt.read_bytes()))
        self.assertEqual(setup.uninstall(plan_id=dry["plan_id"])["status"], "done")
        self.assertEqual(json.loads(self.settings.read_text()), {"model": "keep"})
        self.assertEqual(setup.apply(setup.plan()["plan_id"])["status"], "done")
        self.settings.write_text('{"model":"keep","cleanupPeriodDays":500}', encoding="utf-8")
        dry = setup.uninstall(dry_run=True)
        self.assertTrue(any("보존" in step["message"] for step in dry["steps"]))
        self.assertEqual(setup.uninstall(plan_id=dry["plan_id"])["status"], "deferred")
        self.assertEqual(json.loads(self.settings.read_text())["cleanupPeriodDays"], 500)

    def test_change_during_apply_preserves_user_settings_and_marks_incomplete(self):
        """covers: SETUP-37 — 영수증 기록 직후 설정이 바뀌어도 덮어쓰지 않는다."""
        from jarvis import session_retention
        self.settings.write_text('{"model":"before"}', encoding="utf-8")
        real_write = session_retention._write

        def change_after_receipt(path, data, *args):
            result = real_write(path, data, *args)
            if path == self.receipt and data.get("session_retention", {}).get("state") == "pending":
                self.settings.write_text('{"model":"changed"}', encoding="utf-8")
            return result

        with mock.patch.object(session_retention, "_write", side_effect=change_after_receipt):
            result = setup.apply(setup.plan()["plan_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(json.loads(self.settings.read_text()), {"model": "changed"})
        self.assertTrue(setup.plan()["blocked"])

    def test_link_and_invalid_receipt_are_never_written(self):
        """covers: SETUP-37 — 설정 링크와 잘못된 영수증은 쓰지 않는다."""
        target = self.home / "other.json"
        target.write_text('{}', encoding="utf-8")
        self.settings.symlink_to(target)
        self.assertTrue(setup.plan()["blocked"])
        self.assertEqual(target.read_text(), '{}')
        self.settings.unlink()
        self.receipt.write_text('[]', encoding="utf-8")
        self.assertTrue(setup.plan()["blocked"])
        self.assertFalse(self.settings.exists())

    def test_existing_unrecognized_receipt_key_is_preserved(self):
        """covers: SETUP-37 — 다른 소유자의 알 수 없는 영수증 키를 쓰지 않는다."""
        self.receipt.write_text('{"session_retention":null}', encoding="utf-8")
        plan = setup.plan()
        self.assertTrue(plan["blocked"])
        self.assertEqual(setup.apply(plan["plan_id"])["status"], "failed")
        self.assertEqual(json.loads(self.receipt.read_text()), {"session_retention": None})
        self.assertFalse(self.settings.exists())

    def test_changes_immediately_before_each_file_replacement_are_preserved(self):
        """covers: SETUP-37 — 임시 파일 작성 중 외부 설정·영수증 변경을 보존한다."""
        from jarvis import session_retention
        for target_name in ("settings", "receipt"):
            with self.subTest(target=target_name):
                self.settings.write_text('{"model":"before"}', encoding="utf-8")
                self.receipt.write_text('{"other":"before"}', encoding="utf-8")
                plan = setup.plan()
                target = self.settings if target_name == "settings" else self.receipt
                triggered = []
                real_mkstemp = session_retention.tempfile.mkstemp

                def mutate_after_temp(*args, **kwargs):
                    result = real_mkstemp(*args, **kwargs)
                    if kwargs.get("prefix") == '.' + target.name + '.' and not triggered:
                        triggered.append(True)
                        target.write_text('{"changed":true}', encoding="utf-8")
                    return result
                with mock.patch.object(session_retention.tempfile, "mkstemp", side_effect=mutate_after_temp):
                    result = setup.apply(plan["plan_id"])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(json.loads(target.read_text()), {"changed": True})
                self.assertTrue(triggered)

    def test_final_receipt_change_is_preserved(self):
        """covers: SETUP-37 — 마지막 영수증 교체의 준비 중 수정도 보존한다."""
        from jarvis import session_retention
        self.settings.write_text('{}', encoding="utf-8")
        self.receipt.write_text('{"other":"before"}', encoding="utf-8")
        plan = setup.plan()
        real_mkstemp = session_retention.tempfile.mkstemp
        count = []
        def mutate_final_receipt(*args, **kwargs):
            result = real_mkstemp(*args, **kwargs)
            if kwargs.get("prefix") == '.' + self.receipt.name + '.':
                count.append(True)
                if len(count) == 2:
                    self.receipt.write_text('{"changed":true}', encoding="utf-8")
            return result
        with mock.patch.object(session_retention.tempfile, "mkstemp", side_effect=mutate_final_receipt):
            result = setup.apply(plan["plan_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(json.loads(self.receipt.read_text()), {"changed": True})


if __name__ == "__main__":
    unittest.main()
