"""Orca 업데이트: 매니페스트에서 deb 를 고르고, 해시가 맞는 뒤에만 서비스를 멈추고, 실패해도 다시 띄운다."""
import hashlib
import base64
import os
import tempfile
import json
import unittest
from unittest.mock import patch

from jarvis import orca_update


YML = """version: 1.4.232
files:
  - url: orca-linux-arm64.AppImage
    sha512: AAAA
    size: 10
  - url: orca-ide_1.4.232_arm64.deb
    sha512: HASH
    size: 173
  - url: orca-ide-1.4.232.aarch64.rpm
    sha512: BBBB
    size: 14
path: orca-linux-arm64.AppImage
sha512: AAAA
"""


class ParseTest(unittest.TestCase):
    def test_picks_deb_for_arch(self):
        version, meta = orca_update.parse_manifest(YML, "arm64")
        self.assertEqual(version, "1.4.232")
        self.assertEqual(meta, {"name": "orca-ide_1.4.232_arm64.deb", "sha512": "HASH", "size": 173})

    def test_rejects_other_arch_and_bad_version(self):
        with self.assertRaises(RuntimeError):
            orca_update.parse_manifest(YML, "amd64")
        with self.assertRaises(RuntimeError):
            orca_update.parse_manifest("version: latest\n", "arm64")

    def test_hash(self):
        body = b"deb-bytes"
        digest = base64.b64encode(hashlib.sha512(body).digest()).decode()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.deb")
            with open(path, "wb") as f:
                f.write(body)
            orca_update.check_file(path, digest, len(body))
            with self.assertRaises(RuntimeError):
                orca_update.check_file(path, digest, len(body) - 1)
            with self.assertRaises(RuntimeError):
                orca_update.check_file(path, "nope", len(body))


class Ops:
    def __init__(self, installed="1.4.220", target="1.4.232", sudo=True, install_fails=False, skills_fails=False, stop_fails=False):
        self.calls = []
        self.installed_v = installed
        self.target = target
        self.sudo = sudo
        self.install_fails = install_fails
        self.skills_fails = skills_fails
        self.stop_fails = stop_fails
        self.running = target

    def phase(self, name):
        self.calls.append("phase:" + name)

    def manifest(self):
        self.calls.append("manifest")
        return self.target, {"name": f"orca-ide_{self.target}_arm64.deb", "sha512": "H", "size": 3}

    def installed(self):
        self.calls.append("installed")
        return self.installed_v

    def download(self, target, meta):
        self.calls.append("download")
        return "/var/lib/jarvis/" + meta["name"]

    def sudo_ok(self):
        self.calls.append("sudo")
        return self.sudo

    def mark_stopped(self):
        self.calls.append("mark-stop")

    def mark_started(self):
        self.calls.append("mark-start")

    def stop(self):
        self.calls.append("stop")
        if self.stop_fails:
            raise RuntimeError("stop failed")

    def install(self, path):
        self.calls.append("install")
        if self.install_fails:
            raise RuntimeError("apt failed")
        self.installed_v = self.target

    def start(self):
        self.calls.append("start")

    def running_version(self):
        self.calls.append("running")
        return self.running

    def skills(self):
        self.calls.append("skills")
        if self.skills_fails:
            raise RuntimeError("npx failed")

    def cleanup(self, path):
        self.calls.append("cleanup")


class ExecuteTest(unittest.TestCase):
    def test_updates_then_skills(self):
        ops = Ops()
        out = orca_update.execute(ops)
        self.assertEqual(out["app"], "updated")
        self.assertEqual(out["skills"], "updated")
        self.assertEqual((out["from_version"], out["to_version"]), ("1.4.220", "1.4.232"))
        self.assertLess(ops.calls.index("sudo"), ops.calls.index("stop"))
        self.assertLess(ops.calls.index("stop"), ops.calls.index("install"))
        self.assertLess(ops.calls.index("install"), ops.calls.index("start"))
        self.assertLess(ops.calls.index("start"), ops.calls.index("skills"))
        self.assertIn("cleanup", ops.calls)

    def test_current_skips_install_and_updates_skills(self):
        ops = Ops(installed="1.4.232", target="1.4.232")
        out = orca_update.execute(ops)
        self.assertEqual(out["app"], "current")
        self.assertNotIn("stop", ops.calls)
        self.assertNotIn("download", ops.calls)
        self.assertIn("skills", ops.calls)

    def test_no_sudo_does_not_stop(self):
        ops = Ops(sudo=False)
        with self.assertRaises(RuntimeError):
            orca_update.execute(ops)
        self.assertNotIn("stop", ops.calls)
        self.assertIn("cleanup", ops.calls)

    def test_install_failure_restarts(self):
        ops = Ops(install_fails=True)
        with self.assertRaises(RuntimeError):
            orca_update.execute(ops)
        self.assertLess(ops.calls.index("install"), ops.calls.index("start"))
        self.assertNotIn("skills", ops.calls)

    def test_stop_failure_still_starts(self):
        ops = Ops(stop_fails=True)
        with self.assertRaises(RuntimeError):
            orca_update.execute(ops)
        self.assertIn("start", ops.calls)
        self.assertNotIn("install", ops.calls)

    def test_skills_failure_keeps_app_result(self):
        ops = Ops(skills_fails=True)
        out = orca_update.execute(ops)
        self.assertEqual(out["app"], "updated")
        self.assertEqual(out["skills"], "failed")
        self.assertIn("npx", out["error"])

    def test_does_not_downgrade(self):
        ops = Ops(installed="1.4.240", target="1.4.232")
        out = orca_update.execute(ops)
        self.assertEqual(out["app"], "current")
        self.assertNotIn("download", ops.calls)


