"""기존 세션의 호환성·격리·연속성을 확인해야만 갱신 성공을 보고한다."""
import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from jarvis import orca_sessions as sessions


def inventory(rows, key):
    return {key: rows, "totalCount": len(rows), "truncated": False,
            "hostScope": {"hostIds": ["local"], "omittedHostIds": []}}


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.t = {"ptyId": "pty-1", "incarnationId": "same-process", "connected": True, "executionHostId": "local", "tabId": "tab", "leafId": "leaf"}
        self.tree = {"liveTerminalCount": 1, "unverifiableTerminalCount": 0, "hasAttachedPty": True,
                     "agents": [{"state": "working", "structuredHostOwned": False, "paneKey": "tab:leaf"}]}
        self.facts = {"liveSessions": 1, "inProcessSessions": 0}
        self.daemon = {"pid": 123, "created": 12.5, "protocol": 43}
        self.body = {"worktree.ps": inventory([self.tree], "worktrees"),
                     "terminal.list": inventory([self.t], "terminals"), "orcad.terminalCensus": self.facts}
        self.rpc = patch.object(sessions.orca_rpc, "call", side_effect=lambda m, *a, **k: copy.deepcopy(self.body[m]))
        self.records = patch.object(sessions, "daemon_records", return_value=[self.daemon])
        self.isolated = patch.object(sessions, "assert_isolated")
        for p in (self.rpc, self.records, self.isolated):
            p.start(); self.addCleanup(p.stop)

    def test_working_daemon_terminal_survives_without_being_restarted(self):
        guard = sessions.Sessions(); guard.capture(); guard.compatible({43, 50})
        with patch.object(sessions, "process_identity", return_value={"pid": 123, "created": 12.5}):
            proof = guard.verify()
        self.assertEqual(proof, {"state": "preserved", "terminals": 1, "daemons": 1, "admission_fenced": False})

    def test_in_process_fallback_is_never_treated_as_persistent(self):
        for value in (1, None, False, "0"):
            self.facts["inProcessSessions"] = value
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                sessions.Sessions().capture()

    def test_unknown_active_agent_without_a_daemon_terminal_is_blocked(self):
        self.tree["agents"] = [{"state": "working"}]
        with self.assertRaises(RuntimeError):
            sessions.Sessions().capture()

    def test_active_native_chat_blocks_but_idle_chat_does_not(self):
        self.tree["agents"][0]["structuredHostOwned"] = True
        with self.assertRaisesRegex(RuntimeError, "네이티브 채팅"):
            sessions.Sessions().capture()
        self.tree["agents"][0]["state"] = "done"
        sessions.Sessions().capture()

    def test_unknown_or_partial_terminal_coverage_blocks(self):
        for change in ({"truncated": True}, {"totalCount": 2}, {"hostScope": {"hostIds": ["local"], "omittedHostIds": ["ssh-1"]}}):
            original = self.body["terminal.list"]
            self.body["terminal.list"] = {**original, **change}
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                sessions.Sessions().capture()
            self.body["terminal.list"] = original

    def test_terminal_count_mismatch_blocks(self):
        self.facts["liveSessions"] = 2
        with self.assertRaises(RuntimeError):
            sessions.Sessions().capture()

    def test_incompatible_target_is_refused_before_restart(self):
        guard = sessions.Sessions(); guard.capture()
        with self.assertRaisesRegex(RuntimeError, "재연결"):
            guard.compatible({50})

    def test_new_final_census_keeps_original_baseline(self):
        guard = sessions.Sessions(); guard.capture()
        self.body["terminal.list"] = inventory([{**self.t, "ptyId": "new-terminal"}], "terminals")
        with self.assertRaises(RuntimeError):
            guard.capture()
        self.assertEqual(guard.terminals, {"pty-1": "same-process"})

    def test_same_pty_with_replacement_process_is_not_a_success(self):
        guard = sessions.Sessions(); guard.capture()
        self.t["incarnationId"] = "replacement"
        with patch.object(sessions, "process_identity", return_value={"pid": 123, "created": 12.5}), self.assertRaisesRegex(RuntimeError, "PTY"):
            guard.verify()

    def test_an_incomplete_inventory_after_the_update_is_a_failure_not_a_hold(self):
        """covers: ORCA-10 (X3) — 보류(Deferred)는 바꾸기 전에만 성립한다. 바꾼 뒤 확인하지 못하면 실패다."""
        guard = sessions.Sessions(); guard.capture()
        self.body["terminal.list"] = dict(self.body["terminal.list"], truncated=True)
        with patch.object(sessions, "process_identity", return_value={"pid": 123, "created": 12.5}):
            with self.assertRaises(RuntimeError) as caught:
                guard.verify()
        self.assertNotIsInstance(caught.exception, sessions.Deferred)
        with self.assertRaises(sessions.Deferred):  # 같은 상태를 바꾸기 전에 만나면 보류다
            sessions.Sessions().capture()

    def test_reused_daemon_pid_is_not_a_success(self):
        guard = sessions.Sessions(); guard.capture()
        with patch.object(sessions, "process_identity", return_value={"pid": 123, "created": 99}), self.assertRaisesRegex(RuntimeError, "데몬"):
            guard.verify()

    def test_only_a_confirmed_server_fence_is_reported(self):
        guard = sessions.Sessions()
        with patch.object(sessions.time, "time", return_value=1000):
            self.facts["admissionClosedUntil"] = 1200000
            guard.capture(fence=True)
        self.assertTrue(guard.fenced)
        guard.release()
        self.assertEqual(guard.fence_until, 0)

    def test_old_final_census_cannot_authorize_later_stop(self):
        guard = sessions.Sessions()
        with patch.object(sessions.time, "monotonic", return_value=100):
            guard.capture()
        with patch.object(sessions.time, "monotonic", return_value=111), self.assertRaises(RuntimeError):
            guard.before_stop()


class ArtifactTest(unittest.TestCase):
    def test_literal_contract_is_read_without_executing_package_code(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "contract.js")
            with open(p, "w") as f:
                f.write("throw new Error('must not execute'); exports.contract={daemonProtocolVersion:50,attachableDaemonProtocolVersions:[39,43,50]};")
            self.assertEqual(sessions.read_contract(p), {39, 43, 50})
            with open(p, "w") as f:
                f.write("exports.contract={daemonProtocolVersion:50,attachableDaemonProtocolVersions:guess()};")
            with self.assertRaises(RuntimeError):
                sessions.read_contract(p)

    def test_service_cgroup_descendant_is_not_isolated(self):
        record = {"pid": 123}
        with patch.object(sessions.sys, "platform", "linux"), \
                patch.object(sessions, "run", return_value=(0, "ControlGroup=/unit", "")), \
                patch.object(sessions.psutil, "Process") as p, \
                patch.object(sessions, "cgroup", return_value="/unit/child"):
            p.return_value.pid = 123
            p.return_value.children.return_value = []
            with self.assertRaisesRegex(RuntimeError, "종료 범위"):
                sessions.assert_isolated([record])

    def test_sibling_daemon_scope_is_isolated(self):
        with patch.object(sessions.sys, "platform", "linux"), \
                patch.object(sessions, "run", return_value=(0, "ControlGroup=/unit", "")), \
                patch.object(sessions.psutil, "Process") as p, \
                patch.object(sessions, "cgroup", return_value="/daemon-scope"):
            p.return_value.pid = 123
            p.return_value.children.return_value = []
            sessions.assert_isolated([{"pid": 123}])
