"""실행 주체: 도커 컨테이너·compose 프로젝트·pm2 앱·리스닝 소켓. 서비스 관리자(systemd·launchd)는 svc/."""

import json
import os
import re
import shutil
import time

import sys

from . import config, platform
from .system import redact
from .util import Rate, docker_api, ip_obj, is_tailnet_ip, parse_iso, read, run

INSPECT_TTL_S = 60
DOCKER_DEADLINE_S = 20


# ---------------------------------------------------------------- docker
class DockerCollector:
    def __init__(self):
        self.rate = Rate()
        self.inspect = {}  # id → (시각, 상태 키, inspect 결과)

    def _inspect(self, c, now, deadline):
        """inspect 는 상태·헬스가 바뀌었거나 TTL 이 지났을 때만 다시 부른다. 전체 데드라인을 넘기면 캐시를 쓴다."""
        cid = c["Id"]
        health = re.search(r"\((healthy|unhealthy|health: starting)\)", c.get("Status") or "")
        key = (c.get("State"), health.group(1) if health else None)
        hit = self.inspect.get(cid)
        if hit and (hit[1] == key and now - hit[0] < INSPECT_TTL_S or time.monotonic() > deadline):
            return hit[2]
        try:
            ins = docker_api(f"/containers/{cid}/json", timeout=4)
            self.inspect[cid] = (now, key, ins)
            return ins
        except Exception:
            return hit[2] if hit else {}

    def collect(self, rss_by_pid):
        now = time.time()
        deadline = time.monotonic() + DOCKER_DEADLINE_S
        lst = docker_api("/containers/json?all=1")
        out = []
        for c in lst:
            cid = c["Id"]
            ins = self._inspect(c, now, deadline)
            st = ins.get("State") or {}
            labels = c.get("Labels") or {}
            hc = ins.get("HostConfig") or {}
            ns = (ins.get("NetworkSettings") or {}).get("Networks") or {}
            cpu_pct, mem, npids = None, None, 0
            if c.get("State") == "running":
                usage, mem, npids = platform.container_usage(cid, rss_by_pid)
                if usage is not None:
                    r = self.rate.per_s(cid, usage, now)
                    cpu_pct = round(r / 1e6 * 100, 1) if r is not None else None
            started = parse_iso(st.get("StartedAt"))
            finished = parse_iso(st.get("FinishedAt"))
            health = (st.get("Health") or {}).get("Status")
            health_log = (st.get("Health") or {}).get("Log") or []
            aliases = set()
            for net in ns.values():
                aliases.update(net.get("Aliases") or [])
                aliases.update(net.get("DNSNames") or [])
            name = (c.get("Names") or ["/?"])[0].lstrip("/")
            out.append({
                "id": cid[:12],
                "full_id": cid,
                "name": name,
                "image": c.get("Image"),
                "image_id": c.get("ImageID") or ins.get("Image"),
                "revision": labels.get("org.opencontainers.image.revision"),
                "state": c.get("State"),  # running|exited|restarting|paused|created|dead
                "status_text": c.get("Status"),
                "health": health,  # healthy|unhealthy|starting|None
                "health_last": redact((health_log[-1].get("Output") or "").strip())[:200] if health_log else None,
                "started_at": started,
                "finished_at": finished,
                "uptime_s": round(now - started) if started and c.get("State") == "running" else None,
                "restart_count": ins.get("RestartCount", 0),
                "restart_policy": (hc.get("RestartPolicy") or {}).get("Name"),
                "exit_code": st.get("ExitCode"),
                "oom_killed": st.get("OOMKilled"),
                "project": labels.get("com.docker.compose.project"),
                "service": labels.get("com.docker.compose.service"),
                "project_dir": labels.get("com.docker.compose.project.working_dir"),
                "network_mode": hc.get("NetworkMode"),
                "networks": sorted(ns.keys()),
                "aliases": sorted(a for a in aliases if not re.fullmatch(r"[0-9a-f]{12}", a)),
                "ips": sorted({n.get("IPAddress") for n in ns.values() if n.get("IPAddress")}),
                "ports": [
                    {"ip": p.get("IP"), "public": p.get("PublicPort"), "private": p.get("PrivatePort"),
                     "proto": p.get("Type")}
                    for p in c.get("Ports") or []
                ],
                "cpu_pct": cpu_pct,
                "mem_bytes": mem,
                "pids": npids,
            })
        self.rate.prune({c["full_id"] for c in out})
        live = {c["full_id"] for c in out}
        for k in list(self.inspect):
            if k not in live:
                del self.inspect[k]
        out.sort(key=lambda c: (c["project"] or "~", c["name"]))
        return out


