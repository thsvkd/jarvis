"""트레이: 오류 문구."""
import unittest

from jarvis import tray


class ErrorTextTest(unittest.TestCase):
    def test_drops_path_and_truncates(self):
        err = r"C:\Users\someone\.config\jarvis\config.toml: Expected '=' after a key (at line 3, column 5)"
        self.assertEqual(tray.error_text(err), "Expected '=' after a key (at line 3, column 5)")
        self.assertEqual(tray.error_text("x" * 80, n=10), "x" * 9 + "…")
        self.assertEqual(tray.error_text(None), "")


if __name__ == "__main__":
    unittest.main()
