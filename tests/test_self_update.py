"""jarvis 자체 갱신: 릴리스를 받아 검증해 versions/<버전> 에 두고(self_update), 런처가 그 버전으로 넘어가거나 되돌린다(launch)."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jarvis import launch, self_update  # noqa: E402


def tgz(v, files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, body in files.items():
            info = tarfile.TarInfo(name.replace("{p}", f"jarvis-{v}"))
            info.size = len(body)
            t.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def manifest(v, data):
    return {"version": v, "url": f"https://github.com/thsvkd/jarvis/releases/download/v{v}/jarvis-{v}.tar.gz",
            "sha256": hashlib.sha256(data).hexdigest()}


class Releases(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        p = mock.patch.multiple(launch, ROOT=self.d, VERSIONS=os.path.join(self.d, "versions"),
                                CURRENT=os.path.join(self.d, "current.json"))
        p.start()
        self.addCleanup(p.stop)

    def serve(self, m, data):
        """latest() 는 manifest 를, install() 은 묶음을 받는다."""
        def fake(req, timeout):
            body = json.dumps(m).encode() if req.full_url == self_update.MANIFEST else data
            return mock.MagicMock(__enter__=lambda s: io.BytesIO(body))
        return mock.patch.object(self_update.orca_update, "_open", fake)

    def test_update_and_rollback(self):
        data = tgz("1.2.0", {"{p}/jarvis/__init__.py": b'__version__ = "1.2.0"\n', "{p}/static/x.css": b""})
        with self.serve(manifest("1.2.0", data), data):
            self.assertEqual(self_update.start("1.2.0"), (200, {"running": "1.2.0", "to": "1.2.0", "restart": False}))
            self.assertEqual(self_update.start("1.1.0"), (200, {"running": "1.1.0", "to": "1.2.0", "restart": True}))
        self.assertTrue(os.path.isfile(os.path.join(self.d, "versions", "1.2.0", "static", "x.css")))
        self.assertEqual(launch.read(), {"version": "1.2.0", "previous": None, "pending": True, "tries": 0})
        # 저장소 1.1.0 에서 뜨면 새 릴리스로 넘어간다. 저장소가 같거나 새로우면(s2 개발 저장소) 저장소 그대로
        want = os.path.join(self.d, "versions", "1.2.0")
        self.assertEqual(launch.target("1.1.0", boot=False), want)
        self.assertIsNone(launch.target("1.2.0", boot=False))
        # 확정 전에 MAX_TRIES 번 넘게 다시 뜨면 이전(여기서는 저장소)으로 되돌린다
        for _ in range(launch.MAX_TRIES):
            self.assertEqual(launch.target("1.1.0"), want)
        self.assertIsNone(launch.target("1.1.0"))
        self.assertEqual((launch.read()["version"], launch.read()["bad"]), (None, "1.2.0"))
        with self.serve(manifest("1.2.0", data), data):  # 되돌린 버전은 다시 받지 않는다
            self.assertEqual(self_update.start("1.1.0")[0], 409)

    def test_keeps_running_versions(self):
        """CLI 가 두 번 받아도 서비스가 도는 버전(마지막 확정본)은 지우지 않는다."""
        os.makedirs(os.path.join(self.d, "versions", "1.3.0", "jarvis"))
        launch.write({"version": "1.3.0", "previous": None, "pending": False, "tries": 0})
        for v in ("1.4.0", "1.5.0"):
            data = tgz(v, {"{p}/jarvis/__init__.py": b""})
            with self.serve(manifest(v, data), data), mock.patch.object(launch, "release", return_value=None):
                self.assertTrue(self_update.start("1.1.0")[1]["restart"])
        self.assertEqual(launch.read()["previous"], "1.3.0")  # 한 번도 안 뜬 1.4.0 이 아니라
        # 확정 전인 1.4.0 은 서비스가 막 띄웠을 수 있어 이번에는 남긴다(다음 갱신 때 지운다)
        self.assertEqual(sorted(os.listdir(os.path.join(self.d, "versions"))), ["1.3.0", "1.4.0", "1.5.0"])

    def test_repo_newer_is_not_a_failed_boot(self):
        data = tgz("1.2.0", {"{p}/jarvis/__init__.py": b""})
        with self.serve(manifest("1.2.0", data), data):
            self_update.start("1.1.0")
        for _ in range(launch.MAX_TRIES + 2):  # 저장소를 1.3.0 으로 pull 한 기기: 릴리스는 뜨지 않으니 실패로 세지 않는다
            self.assertIsNone(launch.target("1.3.0"))
        self.assertNotIn("bad", launch.read())
        with mock.patch.dict(os.environ, {"JARVIS_NO_RELEASE": "1"}), mock.patch.object(launch, "target") as t:
            launch.run("1.1.0")  # 비상구: 받아 둔 릴리스로 넘어가지 않는다
        t.assert_not_called()

    def test_confirm(self):
        launch.write({"version": "1.2.0", "previous": None, "pending": True, "tries": 1})
        with mock.patch.object(launch, "release", return_value="1.2.0"):
            launch.confirm()
        self.assertEqual(launch.read()["pending"], False)
        self.assertEqual(launch.target("1.1.0"), None)  # 폴더가 없으면 저장소로 뜬다

    def test_rejects(self):
        good = tgz("1.2.0", {"{p}/jarvis/__init__.py": b""})
        evil = tgz("1.2.0", {"{p}/../../x": b"", "{p}/jarvis/__init__.py": b""})
        win = tgz("1.2.0", {"{p}/..\\..\\x": b"", "{p}/jarvis/__init__.py": b""})
        for m, data, why in ((manifest("1.2.0", good), good + b"x", "SHA256"),
                             (manifest("1.2.0", evil), evil, "이상한 항목"),
                             (manifest("1.2.0", win), win, "이상한 항목"),
                             ({**manifest("1.2.0", good), "url": "https://evil.example/x.tar.gz"}, good, "형식")):
            with self.serve(m, data):
                code, body = self_update.start("1.1.0")
            self.assertEqual(code, 502)
            self.assertIn(why, body["error"])
        self.assertEqual(launch.read(), {})
        self.assertEqual(os.listdir(os.path.join(self.d, "versions")), [])  # 반쯤 푼 폴더를 남기지 않는다

    def test_launcher_switches_in_process(self):
        """저장소에서 python -m jarvis 를 띄우면 받아 둔 릴리스의 코드가 돈다."""
        dest = os.path.join(self.d, "versions", "9.9.9")
        shutil.copytree(os.path.join(ROOT, "jarvis"), os.path.join(dest, "jarvis"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        with open(os.path.join(dest, "jarvis", "__init__.py"), "w") as f:
            f.write('__version__ = "9.9.9"\n')
        launch.write({"version": "9.9.9", "previous": None, "pending": True, "tries": 0})
        out = subprocess.run([sys.executable, "-m", "jarvis", "--version"], cwd=ROOT, capture_output=True, text=True,
                             env={**os.environ, "JARVIS_RELEASES": self.d, "JARVIS_CONFIG": os.devnull}, timeout=60)
        self.assertEqual(out.stdout.strip(), "jarvis 9.9.9 (릴리스)", out.stderr)
        self.assertEqual(launch.read()["tries"], 0)  # 명령 실행은 기동으로 세지 않는다


if __name__ == "__main__":
    unittest.main()
