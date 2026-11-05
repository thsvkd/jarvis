"""Claude 계정 모듈: 독립·호환 경로의 신선도, 만료 경고, 상태 문구, 기기 로그인 상태. 임시 저장소와 가짜 hswap 응답만 쓴다."""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from jarvis import claude_accounts, cswap_cached_usage, cswap_usage, independent_usage, orca_usage


def iso(at):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def hswap_row(**kw):
    now = time.time()
    return {"number": 1, "email": "a@example.com", "organizationName": "Org", "usageStatus": "ok", "active": True,
            "usage": {"fiveHour": {"pct": 12, "resetsAt": iso(now + 3600)}}, "usageFetchedAt": iso(now - 60), **kw}


class StoreCase(unittest.TestCase):
    """임시 저장소 하나. hswap 은 기본으로 없다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        os.mkdir(os.path.join(self.root, "cache"), 0o700)
        for name, kw in (("_root", {"return_value": self.root}), ("_live_identity", {"return_value": ("a@example.com", "o1")}),
                         ("binary", {"return_value": None}), ("_cache", {"new": None}),
                         ("ensure", {"side_effect": AssertionError("독립 경로는 설치하지 않는다")})):
            p = mock.patch.object(claude_accounts, name, **kw)
            setattr(self, name.strip("_"), p.start())
            self.addCleanup(p.stop)
        self.root = tmp.name  # 위의 _root 패치가 같은 이름의 속성을 덮었다

    def write(self, name, body):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
        os.chmod(path, 0o600)
        return path

    def store(self, fetched_at, **row):
        self.write("sequence.json", {"sequence": [1], "accounts": {"1": {"email": "a@example.com", "organizationUuid": "o1", "organizationName": "Org"}}})
        self.write("cache/usage.json", {"schemaVersion": 2, "accounts": {"1": {
            "email": "a@example.com", "organizationUuid": "o1", "fetchedAt": fetched_at, "authDeadStrikes": 0,
            "lastGood": {"five_hour": {"pct": 12, "resets_at": iso(time.time() + 3600)},
                         "scoped": [{"name": "fable", "pct": 3}, {"name": "Opus", "pct": 4}, {"name": "private-name", "pct": 5}]}, **row}}})

    def hswap(self, *rows, **top):
        """가짜 hswap: 프로세스를 띄우지 않고 _json 응답만 준다."""
        self.binary.return_value = "/fake/hswap"
        p = mock.patch.object(claude_accounts, "_json", return_value={"accounts": list(rows), **top})
        self.addCleanup(p.stop)
        return p.start()

    def keepalive(self, **kw):
        self.write("cache/keepalive.json", {"schemaVersion": 1, "checkedAt": time.time() - 300, "ok": True, "action": "none", "note": None,
                                            "activeSlot": 2, "accessExpiresAt": iso(time.time() + 3 * 3600 + 30),
                                            "loginExpiresAt": iso(time.time() + 30 * 86400), "error": None, **kw})


class FreshnessTest(StoreCase):
    def test_old_cache_without_hswap_says_why_and_how_old(self):
        """covers: USAGE-6"""
        self.store(time.time() - 9 * 3600)
        a = claude_accounts.read()["accounts"][0]
        self.assertTrue(a["attention"])
        self.assertIn("9시간 전 값", a["note"])
        self.assertIn("hswap 이 없습니다", a["note"])
        self.assertEqual(a["source"], "cswap-cache")
        self.ensure.assert_not_called()

    def test_old_cache_asks_hswap_and_uses_real_values(self):
        """covers: USAGE-6"""
        self.store(time.time() - 9 * 3600)
        run = self.hswap(hswap_row())
        a = claude_accounts.read()["accounts"][0]
        run.assert_called_once_with("/fake/hswap", "list")
        self.assertEqual((a["source"], a["note"], a["attention"]), ("cswap", None, False))

    def test_hswap_failure_falls_back_with_the_reason(self):
        """covers: USAGE-6"""
        self.store(time.time() - 9 * 3600)
        self.hswap().side_effect = RuntimeError("hswap 이 15초 안에 답하지 않았습니다")
        a = claude_accounts.read(force=True)["accounts"][0]
        self.assertEqual(a["source"], "cswap-cache")
        self.assertIn("15초 안에 답하지 않았습니다. 저장된 값을 보입니다.", a["note"])
        self.assertTrue(a["attention"])

    def test_force_reaches_hswap_even_when_cache_is_fresh(self):
        """covers: USAGE-6"""
        self.store(time.time() - 60)
        run = self.hswap(hswap_row())
        claude_accounts.read()
        claude_accounts.read(force=True)
        self.assertEqual(run.call_count, 2)

    def test_recent_real_values_are_reused_until_the_threshold(self):
        """covers: USAGE-6. 예약 실행이 저장소를 늘 새로 써도 만료 경고가 실값으로 계속 보인다"""
        self.store(time.time() - 60)
        soon = iso(time.time() + 2 * 86400)
        run = self.hswap(hswap_row(loginExpiresAt=soon))
        for _ in range(3):
            self.assertIn("로그인 만료", claude_accounts.read()["accounts"][0]["note"])
        self.assertEqual(run.call_count, 1)
        claude_accounts._cache = (time.monotonic() - claude_accounts.config.CLAUDE_STALE_S - 1, claude_accounts._cache[1])
        claude_accounts.read()
        self.assertEqual(run.call_count, 2)

    def test_threshold_is_configurable(self):
        """covers: USAGE-6"""
        self.store(time.time() - 3600)
        self.assertTrue(claude_accounts.read()["accounts"][0]["attention"])
        with mock.patch.object(claude_accounts.config, "CLAUDE_STALE_S", 7200):
            self.assertFalse(claude_accounts.read()["accounts"][0]["attention"])

    def test_old_real_values_are_flagged_without_touching_the_held_result(self):
        """covers: USAGE-6"""
        self.store(time.time() - 60)
        self.hswap(hswap_row(usageFetchedAt=iso(time.time() - 2 * 3600)))
        self.assertIn("2시간 전 값", claude_accounts.read()["accounts"][0]["note"])
        self.assertIsNone(claude_accounts._cache[1]["accounts"][0]["note"])

    def test_no_store_and_no_force_does_not_run_hswap(self):
        """covers: USAGE-6"""
        run = self.hswap(hswap_row())
        self.assertIsNone(claude_accounts.read())
        run.assert_not_called()
        self.assertEqual(claude_accounts.read(force=True)["accounts"][0]["label"], "a@example.com")

    def test_no_store_and_hswap_error_is_an_error(self):
        """covers: USAGE-6. 대체값이 없으면 독립 경로의 unavailable 이 되도록 오류를 그대로 준다"""
        self.hswap().side_effect = RuntimeError("locked")
        self.assertEqual(claude_accounts.read(force=True), {"error": "locked"})

    def test_page_refresh_reaches_hswap_through_independent_read(self):
        """covers: USAGE-6"""
        self.store(time.time() - 9 * 3600)
        with mock.patch.object(claude_accounts, "live", return_value=None) as live:
            self.assertEqual(independent_usage._claude(True)["accounts"][0]["source"], "cswap-cache")
        live.assert_called_once_with(True, install=False)


class SameRulesTest(StoreCase):
    """두 어댑터와 두 경로가 같은 규칙을 쓴다."""

    def test_window_and_bucket_names(self):
        """covers: USAGE-6"""
        self.store(time.time() - 60)
        cached = claude_accounts._store_read()["accounts"][0]
        real = claude_accounts.account(hswap_row(usage={"scoped": [{"name": "fable", "pct": 3}, {"name": "Opus", "pct": 4}, {"name": "private-name", "pct": 5}]}))
        for a in (cached, real):
            self.assertIn("fable", [w["key"] for w in a["windows"]])
            self.assertEqual([b["name"] for b in a["buckets"]], ["Opus", "모델별 한도 2"])
        self.assertNotIn("private-name", json.dumps([cached, real]))

    def test_active_follows_the_real_login_on_both_adapters(self):
        """covers: USAGE-6. hswap 의 active 가 어긋나도 실제 로그인과 같은 슬롯이 사용 중이다. 로그인을 읽지 못하면 hswap 값"""
        self.store(time.time() - 60)
        self.hswap(hswap_row(active=False))
        self.assertTrue(claude_accounts.read()["accounts"][0]["active"])
        self.live_identity.return_value = None
        claude_accounts._cache = None
        self.hswap(hswap_row(active=True))
        self.assertTrue(claude_accounts.read()["accounts"][0]["active"])

    def test_personal_org_is_dropped_on_the_real_path_too(self):
        """covers: USAGE-6"""
        self.hswap(hswap_row(organizationName="a@example.com's Organization"))
        self.assertIsNone(claude_accounts.read(force=True)["accounts"][0]["org"])

    def compat(self):
        with mock.patch.object(orca_usage, "_orca", return_value={"installed": False, "providers": []}), \
                mock.patch.object(orca_usage.caam_usage, "read", return_value=None), \
                mock.patch.object(orca_usage.cli_login, "available", return_value=[]), \
                mock.patch.object(orca_usage.cli_login, "fresh", return_value=[]):
            return next(p for p in orca_usage.read()[1]["providers"] if p["id"] == "claude")

    def test_expiry_and_status_text_match_on_both_paths(self):
        """covers: USAGE-6"""
        soon = iso(time.time() + 2 * 86400)
        for row in (hswap_row(loginExpiresAt=soon),
                    hswap_row(usageStatus="relogin_required", usage=None, lastGoodUsage={"sevenDay": {"pct": 1}}, lastGoodFetchedAt=iso(time.time() - 60)),
                    hswap_row(usageStatus="keychain_unavailable", usage=None, lastGoodUsage={"sevenDay": {"pct": 1}}, lastGoodFetchedAt=iso(time.time() - 60)),
                    hswap_row(usageStatus="foreign_credential", usage=None, lastGoodUsage={"sevenDay": {"pct": 1}}, lastGoodFetchedAt=iso(time.time() - 60))):
            self.hswap(row)
            claude_accounts._cache = None
            mine = claude_accounts.read(force=True)["accounts"][0]
            claude_accounts._cache = None
            theirs = self.compat()["accounts"][0]
            self.assertTrue(mine["note"])
            self.assertEqual((mine["note"], mine["attention"]), (theirs["note"], theirs["attention"]))

    def test_login_state_is_on_both_paths(self):
        """covers: USAGE-7"""
        self.keepalive(ok=False, error={"type": "refresh_failed", "message": "network"})
        self.hswap(hswap_row())
        self.assertEqual(claude_accounts.read(force=True)["login"]["state"], "failed")
        claude_accounts._cache = None
        self.assertEqual(self.compat()["login"]["state"], "failed")


class LoginStateTest(StoreCase):
    def test_ok(self):
        """covers: USAGE-7"""
        self.keepalive()
        s = claude_accounts.login()
        self.assertEqual((s["state"], s["attention"], s["slot"]), ("ok", False, "2"))
        self.assertEqual(s["text"], "로그인됨, 자동 갱신 5분 전")
        self.assertEqual(s["note"], "접근 토큰 3시간 뒤 만료(자동 갱신)")

    def test_expiring(self):
        """covers: USAGE-7"""
        self.keepalive(loginExpiresAt=iso(time.time() + 2 * 86400 + 30))
        s = claude_accounts.login()
        self.assertEqual((s["state"], s["attention"], s["text"]), ("expiring", True, "로그인 만료 임박(2일)"))

    def test_relogin_from_keepalive_and_from_the_active_row(self):
        """covers: USAGE-7"""
        self.keepalive(ok=False, error={"type": "relogin_required", "message": "m"})
        self.assertEqual(claude_accounts.login()["state"], "relogin")
        os.remove(os.path.join(self.root, "cache", "keepalive.json"))
        # 저장소의 authDeadStrikes 는 사용 중 계정일 때만 기기 상태가 된다
        self.store(time.time() - 60, authDeadStrikes=1)
        rows = claude_accounts._store_read()["accounts"]
        self.assertEqual(claude_accounts.login(rows)["state"], "relogin")
        self.live_identity.return_value = ("other@example.com", "")
        self.assertIsNone(claude_accounts.login(claude_accounts._store_read()["accounts"]))

    def test_keychain_from_keepalive_and_from_hswap(self):
        """covers: USAGE-7"""
        self.keepalive(ok=False, error={"type": "keychain_unavailable", "message": "m"})
        self.assertEqual(claude_accounts.login()["state"], "keychain")
        os.remove(os.path.join(self.root, "cache", "keepalive.json"))
        self.hswap(hswap_row(), keychainUnavailable=True)
        s = claude_accounts.read(force=True)["login"]
        self.assertEqual((s["state"], s["attention"]), ("keychain", True))
        self.assertIn("키체인에 접근할 수 없는 곳에서 실행됨", s["text"])
        self.assertTrue(claude_accounts._cache[1]["keychainUnavailable"])

    def test_keychain_field_is_ignored_when_absent(self):
        """covers: USAGE-7"""
        self.hswap(hswap_row())
        out = claude_accounts.read(force=True)
        self.assertNotIn("login", out)
        self.assertNotIn("keychainUnavailable", claude_accounts._cache[1])

    def test_stalled(self):
        """covers: USAGE-7"""
        self.keepalive(checkedAt=time.time() - 2 * 3600)
        s = claude_accounts.login()
        self.assertEqual((s["state"], s["attention"], s["text"]), ("stalled", True, "자동 갱신 멈춤(마지막 2시간 전)"))
        with mock.patch.object(claude_accounts.config, "CLAUDE_KEEPALIVE_STALE_S", 3 * 3600):
            self.assertEqual(claude_accounts.login()["state"], "ok")
        self.keepalive(checkedAt="soon")
        self.assertEqual(claude_accounts.login()["state"], "stalled")

    def test_failed_keeps_type_and_message_short(self):
        """covers: USAGE-7"""
        self.keepalive(ok=False, error={"type": "split_brain", "message": "x" * 500})
        s = claude_accounts.login()
        self.assertEqual((s["state"], s["attention"]), ("failed", True))
        self.assertTrue(s["text"].startswith("자동 갱신 실패(split_brain: xxx"))
        self.assertLess(len(s["text"]), 300)

    def test_unknown_when_the_file_is_missing(self):
        """covers: USAGE-7. keepalive 를 쓰지 않는 기기에는 경고하지 않는다"""
        self.assertIsNone(claude_accounts.login())

    def test_unsafe_or_foreign_files_are_ignored(self):
        """covers: USAGE-7. opencode_usage._private_json 과 같은 안전 규칙"""
        path = os.path.join(self.root, "cache", "keepalive.json")
        self.keepalive(ok=False, error={"type": "refresh_failed"})
        os.chmod(path, 0o644)
        self.assertIsNone(claude_accounts.login())
        os.remove(path)
        self.write("real.json", {"schemaVersion": 1, "ok": False})
        os.symlink(os.path.join(self.root, "real.json"), path)
        self.assertIsNone(claude_accounts.login())
        os.remove(path)
        self.write("cache/keepalive.json", json.dumps({"schemaVersion": 1, "pad": "x" * 70000}))
        self.assertIsNone(claude_accounts.login())
        for body in ("{broken", '["x"]', '{"schemaVersion": 2, "ok": false}'):
            self.write("cache/keepalive.json", body)
            self.assertIsNone(claude_accounts.login())

    def test_order_when_several_apply(self):
        """covers: USAGE-7"""
        soon = iso(time.time() + 86400 + 30)
        old = time.time() - 9 * 3600
        for kw, want in (({"loginExpiresAt": soon, "checkedAt": old}, "expiring"),
                         ({"loginExpiresAt": soon, "ok": False, "error": {"type": "keychain_unavailable"}}, "keychain"),
                         ({"loginExpiresAt": soon, "ok": False, "error": {"type": "relogin_required"}}, "relogin"),
                         ({"checkedAt": old, "ok": False, "error": {"type": "locked"}}, "stalled")):
            self.keepalive(**kw)
            self.assertEqual(claude_accounts.login()["state"], want, kw)

    def test_access_expiry_and_slot_fallback(self):
        """covers: USAGE-7"""
        self.keepalive(accessExpiresAt=iso(time.time() - 60), activeSlot=None)
        s = claude_accounts.login([{"active": True, "slot": "7"}])
        self.assertEqual((s["note"], s["slot"]), ("접근 토큰 만료됨(자동 갱신 대기)", "7"))
        self.keepalive(accessExpiresAt=None)
        self.assertIsNone(claude_accounts.login()["note"])

    def test_provider_carries_login_only_when_there_is_something_to_say(self):
        """covers: USAGE-7"""
        self.store(time.time() - 60)
        empty = {"status": "unavailable", "accounts": []}
        with mock.patch.object(independent_usage, "_caam", side_effect=RuntimeError("없음")), \
                mock.patch.object(independent_usage, "_opencode", return_value=empty), \
                mock.patch.object(independent_usage.codex_native_usage, "read", return_value=[]), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.minimax_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.orca_usage, "_orca", return_value={"providers": []}), \
                mock.patch.object(independent_usage.cli_login, "available", return_value=[]), \
                mock.patch.object(independent_usage.cli_login, "fresh", return_value=[]):
            independent_usage._tasks.clear()
            self.addCleanup(independent_usage._tasks.clear)
            claude = next(p for p in independent_usage.read(force=True)["providers"] if p["id"] == "claude")
            self.assertNotIn("login", claude)
            self.keepalive()
            claude = next(p for p in independent_usage.read(force=True)["providers"] if p["id"] == "claude")
        self.assertEqual(claude["login"]["state"], "ok")
        self.assertNotIn("error", claude["login"])


class AliasTest(unittest.TestCase):
    def test_old_names_read_and_patch_the_new_module(self):
        """covers: USAGE-6. 옛 이름으로 바꿔 끼운 값이 새 모듈에 그대로 닿고, 끝나면 되돌아간다"""
        live, store = claude_accounts.live, claude_accounts._store_read
        self.assertIs(cswap_usage.read, live)
        self.assertIs(cswap_cached_usage.read, store)
        self.assertIs(cswap_usage.account, claude_accounts.account)
        with mock.patch.object(cswap_usage, "read", return_value="x"), mock.patch.object(cswap_cached_usage, "_root", return_value="/none"):
            self.assertEqual(claude_accounts.live(), "x")
            self.assertEqual(claude_accounts._root(), "/none")
        self.assertIs(claude_accounts.live, live)
        self.assertNotEqual(claude_accounts._root(), "/none")


if __name__ == "__main__":
    unittest.main()
