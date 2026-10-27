"""파서·플랫폼 층 단위 테스트. macOS 파서는 명령 출력 fixture(tests/data/darwin)로만 검증한다(실기 미검증).

    python3 -m unittest discover -s tests -v
"""

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "tests", "data")

from jarvis.hw import macmon, rpi  # noqa: E402
from jarvis.platform import darwin, linux  # noqa: E402
from jarvis.svc import launchd  # noqa: E402


def data(*p):
    with open(os.path.join(DATA, *p)) as f:
        return f.read()


class LinuxParsers(unittest.TestCase):
    def test_ss(self):
        rows = linux.parse_ss(data("linux", "ss_tulnp.txt"))
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0], {"proto": "udp", "addr": "0.0.0.0", "port": 5353, "process": "avahi-daemon", "pid": 612})
        self.assertEqual(rows[4]["addr"], "::")
        self.assertEqual(rows[5]["addr"], "fe80::1")  # %eth0 스코프는 뗀다
        self.assertEqual(rows[6], {"proto": "tcp", "addr": "127.0.0.1", "port": 43163, "process": None, "pid": None})

    def test_sees_others(self):
        out = data("linux", "ss_tulnp.txt")
        self.assertTrue(linux._sees_others(out))  # 7줄 중 6줄(≥80%)에 프로세스가 보임
        self.assertFalse(linux._sees_others("\n".join(l.split("users:")[0] for l in out.splitlines())))

    def test_owner_of_cgroup(self):
        cid = "a" * 64
        self.assertEqual(linux.owner_of_cgroup(f"/system.slice/docker-{cid}.scope"), ("container", cid))
        self.assertEqual(linux.owner_of_cgroup("/system.slice/ssh.service"), ("unit", "ssh.service"))
        self.assertEqual(linux.owner_of_cgroup("/user.slice/user-1000.slice/user@1000.service/app.slice/x.service"),
                         ("user-unit", "x.service"))
        self.assertEqual(linux.owner_of_cgroup("/user.slice/user-1000.slice/session-3.scope"), ("scope", "session-3.scope"))

    def test_pmic(self):
        p = rpi.parse_pmic_adc(data("linux", "pmic_read_adc.txt"))
        self.assertEqual([r["name"] for r in p["rails"]], ["VDD_CORE", "3V3_SYS", "3V7_WL_SW"])  # 전력 큰 순
        self.assertAlmostEqual(p["total_w"], 2.75, places=2)
        self.assertEqual(p["input_v"], 5.1657)
        self.assertEqual(rpi.parse_pmic_adc("")["total_w"], None)

    def test_throttled(self):
        self.assertEqual(rpi.parse_throttled("throttled=0x0"), {"raw": "0x0", "ok": True, "now": [], "since_boot": []})
        t = rpi.parse_throttled("throttled=0x50005")
        self.assertEqual(t["now"], ["저전압", "스로틀링"])
        self.assertEqual(t["since_boot"], ["저전압", "스로틀링"])
        self.assertIsNone(rpi.parse_throttled(""))


