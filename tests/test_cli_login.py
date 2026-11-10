import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

from jarvis import claude_accounts, cli_login

FAKE = """#!/bin/sh
printf '\\033[94mhttps://auth.example.com/device\\033[0m\\n2. Enter this one-time code\\n   \\033[94mABCD-EFGH1\\033[0m\\n'
sleep 30
"""


class CliLoginTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.exe = os.path.join(self.dir, "codex")
        with open(self.exe, "w") as f:
            f.write(FAKE)
        os.chmod(self.exe, stat.S_IRWXU)
        self.old = cli_login.CLIS["codex"]
        cli_login.CLIS["codex"] = lambda: self.exe

    def tearDown(self):
        cli_login.CLIS["codex"] = self.old
        job = cli_login._jobs.pop("codex", None)
        if job:
            job["proc"].kill()

    def test_start(self):
        st, o = cli_login.start("codex")
        self.assertEqual((st, o["url"], o["code"]), (200, "https://auth.example.com/device", "ABCD-EFGH1"))
        pid = cli_login._jobs["codex"]["proc"].pid
        self.assertEqual(cli_login.start("codex")[1]["code"], "ABCD-EFGH1")  # 기다리는 로그인은 다시 띄우지 않는다
        self.assertEqual(cli_login._jobs["codex"]["proc"].pid, pid)
        self.assertEqual(cli_login.start("nope")[0], 400)
        self.assertEqual(cli_login.status("codex"), {"state": "waiting"})
        cli_login._jobs["codex"]["proc"].kill()
        cli_login._jobs["codex"]["proc"].wait()
        self.assertEqual(cli_login.status("codex"), {"state": "failed"})

    def test_no_code(self):
        with open(self.exe, "w") as f:
            f.write("#!/bin/sh\necho 'not logged in'\n")
        st, o = cli_login.start("codex")
        self.assertEqual(st, 502)
        self.assertIn("not logged in", o["error"])
        cli_login._jobs["codex"]["proc"].wait()
        self.assertEqual(cli_login.status("codex"), {"state": "done"})  # 코드 없이 끝났어도 종료 코드 0 이면 저장된 것으로 본다


FAKE_CLAUDE = """#!/bin/sh
echo 'Opening browser to sign in…'
echo 'If the browser did not open, visit: https://claude.example.com/oauth/authorize?code=true&state=s'
printf 'Paste code here if prompted > '
read line
[ "$line" = "good1234#state5678" ]
"""


