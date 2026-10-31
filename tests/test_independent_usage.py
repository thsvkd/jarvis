"""Orca 가 없는 상태에서도 계정 수집기를 독립적으로 조합한다."""
import unittest
import threading
import time
from unittest import mock

from jarvis import independent_usage, orca_rpc


class IndependentUsageTest(unittest.TestCase):
    def tearDown(self):
        independent_usage._tasks.clear()

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