def pm2_apps():
    """pm2 jlist 에서 필요한 필드만 뽑는다(pm2_env 에는 환경 변수 전체가 들어 있으니 통째로 내보내지 않는다).
    pm2 명령은 데몬이 없으면 새로 띄우므로, 데몬 pid 파일이 있을 때만 부른다."""
    home = os.path.expanduser("~/.pm2")
    if not os.path.exists(os.path.join(home, "pm2.pid")) or not shutil.which(config.PM2_BIN):  # pm2 를 지운 기기의 옛 pid 파일
        return {"available": False, "apps": []}
    rc, out, err = run([config.PM2_BIN, "jlist"], timeout=15)
    if rc != 0:
        raise RuntimeError(f"pm2 jlist 실패: {(err or out).strip()[:160]}")
    start = out.find("[")
    data = json.loads(out[start:]) if start >= 0 else []
    now = time.time()
    apps = []
    for a in data:
        env = a.get("pm2_env") or {}
        mon = a.get("monit") or {}
        up = env.get("pm_uptime")
        apps.append({
            "name": a.get("name"), "pm_id": a.get("pm_id"), "pid": a.get("pid") or None,
            "status": env.get("status"),  # online|stopping|stopped|launching|errored|one-launch-status
            "restarts": env.get("restart_time", 0), "unstable_restarts": env.get("unstable_restarts", 0),
            "uptime_s": round(now - up / 1000) if up and env.get("status") == "online" else None,
            "started_at": up / 1000 if up else None, "cpu_pct": mon.get("cpu"), "mem_bytes": mon.get("memory"),
            "exec": env.get("pm_exec_path"), "cwd": env.get("pm_cwd"), "exec_mode": env.get("exec_mode"),
            "autorestart": env.get("autorestart"), "version": env.get("version"),
        })
    pid_file = os.path.join(home, "pm2.pid")
    daemon_pid = read(pid_file, "").strip()
    return {"available": True, "apps": apps, "daemon_pid": int(daemon_pid) if daemon_pid.isdigit() else None}


def docker_networks():
    out = []
    for n in docker_api("/networks"):
        cfg = ((n.get("IPAM") or {}).get("Config") or [{}])[0] or {}
        out.append({"name": n.get("Name"), "driver": n.get("Driver"), "subnet": cfg.get("Subnet"),
                    "gateway": cfg.get("Gateway"), "internal": bool(n.get("Internal"))})
    return out


def compose_projects(containers):
    projects = {}
    for c in containers:
        if not c["project"]:
            continue
        p = projects.setdefault(c["project"], {"name": c["project"], "dir": c["project_dir"], "containers": [],
                                               "running": 0, "total": 0})
        p["containers"].append(c["name"])
        p["total"] += 1
        p["running"] += c["state"] == "running"
    for p in projects.values():
        p["status"] = "running" if p["running"] == p["total"] else ("partial" if p["running"] else "stopped")
    return sorted(projects.values(), key=lambda p: p["name"])


# ---------------------------------------------------------------- 리스닝 소켓
# 게시된 컨테이너 포트를 호스트에서 잡는 프로세스. Linux 는 docker-proxy, macOS(Docker Desktop·OrbStack)는
# VM 이 포워딩하므로 게시 포트와 번호가 맞으면 프로세스 이름과 상관없이 그 컨테이너로 본다.
PORT_FORWARDER_ANY = sys.platform == "darwin"


