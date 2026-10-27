"""서버의 LAN 이웃 테이블. 관측 이력과 현재 온라인 증거를 구분한다."""
import ipaddress
import json
import re
import sys
import time

from .util import run

LAN_NETS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def is_lan_ip(s):
    try:
        ip = ipaddress.ip_address(s)
        return ip.version == 4 and any(ip in n for n in LAN_NETS)
    except ValueError:
        return False


def parse_neighbors(rows, ifaces):
    networks = {}
    for name, a in ifaces.items():
        if name.startswith(("eth", "en", "wlan", "wl")):
            networks[name] = [ipaddress.ip_interface(c).network for c in a.get("ipv4_cidr") or []]
    out = {}
    for r in rows:
        ip, iface, mac = r.get("dst"), r.get("dev"), r.get("lladdr")
        if not ip or not is_lan_ip(ip) or not mac or not re.fullmatch(r"(?:[0-9a-fA-F]{1,2}:){5}[0-9a-fA-F]{1,2}", mac):
            continue
        if not any(ipaddress.ip_address(ip) in n for n in networks.get(iface, [])):
            continue
        states = r.get("state") or []
        if isinstance(states, str):
            states = [states]
        if "FAILED" in states or "INCOMPLETE" in states:
            continue
        out[ip] = {"ip": ip, "mac": mac.lower(), "interface": iface, "state": ",".join(states),
                   "online": True if "REACHABLE" in states else None, "observed_at": time.time(),
                   "source": "neighbor-cache"}
    return sorted(out.values(), key=lambda n: n["ip"])


def neighbors(ifaces):
    if sys.platform.startswith("linux"):
        rc, out, _ = run(["ip", "-j", "neigh", "show"], timeout=4)
        if rc:
            raise RuntimeError("LAN neighbor table unavailable")
        rows = json.loads(out)
    elif sys.platform == "darwin":
        rc, out, _ = run(["arp", "-an"], timeout=4)
        if rc:
            raise RuntimeError("LAN neighbor table unavailable")
        rows = []
        for line in out.splitlines():
            m = re.search(r"\(([^)]+)\) at ([\da-fA-F:]+) on (\S+)", line)
            if m:
                rows.append({"dst": m[1], "lladdr": m[2], "dev": m[3], "state": ["STALE"]})
    else:
        rows = []
    return parse_neighbors(rows, ifaces)
