"""공식 패키지 선택과 네이티브 업데이터 완료 판정."""
import json
import os
import struct
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from jarvis import orca_install


class InstallTest(unittest.TestCase):
    def test_native_cached_package_must_match_target_version(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "incoming.zip")
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("Orca.app/Contents/Resources/orca-local-build.json", json.dumps({
                    "version": "1.4.229", "appId": "com.stablyai.orca", "architecture": "arm64",
                    "daemonProtocolVersion": 48, "attachableDaemonProtocolVersions": [43, 48]}))
            with self.assertRaisesRegex(RuntimeError, "대상 버전"):
                orca_install.archive_contract(path, "1.4.237", "arm64")

    def test_native_target_contract_covers_old_daemon(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "incoming.zip")
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("Orca.app/Contents/Resources/orca-local-build.json", json.dumps({
                    "version": "1.4.237", "appId": "com.stablyai.orca", "architecture": "arm64",
                    "daemonProtocolVersion": 50, "attachableDaemonProtocolVersions": [43, 50]}))
            self.assertEqual(orca_install.archive_contract(path, "1.4.237", "arm64"), {43, 50})

    def test_managed_runtime_child_is_bound_to_official_parent(self):
        with patch.object(orca_install, "profile", return_value={"kind": "windows-user", "root": "/official"}), \
                patch.object(orca_install.orca, "find_dir", return_value="/tmp/fake"), \
                patch.object(orca_install.orca, "_read", return_value={"pid": 123}), \
                patch.object(orca_install.psutil, "Process") as process, \
                patch.object(orca_install.orca_rpc, "call", return_value={"appVersion": "1.4.237"}), \
                patch.object(orca_install, "installed", return_value="1.4.237"):
            process.return_value.exe.return_value = "/profile/orcad/node"
            parent = type("Parent", (), {"exe": lambda self: "/official/Orca.exe"})()
            process.return_value.parents.return_value = [parent]
            orca_install.validate_runtime()

    def test_runtime_from_another_install_cannot_be_updated(self):
        with patch.object(orca_install.orca, "find_dir", return_value="/tmp/fake"), \
                patch.object(orca_install.orca, "_read", return_value={"pid": 123}), \
                patch.object(orca_install.psutil, "Process") as process:
            process.return_value.exe.return_value = "/elsewhere/Orca"
            with self.assertRaises(RuntimeError):
                orca_install.validate_runtime()

    def test_asar_version_uses_package_not_running_process(self):
        with tempfile.TemporaryDirectory() as d:
            body = b'{"version":"1.4.237"}'
            header = json.dumps({"files": {"package.json": {"size": len(body), "offset": "0"}}}).encode()
            with open(os.path.join(d, "app.asar"), "wb") as f:
                f.write(struct.pack("<4I", 4, len(header) + 8, len(header) + 4, len(header)))
                f.write(header + body)
            self.assertEqual(orca_install.asar_version(d), "1.4.237")

    def test_native_install_waits_for_replacement_and_keeps_skills_failure(self):
        snaps = iter([
            {"status": {"state": "available", "version": "1.4.237"}},
            {"status": {"state": "downloaded", "version": "1.4.237"}},
        ])
        calls = []
        def rpc(method, params=None, **kw):
            calls.append(method)
            if method == "updater.install":
                return {"accepted": True, "targetVersion": "1.4.237"}
            if method == "updater.getStatus":
                return next(snaps)
            return {}
        with patch.object(orca_install.orca_rpc, "call", side_effect=rpc), \
                patch.object(orca_install, "wait_running", return_value="1.4.237"), \
                patch.object(orca_install, "installed", return_value="1.4.237"), \
                patch.object(orca_install, "skills", side_effect=RuntimeError("skills failed")):
            result = orca_install.native_update("1.4.224", "1.4.237", lambda _: None, lambda: None)
        self.assertEqual((result["app"], result["skills"]), ("updated", "failed"))
        self.assertIn("updater.install", calls)

    def test_native_error_never_installs(self):
        def rpc(method, *args, **kw):
            if method == "updater.install":
                self.fail("must not install failed download")
            return {"status": {"state": "error", "message": "download failed"}}
        with patch.object(orca_install.orca_rpc, "call", side_effect=rpc):
            with self.assertRaisesRegex(RuntimeError, "download failed"):
                orca_install.native_update("1.4.224", "1.4.237", lambda _: None, lambda: None)

class RecoveryTest(unittest.TestCase):
    def test_interrupted_directory_swap_restores_previous_installation(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, 'orca')
            resources = root + '.previous/opt/Orca/resources'
            os.makedirs(resources)
            body = b'{"version":"1.4.230"}'
            header = json.dumps({"files": {"package.json": {"size": len(body), "offset": "0"}}}).encode()
            with open(os.path.join(resources, 'app.asar'), 'wb') as f:
                f.write(struct.pack('<4I', 4, len(header) + 8, len(header) + 4, len(header)))
                f.write(header + body)
            with patch.object(orca_install, 'USER_DEB', root):
                orca_install.recover_user_deb()
            self.assertEqual(orca_install.asar_version(root + '/opt/Orca/resources'), '1.4.230')
            self.assertFalse(os.path.exists(root + '.previous'))
