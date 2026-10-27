"""Linux: cgroup·ss·ip(iproute2)·/proc·sysfs 에만 있는 정보."""

import json
import os
import re

from .. import config
from ..util import read, read_int, run
from .common import *  # noqa: F401,F403  (공통 구현을 기본으로 쓰고 아래에서 덮어쓴다)
from .common import cpu_freqs as super_cpu_freqs

NAME = "linux"
DISK_RE = re.compile(r"^(mmcblk\d+|nvme\d+n\d+|sd[a-z]+|vd[a-z]+)$")


# ---------------------------------------------------------------- 기기 정보
def host_model():
    m = (read("/proc/device-tree/model", "") or "").strip("\x00").strip()
    if not m:  # x86 등 device-tree 가 없는 기기
        m = " ".join(filter(None, ((read(f"/sys/class/dmi/id/{k}", "") or "").strip()
                                   for k in ("sys_vendor", "product_name"))))
    return m or None


def os_name():
    osr = {}
    for line in (read("/etc/os-release", "") or "").splitlines():
        k, _, v = line.partition("=")
        osr[k] = v.strip('"')
    return osr.get("PRETTY_NAME")


def cpu_governor():
    return (read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "") or "").strip() or None


def cpu_freqs():
    """psutil 은 cpufreq 정책(Pi 는 4코어가 정책 하나)마다 한 값이라, 코어별 값은 sysfs 에서 읽는다."""
    freqs = []
    for i in range(os.cpu_count() or 0):
        f = read_int(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq")
        if f:
            freqs.append(f // 1000)
    return freqs or super_cpu_freqs()


def run_queue():
    """(실행 대기 프로세스 수, 전체 스레드 수) — /proc/loadavg 4번째 칸."""
    la = (read("/proc/loadavg", "") or "").split()
    rt = la[3].split("/") if len(la) > 3 else []
    return (int(rt[0]), int(rt[1])) if len(rt) == 2 else (None, None)


def disk_io_ok(name):
    return bool(DISK_RE.match(name))


# ---------------------------------------------------------------- 네트워크
def default_routes():
    rc, s, _ = run(["ip", "-j", "route", "show", "default"])
    try:
        return [{"gateway": r.get("gateway"), "dev": r.get("dev"), "metric": r.get("metric")}
                for r in json.loads(s or "[]")]
    except ValueError:
        return []


def wifi_signal():
    wifi = {}
    for line in (read("/proc/net/wireless", "") or "").splitlines()[2:]:
        p = line.split()
        if len(p) >= 4:
            try:
                wifi[p[0].rstrip(":")] = {"quality": float(p[2].rstrip(".")), "signal_dbm": float(p[3].rstrip("."))}
            except ValueError:
                pass
    return wifi


# ---------------------------------------------------------------- 리스닝 소켓 (ss)
_USERS_RE = re.compile(r'\("([^"]+)",pid=(\d+),fd=\d+\)')
_SUDO_OK = None


def _sees_others(out):
    """root 소유 리스너(sshd·docker-proxy 등)의 프로세스가 보이면 권한이 충분한 것이다."""
    lines = [l for l in (out or "").splitlines() if l.strip()]
    return bool(lines) and sum("users:" in l for l in lines) >= len(lines) * 0.8


def parse_ss(out):
    """`ss -Htulnp` 출력 → [{proto, addr, port, process, pid}]"""
    rows = []
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        proto, local = parts[0], parts[4]
        addr, _, port = local.rpartition(":")
        addr = addr.strip("[]").split("%")[0]
        if not port.isdigit():
            continue
        users = _USERS_RE.findall(" ".join(parts[6:]))
        pname, pid = (users[0][0], int(users[0][1])) if users else (None, None)
        rows.append({"proto": proto, "addr": addr, "port": int(port), "process": pname, "pid": pid})
    return rows


def listening_sockets():
    """(행 목록, 남의 프로세스까지 보였는가).
    pm2 로 돌 때는 CAP_SYS_PTRACE·CAP_DAC_READ_SEARCH 를 준 ss 사본으로 sudo 없이 프로세스 이름을 본다.
    수동 실행처럼 권한이 없으면 sudo -n 을 한 번 시도하고, 그것도 안 되면 프로세스 없이 간다.
    ss 가 없으면 psutil 로 대신한다(비root 면 남의 PID 는 비어 있다)."""
    global _SUDO_OK
    rc, out, _ = run([config.SS_BIN, "-Htulnp"])
    if rc != 0 and not out:
        return psutil_listening_sockets()
    has_proc = _sees_others(out)
    if not has_proc and _SUDO_OK is not False:
        rc2, out2, _ = run(["ss", "-Htulnp"], sudo=True)
        _SUDO_OK = rc2 == 0 and _sees_others(out2)  # 한 번 실패하면 다시 시도하지 않는다(저널 sudo 로그)
        if _SUDO_OK:
            out, has_proc = out2, True
    return parse_ss(out), has_proc


# ---------------------------------------------------------------- cgroup (v2)
def cgroup_of(pid):
    s = read(f"/proc/{pid}/cgroup", "")
    for line in s.splitlines():
        if line.startswith("0::"):
            return line[3:]
    return ""


_CTR_RE = re.compile(r"docker-([0-9a-f]{64})\.scope")


def owner_of_cgroup(path):
    """cgroup 경로 → (kind, name). kind: container|unit|user-unit|scope|other."""
    m = _CTR_RE.search(path)
    if m:
        return ("container", m.group(1))
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    if leaf.endswith(".service"):
        return ("user-unit" if "/user@" in path else "unit", leaf)
    if leaf.endswith(".scope"):
        return ("scope", leaf)
    return ("other", leaf)


def owner_of_pid(pid):
    return owner_of_cgroup(cgroup_of(pid))


def cgroup_pids(cg_rel):
    """cgroup(상대 경로) 아래 모든 pid."""
    base = "/sys/fs/cgroup" + cg_rel
    pids = []
    for root, _dirs, files in os.walk(base):
        if "cgroup.procs" in files:
            s = read(os.path.join(root, "cgroup.procs"), "")
            pids.extend(int(x) for x in s.split() if x.isdigit())
    return pids


def pss_bytes(pid, fallback=0):
    """비례 배분 메모리(PSS). 여러 프로세스가 공유 페이지를 나눠 쓰는 컨테이너에서 RSS 합보다 정확하다."""
    s = read(f"/proc/{pid}/smaps_rollup")
    if s:
        for line in s.splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1]) * 1024
    return fallback


