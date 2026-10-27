"""POSIX 공통 구현(psutil·Docker API). linux.py·darwin.py 가 이것을 기본으로 가져와 필요한 것만 덮어쓴다."""

import ipaddress
import platform as _pf
import socket

import psutil

from ..util import docker_api

NAME = "posix"
REAL_FS = {"ext4", "ext3", "ext2", "xfs", "btrfs", "vfat", "exfat", "f2fs", "ntfs", "ntfs3", "nfs", "nfs4", "cifs", "zfs",
           "apfs", "hfs"}


# ---------------------------------------------------------------- 기기 정보
def host_model():
    return None


def os_name():
    return f"{_pf.system()} {_pf.release()}"


def cpu_governor():
    return None


def cpu_freqs():
    """코어별 현재 클럭(MHz)."""
    try:
        return [round(f.current) for f in psutil.cpu_freq(percpu=True) or [] if f.current]
    except (NotImplementedError, OSError):
        return []


def run_queue():
    return None, None


def disk_io_ok(name):
    return True


def disk_mount_ok(mount, fstype, opts=""):
    # /tmp 가 RAM(tmpfs)이면 차는 만큼 메모리를 먹는다 — 디스크와 같이 보여 주고 경보한다.
    # 읽기 전용 마운트는 빼지 않는다: Linux 루트가 디스크 오류로 ro 로 바뀌면 그것이 장애다.
    # Windows: CD/DVD(cdrom)·매체 없는 드라이브(fstype 빈 값)는 뺀다. FAT32 는 일부러 REAL_FS 에 없다(vfat 만): Google Drive
    # 같은 가상 드라이브가 FAT32·C: 와 같은 크기로 보이기 때문이고, 그 대가로 진짜 FAT32 USB·SD 도 빠진다.
    if "cdrom" in opts.split(","):
        return False
    return fstype.lower() in REAL_FS or (fstype == "tmpfs" and mount == "/tmp")  # Windows 는 "NTFS" 처럼 대문자


SENSOR_PREF = ("cpu_thermal", "coretemp", "k10temp", "zenpower", "cpu-thermal", "soc_thermal", "acpitz")


def sensors_cpu_temp_c():
    """psutil 온도 센서 중 CPU 로 보이는 것(Linux·FreeBSD). 없으면 None."""
    try:
        temps = psutil.sensors_temperatures()
    except (AttributeError, OSError):
        return None
    for k in SENSOR_PREF + tuple(temps):
        if temps.get(k):
            return round(temps[k][0].current, 1)
    return None


def cpu_temp_c():
    return sensors_cpu_temp_c()


# ---------------------------------------------------------------- 네트워크
def default_routes():
    return []


def wifi_signal():
    return {}


def iface_addrs():
    """인터페이스별 주소·링크 상태. `ip -j addr` 가 주던 모양 그대로."""
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    out = {}
    for name in sorted(set(stats) | set(addrs)):
        st = stats.get(name)
        v4, cidr, v6, mac = [], [], [], None
        for a in addrs.get(name, []):
            if a.family == socket.AF_INET:
                v4.append(a.address)
                try:
                    plen = ipaddress.ip_network(f"0.0.0.0/{a.netmask}").prefixlen if a.netmask else 32
                except ValueError:
                    plen = 32
                cidr.append(f"{a.address}/{plen}")
            elif a.family == socket.AF_INET6:
                ip = a.address.split("%")[0]
                try:
                    o = ipaddress.ip_address(ip)
                except ValueError:
                    continue
                if not (o.is_link_local or o.is_loopback):  # ip 의 scope global 과 같게
                    v6.append(ip)
            elif a.family == psutil.AF_LINK:
                mac = a.address
        flags = set((getattr(st, "flags", "") or "").split(",")) if st else set()
        out[name] = {
            "ipv4": v4, "ipv4_cidr": cidr, "ipv6": v6,
            # UP(관리상 켜짐) + RUNNING(링크 연결)이 `ip` 의 UP·LOWER_UP 에 해당한다
            "up": bool(st and st.isup and ("running" in flags or not flags)),
            "mac": mac, "mtu": st.mtu if st else None,
            "speed_mbps": st.speed if st and st.speed and st.speed > 0 else None,
        }
    return out


def psutil_listening_sockets():
    """psutil 로 본 리스닝 소켓. 권한이 없어 남의 PID 가 안 보이면 has_proc=False."""
    rows = []
    for c in psutil.net_connections(kind="inet"):
        if c.type == socket.SOCK_STREAM and c.status != psutil.CONN_LISTEN:
            continue
        if c.type == socket.SOCK_DGRAM and c.raddr:
            continue
        if not c.laddr:
            continue
        name = None
        if c.pid:
            try:
                name = psutil.Process(c.pid).name()
            except psutil.Error:
                pass
        rows.append({"proto": "tcp" if c.type == socket.SOCK_STREAM else "udp",
                     "addr": c.laddr.ip.split("%")[0], "port": c.laddr.port, "process": name, "pid": c.pid})
    return rows, bool(rows) and sum(r["pid"] is not None for r in rows) >= len(rows) * 0.8


def listening_sockets():
    return psutil_listening_sockets()


# ---------------------------------------------------------------- 프로세스·컨테이너
def owner_of_pid(pid):
    """(kind, name). cgroup 이 없는 플랫폼에서는 서비스 관리자 대신 프로세스 단위로 본다."""
    return ("scope", None)


def proc_cwd(pid):
    try:
        return psutil.Process(pid).cwd()
    except (psutil.Error, OSError):
        return None


def docker_container_usage(cid):
    """Docker stats API 로 본 (누적 CPU µs, 메모리 바이트, 프로세스 수). Docker Desktop·OrbStack 은 VM 안이라 이것만 된다."""
    try:
        s = docker_api(f"/containers/{cid}/stats?stream=false&one-shot=true", timeout=4)
    except Exception:
        return None, None, 0
    cpu = ((s.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage")
    mem = s.get("memory_stats") or {}
    st = mem.get("stats") or {}
    used = mem.get("usage")
    if used is not None:  # docker stats CLI 와 같게 페이지 캐시는 뺀다
        used -= st.get("inactive_file", st.get("total_inactive_file", 0)) or 0
    return (cpu // 1000 if cpu is not None else None), used, (s.get("pids_stats") or {}).get("current") or 0


def container_usage(cid, rss_by_pid):
    return docker_container_usage(cid)
