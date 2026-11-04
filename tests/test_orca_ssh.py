"""Orca SSH 호스트 연결: 이 기기에 배포된 orcad(~/.orca)와 데스크톱 환경의 연결 방식. 임시 폴더만 본다(실제 Orca·홈 없음)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import psutil

from jarvis import orca


def wait_exec(pid):
    """fork 직후 exec 전에는 부모(테스트)의 명령행이 보인다. 가짜 프로세스의 명령행이 보일 때까지 기다린다."""
    for _ in range(500):
        if "import time; time.sleep(60)" in psutil.Process(pid).cmdline():
            return
        time.sleep(0.01)
    raise AssertionError("가짜 프로세스의 명령행이 보이지 않습니다")

SECRETS = ("AUTH-SECRET", "DEV-TOKEN", "MOBILE-TOKEN", "ENV-TOKEN", "TUNNEL-TOKEN", "PUBKEY")


def write(d, name, body):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(body if isinstance(body, str) else json.dumps(body))


def endpoint(token):
    return {"id": "e", "kind": "websocket", "label": "x", "endpoint": "ws://100.64.0.9:6768", "deviceToken": token,
            "publicKeyB64": "PUBKEY"}


class SshHostTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.ssh = os.path.join(self.root, "ssh")

    def orcad(self, pid):
        write(self.ssh, "orca-runtime.json", {"pid": pid, "runtimeId": "r", "authToken": "AUTH-SECRET",
                                              "transports": [{"kind": "websocket", "endpoint": "ws://127.0.0.1:6770"}]})
        write(self.ssh, "orca-devices.json", [
            {"deviceId": "638d1290", "name": "kgolf", "scope": "runtime", "token": "DEV-TOKEN", "lastSeenAt": 5},
            {"deviceId": "m1", "name": "phone", "scope": "mobile", "token": "MOBILE-TOKEN", "lastSeenAt": 0}])

    def test_reports_the_ssh_host_orcad_with_client_names_and_no_tokens(self):
        """covers: ORCA-14"""
        self.assertIsNone(orca.ssh_host((self.ssh,)))
        with mock.patch.dict(os.environ, {"HOME": self.root, "USERPROFILE": self.root}):
            # 명령행에 <홈>/.orca-remote/.../orcad.js 가 있는 가짜 orcad
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                                  os.path.join(self.root, ".orca-remote", "orcad-x", "orcad.js")])
            self.addCleanup(p.wait)
            self.addCleanup(p.kill)
            wait_exec(p.pid)
            self.orcad(p.pid)
            s = orca.ssh_host((self.ssh,))
            self.assertEqual(s, {"dir": self.ssh, "pid": p.pid, "running": True, "clients": ["kgolf"]})
            self.orcad(os.getpid())  # 런타임 파일이 남은 채 pid 가 orcad 아닌 프로세스에 재사용됨
            self.assertEqual(orca.ssh_host((self.ssh,))["running"], False)
        self.orcad(99999999)  # pid_max 보다 큰 pid: 꺼진 orcad
        self.assertEqual((orca.ssh_host((self.ssh,))["running"]), False)
        write(self.ssh, "orca-runtime.json", "{broken")
        self.assertEqual(orca.ssh_host((self.ssh,))["pid"], None)

    def test_status_carries_ssh_host_and_environments_even_without_the_official_server(self):
        """covers: ORCA-14, ORCA-15"""
        self.orcad(os.getpid())
        desk = os.path.join(self.root, "desk")
        write(desk, "orca-environments.json", {"version": 1, "environments": [
            {"id": "a", "name": "pi5-s2", "connectionDependency": "ssh-tunnel", "endpoints": [endpoint("ENV-TOKEN")]}]})
        s = orca.status("100.64.0.2", dirs=(os.path.join(self.root, "none"),), ssh_dirs=(self.ssh,), env_dirs=(desk,))
        self.assertEqual((s["installed"], s["codes"]), (False, {"desktop": None, "mobile": None}))
        self.assertEqual(s["ssh_host"]["clients"], ["kgolf"])
        self.assertEqual(s["environments"], [{"name": "pi5-s2", "via": "ssh"}])
        for secret in SECRETS:
            self.assertNotIn(secret, json.dumps(s))
        s = orca.status("100.64.0.2", dirs=(self.ssh,), ssh_dirs=(os.path.join(self.root, "none"),), env_dirs=())
        self.assertEqual((s["installed"], s["ssh_host"], s["environments"]), (True, None, []))


class EnvironmentsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.desk = os.path.join(tmp.name, "orca-desktop")
        self.server = os.path.join(tmp.name, "orca")

    def test_via_follows_the_sidecar_and_tokens_are_never_read_out(self):
        """covers: ORCA-15"""
        write(self.desk, "orca-environments.json", {"version": 1, "environments": [
            {"id": "a", "name": "pi5-s2", "runtimeId": "r", "pairedDeviceId": "d", "endpoints": [endpoint("ENV-TOKEN")]},
            {"id": "b", "name": "s3", "endpoints": [endpoint("ENV-TOKEN")]},
            {"id": "c", "name": "mac", "endpoints": [endpoint("ENV-TOKEN")]},
            {"id": "d", "name": "dep", "connectionDependency": "ssh-tunnel", "endpoints": []},
            "junk"]})
        write(self.desk, "orca-environment-sidecar.json", {"version": 1, "entries": {
            "a": {"binding": {}, "orcadDeployment": {"sshTargetId": "t", "localPort": 51000, "remotePort": 6768}},
            "b": {"binding": {}, "sshAccess": {"sshTargetId": "t", "localPort": 51001, "remotePort": 6768,
                                               "endpoint": {**endpoint("TUNNEL-TOKEN"), "label": "SSH tunnel"}}},
            "c": {"binding": {}}}})
        write(self.server, "orca-environments.json", {"version": 1, "environments": [{"id": "z", "name": "server-side"}]})
        envs = orca.environments((self.desk, self.server))
        self.assertEqual(envs, [{"name": "pi5-s2", "via": "ssh"}, {"name": "s3", "via": "ssh-tunnel"},
                                {"name": "mac", "via": "pairing"}, {"name": "dep", "via": "ssh"}])
        for secret in SECRETS:
            self.assertNotIn(secret, json.dumps(envs))
        # 데스크톱 프로필이 없으면 서버 프로필의 환경을 본다
        self.assertEqual(orca.environments((os.path.join(self.desk, "missing"), self.server)),
                         [{"name": "server-side", "via": "pairing"}])

    def test_malformed_files_give_an_empty_list(self):
        """covers: ORCA-15"""
        self.assertEqual(orca.environments((self.desk,)), [])
        for body in ("{broken", [], {"environments": {"a": 1}}):
            write(self.desk, "orca-environments.json", body)
            self.assertEqual(orca.environments((self.desk,)), [])
        write(self.desk, "orca-environments.json", {"environments": [{"id": "a", "name": "x"}]})
        write(self.desk, "orca-environment-sidecar.json", ["not", "a", "dict"])
        self.assertEqual(orca.environments((self.desk,)), [{"name": "x", "via": "pairing"}])


if __name__ == "__main__":
    unittest.main()
