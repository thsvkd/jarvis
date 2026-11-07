"""caam(coding_agent_account_manager) → Codex 계정별 사용량, 고정 릴리스 설치."""
import hashlib
import io
import json
import subprocess
import tarfile
import unittest
from unittest import mock

from jarvis import caam_usage

H = 3600 * 10**9  # Go time.Duration 은 나노초
LS = {"profiles": [
    {"tool": "codex", "name": "work", "type": "vault", "active": False,
     "health": {"login_required": False}, "identity": {"email": "work@example.com", "account_id": "acct-secret"}},
    {"tool": "codex", "name": "old", "type": "vault", "active": False,
     "health": {"login_required": True}, "identity": {"email": "old@example.com"}},
    {"tool": "codex", "name": "iso", "type": "isolated", "active": False, "identity": {"email": "iso@example.com"}}],
    "count": 3}
LIMITS = [
    {"provider": "codex", "profile_name": "work", "usage": {
        "provider": "codex", "plan_type": "pro", "account_id": "acct-secret", "fetched_at": "2026-10-28T02:01:47.190237366+09:00",
        "primary_window": {"used_percent": 47, "resets_at": "2026-10-28T03:00:00Z", "window_duration": 5 * H},
        "secondary_window": {"used_percent": 88, "resets_at": "2026-10-30T00:00:00Z", "window_duration": 168 * H},
        "credits": {"has_credits": True, "unlimited": False, "balance": 12.5}}},
    {"provider": "codex", "profile_name": "old", "usage": {
        "provider": "codex", "fetched_at": "2026-10-28T02:01:47Z", "error": "unauthorized: token expired or invalid"}},
    {"provider": "codex", "profile_name": "gone", "usage": {"provider": "codex"}}]


def proc(body, rc=0):
    return mock.Mock(stdout=json.dumps(body), returncode=rc)


class AccountTest(unittest.TestCase):
    def test_rows(self):
        work = caam_usage.account(LIMITS[0], LS["profiles"][0])
        self.assertEqual((work["label"], work["plan"], work["status"], work["attention"]), ("work@example.com", "pro · work", "ok", False))
        self.assertEqual([(w["key"], w["used"]) for w in work["windows"]], [("session", 47), ("weekly", 88)])
        self.assertEqual(work["windows"][0]["resets_at"], 1793156400.0)
        self.assertAlmostEqual(work["updated_at"], 1793120507.190237)
        self.assertEqual(work["extra"], {"unit": "credits", "balance": 12.5, "unlimited": False, "enabled": True, "reason": None})
        old = caam_usage.account(LIMITS[1], LS["profiles"][1])
        self.assertEqual((old["status"], old["attention"], old["windows"]), ("error", True, []))
        self.assertIn("다시 로그인", old["error"])
        self.assertNotIn("acct-secret", json.dumps([work, old]))

    def test_window_without_duration_and_unmeasured(self):
        a = caam_usage.account({"profile_name": "p", "usage": {
            "primary_window": {"used_percent": 5}, "secondary_window": {"used_percent": 0, "unmeasured": True},
            "tertiary_window": {"used_percent": 9, "window_duration": 720 * H}}}, {})
        self.assertEqual([(w["key"], w["used"]) for w in a["windows"]], [("session", 5), ("monthly", 9)])
        self.assertEqual(a["label"], "p")


@mock.patch.object(caam_usage, "_cache", None)
@mock.patch.object(caam_usage, "binary", return_value="/home/alice/.local/bin/caam")
class RunTest(unittest.TestCase):
    def test_reads_vault_profiles_only(self, _):
        with mock.patch.object(caam_usage.subprocess, "run", side_effect=[proc(LS), proc(LIMITS)]) as r:
            out = caam_usage.read(force=True)
        self.assertEqual([c[0][0][1:] for c in r.call_args_list], [["ls", "codex", "--json"], ["limits", "codex", "--format", "json"]])
        self.assertEqual([a["label"] for a in out["accounts"]], ["work@example.com", "old@example.com"])  # ls 에 없는 gone 은 뺀다

    def test_no_profiles_skips_api(self, _):
        with mock.patch.object(caam_usage.subprocess, "run", return_value=proc({"profiles": [], "count": 0})) as r:
            self.assertIsNone(caam_usage.read(force=True))
        self.assertEqual(r.call_count, 1)

    def test_errors_hide_home(self, _):
        for side, want in ((subprocess.TimeoutExpired(["/home/alice/.local/bin/caam"], 10), "10초"),
                           (PermissionError(13, "denied", "/home/alice/x"), "PermissionError")):
            with mock.patch.object(caam_usage.subprocess, "run", side_effect=side):
                err = caam_usage.read(force=True)["error"]
            self.assertIn(want, err)
            self.assertNotIn("alice", err)

    def test_missing_binary_no_install(self, _):
        with mock.patch.object(caam_usage, "binary", return_value=None), \
                mock.patch.object(caam_usage.config, "CAAM_INSTALL", False):
            self.assertIsNone(caam_usage.read())


