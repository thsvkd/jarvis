"""macOS: route·lsof·sysctl 로 채운다. 실동작은 Mac Studio 에서 한 번 확인해야 한다(README 참고).
파서는 tests/ 의 명령 출력 fixture 로 검증한다."""

import platform as _pf
import re

import psutil

from ..util import run
from .common import *  # noqa: F401,F403
from .common import psutil_listening_sockets

NAME = "darwin"
DISK_RE = re.compile(r"^disk\d+$")


def host_model():
    rc, out, _ = run(["sysctl", "-n", "hw.model"])
    return out.strip() or None if rc == 0 else None


def os_name():
    return f"macOS {_pf.mac_ver()[0]}".strip()


def disk_io_ok(name):
    return bool(DISK_RE.match(name))


def disk_mount_ok(mount, fstype, opts=""):
    # APFS 는 시스템 볼륨(/·Preboot·VM·Update…)과 Data 가 한 컨테이너를 나눠 쓴다. 둘 다 넣으면 용량 합계가 두 배가
    # 되므로 사용자 데이터가 있는 Data 만 남긴다(외장 /Volumes/* 는 그대로).
    if mount == "/" or (mount.startswith("/System/Volumes/") and mount != "/System/Volumes/Data"):
        return False
    # 읽기 전용(봉인된 시스템·Xcode 시뮬레이터 런타임·.dmg·ISO)은 늘 가득 차 보이지만 채울 수 없는 볼륨이다.
    # Recovery 도 같은 컨테이너의 시스템 볼륨이다. macOS 는 NTFS 외장도 ro 로 마운트해 그 디스크도 빠진다.
    if "ro" in opts.split(",") or mount == "/Volumes/Recovery" or mount.startswith("/Library/Developer/CoreSimulator/"):
        return False
    return fstype in REAL_FS or (fstype == "tmpfs" and mount == "/tmp")


def parse_route_get(out):
    """`route -n get default` → [{gateway, dev, metric}]"""
    kv = {}
    for line in (out or "").splitlines():
        k, _, v = line.strip().partition(":")
        kv[k.strip()] = v.strip()
    if not kv.get("gateway") and not kv.get("interface"):
        return []
    return [{"gateway": kv.get("gateway") or None, "dev": kv.get("interface") or None, "metric": None}]


def default_routes():
    rc, out, _ = run(["route", "-n", "get", "default"])
    return parse_route_get(out) if rc == 0 else []


def parse_lsof(out):
    """`lsof -nP -iTCP -sTCP:LISTEN -iUDP -F pcPn` → [{proto, addr, port, process, pid}]"""
    rows, pid, cmd, proto = [], None, None, None
    for line in (out or "").splitlines():
        if not line:
            continue
        tag, val = line[0], line[1:]
        if tag == "p":
            pid, cmd, proto = int(val), None, None
        elif tag == "c":
            cmd = val
        elif tag == "P":
            proto = val.lower()
        elif tag == "n":
            if "->" in val or proto not in ("tcp", "udp"):
                continue  # 연결된 소켓은 리스너가 아니다
            addr, _, port = val.rpartition(":")
            if not port.isdigit():
                continue
            addr = addr.strip("[]").split("%")[0]
            rows.append({"proto": proto, "addr": "0.0.0.0" if addr == "*" else addr, "port": int(port),
                         "process": cmd, "pid": pid})
    seen, uniq = set(), []
    for r in rows:  # lsof 는 fd 마다 한 줄이라 같은 소켓이 여러 번 나온다
        k = (r["proto"], r["addr"], r["port"], r["pid"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def listening_sockets():
    """psutil.net_connections 는 macOS 에서 root 가 아니면 AccessDenied 다. 그러면 lsof 로(자기 프로세스만 보인다)."""
    try:
        return psutil_listening_sockets()
    except psutil.AccessDenied:
        pass
    rc, out, _ = run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-iUDP", "-F", "pcPn"], timeout=10)
    return parse_lsof(out), False