class DarwinParsers(unittest.TestCase):
    def test_lsof(self):
        rows = darwin.parse_lsof(data("darwin", "lsof.txt"))
        self.assertEqual(rows, [
            {"proto": "tcp", "addr": "0.0.0.0", "port": 7000, "process": "ControlCenter", "pid": 412},
            {"proto": "tcp", "addr": "::", "port": 7000, "process": "ControlCenter", "pid": 412},
            {"proto": "tcp", "addr": "127.0.0.1", "port": 9300, "process": "python3.12", "pid": 733},
            {"proto": "tcp", "addr": "0.0.0.0", "port": 8080, "process": "OrbStack Helper", "pid": 980},
            {"proto": "udp", "addr": "192.168.0.20", "port": 5353, "process": "OrbStack Helper", "pid": 980},
        ])

    def test_route(self):
        self.assertEqual(darwin.parse_route_get(data("darwin", "route_get_default.txt")),
                         [{"gateway": "192.168.0.1", "dev": "en0", "metric": None}])
        self.assertEqual(darwin.parse_route_get("route: writing to routing socket: not in table"), [])

    def test_disk_mount(self):
        self.assertFalse(darwin.disk_mount_ok("/", "apfs"))  # Data 와 같은 컨테이너라 합계가 두 배가 된다
        self.assertTrue(darwin.disk_mount_ok("/System/Volumes/Data", "apfs"))
        self.assertFalse(darwin.disk_mount_ok("/System/Volumes/VM", "apfs"))
        self.assertFalse(darwin.disk_mount_ok("/dev", "devfs"))
        # 늘 가득 차 보이는 읽기 전용 볼륨(시뮬레이터 런타임·.dmg)과 Recovery 는 빼고, 쓰기 가능한 외장은 남긴다
        sim = "/Library/Developer/CoreSimulator/Volumes/iOS_23F77"
        self.assertFalse(darwin.disk_mount_ok(sim, "apfs", "ro,nosuid,local,dovolfs"))
        self.assertFalse(darwin.disk_mount_ok("/Volumes/Installer", "hfs", "ro,local,nodev"))
        self.assertFalse(darwin.disk_mount_ok("/Volumes/Recovery", "apfs", "rw,local"))
        self.assertTrue(darwin.disk_mount_ok("/Volumes/Backup", "apfs", "rw,local,journaled"))
        self.assertTrue(darwin.disk_io_ok("disk3") and not darwin.disk_io_ok("disk3s1"))

    def test_launchctl_print(self):
        rows = launchd.parse_print(data("darwin", "launchctl_print_system.txt"))
        self.assertIn(("com.apple.logd", 105, None), rows)
        self.assertIn(("org.example.broken", None, 1), rows)
        self.assertIn(("com.apple.fsck_hfs", None, 78), rows)
        self.assertEqual(len(rows), 6)  # unmanaged processes 블록은 읽지 않는다

    def test_launchctl_list(self):
        rows = launchd.parse_list(data("darwin", "launchctl_list.txt"))
        self.assertEqual(rows[1], ("com.apple.Finder", 678, 0))
        self.assertEqual(rows[2], ("homebrew.mxcl.broken", None, 78))

    def test_launchd_collect(self):
        """Apple 기본 작업은 빼고, 떠 있거나 실패한 작업·감시 대상만 남긴다."""
        c = launchd.UnitCollector()
        c._jobs = lambda: iter([("system", "com.apple.logd", None, None), ("system", "com.openssh.sshd", None, None),
                                ("user", "homebrew.mxcl.broken", None, 78), ("user", "homebrew.mxcl.idle", None, 0),
                                ("user", "pm2.me", os.getpid(), 0)])
        old = launchd.config.WATCH_UNITS
        launchd.config.WATCH_UNITS = ("com.openssh.sshd",)
        try:
            units = {u["name"]: u for u in c.collect({})}
        finally:
            launchd.config.WATCH_UNITS = old
        self.assertEqual(sorted(units), ["com.openssh.sshd", "homebrew.mxcl.broken", "pm2.me"])
        self.assertEqual(units["com.openssh.sshd"]["active"], "active")  # on-demand 대기
        self.assertEqual(units["com.openssh.sshd"]["sub"], "waiting")
        self.assertEqual(units["homebrew.mxcl.broken"]["active"], "failed")
        self.assertEqual(units["pm2.me"]["active"], "active")
        self.assertGreater(units["pm2.me"]["mem_bytes"], 0)

    def test_macmon(self):
        m = macmon.parse(data("darwin", "macmon_pipe.json"))
        self.assertEqual(m, {"cpu_temp_c": 43.7, "gpu_temp_c": 38.2, "gpu_mhz": 444, "gpu_pct": 12.3, "power_w": 12.35})
        self.assertIsNone(macmon.parse("not json"))