class CheckTest(unittest.TestCase):
    def test_mac_224_supervised_updater_requires_initial_migration(self):
        with patch.object(orca_update.orca_install, "profile", return_value={"kind": "mac-app", "cli": "/orca"}), \
                patch.object(orca_update.os.path, "isfile", return_value=True), \
                patch.object(orca_update.orca_rpc, "call", return_value={"appVersion": "1.4.224", "support": {
                    "automatic": True, "installMode": "supervised-headless-serve"}}):
            supported, reason = self.old[0]()
        self.assertFalse(supported)
        self.assertIn("1.4.224", reason)

    def test_fixed_mac_updater_remains_supported(self):
        with patch.object(orca_update.orca_install, "profile", return_value={"kind": "mac-app", "cli": "/orca"}), \
                patch.object(orca_update.os.path, "isfile", return_value=True), \
                patch.object(orca_update.orca_rpc, "call", return_value={"appVersion": "1.4.237", "support": {
                    "automatic": True, "installMode": "supervised-headless-serve"}}):
            self.assertEqual(self.old[0](), (True, None))

    def setUp(self):
        self.old = orca_update._supported, orca_update._installed, orca_update.Live.manifest, orca_update.job
        self.manifest_calls = 0

        def manifest(self_ops):
            self.manifest_calls += 1
            return self.latest, {"name": "x.deb"}

        orca_update.Live.manifest = manifest
        orca_update.job = None

    def tearDown(self):
        orca_update._supported, orca_update._installed, orca_update.Live.manifest, orca_update.job = self.old

    def test_current_and_newer_and_ahead(self):
        orca_update._supported = lambda: (True, None)
        orca_update._installed = lambda: "1.4.232"
        self.latest = "1.4.232"
        same = orca_update.check()
        self.assertTrue(same["current"])
        self.assertEqual(same["installed"], "1.4.232")
        self.assertIsNone(same["error"])
        self.latest = "1.4.240"
        newer = orca_update.check()
        self.assertFalse(newer["current"])
        self.assertEqual((newer["installed"], newer["latest"]), ("1.4.232", "1.4.240"))
        orca_update._installed = lambda: "1.4.241"
        self.latest = "1.4.240"
        self.assertTrue(orca_update.check()["current"])
        self.assertIsNone(orca_update.job)
        self.assertGreater(self.manifest_calls, 0)

    def test_version_check_works_even_when_installation_is_unsupported(self):
        orca_update._supported = lambda: (False, "deb 가 아닙니다")
        orca_update._installed = lambda: "1.4.232"
        self.latest = "9.9.9"
        body = orca_update.check()
        self.assertFalse(body["supported"])
        self.assertFalse(body["current"])
        self.assertIsNone(body["error"])
        self.assertIn("deb", body["reason"])
        self.assertEqual(body["latest"], "9.9.9")
        self.assertEqual(self.manifest_calls, 1)
        self.assertIsNone(orca_update.job)

    def test_current_version_can_report_installation_unavailable(self):
        orca_update._supported = lambda: (False, "권한 없음")
        orca_update._installed = lambda: "1.4.237"
        self.latest = "1.4.237"
        body = orca_update.check()
        self.assertTrue(body["current"])
        self.assertFalse(body["supported"])
        self.assertIsNone(body["error"])


