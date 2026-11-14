"""macOS 게이트웨이 로그 회전(jarvis/logrotate.py). 임시 폴더의 파일만 쓴다."""
import os
import stat
import tempfile
import unittest
from unittest import mock

from jarvis import logrotate


class RotateTest(unittest.TestCase):
    """covers: DAEMON-17"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = os.path.join(tmp.name, "jarvis.log")

    def write(self, text, path=None, mode="w"):
        with open(path or self.log, mode) as f:
            f.write(text)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_a_small_log_is_left_alone_but_narrowed_to_0600(self):
        self.write("short\n")
        os.chmod(self.log, 0o644)
        self.assertFalse(logrotate.rotate(self.log, limit=100))
        self.assertEqual((self.read(self.log), self.mode(self.log)), ("short\n", 0o600))
        self.assertFalse(os.path.exists(self.log + ".1"))

    def test_a_big_log_is_copied_then_emptied_and_three_are_kept(self):
        for i in range(5):
            self.write(f"round {i}\n" * 50)
            self.assertTrue(logrotate.rotate(self.log, limit=100))
            self.assertEqual(self.read(self.log), "")
        self.assertEqual(self.read(self.log + ".1"), "round 4\n" * 50)
        self.assertEqual(self.read(self.log + ".3"), "round 2\n" * 50)
        self.assertFalse(os.path.exists(self.log + ".4"))
        for p in (self.log, self.log + ".1", self.log + ".2", self.log + ".3"):
            self.assertEqual(self.mode(p), 0o600, p)

    def test_a_writer_that_appends_keeps_writing_from_the_start(self):
        """launchd 의 StandardOutPath 와 LaunchDaemon 의 >> 는 덧붙이기로 연다: 비운 뒤의 줄은 앞에서부터 쌓인다(구멍 없음)"""
        writer = open(self.log, "a")
        self.addCleanup(writer.close)
        writer.write("x" * 500 + "\n")
        writer.flush()
        self.assertTrue(logrotate.rotate(self.log, limit=100))
        writer.write("after\n")
        writer.flush()
        self.assertEqual(self.read(self.log), "after\n")

    def test_a_symlink_or_a_missing_log_is_not_touched(self):
        self.assertFalse(logrotate.rotate(self.log, limit=1))
        target = self.log + ".target"
        self.write("y" * 500, target)
        os.symlink(target, self.log)
        self.assertFalse(logrotate.rotate(self.log, limit=1))
        self.assertEqual(self.read(target), "y" * 500)

    def test_only_macos_starts_the_watch_and_the_daemon_log_is_used(self):
        for platform, want in (("darwin", 1), ("linux", 0), ("win32", 0)):
            with mock.patch.object(logrotate.sys, "platform", platform), mock.patch.object(logrotate.threading, "Thread") as thread:
                logrotate.start()
            self.assertEqual(thread.call_count, want, platform)
        with mock.patch.dict(os.environ, {"JARVIS_LOG": "/x/daemon.log"}):
            self.assertEqual(logrotate.path(), "/x/daemon.log")
        with mock.patch.dict(os.environ, {"HOME": "/u"}), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_LOG", None)
            self.assertEqual(logrotate.path(), "/u/Library/Logs/jarvis.log")
        self.assertEqual((logrotate.LIMIT, logrotate.KEEP), (10 << 20, 3))


if __name__ == "__main__":
    unittest.main()
