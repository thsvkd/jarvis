"""현재 Codex 로그인에서 갱신 없이 사용량을 읽는다."""
import base64
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from jarvis.usage import codex


class NativeCodexTest(unittest.TestCase):
    def test_live_account_without_vault_is_read_without_secret_output(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
            path = os.path.join(tmp, "auth.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"tokens": {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "account_id": "private-id"}}, f)
            os.chmod(path, 0o600)
            body = {"rate_limit": {"primary_window": {"used_percent": 27, "limit_window_seconds": 18000, "reset_at": 1900000000},
                                   "secondary_window": {"used_percent": 61, "limit_window_seconds": 604800, "reset_at": 1900000001}}}
            with mock.patch.object(codex, "_http", return_value=body) as http:
                rows = codex.read()
            http.assert_called_once_with("synthetic-access", "private-id")
            self.assertEqual([(w["key"], w["used"]) for w in rows[0]["windows"]], [("session", 27), ("weekly", 61)])
            self.assertNotIn("synthetic", json.dumps(rows))
            self.assertNotIn("private-id", json.dumps(rows))

    def test_verified_email_of_the_same_login_is_shown(self):
        """covers: USAGE-2"""
        import base64
        def idt(claims):
            enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
            return enc({"alg": "none"}) + "." + enc(claims) + ".sig"
        same = {"email": "me@example.com", "email_verified": True, "https://api.openai.com/auth": {"chatgpt_account_id": "acct"}}
        cases = [(same, "me@example.com"),
                 ({**same, "email_verified": False}, "기본 계정"),
                 ({**same, "https://api.openai.com/auth": {"chatgpt_account_id": "other"}}, "기본 계정")]
        body = {"rate_limit": {"primary_window": {"used_percent": 1, "limit_window_seconds": 18000, "reset_at": 1900000000}}}
        for claims, label in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CODEX_HOME": tmp}), \
                    mock.patch.object(codex, "_http", return_value=body):
                path = os.path.join(tmp, "auth.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"tokens": {"access_token": "a", "account_id": "acct", "id_token": idt(claims)}}, f)
                os.chmod(path, 0o600)
                rows = codex.read()
                self.assertEqual(rows[0]["label"], label)
                self.assertNotIn("acct", json.dumps(rows))

    def test_missing_or_invalid_credentials_do_not_trigger_refresh(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CODEX_HOME": tmp}), \
                mock.patch.object(codex, "_http") as http:
            self.assertEqual(codex.read(), [])
        http.assert_not_called()


class LoginTest(unittest.TestCase):
    """USAGE-19: 로그인 유지가 돈 뒤 auth.json 의 접근 토큰이 2시간 넘게 만료된 채면 기기 단위 login."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "auth.json")
        p = mock.patch.dict(os.environ, {"CODEX_HOME": tmp.name})
        p.start()
        self.addCleanup(p.stop)

    def write(self, claims, mode=0o600, access=None):
        enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
        token = access or enc({"alg": "none"}) + "." + enc(claims) + ".synthetic-signature"
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"tokens": {"access_token": token, "account_id": "private-account", "id_token": token},
                       "last_refresh": "2026-11-13T00:00:00Z", "pwd_auth_time": 1762000000}, f)
        os.chmod(self.path, mode)

    def test_stalled_login_is_reported_with_elapsed_time_only(self):
        """covers: USAGE-19"""
        self.write({"exp": time.time() - 3 * 3600, "pwd_auth_time": 1762000000})
        login = codex.login(True)
        self.assertEqual({k: login[k] for k in ("state", "text", "attention", "slot", "other", "store")},
                         {"state": "stalled", "text": "Codex 다시 로그인 필요", "attention": True, "slot": None, "other": False, "store": None})
        self.assertTrue(login["note"].startswith("접근 토큰이 3시간 전에 만료됐습니다. 다시 로그인하세요."), login["note"])
        self.assertIn("건너뜁니다", login["note"])
        text = json.dumps(login, ensure_ascii=False)
        for secret in ("synthetic-signature", "private-account", "1762000000", "eyJ"):
            self.assertNotIn(secret, text)
        self.assertNotIn("건너뜁니다", codex.login(False)["note"])

    def test_nothing_to_report(self):
        """covers: USAGE-19. 유지가 돈 적 없음, 만료 2시간 안, 안전하지 않은 파일, exp 없음·깨진 JWT, 파일 없음"""
        self.assertIsNone(codex.login(True))  # 파일 없음(키링 저장 방식 포함)
        self.write({"exp": time.time() - 3 * 3600})
        self.assertIsNone(codex.login(None))
        self.write({"exp": time.time() - 3600})
        self.assertIsNone(codex.login(True))
        self.write({"exp": time.time() - 3 * 3600}, mode=0o644)
        self.assertIsNone(codex.login(True))
        for claims in ({}, {"exp": "x"}, {"exp": True}, {"exp": float("nan")}, {"exp": 10 ** 400}):
            self.write(claims)
            self.assertIsNone(codex.login(True), claims)
        self.write({}, access="not-a-jwt")
        self.assertIsNone(codex.login(True))

    def test_login_deadline_warns_three_days_ahead(self):
        """covers: USAGE-19. auth_time + 30일이 3일 안이면 expiring, 지났으면 기한 지남, 멀면 없음. 유지가 돌지 않아도 붙는다"""
        now = time.time()
        auth = now - 28 * 86400
        self.write({"exp": now + 3600, "auth_time": auth})
        login = codex.login(None)
        self.assertEqual({k: login[k] for k in ("state", "text", "attention", "slot", "other", "store")},
                         {"state": "expiring", "text": "Codex 로그인 만료 임박(약 2일)", "attention": True, "slot": None, "other": False, "store": None})
        self.assertNotIn(str(int(auth)), json.dumps(login, ensure_ascii=False))
        self.write({"exp": now + 3600, "auth_time": now - 31 * 86400})
        self.assertEqual(codex.login(True)["text"], "Codex 로그인 기한이 지났습니다")
        self.write({"exp": now + 3600, "auth_time": now - 20 * 86400})
        self.assertIsNone(codex.login(True))
        self.write({"exp": now - 3 * 3600, "auth_time": now - 31 * 86400})  # 이미 끊겼으면 멈춤이 앞선다
        self.assertEqual(codex.login(True)["state"], "stalled")
        for bad in ("x", True, float("nan"), -1):
            self.write({"exp": now + 3600, "auth_time": bad})
            self.assertIsNone(codex.login(True), bad)

    def test_login_deadline_survives_an_unreadable_access_token(self):
        """covers: USAGE-19. 접근 토큰이 없거나 JWT 가 아니어도 id_token 의 기한 경고는 붙는다"""
        enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
        id_token = enc({"alg": "none"}) + "." + enc({"auth_time": time.time() - 31 * 86400}) + ".synthetic-signature"
        for tokens in ({"access_token": "not-a-jwt", "id_token": id_token}, {"id_token": id_token}):
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"tokens": tokens}, f)
            os.chmod(self.path, 0o600)
            self.assertEqual(codex.login(True)["state"], "expiring", tokens.keys())


if __name__ == "__main__":
    unittest.main()