def cgroup_mem(cg_rel, rss_by_pid):
    """cgroup 안 프로세스들의 PSS 합(메모리 cgroup 컨트롤러가 꺼진 커널용)."""
    return sum(pss_bytes(p, rss_by_pid.get(p, 0)) for p in cgroup_pids(cg_rel))


def cgroup_cpu_usec(cg_rel):
    s = read("/sys/fs/cgroup" + cg_rel + "/cpu.stat")
    if not s:
        return None
    for line in s.splitlines():
        if line.startswith("usage_usec"):
            return int(line.split()[1])
    return None


def container_usage(cid, rss_by_pid):
    """(누적 CPU µs, 메모리 바이트, 프로세스 수). systemd cgroup 드라이버 경로를 먼저 보고,
    없으면(cgroupfs 드라이버 등) Docker stats API 로 대신한다."""
    cg = f"/system.slice/docker-{cid}.scope"
    usage = cgroup_cpu_usec(cg)
    if usage is None:
        return docker_container_usage(cid)
    return usage, cgroup_mem(cg, rss_by_pid), len(cgroup_pids(cg))


def cpu_temp_c():
    t = read_int("/sys/class/thermal/thermal_zone0/temp")
    return round(t / 1000, 1) if t is not None else sensors_cpu_temp_c()
