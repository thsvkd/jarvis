"""트레이: 오류 문구."""
import os
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


class ConsolePythonTest(unittest.TestCase):
    def test_a_hidden_spawn_of_pythonw_uses_the_console_python_next_to_it(self):
        """창 없는 게이트웨이(pythonw)가 Python 자식을 pythonw 로 띄우면 그 아래 콘솔 프로그램이 저마다 창을 연다(thsvkd-win).
        같은 폴더의 python.exe 로 바꿔야 숨은 콘솔을 자손이 물려받는다."""
        import tempfile
        from jarvis.util import console_python
        with tempfile.TemporaryDirectory() as d:
            pyw, py = os.path.join(d, "pythonw.exe"), os.path.join(d, "python.exe")
            self.assertEqual(console_python([pyw, "-m", "jarvis"]), [pyw, "-m", "jarvis"])  # python.exe 가 없으면 그대로
            open(py, "w").close()
            self.assertEqual(console_python([pyw, "-m", "jarvis"]), [py, "-m", "jarvis"])
            self.assertEqual(console_python((pyw, "x")), (py, "x"))
        self.assertEqual(console_python(["npm", "install"]), ["npm", "install"])
        self.assertEqual(console_python("pythonw.exe -c pass"), "pythonw.exe -c pass")  # 문자열 명령은 건드리지 않는다