class ClaudeLoginTest(unittest.TestCase):
    """covers: USAGE-10. Claude 는 URL 로 로그인한 사람이 받은 코드를 CLI 표준 입력으로 넘기고, 끝나면 hswap 이 저장한다."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.exe, self.hswap, self.marker = (os.path.join(self.dir, n) for n in ("claude", "hswap", "added"))
        for path, body in ((self.exe, FAKE_CLAUDE), (self.hswap, f"#!/bin/sh\nif [ \"$2\" = keepalive ]; then\n echo '{{\"schemaVersion\":1,\"ok\":true}}'\nelse\n echo \"$@\" > {self.marker}\nfi\n")):
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, stat.S_IRWXU)
        self.patches = [mock.patch.dict(cli_login.CLIS, {"claude": lambda: self.exe}),
                        mock.patch.object(cli_login.claude_accounts, "binary", return_value=self.hswap),
                        # 시험하는 기기의 실제 hswap 저장소 상태에 따라 macOS 에서 버튼 가용성이 달라지지 않게 한다(가용성 판정은 아래 테스트가 따로 본다)
                        mock.patch.object(cli_login.claude_accounts, "_keepalive", return_value={"store": "file"}),
                        mock.patch.object(cli_login.claude_accounts, "_keychain_open", return_value=False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        job = cli_login._jobs.pop("claude", None)
        if job:
            job["proc"].kill()
        shutil.rmtree(self.dir, ignore_errors=True)

    def wait_state(self, want):
        for _ in range(100):
            if cli_login.status("claude")["state"] == want:
                return want
            time.sleep(0.05)
        return cli_login.status("claude")["state"]

    def test_start_gives_the_url_and_asks_for_a_pasted_code(self):
        st, o = cli_login.start("claude")
        self.assertEqual((st, o["url"], o["code"], o["paste"]), (200, "https://claude.example.com/oauth/authorize?code=true&state=s", None, True))
        self.assertEqual(cli_login.status("claude"), {"state": "waiting"})
        self.assertEqual(cli_login.start("claude")[1]["url"], o["url"])  # 기다리는 로그인은 다시 띄우지 않는다

    def test_good_code_finishes_the_cli_then_hswap_saves_it(self):
        cli_login.start("claude")
        self.assertEqual(cli_login.submit("claude", "good1234#state5678"), (200, {"submitted": True}))
        self.assertEqual(self.wait_state("done"), "done")
        with open(self.marker) as f:
            self.assertEqual(f.read().strip(), "claude add")

    def test_bad_code_fails_and_nothing_is_saved(self):
        cli_login.start("claude")
        self.assertEqual(cli_login.submit("claude", "wrong123#state5678")[0], 200)
        self.assertEqual(self.wait_state("failed"), "failed")
        self.assertEqual(cli_login.status("claude"), {"state": "failed"})  # CLI 가 실패하면 저장 실패가 아니다
        self.assertFalse(os.path.exists(self.marker))

    def test_submit_checks_provider_shape_and_a_waiting_login(self):
        self.assertEqual(cli_login.submit("codex", "good1234#state5678")[0], 400)  # device-code 프로바이더는 코드를 받지 않는다
        self.assertEqual(cli_login.submit("claude", "good1234#state5678")[0], 409)  # 기다리는 로그인이 없다
        cli_login.start("claude")
        for bad in ("", "short", "a b c d e f g h", "good1234\nstate", "x" * 300, None, 12345678):
            self.assertEqual(cli_login.submit("claude", bad)[0], 400, bad)

    def test_start_refuses_claude_where_the_button_is_not_offered(self):
        """버튼을 내지 않는 기기(키체인 macOS)는 직접 호출해도 CLI 를 띄우지 않는다"""
        with mock.patch.object(cli_login, "sys", mock.Mock(platform="darwin")), \
                mock.patch.object(cli_login.claude_accounts, "_keepalive", return_value=None):
            self.assertEqual(cli_login.start("claude")[0], 404)
        self.assertNotIn("claude", cli_login._jobs)

    def test_cli_done_but_hswap_add_failed_is_reported_as_unsaved(self):
        with open(self.hswap, "w") as f:
            f.write("#!/bin/sh\nexit 3\n")
        cli_login.start("claude")
        cli_login.submit("claude", "good1234#state5678")
        self.assertEqual(self.wait_state("failed"), "failed")
        self.assertEqual(cli_login.status("claude"), {"state": "failed", "unsaved": True})  # 로그인은 이미 바뀌었다

    def keepalive_cli(self, output, code=0, wait=False):
        """실제 홈·키체인을 쓰지 않는 hswap: add 는 성공해도 두 저장소를 맞추지 않는다."""
        with open(self.hswap, "w") as f:
            f.write(f"#!{sys.executable}\nimport pathlib, sys, time\n")
            f.write(f"marker = pathlib.Path({self.marker!r})\n")
            f.write("if sys.argv[1:] == ['claude', 'add']:\n marker.write_text('claude add')\n sys.exit(0)\n")
            f.write("if sys.argv[1:] != ['claude', 'keepalive', '--json']:\n sys.exit(99)\n")
            f.write("marker.with_suffix('.checking').touch()\n")
            if wait:
                f.write("while not marker.with_suffix('.release').exists():\n time.sleep(.01)\n")
            f.write(f"print({output!r})\nsys.exit({code})\n")

    def test_macos_add_success_does_not_complete_with_split_stores(self):
        """covers: USAGE-10 — add 종료 0 뒤 split_brain 이 남으면 완료 대신 동기화 실패를 알린다."""
        self.keepalive_cli(json.dumps({"schemaVersion": 1, "ok": False,
                                      "error": {"type": "split_brain", "message": "fixture-secret"}}), code=1)
        with mock.patch.object(cli_login, "sys", mock.Mock(platform="darwin")):
            cli_login.start("claude")
            cli_login.submit("claude", "good1234#state5678")
            self.assertEqual(self.wait_state("failed"), "failed")
        self.assertEqual(cli_login.status("claude"), {"state": "failed", "unsynced": True})
        self.assertNotIn("fixture-secret", repr(cli_login.status("claude")))

    def test_macos_requires_a_successful_keepalive_report(self):
        """covers: USAGE-10 — 종료 코드·형식·ok·error 를 모두 확인하고 불명확한 답은 완료로 보지 않는다."""
        cases = ((0, '{"schemaVersion":1,"ok":true}', "done"),
                 (3, '{"schemaVersion":1,"ok":true}', "failed"),
                 (0, '{"schemaVersion":1,"ok":false}', "failed"),
                 (0, '{"schemaVersion":1,"ok":true,"error":{"type":"split_brain"}}', "failed"),
                 (0, '{"schemaVersion":1,"ok":1}', "failed"),
                 (0, '{"schemaVersion":2,"ok":true}', "failed"),
                 (0, '{"schemaVersion":true,"ok":true}', "failed"),
                 (0, '{"ok":true}', "failed"),
                 (0, 'not-json', "failed"), (0, '[]', "failed"), (0, 'null', "failed"))
        for code, output, want in cases:
            with self.subTest(code=code, output=output), mock.patch.object(cli_login, "sys", mock.Mock(platform="darwin")):
                self.keepalive_cli(output, code)
                cli_login.start("claude")
                cli_login.submit("claude", "good1234#state5678")
                self.assertEqual(self.wait_state(want), want)
                self.assertEqual(cli_login.status("claude"), {"state": "done"} if want == "done" else {"state": "failed", "unsynced": True})

    def test_macos_waits_for_keepalive_before_publishing_done(self):
        """covers: USAGE-10 — CLI 와 add 가 끝나도 keepalive 검증이 끝나기 전에는 waiting 이다."""
        self.keepalive_cli('{"schemaVersion":1,"ok":true}', wait=True)
        checking, release = (self.marker + suffix for suffix in (".checking", ".release"))
        with mock.patch.object(cli_login, "sys", mock.Mock(platform="darwin")):
            cli_login.start("claude")
            cli_login.submit("claude", "good1234#state5678")
            try:
                for _ in range(100):
                    if os.path.exists(checking):
                        break
                    time.sleep(.01)
                self.assertTrue(os.path.exists(checking), "keepalive 검증이 실행되어야 한다")
                self.assertEqual(cli_login.status("claude"), {"state": "waiting"})
                pending = cli_login._jobs["claude"]
                for elapsed in (0, cli_login.LIFE_S + 1):
                    with self.subTest(elapsed=elapsed), mock.patch.object(cli_login.time, "time", return_value=pending["started_at"] + elapsed):
                        cli_login.start("claude")
                        self.assertIs(cli_login._jobs["claude"], pending, "URL 만료 뒤에도 검증 중이면 두 번째 로그인을 시작하지 않는다")
            finally:
                with open(release, "w"):
                    pass
            self.assertEqual(self.wait_state("done"), "done")

    def test_non_macos_login_does_not_require_keychain_verification(self):
        """covers: USAGE-10 — macOS 이외 기기의 기존 로그인·슬롯 저장 완료 조건을 유지한다."""
        self.keepalive_cli('not-json', code=1)
        with mock.patch.object(cli_login, "sys", mock.Mock(platform="linux")):
            cli_login.start("claude")
            cli_login.submit("claude", "good1234#state5678")
            self.assertEqual(self.wait_state("done"), "done")
        self.assertFalse(os.path.exists(self.marker + ".checking"))

    def test_without_hswap_login_still_follows_the_cli_exit_code(self):
        """covers: USAGE-10 — hswap 이 없는 경로는 Claude CLI 의 성공을 따른다."""
        with mock.patch.object(cli_login.claude_accounts, "binary", return_value=None):
            cli_login.start("claude")
            cli_login.submit("claude", "good1234#state5678")
            self.assertEqual(self.wait_state("done"), "done")

    def test_macos_offers_claude_login_with_file_store_or_a_keychain_the_gateway_reaches(self):
        """파일 저장소이거나, 키체인 모드에서 게이트웨이의 hswap 조회가 최근에 키체인에 닿았을 때만 버튼을 낸다"""
        fresh, stale = time.monotonic(), time.monotonic() - claude_accounts.config.CLAUDE_STALE_S - 1
        err = lambda kind: {"error": {"type": kind}}
        cases = (("linux", None, None, None, True), ("win32", None, None, None, True),
                 ("darwin", None, None, None, False),  # keepalive 기록이 없으면 hswap 상태를 모른다
                 ("darwin", {"store": "file"}, None, None, True),
                 ("darwin", {"store": "auto"}, (fresh, False), None, True),
                 ("darwin", {"store": "keychain"}, (fresh, False), None, True),
                 ("darwin", {"store": "auto"}, None, None, False),  # 게이트웨이의 키체인 접근을 아직 확인하지 못했다
                 ("darwin", {"store": "auto"}, (stale, False), None, False),
                 ("darwin", {"store": "auto"}, (fresh, True), None, False),  # LaunchDaemon·SSH: 키체인에 닿지 못한다
                 ("darwin", {"store": "auto", **err("config_conflict")}, (fresh, False), None, False),
                 ("darwin", {"store": "auto", **err("keychain_item_present")}, (fresh, False), None, False),
                 ("darwin", {"store": "auto"}, (fresh, False), (fresh, "config_conflict"), False))
        for platform, keepalive, device, fatal, want in cases:
            with mock.patch.object(cli_login, "sys", mock.Mock(platform=platform)), \
                    mock.patch.object(cli_login.claude_accounts, "_keepalive", return_value=keepalive), \
                    mock.patch.object(claude_accounts._state, "device", device), mock.patch.object(claude_accounts._state, "fatal", fatal):
                self.assertEqual("claude" in cli_login.available(), want, (platform, keepalive, device, fatal))

    def test_macos_file_store_hides_claude_login_when_the_gateway_reaches_the_keychain(self):
        """covers: USAGE-10 — 화면 세션의 게이트웨이는 로그인을 키체인에 써서 파일 저장소의 hswap claude add 가 실패한다"""
        with mock.patch.object(cli_login, "sys", mock.Mock(platform="darwin")), \
                mock.patch.object(cli_login.claude_accounts, "_keychain_open", return_value=True):
            self.assertNotIn("claude", cli_login.available())
            self.assertEqual(cli_login.start("claude")[0], 404)


class FreshLoginTest(unittest.TestCase):
    """covers: USAGE-4. fresh(): 저장한 자격가 없을 때만 기기 행의 '로그인' 버튼을 낸다(자격을 잃지 않기 위해)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.codex_auth = os.path.join(self.dir, "codex-auth.json")
        self.grok_auth = os.path.join(self.dir, "grok-auth.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fresh(self, codex_auth=None, grok_auth=None, status=(1, "Not logged in\n", ""), clis=("codex", "grok"), keyring=None, allowed=True):
        """임시 홈의 자격 파일과 가짜 CLI 답으로 fresh() 를 부른다. 실제 홈·실제 CLI 는 쓰지 않는다."""
        for path in (self.codex_auth, self.grok_auth):
            if os.path.exists(path):
                os.remove(path)
        for path, body in ((self.codex_auth, codex_auth), (self.grok_auth, grok_auth)):
            if body is not None:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body)
        exes = {k: (lambda name=k: "/fake/" + name) for k in clis}
        with mock.patch.object(cli_login, "CLIS", exes), \
                mock.patch.object(cli_login, "_codex_auth", return_value=self.codex_auth), \
                mock.patch.object(cli_login, "_grok_auth", return_value=self.grok_auth), \
                mock.patch.object(cli_login, '_codex_keyring_allowed', return_value=allowed), \
                mock.patch.object(cli_login, "run", side_effect=[status, keyring if keyring is not None else status]) as run:
            return cli_login.fresh(), run

    def test_offers_login_when_nothing_is_stored(self):
        got, run = self.fresh(status=(1, "Not logged in\n", ""))
        self.assertEqual(got, ["codex", "grok"])  # 자격 파일이 없고 codex 도 '아니오' 라고 답함
        self.assertEqual(run.call_args_list[0], mock.call(["/fake/codex", "login", "status"], cli_login.STATUS_S))
        self.assertEqual(run.call_args_list[1].args[0], ["/fake/codex", "-c", 'cli_auth_credentials_store="keyring"', "login", "status"])
        self.assertLessEqual(run.call_args_list[1].args[1], cli_login.STATUS_S)

    def test_never_offers_login_when_something_is_stored(self):
        got, run = self.fresh(codex_auth='{"tokens": {"access_token": "x"}}',
                               grok_auth='{"https://auth.x.ai::a": {"email": "a@b.c"}}')
        self.assertEqual(got, [])
        run.assert_not_called()  # 파일에 자격이 있으면 매번 CLI 를 부르지 않는다

    def test_codex_keyring_or_env_login_is_not_offered(self):
        # 파일이 없어도 codex 가 로그인 중이라 답하면( keyring·환경변수 백엔드) 버튼을 내지 않는다
        got, _ = self.fresh(status=(0, "Logged in using ChatGPT\n", ""), grok_auth='{"k": {}}')
        self.assertEqual(got, [])

    def test_codex_unknown_answer_keeps_the_login_hidden(self):
        # 답을 못 알아들으면(오류·시간 초과) 있는 쪽으로 본다: 없다고 오판하면 자격을 지운다
        for status in ((1, "", "Error checking login status: boom"), (-1, "", "timed out")):
            got, _ = self.fresh(status=status, grok_auth='{"k": {}}')
            self.assertEqual(got, [], status)

    def test_codex_absence_requires_the_exact_status_and_exit_code(self):
        """covers: USAGE-4 — 오류 설명에 'Not logged in'이 들어 있어도 자격 없음으로 단정하지 않는다."""
        for status in ((-1, '', 'Not logged in'),
                       (0, 'Not logged in', ''),
                       (1, '', 'Error checking login status: Not logged in'),
                       (1, 'Logged in using ChatGPT', 'Not logged in')):
            with self.subTest(status=status):
                got, _ = self.fresh(status=status, grok_auth='{"k": {}}')
                self.assertEqual(got, [])

    def test_codex_auto_fallback_is_not_proof_that_the_keyring_is_empty(self):
        """covers: USAGE-4 — auto는 키링 읽기 오류를 삼키므로 명시적인 키링 확인 실패도 자격을 보존한다."""
        for keyring in ((1, '', 'Error checking login status: keyring unavailable'),
                        (0, '', 'Logged in using ChatGPT'), (-1, '', 'timed out')):
            with self.subTest(keyring=keyring):
                got, _ = self.fresh(grok_auth='{"k": {}}', keyring=keyring)
                self.assertEqual(got, [])

    def test_codex_storage_checks_share_one_timeout_budget(self):
        """covers: USAGE-4 — 키링 확인을 더해도 기존 STATUS_S 전체 예산을 늘리지 않는다."""
        with mock.patch.object(cli_login.time, 'monotonic', side_effect=[100, 102, 104]):
            got, run = self.fresh(grok_auth='{"k": {}}')
        self.assertEqual(got, ['codex'])
        self.assertEqual(run.call_args_list[1].args[1], 2)

    def test_codex_managed_override_or_unknown_requirements_keep_login_hidden(self):
        """covers: USAGE-4 — 관리자 auto 설정이 -c keyring을 무시할 수 있으면 자격 부재를 단정하지 않는다."""
        got, run = self.fresh(grok_auth='{"k": {}}', allowed=False)
        self.assertEqual(got, [])
        self.assertEqual(run.call_count, 1)

    @unittest.skipIf(os.name == 'nt', '가짜 POSIX CLI 실행 파일')
    def test_codex_requirements_are_read_from_its_own_stdio_protocol(self):
        """covers: USAGE-4 — 관리 요건을 알 수 없거나 auto로 강제하면 keyring -c를 믿지 않는다."""
        import sys
        exe = os.path.join(self.dir, 'codex-protocol')
        for result, expected in (({'requirements': None}, True),
                                 ({'requirements': {'cliAuthCredentialsStore': 'keyring'}}, True),
                                 ({'requirements': {'cliAuthCredentialsStore': 'auto'}}, False),
                                 ({'requirements': {'cliAuthCredentialsStore': 'file'}}, False),
                                 ({}, False)):
            with self.subTest(result=result):
                with open(exe, 'w') as f:
                    f.write('#!' + sys.executable + '\nimport json,sys\ninitialized=False\n')
                    f.write('for line in sys.stdin:\n')
                    f.write(' req=json.loads(line)\n')
                    f.write(' if req["method"]=="initialize":\n  initialized=True\n  print(json.dumps({"id":req["id"],"result":{}}),flush=True)\n')
                    f.write(' elif req["method"]=="configRequirements/read" and initialized:\n')
                    f.write('  print(json.dumps({"id":req["id"],"result":' + repr(result) + '}),flush=True)\n  break\n')
                os.chmod(exe, stat.S_IRWXU)
                with mock.patch.dict(os.environ, {'HOME': self.dir, 'USERPROFILE': self.dir}):
                    self.assertEqual(cli_login._codex_keyring_allowed(exe, 1), expected)

    @unittest.skipIf(os.name == 'nt', '가짜 POSIX CLI 실행 파일')
    def test_codex_requirements_timeout_is_unknown(self):
        """covers: USAGE-4 — CLI가 요건을 답하지 않으면 자격 부재를 추측하지 않는다."""
        import sys
        exe = os.path.join(self.dir, 'codex-protocol')
        with open(exe, 'w') as f:
            f.write('#!' + sys.executable + '\nimport time\ntime.sleep(10)\n')
        os.chmod(exe, stat.S_IRWXU)
        with mock.patch.dict(os.environ, {'HOME': self.dir, 'USERPROFILE': self.dir}):
            self.assertFalse(cli_login._codex_keyring_allowed(exe, .1))

    @unittest.skipIf(os.name == 'nt', '가짜 POSIX CLI 실행 파일')
    def test_codex_requirements_cleanup_does_not_wait_for_launcher_children(self):
        """covers: USAGE-4 — npm 런처의 자식이 stdout을 상속해도 조회 예산 밖에서 기다리지 않는다."""
        import sys
        import time
        exe = os.path.join(self.dir, 'codex-protocol')
        with open(exe, 'w') as f:
            f.write('#!' + sys.executable + '\nimport subprocess,sys,time\n')
            f.write('subprocess.Popen([sys.executable,"-c","import time; time.sleep(2)"])\ntime.sleep(10)\n')
        os.chmod(exe, stat.S_IRWXU)
        with mock.patch.dict(os.environ, {'HOME': self.dir, 'USERPROFILE': self.dir}):
            started = time.monotonic()
            self.assertFalse(cli_login._codex_keyring_allowed(exe, .1))
            self.assertLess(time.monotonic() - started, 1.2)

    @unittest.skipIf(os.name == 'nt', '가짜 POSIX CLI 실행 파일')
    def test_codex_requirements_closed_stdin_is_unknown(self):
        """covers: USAGE-4 — 초기화 뒤 CLI의 stdin이 닫혀도 정리 오류가 사용량 API로 새지 않는다."""
        import sys
        exe = os.path.join(self.dir, 'codex-protocol')
        with open(exe, 'w') as f:
            f.write('#!' + sys.executable + '\nimport json,os,sys,time\n')
            f.write('req=json.loads(sys.stdin.readline())\nos.close(0)\n')
            f.write('print(json.dumps({"id":req["id"],"result":{}}),flush=True)\ntime.sleep(10)\n')
        os.chmod(exe, stat.S_IRWXU)
        with mock.patch.dict(os.environ, {'HOME': self.dir, 'USERPROFILE': self.dir}):
            self.assertFalse(cli_login._codex_keyring_allowed(exe, 1))

    def test_codex_without_status_command_is_treated_as_stored(self):
        # status 하위 명령이 없는 구버전: 알 수 없으므로 있는 쪽으로 본다.
        # 없다고 오판하면 '로그인' 버튼이 멀쩡한 자격을 지운다(000033e 가 막은 위험).
        for status in ((2, "", "error: unrecognized subcommand 'status'"), (1, "", "Usage: codex login")):
            got, _ = self.fresh(status=status, grok_auth='{"k": {}}')
            self.assertEqual(got, [], status)

    def test_grok_without_auth_file_is_offered(self):
        got, run = self.fresh(codex_auth="{}", grok_auth=None, status=(0, "Logged in using ChatGPT\n", ""))
        self.assertEqual(got, ["grok"])

    def test_grok_empty_auth_file_is_offered(self):
        got, _ = self.fresh(codex_auth="{}", grok_auth="{}", status=(0, "Logged in using ChatGPT\n", ""))
        self.assertEqual(got, ["grok"])  # 빈 파일은 저장한 자격이 아니다

    def test_unreadable_grok_auth_is_assumed_stored(self):
        got, _ = self.fresh(codex_auth="{}", grok_auth="not json", status=(0, "Logged in using ChatGPT\n", ""))
        self.assertEqual(got, [])  # 깨진 파일은 있는 쪽으로(실제 자격을 잃지 않게)

    def test_provider_without_stored_check_is_never_offered(self):
        # 저장 여부를 확인할 방법이 없는 프로바이더는 넣지 않는다
        got, _ = self.fresh(codex_auth="{}", grok_auth='{"k": {}}', status=(0, "Logged in using ChatGPT\n", ""),
                             clis=("codex", "grok", "cursor"))
        self.assertEqual(got, [])

    def test_missing_cli_is_never_offered(self):
        got, _ = self.fresh(codex_auth="{}", grok_auth='{"k": {}}', clis=())
        self.assertEqual(got, [])  # CLI 가 없으면 로그인 버튼도 낼 수 없다

    def test_codex_auth_path_honors_code_home(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": self.dir}):
            self.assertEqual(cli_login._codex_auth(), os.path.join(self.dir, "auth.json"))

    def test_grok_home_env_moves_the_credential_path(self):
        # 공식 GROK_HOME 을 재정의한 기기에서 ~/.grok 을 보면 자격을 없는 쪽으로 오판한다
        with mock.patch.dict(os.environ, {"GROK_HOME": self.dir}):
            self.assertEqual(cli_login._grok_home(), self.dir)
            self.assertEqual(cli_login._grok_auth(), os.path.join(self.dir, "auth.json"))

        # _grok() 는 PATH(which_local) 가 먼저다. PATH 가 없고 GROK_HOME 쪽에도 없으면 없다
        with mock.patch.dict(os.environ, {"GROK_HOME": self.dir}), \
                mock.patch.object(cli_login, "which_local", return_value=None):
            self.assertIsNone(cli_login._grok())
        with mock.patch.dict(os.environ, {"GROK_HOME": self.dir}), \
                mock.patch.object(cli_login, "which_local", return_value="/x/grok"):
            self.assertEqual(cli_login._grok(), "/x/grok")  # PATH 에 있으면 PATH 가 먼저다

    def test_grok_without_home_env_falls_back_to_dot_grok(self):
        env = {k: v for k, v in os.environ.items() if k != "GROK_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cli_login._grok_home(), os.path.join(os.path.expanduser("~"), ".grok"))
            self.assertEqual(cli_login._grok_auth(), os.path.join(os.path.expanduser("~"), ".grok", "auth.json"))

    def test_grok_home_relocates_the_binary_too(self):
        # GROK_HOME 을 옮긴 기기에서는 그쪽 bin 에서 CLI 를 찾아야 로그인 버튼이 난다
        exe = os.path.join(self.dir, "bin", "grok" + (".exe" if os.name == "nt" else ""))
        os.makedirs(os.path.dirname(exe))
        with open(exe, "w", encoding="utf-8") as f:
            f.write("")
        with mock.patch.dict(os.environ, {"GROK_HOME": self.dir}), \
                mock.patch.object(cli_login, "which_local", return_value=None):
            self.assertEqual(cli_login._grok(), exe)

    def test_grok_with_home_env_reports_stored_login_from_that_home(self):
        # GROK_HOME 쪽에 자격이 있으면 있는 쪽, 없으면 없는 쪽으로 판정한다
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, "auth.json"), "w", encoding="utf-8") as f:
            f.write('{"https://auth.x.ai::a": {"email": "a@b.c"}}')
        with mock.patch.dict(os.environ, {"GROK_HOME": self.dir}), \
                mock.patch.object(cli_login, "CLIS", {"grok": lambda: "/x/grok"}), \
                mock.patch.object(cli_login, "_codex_auth", return_value=os.path.join(self.dir, "codex-auth.json")):
            self.assertTrue(cli_login._grok_stored())
            self.assertEqual(cli_login.fresh(), [])  # 자격이 있으므로 버튼 후보에서 빠진다
        with open(os.path.join(self.dir, "auth.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        with mock.patch.dict(os.environ, {"GROK_HOME": self.dir}), \
                mock.patch.object(cli_login, "CLIS", {"grok": lambda: "/x/grok"}), \
                mock.patch.object(cli_login, "_codex_auth", return_value=os.path.join(self.dir, "codex-auth.json")):
            self.assertEqual(cli_login.fresh(), ["grok"])  # 빈 파일은 저장한 자격이 아니다


if __name__ == "__main__":
    unittest.main()