class Common(unittest.TestCase):
    def test_iface_addrs_shape(self):
        from jarvis import platform
        a = platform.iface_addrs()
        lo = a.get("lo") or a.get("lo0")
        self.assertIn("127.0.0.1", lo["ipv4"])
        self.assertEqual(set(lo), {"ipv4", "ipv4_cidr", "ipv6", "up", "mac", "mtu", "speed_mbps"})
        self.assertIn("127.0.0.1/8", lo["ipv4_cidr"])

    def test_listening_sockets_shape(self):
        from jarvis import platform
        rows, has_proc = platform.listening_sockets()
        for r in rows:
            self.assertEqual(set(r), {"proto", "addr", "port", "process", "pid"})
            self.assertIn(r["proto"], ("tcp", "udp"))

    def test_disk_mount_tmpfs(self):
        """ebf8aff: /tmp 가 tmpfs 면 디스크 목록에 넣고, 다른 tmpfs 는 뺀다."""
        from jarvis.platform import common
        self.assertTrue(common.disk_mount_ok("/tmp", "tmpfs"))
        self.assertFalse(common.disk_mount_ok("/run", "tmpfs"))
        self.assertTrue(common.disk_mount_ok("/", "ext4"))
        self.assertTrue(common.disk_mount_ok("/", "ext4", "ro,relatime"))  # Linux 루트가 ro 로 바뀌면 장애라 계속 보인다
        self.assertTrue(common.disk_mount_ok("C:\\", "NTFS", "rw,fixed"))
        self.assertFalse(common.disk_mount_ok("E:\\", "", "fixed"))  # 매체 없는 카드 리더
        self.assertFalse(common.disk_mount_ok("F:\\", "exFAT", "ro,cdrom"))  # 광학 드라이브는 파일 시스템과 무관하게 뺀다
        self.assertFalse(common.disk_mount_ok("G:\\", "FAT32", "rw,fixed"))  # FAT32 는 REAL_FS 에 없다(가상 드라이브도 이렇게 빠진다)


class Config(unittest.TestCase):
    def _cfg(self, toml, **env):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(toml)
        e = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_")}
        e.update(JARVIS_CONFIG=f.name, **env)
        code = "from jarvis import config as c; print(c.PORT, c.CADDY_CONTAINER, c.EXTRA_HOSTS, c.INFRA_UNITS['pm2'], sep='|', end='')"
        try:
            return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e, capture_output=True, text=True,
                                  check=True).stdout.split("|")
        finally:
            os.unlink(f.name)

    def test_priority(self):
        toml = 'port = 9311\ncaddy = "file-caddy"\nhosts = ["a.example"]\n[infra_units]\npm2 = "pm2-x.service"\n'
        self.assertEqual(self._cfg(toml), ["9311", "file-caddy", "['a.example']", "pm2-x.service"])
        # 환경 변수가 파일보다 앞서고, 옛 이름(PIT5MON_*)은 읽지 않는다
        self.assertEqual(self._cfg(toml, JARVIS_PORT="9313")[0], "9313")
        self.assertEqual(self._cfg(toml, PIT5MON_PORT="9312")[0], "9311")
        self.assertEqual(self._cfg(toml, JARVIS_HOSTS="b.example,c.example")[2], "['b.example', 'c.example']")


class SnapCheck(unittest.TestCase):
    def test_compare(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import snapcheck
        base = snapcheck.schema({"a": 1, "l": [{"x": 1, "y": "s"}], "d": {"k": None}})
        self.assertEqual(snapcheck.compare(base, snapcheck.schema({"a": 2, "l": [{"x": 2, "y": "t"}], "d": {"k": 3}}))[0], [])
        self.assertEqual(snapcheck.compare(base, snapcheck.schema({"a": 2, "l": [], "d": {"k": 1}}))[0], [])  # 빈 목록은 비교 불가
        errs = snapcheck.compare(base, snapcheck.schema({"a": "x", "l": [{"x": 1}], "d": {}}))[0]
        self.assertEqual(errs, ["종류 변경 .a: ['num'] → ['str']", "누락 .d.k", "누락 .l[].y"])


class Redact(unittest.TestCase):
    def test_masks_secrets_keeps_diagnostics(self):
        from jarvis.system import redact
        for cmd in ("app --key X", "app --pin 1234", "app --session=abc", "curl -H X-Jarvis-Proxy-Key:abcd1234",
                    "sh -c export OPENAI_KEY=sk123", "app " + "ab" * 32, 'app --password "two words"'):
            out = redact(cmd)
            self.assertIn("***", out, cmd)
            self.assertNotRegex(out, r"X$|1234|abc$|abcd1234|sk123|abab|two|words", cmd)
        for cmd in ("git checkout " + "a" * 40, "docker run -p 80:80 nginx", "python3 -m jarvis"):
            self.assertEqual(redact(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