class InstallTest(unittest.TestCase):
    def tarball(self, body=b"#!/bin/sh\n"):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            for name, data in (("README.md", b"x"), ("caam", body)):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def fetch(self, data):
        r = mock.MagicMock()
        r.__enter__.return_value.read.return_value = data
        return mock.patch.object(caam_usage.release_download, "open_url", return_value=r)

    def test_verified_install(self):
        import os
        import tempfile
        data = self.tarball()
        with tempfile.TemporaryDirectory() as home, self.fetch(data) as get, \
                mock.patch.object(caam_usage, "asset", return_value=("caam_0.1.24_linux_arm64.tar.gz", hashlib.sha256(data).hexdigest())), \
                mock.patch.object(caam_usage.os.path, "expanduser", return_value=home), \
                mock.patch.object(caam_usage.sys, "platform", "linux"):
            caam_usage._install()
            dest = os.path.join(home, ".local", "bin", "caam")
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"#!/bin/sh\n")
            self.assertTrue(os.access(dest, os.X_OK))
            self.assertEqual(os.listdir(os.path.join(home, ".local", "bin")), ["caam"])  # 임시 파일이 남지 않는다
        self.assertIn("/releases/download/v0.1.24/caam_0.1.24_linux_arm64.tar.gz", get.call_args[0][0].full_url)

    def test_hash_mismatch_and_unknown_platform(self):
        with self.fetch(b"tampered"), mock.patch.object(caam_usage, "asset", return_value=("x.tar.gz", "0" * 64)):
            with self.assertRaisesRegex(RuntimeError, "SHA256"):
                caam_usage._install()
        with mock.patch.object(caam_usage, "asset", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "플랫폼"):
                caam_usage._install()
        data = self.tarball()
        bad = io.BytesIO()
        with tarfile.open(fileobj=bad, mode="w:gz") as t:
            info = tarfile.TarInfo("README.md")
            t.addfile(info, io.BytesIO(b""))
        bad = bad.getvalue()
        with self.fetch(bad), mock.patch.object(caam_usage, "asset", return_value=("x.tar.gz", hashlib.sha256(bad).hexdigest())):
            with self.assertRaisesRegex(RuntimeError, "실행 파일이 없습니다"):
                caam_usage._install()

    def test_assets(self):
        for plat, mach, want in (("linux", "aarch64", "linux_arm64"), ("linux", "x86_64", "linux_amd64"),
                                 ("darwin", "arm64", "darwin_arm64"), ("win32", "AMD64", "windows_amd64.exe")):
            with mock.patch.object(caam_usage.sys, "platform", plat), \
                    mock.patch.object(caam_usage.platform, "machine", return_value=mach):
                self.assertIn(want, caam_usage.asset()[0])
        with mock.patch.object(caam_usage, "_codex_cli", return_value=None):
            self.assertIsNone(caam_usage.ensure())  # Codex CLI 가 없으면 설치하지 않는다
        with mock.patch.object(caam_usage, "_codex_cli", return_value="/x/codex"), \
                mock.patch.object(caam_usage, "asset", return_value=None):
            self.assertIsNone(caam_usage.ensure())  # 릴리스가 없는 플랫폼은 조용히 건너뛴다

    def test_ensure_starts_installer(self):
        with mock.patch.object(caam_usage, "_codex_cli", return_value="/x/codex"), \
                mock.patch.object(caam_usage.installer, "ensure", return_value="caam 을 설치하는 중입니다(x)") as e:
            self.assertEqual(caam_usage.ensure(), "caam 을 설치하는 중입니다(x)")
        e.assert_called_once_with("Codex 계정별 사용량")


if __name__ == "__main__":
    unittest.main()
