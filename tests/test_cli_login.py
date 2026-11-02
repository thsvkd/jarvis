import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from jarvis import cli_login

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


class FreshLoginTest(unittest.TestCase):
    """covers: USAGE-4. fresh(): 저장한 자격가 없을 때만 기기 행의 '로그인' 버튼을 낸다(자격을 잃지 않기 위해)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.codex_auth = os.path.join(self.dir, "codex-auth.json")
        self.grok_auth = os.path.join(self.dir, "grok-auth.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fresh(self, codex_auth=None, grok_auth=None, status=(1, "Not logged in\n", ""), clis=("codex", "grok")):
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
                mock.patch.object(cli_login, "run", return_value=status) as run:
            return cli_login.fresh(), run

    def test_offers_login_when_nothing_is_stored(self):
        got, run = self.fresh(status=(1, "Not logged in\n", ""))
        self.assertEqual(got, ["codex", "grok"])  # 자격 파일이 없고 codex 도 '아니오' 라고 답함
        run.assert_called_once_with(["/fake/codex", "login", "status"], cli_login.STATUS_S)

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
