import unittest
from unittest import mock

from jarvis import tunnels


class ParseTest(unittest.TestCase):
    """covers: MAP-5"""

    def test_reads_loopback_forwards_and_the_ssh_destination_only(self):
        argv = ["/usr/bin/ssh", "-NT", "-i", "/home/pi/.ssh/secret-key", "-o", "IdentitiesOnly=yes",
                "-L", "127.0.0.1:28899:127.0.0.1:8899", "-L29119:localhost:9119", "thsvkd@100.107.29.19"]
        out = tunnels.parse(argv)
        self.assertEqual(out, [{"port": 28899, "host": "100.107.29.19", "to_port": 8899},
                               {"port": 29119, "host": "100.107.29.19", "to_port": 9119}])
        self.assertNotIn("secret-key", repr(out))  # 키 경로·옵션은 넣지 않는다

    def test_skips_forwards_to_other_hosts_or_bound_outside_loopback(self):
        argv = ["ssh", "-L", "8080:db.internal:5432", "-L", "0.0.0.0:9000:127.0.0.1:9000", "-p", "2222", "box"]
        self.assertEqual(tunnels.parse(argv), [])

    def test_ignores_non_ssh_and_commands_without_destination(self):
        self.assertEqual(tunnels.parse(["python3", "-L", "1:127.0.0.1:2", "h"]), [])
        self.assertEqual(tunnels.parse(["ssh", "-L", "1:127.0.0.1:2"]), [])

    def test_option_values_are_not_taken_as_the_destination(self):
        argv = ["ssh", "-J", "jump", "-o", "ServerAliveInterval=15", "-L", "1:127.0.0.1:2", "mac"]
        self.assertEqual(tunnels.parse(argv), [{"port": 1, "host": "mac", "to_port": 2}])


class CollectTest(unittest.TestCase):
    """covers: MAP-5"""

    def test_a_process_whose_cmdline_raises_systemerror_is_skipped(self):
        """macOS 에서 명령줄을 읽는 도중 끝난 프로세스는 psutil 이 SystemError 를 던진다(2026-11-14 kgolf-macmini-m2pro). 그 하나만 건너뛴다"""
        def proc(argv):
            p = mock.Mock(info={"name": "ssh"})
            p.cmdline.side_effect = argv if isinstance(argv, Exception) else None
            p.cmdline.return_value = argv
            return p
        dying = proc(SystemError("<built-in function proc_cmdline> returned a result with an exception set"))
        live = proc(["ssh", "-L", "127.0.0.1:1:127.0.0.1:2", "mac"])
        with mock.patch.object(tunnels.psutil, "process_iter", return_value=[dying, live]):
            self.assertEqual(tunnels.collect(), [{"port": 1, "host": "mac", "to_port": 2}])


if __name__ == "__main__":
    unittest.main()
