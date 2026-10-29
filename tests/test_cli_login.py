import os
import stat
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