def scope_of_bind(addr, local_ifaces):
    """바인딩 주소 → 도달 범위. all|lan|tailnet|docker|loopback|other."""
    if addr in ("0.0.0.0", "::", "*", ""):
        return "all"
    ip = ip_obj(addr)
    if ip is None:
        return "other"
    if ip.is_loopback:
        return "loopback"
    if is_tailnet_ip(addr):
        return "tailnet"
    iface = local_ifaces.get(addr, "")
    if iface.startswith(("docker", "br-")):
        return "docker"
    if iface.startswith(("eth", "en", "wlan", "wl")):
        return "lan"
    if ip.is_private:
        return "lan"
    return "other"


SCOPE_RANK = {"all": 5, "lan": 4, "tailnet": 3, "docker": 2, "loopback": 1, "other": 0}
REACH = {
    "all": ["lan", "tailnet", "docker", "local"],
    "lan": ["lan"],
    "tailnet": ["tailnet"],
    "docker": ["docker"],
    "loopback": ["local"],
    "other": [],
}


def collect_listeners(iface_addrs, containers, units=None):
    rows, has_proc = platform.listening_sockets()
    local_ifaces = {}
    for name, a in (iface_addrs or {}).items():
        for ip in (a.get("ipv4") or []) + (a.get("ipv6") or []):
            local_ifaces[ip] = name
    # docker-proxy 가 잡은 포트 → 컨테이너
    published = {}
    for c in containers or []:
        for p in c["ports"]:
            if p.get("public"):
                published[(p["proto"], p["public"], p.get("ip") or "0.0.0.0")] = c["name"]
                published.setdefault((p["proto"], p["public"], "*"), c["name"])
    ctr_by_full = {c["full_id"]: c["name"] for c in containers or []}
    # cgroup 이 없는 플랫폼(macOS)에서는 서비스 관리자의 주 PID 로 유닛을 찾는다
    unit_by_pid = {u["main_pid"]: u for u in units or [] if u.get("main_pid")}

    groups = {}
    for row in rows:
        proto, addr, port, pname, pid = row["proto"], row["addr"], row["port"], row["process"], row["pid"]
        if proto == "udp" and port >= 32768 and pname not in ("tailscaled",):
            continue  # 임시 UDP 포트(QUIC 클라이언트 등)는 서버가 아니다
        owner_kind, owner = "unknown", pname
        ctr = published.get((proto, port, addr)) or published.get((proto, port, "*"))
        if pname == "docker-proxy" or (PORT_FORWARDER_ANY and ctr):
            owner_kind, owner = ("container", ctr) if ctr else ("unknown", pname)
        elif pid:
            kind, name = platform.owner_of_pid(pid)
            if kind == "scope" and pid in unit_by_pid:
                u = unit_by_pid[pid]
                kind, name = ("user-unit" if u.get("scope") == "user" else "unit"), u["name"]
            if kind == "container":
                owner_kind, owner = "container", ctr_by_full.get(name, name[:12])
            elif kind in ("unit", "user-unit"):
                owner_kind, owner = kind, name
            elif kind == "scope":
                owner_kind, owner = "scope", name
        key = (proto, port, owner_kind, owner, pname)
        g = groups.setdefault(key, {
            "proto": proto, "port": port, "addrs": [], "process": pname, "pid": pid,
            "owner_kind": owner_kind, "owner": owner,
        })
        if addr not in g["addrs"]:
            g["addrs"].append(addr)
    res = []
    for g in groups.values():
        scopes = [scope_of_bind(a, local_ifaces) for a in g["addrs"]]
        best = max(scopes, key=lambda s: SCOPE_RANK[s]) if scopes else "other"
        reach = sorted({r for s in scopes for r in REACH[s]})
        g["scope"] = best
        g["reach"] = reach
        g["cwd"] = None
        if g["pid"] and g["owner_kind"] == "scope":
            g["cwd"] = platform.proc_cwd(g["pid"])
        res.append(g)
    res.sort(key=lambda g: (g["proto"] != "tcp", -SCOPE_RANK[g["scope"]], g["port"]))
    return res, has_proc
