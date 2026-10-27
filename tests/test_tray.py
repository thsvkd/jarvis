"""트레이 상태 줄의 커밋 해시: loose ref·packed-refs·detached HEAD 를 git 없이 읽는지."""
import os
import tempfile
import unittest

from jarvis import tray

A, B = "a" * 40, "b" * 40


class CommitTest(unittest.TestCase):
    def repo(self, files):
        root = tempfile.mkdtemp()
        for path, text in files.items():
            p = os.path.join(root, ".git", path)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(text)
        return root

    def test_loose_ref(self):
        self.assertEqual(tray.commit(self.repo({"HEAD": "ref: refs/heads/main\n", "refs/heads/main": A + "\n"})), "aaaaaaa")

    def test_packed_ref(self):
        packed = f"# pack-refs with: peeled\n{B} refs/heads/other\n{A} refs/heads/main\n"
        self.assertEqual(tray.commit(self.repo({"HEAD": "ref: refs/heads/main\n", "packed-refs": packed})), "aaaaaaa")

    def test_detached_and_missing(self):
        self.assertEqual(tray.commit(self.repo({"HEAD": B + "\n"})), "bbbbbbb")
        self.assertEqual(tray.commit(tempfile.mkdtemp()), "")


class ErrorTextTest(unittest.TestCase):
    def test_drops_path_and_truncates(self):
        err = r"C:\Users\someone\.config\jarvis\config.toml: Expected '=' after a key (at line 3, column 5)"
        self.assertEqual(tray.error_text(err), "Expected '=' after a key (at line 3, column 5)")
        self.assertEqual(tray.error_text("x" * 80, n=10), "x" * 9 + "…")
        self.assertEqual(tray.error_text(None), "")


if __name__ == "__main__":
    unittest.main()
