"""Orca 가 없는 상태에서도 계정 수집기를 독립적으로 조합한다."""
import unittest
import threading
import time
from unittest import mock

from jarvis import independent_usage, orca_rpc


class IndependentUsageTest(unittest.TestCase):
    def setUp(self):
        # 실제 기기의 Orca 를 읽지 않는다. Orca 보충을 보는 테스트만 따로 값을 준다
        patcher = mock.patch.object(independent_usage, "_orca", return_value=[])
        self.orca = patcher.start()
        self.addCleanup(patcher.stop)
        # 자격 파일·CLI 상태는 실제 홈을 읽으므로 가짜로 고정한다(테스트가 실기기를 건드리지 않게)
        p = mock.patch.object(independent_usage.cli_login, "fresh", return_value=[])
        self.fresh = p.start()
        self.addCleanup(p.stop)
        # 강제 조회는 hswap 을 부른다: 이 기기의 hswap·keepalive.json 에 닿지 않게 한다
        for name, kw in (("binary", {"return_value": None}), ("_keepalive", {"return_value": None}), ("_cache", {"new": None})):
            p = mock.patch.object(independent_usage.claude_accounts, name, **kw)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        independent_usage._tasks.clear()

    def test_merge_carries_active_from_single_default_account_to_orca_email(self):
        """covers: USAGE-3"""
        mine = [{"label": "기본 계정", "active": True}]
        orca = [{"label": "me@example.com", "active": False}]
        merged = independent_usage._merge(mine, orca)
        self.assertEqual([(a["label"], a["active"]) for a in merged], [("me@example.com", True)])
        self.assertFalse(orca[0]["active"])
        two = [{"label": "a@example.com", "active": False}, {"label": "b@example.com", "active": False}]
        self.assertFalse(any(a["active"] for a in independent_usage._merge(mine, two)))

    def test_orca_fills_what_independent_readers_cannot_see(self):
        """covers: USAGE-3"""
        row = lambda label, used=10: {"label": label, "org": None, "active": True, "status": "ok", "windows": [{"key": "weekly", "used": used, "resets_at": None}], "buckets": []}
        self.orca.return_value = [
            {"id": "codex", "accounts": [row("codex@example.com")]},
            {"id": "grok", "accounts": [row("기본 계정")]},
            {"id": "antigravity", "accounts": [row("기본 계정")]},
            {"id": "claude", "accounts": [row("c@example.com"), row("other@example.com")]},
        ]
        empty = {"status": "unavailable", "accounts": []}
        with mock.patch.object(independent_usage, "_claude", return_value={"accounts": [row("c@example.com", 50)]}), \
                mock.patch.object(independent_usage, "_codex", return_value=[row("기본 계정")]), \
                mock.patch.object(independent_usage, "_caam", return_value=[]), \
                mock.patch.object(independent_usage, "_opencode", return_value=empty), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value={"status": "ok", "accounts": [row("a@example.com")]}), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.minimax_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value=empty):
            data = independent_usage.read(force=True)
        got = {p["id"]: [a["label"] for a in p["accounts"]] for p in data["providers"]}
        self.assertEqual(got["codex"], ["codex@example.com"])  # 이메일 모르는 기본 계정은 Orca 의 같은 로그인으로 바꾼다
        self.assertEqual(got["grok"], ["기본 계정"])  # 독립 수집이 못 읽은 프로바이더는 Orca 로 채운다
        self.assertEqual(got["antigravity"], ["a@example.com"])  # 이메일을 아는 독립 값은 그대로 둔다
        self.assertEqual(got["claude"], ["c@example.com", "other@example.com"])  # 같은 이메일은 독립 값, Orca 에만 있는 계정은 더한다
        self.assertEqual(data["providers"][0]["accounts"][0]["windows"][0]["used"], 50)
        self.assertNotIn("grok", data["unavailable"])
        self.assertIn("cursor", data["unavailable"])

    def test_login_buttons_and_grok_refresh_follow_the_orca_path(self):
        """covers: USAGE-3"""
        self.orca.return_value = [{"id": "grok", "accounts": [{"label": "기본 계정", "failure": "delegated-refresh-required", "windows": [], "buckets": []}]}]
        empty = {"status": "unavailable", "accounts": []}
        with mock.patch.object(independent_usage, "_claude", return_value={"accounts": []}), \
                mock.patch.object(independent_usage, "_codex", return_value=[]), \
                mock.patch.object(independent_usage, "_caam", return_value=[]), \
                mock.patch.object(independent_usage, "_opencode", return_value=empty), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.minimax_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.cli_login, "available", return_value=["codex", "grok"]), \
                mock.patch.object(independent_usage.cli_login, "refresh_grok") as refresh:
            data = independent_usage.read(force=True)
        self.assertEqual(data["logins"], ["codex", "grok"])
        refresh.assert_called_once_with()

    def test_all_provider_results_are_returned_without_orca(self):
        """covers: USAGE-3"""
        account = {"label": "fixture@example.com"}
        result = {"status": "ok", "accounts": [account]}
        with mock.patch.object(independent_usage, "_claude", return_value={"accounts": [account]}), \
                mock.patch.object(independent_usage, "_codex", return_value=[account]), \
                mock.patch.object(independent_usage, "_caam", return_value=[account]), \
                mock.patch.object(independent_usage, "_opencode", return_value=result), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value=result), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value=result), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value=result), \
                mock.patch.object(independent_usage.minimax_usage, "read", return_value=result), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value=result), \
                mock.patch.object(orca_rpc, "call", side_effect=AssertionError("Orca 호출 금지")) as rpc:
            data = independent_usage.read(force=True)
        rpc.assert_not_called()
        self.assertEqual({p["id"] for p in data["providers"]}, {"claude", "codex", "grok", "cursor", "opencodeGo", "antigravity", "gemini", "kimi", "minimax", "zcode"})
        self.assertEqual(data["unavailable"], [])

    def test_fresh_logins_identify_where_a_login_would_lose_nothing(self):
        """covers: USAGE-4, USAGE-5"""
        self.fresh.return_value = ["codex"]  # 가짜: codex 자격만 없음
        self.orca.return_value = [{"id": "codex", "accounts": [{"label": "a@example.com", "windows": [], "buckets": []}]}]
        empty = {"status": "unavailable", "accounts": []}
        with mock.patch.object(independent_usage, "_claude", return_value={"accounts": []}), \
                mock.patch.object(independent_usage, "_codex", return_value=[]), \
                mock.patch.object(independent_usage, "_caam", return_value=[]), \
                mock.patch.object(independent_usage, "_opencode", return_value=empty), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.minimax_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.cli_login, "available", return_value=["codex", "grok"]), \
                mock.patch.object(independent_usage.cswap_usage, "status", return_value={"version": "0.2.0", "pinned": "0.2.1", "note": None}):
            data = independent_usage.read(force=True)
        self.assertEqual(data["hswap"], {"version": "0.2.0", "pinned": "0.2.1", "note": None})  # 기기 행의 hswap 버전
        self.assertEqual(data["logins"], ["codex", "grok"])
        self.assertEqual(data["fresh_logins"], ["codex"])  # 화면은 이 목록으로만 기기 행 '로그인' 버튼을 낸다

    def test_claude_is_reread_on_every_read_so_a_switch_shows_at_once(self):
        """covers: USAGE-3"""
        # 계정 전환 뒤 강제 아닌 조회도 새 '사용 중'을 보여야 한다. 다른 프로바이더는 묶은 값을 다시 쓴다
        empty = {"status": "unavailable", "accounts": []}
        claude = mock.Mock(side_effect=[{"accounts": [{"label": "a@example.com", "active": True}]},
                                        {"accounts": [{"label": "a@example.com", "active": False}]}])
        with mock.patch.object(independent_usage, "_claude", claude), \
                mock.patch.object(independent_usage, "_codex", return_value=[]) as codex, \
                mock.patch.object(independent_usage, "_caam", return_value=[]), \
                mock.patch.object(independent_usage, "_opencode", return_value=empty), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.minimax_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value=empty):
            independent_usage.read()
            data = independent_usage.read()
        self.assertEqual(claude.call_count, 2)
        self.assertEqual(codex.call_count, 1)
        self.assertFalse(data["providers"][0]["accounts"][0]["active"])

    def test_fresh_logins_are_omitted_when_nothing_is_confirmed(self):
        """covers: USAGE-4, USAGE-5"""
        # 자격이 없음이 확인되지 않으면(빈 목록) 키 자체를 넣지 않는다: 화면은 버튼을 내지 않는다
        with mock.patch.object(independent_usage, "_claude", return_value={"accounts": []}), \
                mock.patch.object(independent_usage, "_codex", return_value=[]), \
                mock.patch.object(independent_usage, "_caam", return_value=[]), \
                mock.patch.object(independent_usage, "_opencode", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.minimax_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.cswap_usage, "status", return_value=None):
            data = independent_usage.read(force=True)
        self.assertNotIn("fresh_logins", data)  # 키가 없으면 화면도 버튼을 내지 않는다
        self.assertNotIn("hswap", data)  # 게이트웨이가 아직 확인하지 않았다

    def test_collects_without_orca_and_keeps_unavailable_distinct(self):
        """covers: USAGE-3"""
        claude = {"accounts": [{"label": "c@example.com"}]}
        opencode = {"status": "ok", "accounts": [{"label": "기본 계정"}]}
        def provider(_exe, name):
            return [{"label": name + "@example.com"}] if name == "grok" else []
        with mock.patch.object(independent_usage.minimax_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.codex_native_usage, "read", return_value=[]), \
                mock.patch.object(independent_usage.cswap_cached_usage, "read", return_value=claude), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value={"status": "ok", "accounts": [{"label": "a@example.com"}]}), \
                mock.patch.object(independent_usage.caam_usage, "binary", return_value="/fake/caam"), \
                mock.patch.object(independent_usage.provider_usage, "read_provider", side_effect=provider), \
                mock.patch.object(independent_usage, "which_local", return_value="/fake/opencode"), \
                mock.patch.object(independent_usage.opencode_usage, "read", return_value=opencode), \
                mock.patch.object(orca_rpc, "call", side_effect=AssertionError("Orca 호출 금지")) as rpc:
            data = independent_usage.read(force=True)
        rpc.assert_not_called()
        self.assertEqual([p["id"] for p in data["providers"]], ["claude", "grok", "opencodeGo", "antigravity"])
        self.assertEqual(data["mode"], "independent")
        self.assertNotIn("antigravity", data["unavailable"])
        self.assertIn("codex", data["unavailable"])
        self.assertNotIn("orca", str(data).lower())

    def test_missing_caam_is_unavailable_not_empty_usage(self):
        """covers: USAGE-3"""
        with mock.patch.object(independent_usage.minimax_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.codex_native_usage, "read", return_value=[]), \
                mock.patch.object(independent_usage.cswap_cached_usage, "read", return_value=None), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.caam_usage, "binary", return_value=None), \
                mock.patch.object(independent_usage, "which_local", return_value=None):
            data = independent_usage.read(force=True)
        for name in ("codex", "grok", "cursor", "opencodeGo"):
            self.assertIn(name, data["unavailable"])

    def test_caam_providers_do_not_run_concurrently(self):
        """covers: USAGE-3"""
        lock = threading.Lock()
        active = [0, 0]
        def provider(_exe, _name):
            with lock:
                active[0] += 1
                active[1] = max(active)
            time.sleep(.04)
            with lock:
                active[0] -= 1
            return []
        with mock.patch.object(independent_usage.minimax_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.codex_native_usage, "read", return_value=[]), \
                mock.patch.object(independent_usage.cswap_cached_usage, "read", return_value=None), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value={"status": "unavailable", "accounts": []}), \
                mock.patch.object(independent_usage.caam_usage, "binary", return_value="/fake/caam"), \
                mock.patch.object(independent_usage.provider_usage, "read_provider", side_effect=provider), \
                mock.patch.object(independent_usage, "which_local", return_value=None):
            independent_usage.read(force=True)
        self.assertEqual(active[1], 1)

    def test_missing_cswap_does_not_start_installer(self):
        """covers: USAGE-3"""
        with mock.patch.object(independent_usage.cswap_cached_usage, "read", return_value=None), \
                mock.patch.object(independent_usage.cswap_usage, "read") as read:
            self.assertIsNone(independent_usage._claude(False))
        read.assert_not_called()

    def test_cswap_disappearing_after_lookup_cannot_start_installer(self):
        """covers: USAGE-3"""
        with mock.patch.object(independent_usage.cswap_cached_usage, "read", return_value=None), \
                mock.patch.object(independent_usage.cswap_usage, "ensure") as ensure:
            self.assertIsNone(independent_usage._claude(False))
        ensure.assert_not_called()

    def test_cswap_provider_error_does_not_expose_raw_credentials(self):
        """covers: USAGE-3"""
        account = independent_usage.cswap_usage.account({"usageStatus": "other", "usageError": "Bearer synthetic-secret"})
        self.assertNotIn("synthetic-secret", str(account))

    def test_codex_falls_back_to_native_when_caam_absent(self):
        """covers: USAGE-3"""
        with mock.patch.object(independent_usage, "_caam", side_effect=RuntimeError("absent")), \
                mock.patch.object(independent_usage.codex_native_usage, "read", return_value=[{"label": "기본 계정"}]):
            self.assertEqual(independent_usage._codex(), [{"label": "기본 계정"}])

    def test_native_codex_account_is_not_hidden_by_other_vault_accounts(self):
        """covers: USAGE-3"""
        vault = [{"label": "vault@example.com", "status": "error"}]
        native = [{"label": "기본 계정", "status": "ok"}]
        with mock.patch.object(independent_usage, "_caam", return_value=vault), \
                mock.patch.object(independent_usage.codex_native_usage, "read", return_value=native):
            self.assertEqual(independent_usage._codex(), vault + native)

    def test_opencode_user_install_is_found_without_service_path(self):
        """covers: USAGE-3"""
        result = {"status": "unavailable", "accounts": []}
        with mock.patch.object(independent_usage, "which_local", return_value=None), \
                mock.patch.object(independent_usage.os.path, "isfile", return_value=True), \
                mock.patch.object(independent_usage.opencode_usage, "read", return_value=result) as read:
            self.assertEqual(independent_usage._opencode(), result)
        self.assertIn(".opencode", read.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