class SafetyTest(unittest.TestCase):
    def test_failed_session_proof_keeps_user_package_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = os.path.join(directory, "orca.previous")
            os.mkdir(backup)
            live = orca_update.Live(); live.backup = backup
            with patch.object(live.sessions, "verify", side_effect=RuntimeError("lost session")):
                with self.assertRaises(RuntimeError):
                    live.verify_sessions()
            live.cleanup(os.path.join(directory, "missing.deb"))
            self.assertTrue(os.path.isdir(backup))

    def test_busy_daemon_terminal_is_not_rejected_as_busy(self):
        census = {"worktrees": [{"liveTerminalCount": 1, "unverifiableTerminalCount": 0,
                                  "hasAttachedPty": True, "agents": [{"state": "working", "paneKey": "tab:leaf"}]}],
                  "totalCount": 1, "truncated": False,
                  "hostScope": {"hostIds": ["local"], "omittedHostIds": []}}
        replies = {"worktree.ps": census, "orcad.terminalCensus": {"liveSessions": 1, "inProcessSessions": 0},
                   "terminal.list": {"terminals": [{"ptyId": "p", "incarnationId": "i", "connected": True, "executionHostId": "local", "tabId": "tab", "leafId": "leaf"}],
                                     "totalCount": 1, "truncated": False, "hostScope": census["hostScope"]}}
        with patch("jarvis.orca_update.orca_rpc.call", side_effect=lambda method, *a, **kw: replies[method]), \
                patch("jarvis.orca_sessions.daemon_records", return_value=[{"pid": 123, "created": 1, "protocol": 43}]), \
                patch("jarvis.orca_sessions.assert_isolated"):
            proof = orca_update.ensure_idle()
        self.assertEqual(proof.terminals, {"p": "i"})

    def test_busy_machine_does_not_download_or_stop(self):
        ops = Ops()
        ops.idle = lambda: (_ for _ in ()).throw(RuntimeError("작업 중"))
        with self.assertRaisesRegex(RuntimeError, "작업 중"):
            orca_update.execute(ops)
        self.assertNotIn("download", ops.calls)
        self.assertNotIn("stop", ops.calls)

    def test_busy_check_is_repeated_after_download(self):
        ops = Ops()
        count = 0
        def idle():
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("새 작업")
        ops.idle = idle
        with self.assertRaisesRegex(RuntimeError, "새 작업"):
            orca_update.execute(ops)
        self.assertIn("download", ops.calls)
        self.assertIn("cleanup", ops.calls)
        self.assertNotIn("stop", ops.calls)

    def test_live_terminals_and_incomplete_census_block_restart(self):
        for census in ({"worktrees": [{"liveTerminalCount": 1}]},
                       {"worktrees": [{}]},
                       {"worktrees": [{"liveTerminalCount": 0, "unverifiableTerminalCount": 0}], "totalCount": 1, "truncated": False},
                       {"worktrees": [], "truncated": True},
                       {"worktrees": [{"unverifiableTerminalCount": 1}]},
                       {"worktrees": [], "hostScope": {"omittedHostIds": ["local"]}}):
            with self.subTest(census=census), patch("jarvis.orca_update.orca_rpc.call", return_value=census):
                with self.assertRaises(RuntimeError):
                    orca_update.ensure_idle()

    def test_complete_empty_census_allows_update(self):
        census = {"worktrees": [{"liveTerminalCount": 0, "unverifiableTerminalCount": 0,
                                  "hasAttachedPty": False, "agents": []}], "totalCount": 1, "truncated": False,
                  "hostScope": {"hostIds": ["local"], "omittedHostIds": []}}
        replies = {"worktree.ps": census, "orcad.terminalCensus": {"liveSessions": 0, "inProcessSessions": 0},
                   "terminal.list": {"terminals": [], "totalCount": 0, "truncated": False, "hostScope": census["hostScope"]}}
        with patch("jarvis.orca_update.orca_rpc.call", side_effect=lambda method, *a, **kw: replies[method]):
            orca_update.ensure_idle()


class StartGuardTest(unittest.TestCase):
    def test_unresolved_checkpoint_cannot_be_overwritten_by_new_update(self):
        with tempfile.TemporaryDirectory() as d:
            marker = os.path.join(d, "orca-update.json")
            with open(marker, "w") as f:
                json.dump({"stopped": True, "transaction": "previous"}, f)
            with patch.object(orca_update, "MARK", marker), \
                    patch.object(orca_update, "_supported", return_value=(True, None)), \
                    patch.object(orca_update, "job", None), \
                    patch.object(orca_update.threading, "Thread"):
                status, _ = orca_update.start()
            self.assertEqual(status, 409)
            with open(marker) as f:
                self.assertEqual(json.load(f)["transaction"], "previous")

    def test_unsupported_does_not_start_a_job(self):
        old = orca_update._supported, orca_update.job
        orca_update._supported = lambda: (False, "deb 가 아닙니다")
        try:
            code, body = orca_update.start()
            self.assertEqual(code, 409)
            self.assertIn("deb", body["error"])
            self.assertIsNone(orca_update.job)
        finally:
            orca_update._supported, orca_update.job = old
