"""jarvis 자체 갱신(self_update.start): fast-forward 만 받고, 도는 코드와 다를 때만 재시작을 알린다."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis import self_update  # noqa: E402


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *a],
                          check=True, capture_output=True, text=True).stdout.strip()


class SelfUpdate(unittest.TestCase):
    def test_pull(self):
        with tempfile.TemporaryDirectory() as d:
            up, me = os.path.join(d, "up"), os.path.join(d, "me")
            subprocess.run(["git", "init", "-q", "-b", "main", up], check=True)
            git(up, "commit", "-q", "--allow-empty", "-m", "a")
            subprocess.run(["git", "clone", "-q", up, me], check=True)
            boot = git(me, "rev-parse", "HEAD")

            git(up, "commit", "-q", "--allow-empty", "-m", "b")
            code, body = self_update.start(me, boot)
            self.assertEqual((code, body["restart"]), (200, True))
            self.assertEqual(git(me, "rev-parse", "HEAD"), git(up, "rev-parse", "HEAD"))

            new = git(me, "rev-parse", "HEAD")  # 재시작한 뒤: 받을 것이 없으면 재시작하지 않는다
            self.assertEqual(self_update.start(me, new), (200, {"from": new[:10], "to": new[:10], "running": new[:10],
                                                                 "restart": False}))
            self.assertTrue(self_update.start(me, boot)[1]["restart"])  # push 로 트리만 바뀌고 안 돈 경우

            with open(os.path.join(up, "f"), "w") as f:  # 커밋 안 된 수정이 있으면 받지 않는다(s2 작업 트리)
                f.write("1")
            git(up, "add", "f")
            git(up, "commit", "-q", "-m", "f")
            git(me, "pull", "-q")
            with open(os.path.join(me, "f"), "w") as f:
                f.write("2")
            self.assertEqual(self_update.start(me, boot)[0], 409)
            git(me, "checkout", "-q", "f")

            git(me, "commit", "-q", "--allow-empty", "-m", "local")  # 갈라지면 받지 않는다
            git(up, "commit", "-q", "--allow-empty", "-m", "c")
            code, body = self_update.start(me, boot)
            self.assertEqual(code, 409)
            self.assertIn("git pull 실패", body["error"])


if __name__ == "__main__":
    unittest.main()
