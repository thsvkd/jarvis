"""원시 수집 결과 → 화면이 쓰는 스냅샷(서비스·카테고리·경보·토폴로지)."""

import os
import re
import time

import psutil

from . import __version__, config
from .discovery import is_lan_ip
from .util import self_upstream

CATEGORIES = [
    {"id": "public", "label": "공개 서비스", "desc": "인터넷 → Cloudflare Tunnel → Caddy :80"},
    {"id": "tailnet", "label": "Tailnet 전용", "desc": "tailnet → *.<도메인> → Caddy :443 (터널 경로 차단)"},
    {"id": "hermes", "label": "Hermes", "desc": "Hermes 에이전트 게이트웨이(상태 API)와 이 기기 안의 Hermes 컨테이너·프로세스"},
    {"id": "host", "label": "호스트 서버", "desc": "Caddy 를 거치지 않고 이 기기에서 직접 포트를 연 서버"},
    {"id": "internal", "label": "내부 백엔드", "desc": "DB·캐시 등 외부에 열리지 않은 컨테이너"},
    {"id": "infra", "label": "인프라", "desc": "Caddy·cloudflared·Tailscale·Docker·SSH·모니터"},
    {"id": "system", "label": "시스템 데몬", "desc": "OS 기본 데몬(프린터·mDNS 등)"},
]
CAT_LABEL = {c["id"]: c["label"] for c in CATEGORIES}

SYSTEM_PROCS = {"cupsd", "cups-browsed", "avahi-daemon", "systemd-resolve", "dnsmasq", "chronyd", "rpcbind",
                "systemd-timesyncd", "NetworkManager", "dhclient", "dhcpcd"}
INFRA_UNITS = {"docker.service": "docker", "containerd.service": "docker", "tailscaled.service": "tailscale",
               "ssh.service": "ssh", "sshd.service": "ssh", "pm2-pi.service": "pm2",
               **{u: k for k, u in config.INFRA_UNITS.items() if u}}
NON_HTTP_PROCS = {"sshd", "usbipd", "tailscaled", "containerd", "postgres", "redis-server", "avahi-daemon",
                  "cupsd", "docker-proxy"}
DESKTOP_HINT = re.compile(r"pipewire|wireplumber|xdg-|gvfs|at-spi|gnome-keyring|filter-chain|dbus|lightdm|"
                          r"pulse|getty|polkit|rtkit|accounts-daemon|udisks|bluetooth|ModemManager|triggerhappy|"
                          r"user@|cups|avahi|colord|upower", re.I)
LEVEL_RANK = {"crit": 3, "warn": 2, "info": 1}
# 이 모니터 자신의 pm2 앱·systemd 유닛 이름
MONITOR_RUNTIME_NAMES = ("jarvis", "jarvis.service", "dev.jarvis")  # pm2 앱·systemd 유닛·macOS LaunchAgent
STATUS_KO = {"up": "정상", "degraded": "저하", "down": "다운", "unknown": "확인 불가"}


def _unit_short(name):
    base = name[:-8] if name.endswith(".service") else name
    return base.rsplit(".", 1)[-1] if base.count(".") >= 2 else base


def _rt_container(c):
    return {
        "type": "container", "name": c["name"], "project": c["project"], "service": c["service"],
        "image": c["image"], "state": c["state"], "health": c["health"], "uptime_s": c["uptime_s"],
        "container_id": c.get("full_id"), "image_id": c.get("image_id"), "revision": c.get("revision"),
        "started_at": c["started_at"], "restart_count": c["restart_count"], "cpu_pct": c["cpu_pct"],
        "mem_bytes": c["mem_bytes"], "exit_code": c["exit_code"], "network_mode": c["network_mode"],
    }


def _rt_unit(u):
    return {
        "type": "systemd", "name": u["name"], "scope": u["scope"], "state": u["active"], "sub": u["sub"],
        "uptime_s": u["uptime_s"], "started_at": u["since"], "restart_count": u["restarts"],
        "cpu_pct": u["cpu_pct"], "mem_bytes": u["mem_bytes"], "pid": u["main_pid"], "enabled": u["enabled"],
        "description": u["description"],
    }


def _rt_pm2(app):
    return {
        "type": "pm2", "name": app["name"], "pm_id": app.get("pm_id"), "pid": app.get("pid"),
        "state": app.get("status"), "uptime_s": app.get("uptime_s"), "started_at": app.get("started_at"),
        "restart_count": app.get("restarts", 0), "unstable_restarts": app.get("unstable_restarts", 0),
        "cpu_pct": app.get("cpu_pct"), "mem_bytes": app.get("mem_bytes"), "exec": app.get("exec"),
    }


def _rt_process(l, rss_by_pid):
    return {
        "type": "process", "name": l["process"], "pid": l["pid"], "cwd": l.get("cwd"), "scope_unit": l["owner"],
        "state": "running", "mem_bytes": rss_by_pid.get(l["pid"]) if l.get("pid") else None, "cpu_pct": None,
        "uptime_s": _proc_uptime(l["pid"]) if l.get("pid") else None,
    }


def _proc_uptime(pid):
    try:
        return round(time.time() - psutil.Process(pid).create_time())
    except (psutil.Error, OSError):
        return None


def _lst(l):
    return {"proto": l["proto"], "port": l["port"], "addrs": l["addrs"], "scope": l["scope"], "reach": l["reach"],
            "process": l["process"]}


def _exposure(listeners, route=None):
    reach = {r for l in listeners for r in l["reach"]}
    via = []
    public = bool(route and route.get("public"))
    tailnet = bool(route) or "tailnet" in reach
    if public:
        via.append("cloudflare-tunnel")
    if route:
        via.append("caddy-https")
    if "lan" in reach:
        via.append("direct-lan")
    if "tailnet" in reach:
        via.append("direct-tailnet")
    return {
        "public": public, "tailnet": tailnet, "lan": "lan" in reach,
        "local_only": not public and not tailnet and "lan" not in reach, "via": via,
    }


def _status_from_runtime(rt):
    if not rt:
        return "unknown", "실행 주체를 찾지 못함"
    if rt["type"] == "container":
        if rt["state"] == "running":
            if rt["health"] == "unhealthy":
                return "degraded", "헬스체크 실패"
            if rt["health"] == "starting":
                return "degraded", "헬스체크 시작 중"
            if rt["restart_count"] and rt["uptime_s"] is not None and rt["uptime_s"] < 3600:
                return "degraded", f"최근 재시작 (누적 {rt['restart_count']}회, {rt['uptime_s'] // 60}분 전)"
            return "up", None
        if rt["state"] == "restarting":
            return "down", "재시작 반복 중"
        return "down", f"컨테이너 {rt['state']} (exit {rt.get('exit_code')})"
    if rt["type"] == "pm2":
        if rt["state"] == "online":
            if rt.get("unstable_restarts"):
                return "degraded", f"pm2 불안정 재시작 {rt['unstable_restarts']}회"
            return "up", None
        if rt["state"] in ("launching", "stopping"):
            return "degraded", f"pm2 {rt['state']}"
        return "down", f"pm2 {rt['state']}"
    if rt["type"] == "systemd":
        if rt["state"] == "active":
            return "up", None
        if rt["state"] == "failed":
            return "down", "유닛 실패"
        return "degraded", f"유닛 {rt['state']}"
    if rt["type"] == "remote":
        return "unknown", "원격 앱 응답 확인 대기"
    return "up", None


def _worse(a, b):
    order = {"down": 3, "degraded": 2, "unknown": 1, "up": 0}
    return a if order.get(a[0], 0) > order.get(b[0], 0) else b


def build(raw):
    now = time.time()
    st = raw.get("static") or {}
    fast = raw.get("fast") or {}
    med = raw.get("medium") or {}
    procs = raw.get("procs") or {}
    containers = raw.get("containers") or []
    units = raw.get("units") or []
    listeners = raw.get("listeners") or []
    caddy = raw.get("caddy") or {}
    routes = caddy.get("routes") or {}
    tunnel = raw.get("tunnel") or {}
    traffic = raw.get("traffic") or {}
    probes = raw.get("probes") or {}
    dns = raw.get("dns") or {}
    ts = raw.get("tailscale") or {}
    http_ports = raw.get("http_ports") or {}
    pm2 = raw.get("pm2") or {}
    pm2_by_pid = {a["pid"]: a for a in pm2.get("apps") or [] if a.get("pid")}
    rss = raw.get("rss_by_pid") or {}
    domain = caddy.get("domain") or config.DOMAIN or None

    ctr_by_name = {c["name"]: c for c in containers}
    ctr_alias, dup = {}, set()
    caddy_nets = set((ctr_by_name.get(config.CADDY_CONTAINER) or {}).get("networks") or [])
    for c in containers:
        if caddy_nets and not caddy_nets & set(c["networks"]):
            continue
        for a in c["aliases"]:
            if a in ctr_alias and ctr_alias[a] is not c:
                dup.add(a)
            ctr_alias.setdefault(a, c)
    for a in dup:
        ctr_alias.pop(a, None)
    errors = raw.get("errors") or {}
    collected = raw.get("collected_at") or {}
    docker_age = now - collected["docker"] if collected.get("docker") else None
    docker_stale = bool(errors.get("docker")) or docker_age is None or docker_age > 3 * config.MEDIUM_S

    def rt_status(rt):
        """컨테이너 상태가 오래됐으면(도커 무응답) '정상'이라고 우기지 않는다."""
        if rt and rt["type"] == "container" and docker_stale:
            return ("unknown", "도커 응답 없음 — 컨테이너 상태 수집 지연" +
                    (f" ({int(docker_age)}초 전 값)" if docker_age else ""))
        return _status_from_runtime(rt)
    unit_by_name = {u["name"]: u for u in units}
    lan_ips = [ip for n, a in (med.get("iface_addrs") or {}).items() if n.startswith(("eth", "wlan", "en", "wl"))
               for ip in a.get("ipv4") or []]
    ts_self = (ts.get("self") or {})
    ts_ip = next((ip for ip in ts_self.get("ips") or [] if ":" not in ip), None)
    ts_short = ts_self.get("hostname") or st.get("hostname")

    claimed = set()  # (proto, port, owner_kind, owner) 이미 서비스에 붙은 리스너

    def lkey(l):
        return (l["proto"], l["port"], l["owner_kind"], l["owner"])

    def listeners_of(kind, name):
        res = []
        for l in listeners:
            if (kind == "container" and l["owner_kind"] == "container" and l["owner"] == name) or \
               (kind == "systemd" and l["owner_kind"] in ("unit", "user-unit") and l["owner"] == name
                and l.get("pid") not in pm2_by_pid) or \
               (kind == "process" and l["owner_kind"] == "scope" and l["owner"] == name):
                res.append(l)
        return res

    def listeners_of_rt(rt):
        if not rt:
            return []
        if rt["type"] in ("process", "pm2"):
            return [l for l in listeners if rt.get("pid") and l.get("pid") == rt["pid"]]
        return listeners_of(rt["type"], rt["name"])

    def resolve(dial):
        host, _, port = dial.rpartition(":")
        host = host.strip("[]")
        port = int(port) if port.isdigit() else None
        if host in ("host.docker.internal", "172.17.0.1", "localhost", "127.0.0.1") and port:
            # 루프백 리스너는 localhost 로 다이얼할 때만 닿는다(host 네트워크 Caddy → ssh -L 터널 등)
            scopes = ("docker", "all", "lan", "loopback") if host in ("localhost", "127.0.0.1") else ("docker", "all", "lan")
            for l in listeners:
                if l["proto"] == "tcp" and l["port"] == port and l["scope"] in scopes:
                    if l.get("pid") in pm2_by_pid:
                        return _rt_pm2(pm2_by_pid[l["pid"]])
                    if l["owner_kind"] == "container" and l["owner"] in ctr_by_name:
                        return _rt_container(ctr_by_name[l["owner"]])
                    if l["owner_kind"] in ("unit", "user-unit") and l["owner"] in unit_by_name:
                        return _rt_unit(unit_by_name[l["owner"]])
                    if l["owner_kind"] == "scope":
                        return _rt_process(l, rss)
            return None
        c = ctr_by_name.get(host) or ctr_alias.get(host)
        if not c:
            c = next((c for c in containers if host in c.get("ips", [])), None)
        if c:
            return _rt_container(c)
        matches = []
        for p in ts.get("peers") or []:
            names = [p.get("hostname"), p.get("dns_short"), p.get("dns_name")]
            lan_addresses = [p.get("lan_ip_last"), (p.get("cur_addr") or "").rsplit(":", 1)[0].strip("[]")]
            if host in (p.get("ips") or [p.get("ip")]) or host.lower().rstrip(".") in [
                    (n or "").lower().rstrip(".") for n in names] or (is_lan_ip(host) and host in lan_addresses):
                matches.append(p)
        if len(matches) == 1:
            p = matches[0]
            return {"type": "remote", "name": p["hostname"], "peer_id": p.get("id"),
                    "ip": host, "port": port, "online": p.get("online"), "state": "unknown"}
        return None

    services = []
    linked_ctrs, linked_units, linked_pids = set(), set(), set()

    def link(rt):
        if not rt:
            return
        if rt["type"] == "container":
            linked_ctrs.add(rt["name"])
        elif rt["type"] == "systemd":
            linked_units.add(rt["name"])
        elif rt.get("pid"):
            linked_pids.add(rt["pid"])

    # ------------------------------------------------ 1) Caddy 라우트 → 웹 서비스
    tunnel_hosts = set(tunnel.get("hostnames") or [])
    cf_hosts = ((caddy.get("caddyfile") or {}).get("hosts")) or {}
    traffic_hosts = {h["host"]: h for h in ((traffic.get("last_24h") or {}).get("hosts") or [])}
    traffic_hosts_1h = {h["host"]: h for h in ((traffic.get("last_1h") or {}).get("hosts") or [])}
    for host, r in sorted(routes.items()):
        public = r["http"]
        rts = [resolve(u) for u in r["upstreams"]]
        for x in rts:
            link(x)  # 경로별로 업스트림이 여럿이어도 전부 이 라우트 소속으로 잇는다
        rt = next((x for x in rts if x), None)
        lst = listeners_of_rt(rt)
        pr = probes.get(host) or {}
        static = not r["upstreams"] and r.get("static")
        status = ("up", None) if static else rt_status(rt)
        runtime_status = status
        i = pr.get("internal")
        if rt and rt["type"] == "remote" and i and i.get("ok") and (i.get("code") or 0) < 500 \
                and not i.get("tls_error") and not i.get("caddy_fallback") \
                and now - (i.get("t") or 0) <= 3 * config.SLOW_S:
            status = ("up", None)
        if i:
            streak = i.get("fail_streak") or 0
            again = "" if streak >= 2 else " — 1회, 다음 주기에 재확인"
            if i.get("tls_error"):
                status = _worse(("degraded", f"TLS 인증서 검증 실패: {i['tls_error']}"), status)
            if i.get("caddy_fallback"):
                status = _worse(("down" if streak >= 2 else "degraded", "Caddy 기본 응답 — 앱에 도달하지 못함"), status)
            if not i.get("ok"):
                status = _worse(("down" if streak >= 2 else "degraded",
                                 f"Caddy 경유 응답 없음 ({i.get('error') or '?'}){again}"), status)
            elif i["code"] in (502, 503, 504):
                status = _worse(("down" if streak >= 2 else "degraded",
                                 f"업스트림 응답 없음 (HTTP {i['code']}){again}"), status)
            elif i["code"] >= 500:
                status = _worse(("degraded", f"서버 오류 (HTTP {i['code']})"), status)
        p = pr.get("public")
        if public and p:
            if not p.get("ok"):
                status = _worse(("degraded", f"공개 경로 실패 ({p.get('error') or '?'})"), status)
            elif p["code"] >= 500:
                status = _worse(("degraded", f"공개 경로 HTTP {p['code']}"), status)
        sub = host if host == domain else host.split(".")[0]
        cfi = cf_hosts.get(host) or {}
        cert = (i or {}).get("cert")
        t24, t1 = traffic_hosts.get(host) or {}, traffic_hosts_1h.get(host) or {}
        urls = [{"url": f"https://{host}", "audience": "public" if public else "tailnet"}]
        svc = {
            "id": f"route:{host}",
            "name": sub,
            "title": "Caddy 정적 응답" if static else ((rt or {}).get("project") or _unit_short((rt or {}).get("name") or sub)),
            "category": "public" if public else "tailnet",
            "kind": "static" if static else "web",
            "status": status[0],
            "status_reason": status[1],
            "status_source": "probe" if static or status != runtime_status else "runtime",
            "description": cfi.get("comment"),
            "hostnames": [host],
            "urls": urls,
            "upstream": ", ".join(r["upstreams"]) or ("Caddy 가 직접 응답" if static else None),
            "header_inject": r["header_inject"],
            "runtime": rt,
            "listeners": [_lst(l) for l in lst],
            "exposure": _exposure(lst, {"public": public}),
            "probes": {k: {kk: v.get(kk) for kk in ("ok", "code", "ms", "error", "caddy_fallback", "t", "cf_ray",
                                                     "tls_error", "fail_streak")}
                       for k, v in pr.items()},
            "dns": dns.get(host),
            "cert": {k: cert.get(k) for k in ("issuer", "not_after", "days_left", "subject")} if cert else None,
            "edge_cert": {k: (pr.get("public") or {}).get("cert", {}).get(k) for k in ("issuer", "days_left")}
            if public and (pr.get("public") or {}).get("cert") else None,
            "in_tunnel": host in tunnel_hosts,
            "snippet": cfi.get("snippet"),
            "traffic": {
                "requests_1h": t1.get("requests", 0), "requests_24h": t24.get("requests", 0),
                "errors_5xx_24h": t24.get("errors_5xx", 0), "errors_4xx_24h": t24.get("errors_4xx", 0),
                "p50_ms_24h": t24.get("p50_ms"), "p95_ms_24h": t24.get("p95_ms"), "last_ts": t24.get("last_ts"),
            },
            "depends_on": [],
        }
        for l in lst:
            claimed.add(lkey(l))
        services.append(svc)

    # ------------------------------------------------ 2) 인프라
    infra_specs = [
        ("infra:caddy", "caddy", "Caddy", "proxy", ("container", config.CADDY_CONTAINER)),
        ("infra:cloudflared", "cloudflared", "Cloudflare Tunnel", "tunnel", ("container", config.TUNNEL_CONTAINER)),
        ("infra:tailscale", "tailscale", "Tailscale", "vpn", ("systemd", config.INFRA_UNITS["tailscale"])),
        ("infra:docker", "docker", "Docker Engine", "runtime", ("systemd", config.INFRA_UNITS["docker"])),
        ("infra:containerd", "containerd", "containerd", "runtime", ("systemd", config.INFRA_UNITS["containerd"])),
        ("infra:ssh", "ssh", "OpenSSH", "remote-access", ("systemd", config.INFRA_UNITS["ssh"])),
        ("infra:pm2", "pm2", "PM2", "process-manager", ("systemd", config.INFRA_UNITS["pm2"])),
    ]
    infra_specs = [x for x in infra_specs if x[4][1]]  # 이 기기에서 뺀 항목(설정에서 빈 값)
    for sid, name, title, kind, (rtype, rname) in infra_specs:
        if rtype == "container":
            c = ctr_by_name.get(rname)
            rt = _rt_container(c) if c else None
            if c:
                linked_ctrs.add(rname)
        else:
            u = unit_by_name.get(rname)
            rt = _rt_unit(u) if u else None
            if u:
                linked_units.add(rname)
        if rt is None and (name == "containerd" or (name == "pm2" and not pm2.get("available"))):
            continue
        lst = listeners_of_rt(rt)
        status = rt_status(rt)
        if rt is None:
            status = ("down", "실행 주체가 없음(컨테이너·유닛 없음)")
        extra = {}
        if name == "caddy":
            extra = {"version": caddy.get("version"), "routes": len(routes), "config_ok": caddy.get("config_ok")}
            if not caddy.get("config_ok"):
                status = _worse(("degraded", caddy.get("error") or "활성 설정을 읽지 못함"), status)
        elif name == "cloudflared":
            extra = {"version": tunnel.get("version"), "connections": tunnel.get("connections"),
                     "edge_locations": tunnel.get("edge_locations"), "tunnel": config.TUNNEL_NAME}
            if rt and rt["state"] == "running":
                if not tunnel.get("metrics_ok"):
                    status = _worse(("degraded", "metrics 에 닿지 않아 연결 상태 확인 불가"), status)
                elif tunnel.get("ready") is False or tunnel.get("connections") == 0:
                    status = _worse(("down", "엣지 연결 없음"), status)
                elif (tunnel.get("connections") or 0) < 2:
                    status = _worse(("degraded", f"엣지 연결 {tunnel.get('connections')}개"), status)
        elif name == "pm2":
            apps = pm2.get("apps") or []
            extra = {"apps": [{"name": a["name"], "status": a.get("status"), "restarts": a.get("restarts")} for a in apps],
                     "online": sum(a.get("status") == "online" for a in apps), "total": len(apps),
                     "daemon_pid": pm2.get("daemon_pid")}
            if rt is None and pm2.get("available"):
                status = ("degraded", f"pm2 데몬은 떠 있지만 부팅 자동 시작({config.INFRA_UNITS['pm2']})이 없음")
        elif name == "tailscale":
            extra = {"version": ts.get("version"), "backend_state": ts.get("backend_state"),
                     "peers_online": ts.get("peers_online"), "ips": ts_self.get("ips")}
            if ts.get("backend_state") and ts.get("backend_state") != "Running":
                status = _worse(("down", f"Tailscale {ts.get('backend_state')}"), status)
        for l in lst:
            claimed.add(lkey(l))
        services.append({
            "id": sid, "name": name, "title": title, "category": "infra", "kind": kind, "status": status[0],
            "status_reason": status[1], "description": None, "hostnames": [], "urls": [], "upstream": None,
            "runtime": rt, "listeners": [_lst(l) for l in lst], "exposure": _exposure(lst), "probes": {},
            "extra": extra, "depends_on": [],
        })
    # Hermes 게이트웨이(원격 기기). runtime.type "remote" 라 재부팅 가드는 이 기기와 무관한 것으로 본다.
    hermes = raw.get("hermes")
    if hermes:
        base = hermes["url"].split("/api/", 1)[0] + "/"
        services.append({
            "id": "hermes:gateway", "name": "hermes", "title": "Hermes 게이트웨이", "category": "hermes",
            "kind": "agent-gateway", "status": hermes["status"], "status_reason": hermes["status_reason"],
            "description": "봇 게이트웨이(텔레그램·Slack 어댑터) — 상태 API " + hermes["url"], "hostnames": [],
            "urls": [{"url": base, "audience": "tailnet", "code": 200 if hermes["ok"] else None, "ms": hermes["ms"],
                      "error": None if hermes["ok"] else hermes["status_reason"]}],
            "upstream": None, "runtime": {"type": "remote", "name": "hermes", "online": hermes["ok"]}, "listeners": [],
            "exposure": {"public": False, "tailnet": True, "lan": False, "local_only": False, "via": ["tailnet-node"]},
            "probes": {}, "depends_on": [],
            "extra": {k: hermes.get(k) for k in ("version", "gateway_state", "platforms")},
        })
    # docker-proxy 가 잡은 포트(caddy 80/443 등)는 컨테이너 소유로 이미 붙었다. tailscaled 포트도 인프라.

    # ------------------------------------------------ 3) 나머지 컨테이너 → 내부 백엔드(이름·이미지가 Hermes 면 Hermes)
    for c in containers:
        if c["name"] in linked_ctrs:
            continue
        is_hermes = bool(config.HERMES_RE.search(c["name"]) or config.HERMES_RE.search(c["image"] or ""))
        img = (c["image"] or "").lower()
        kind = "database" if re.search(r"postgres|mysql|mariadb|mongo|clickhouse|sqlite", img) else \
            "cache" if re.search(r"redis|valkey|memcache|keydb", img) else "app"
        rt = _rt_container(c)
        lst = listeners_of("container", c["name"])
        for l in lst:
            claimed.add(lkey(l))
        status = rt_status(rt)
        services.append({
            "id": f"{'hermes:' if is_hermes else ''}ctr:{c['name']}", "name": c["name"],
            "title": " / ".join(filter(None, (c["project"], c["service"]))) or c["name"],
            "category": "hermes" if is_hermes else "internal", "kind": kind, "status": status[0], "status_reason": status[1],
            "description": None, "hostnames": [], "urls": [], "upstream": None, "runtime": rt,
            "listeners": [_lst(l) for l in lst], "exposure": _exposure(lst), "probes": {}, "depends_on": [],
        })

    # 같은 compose 프로젝트의 내부 백엔드를 웹 서비스 의존성으로 잇는다
    by_project = {}
    for s in services:
        rt = s.get("runtime") or {}
        if rt.get("type") == "container" and rt.get("project"):
            by_project.setdefault(rt["project"], []).append(s)
    for proj, group in by_project.items():
        webs = [s for s in group if s["category"] in ("public", "tailnet")]
        backs = [s for s in group if s["category"] == "internal"]
        for w in webs:
            w["depends_on"] = [b["id"] for b in backs]
        # 같은 프로젝트의 다른 웹 컨테이너(프론트→API)도 잇는다
        if len(webs) > 1:
            for w in webs:
                if re.search(r"front|web|ui", (w["runtime"] or {}).get("service") or ""):
                    w["depends_on"] += [o["id"] for o in webs if o is not w and
                                        re.search(r"back|api", (o["runtime"] or {}).get("service") or "")]

    # ------------------------------------------------ 4) 남은 리스너 → 호스트 서버 / 시스템 데몬
    groups = {}
    for l in listeners:
        if lkey(l) in claimed:
            continue
        # pm2 앱은 모두 pm2-<사용자>.service cgroup 안에 있다 — 데몬(infra:pm2)에 묻히지 않게 앱 단위로 따로 센다
        if l["owner_kind"] in ("unit", "user-unit") and l["owner"] in linked_units and l.get("pid") not in pm2_by_pid:
            continue
        if l.get("pid") and l["pid"] in linked_pids:
            continue
        if l["owner_kind"] == "container" and l["owner"] in linked_ctrs:
            continue
        if l.get("pid") in pm2_by_pid:
            gk = ("pm2", pm2_by_pid[l["pid"]]["name"])
        elif l["owner_kind"] in ("scope", "unknown", "other"):
            gk = ("pid", l["pid"]) if l.get("pid") else ("port", l["proto"], l["port"])
        else:
            gk = (l["owner_kind"], l["owner"])
        groups.setdefault(gk, []).append(l)
    for gk, lst in groups.items():
        l0 = lst[0]
        okind, owner = l0["owner_kind"], l0["owner"]
        if gk[0] == "pid" and all(l["scope"] == "loopback" and l["port"] >= 32768 for l in lst):
            continue  # 루프백 임시 포트만 연 프로세스(헤드리스 브라우저 디버그 포트 등)는 서버로 치지 않는다
        is_self = any(l.get("pid") == os.getpid() for l in lst)
        is_hermes = not is_self and bool(config.HERMES_RE.search(str(owner) + " " + str(l0["process"])))
        if gk[0] == "pm2":
            rt = _rt_pm2(pm2_by_pid[l0["pid"]])
            name = rt["name"]
        elif okind in ("unit", "user-unit") and owner in unit_by_name:
            rt = _rt_unit(unit_by_name[owner])
            name = _unit_short(owner)
            if okind == "user-unit" and l0["process"]:
                name = l0["process"]
        else:
            rt = _rt_process(l0, rss)
            name = l0["process"] or str(owner)
            cwd = l0.get("cwd") or ""
            if re.match(r"python|node|bun|deno|ruby|php|java|uvicorn|gunicorn", name or "") and cwd and \
                    cwd not in (os.path.expanduser("~"), "/"):
                name = os.path.basename(cwd.rstrip("/")) or name
        procs_names = {x["process"] for x in lst}
        category = "hermes" if is_hermes else ("system" if procs_names & SYSTEM_PROCS else "host")
        if is_self:
            category, name = "infra", "jarvis"
        urls = []
        for l in lst:
            if l["proto"] != "tcp" or l["scope"] == "loopback":
                continue
            hp = http_ports.get(l["port"])
            if not hp or not hp.get("http"):
                continue
            scheme = hp.get("scheme", "http")
            if "lan" in l["reach"]:
                for ip in lan_ips[:1]:
                    urls.append({"url": f"{scheme}://{ip}:{l['port']}", "audience": "lan"})
            if "tailnet" in l["reach"]:
                urls.append({"url": f"{scheme}://{ts_short}:{l['port']}", "audience": "tailnet"})
        status = rt_status(rt) if rt["type"] in ("systemd", "pm2") else ("up", None)
        tcp_ports = sorted(l["port"] for l in lst if l["proto"] == "tcp") or sorted(l["port"] for l in lst)
        if gk[0] == "pm2":
            sid_tail = f"pm2:{rt['name']}"
        elif okind in ("unit", "user-unit"):
            sid_tail = owner
        else:  # 스코프 이름(app-orca-2223882.scope)은 재시작마다 바뀌므로 프로세스 이름과 포트로 만든다
            sid_tail = f"{l0['process'] or 'proc'}:{tcp_ports[0]}"
        desc = rt.get("description") if rt["type"] == "systemd" else None
        if rt["type"] == "process":
            desc = f"{l0['process']} (pid {l0['pid']})" + (f" · {l0['cwd']}" if l0.get("cwd") else "")
        services.append({
            "id": "infra:monitor" if is_self else f"{'hermes' if is_hermes else 'host'}:{sid_tail}", "name": name,
            "title": "Jarvis" if is_self else name,
            "category": category, "kind": "monitor" if is_self else "server", "status": status[0],
            "status_reason": status[1], "description": desc, "hostnames": [], "urls": urls, "upstream": None,
            "runtime": rt, "listeners": [_lst(l) for l in lst], "exposure": _exposure(lst), "probes": {},
            "http": any((http_ports.get(l["port"]) or {}).get("http") for l in lst), "depends_on": [],
        })

    present_ids = {s["id"] for s in services}
    for sid in config.EXPECTED_SERVICES:
        if sid in present_ids:
            continue
        kind, _, name = sid.partition(":")
        source = {"route": "caddy", "ctr": "docker", "host": "systemd"}.get(kind)
        status = "unknown" if source and errors.get(source) else "down"
        services.append({"id": sid, "name": name or sid, "title": name or sid, "category": "host",
                         "kind": "expected", "status": status, "status_reason": "기대 서비스가 관측 목록에 없음",
                         "runtime": None, "listeners": [], "hostnames": [], "urls": [], "upstream": None,
                         "exposure": _exposure([]), "probes": {}, "depends_on": []})

    # ------------------------------------------------ 모니터 자기 진단(이 웹 서비스도 감시 대상)
    monitor = _monitor_health(raw, now)
    me = os.getpid()
    for s in services:
        rt = s.get("runtime") or {}
        if rt.get("pid") == me or s["id"] == "infra:monitor" or \
                (rt.get("type") in ("systemd", "pm2") and rt.get("name") in MONITOR_RUNTIME_NAMES):
            s["kind"] = "monitor"
            s["title"] = "Jarvis"
            s["description"] = f"이 대시보드 자신 — 수집기·API·화면을 한 프로세스로 서빙한다(:{config.PORT})"
            s["extra"] = {**(s.get("extra") or {}), "monitor": monitor}
            if monitor["problems"] and s["status"] == "up":
                s["status"], s["status_reason"] = "degraded", "수집 이상: " + ", ".join(monitor["problems"][:3])

    # ------------------------------------------------ 카테고리 집계
    cat_rows = []
    for c in CATEGORIES:
        items = [s for s in services if s["category"] == c["id"]]
        cat_rows.append({**c, "desc": c["desc"].replace("<도메인>", domain or "<도메인>"), "count": len(items),
                         "up": sum(s["status"] == "up" for s in items),
                         "degraded": sum(s["status"] == "degraded" for s in items),
                         "down": sum(s["status"] == "down" for s in items),
                         "unknown": sum(s["status"] == "unknown" for s in items)})
    order = {c["id"]: i for i, c in enumerate(CATEGORIES)}
    srank = {"down": 0, "degraded": 1, "unknown": 2, "up": 3}
    services.sort(key=lambda s: (order.get(s["category"], 99), srank.get(s["status"], 9), s["name"]))

    # ------------------------------------------------ 유닛 분류
    listener_units = {l["owner"] for l in listeners if l["owner_kind"] in ("unit", "user-unit")}
    route_units = linked_units
    unit_rows = []
    for u in units:
        if u["name"] in INFRA_UNITS or u["name"] in config.INFRA_UNITS.values():
            cat = "infra"
        elif u["name"] in route_units:
            cat = "app"
        elif u["name"] in listener_units:
            cat = "server"
        elif DESKTOP_HINT.search(u["name"]):
            cat = "desktop" if u["scope"] == "user" else "system"
        else:
            cat = "system"
        unit_rows.append({**u, "category": cat})
    unit_rows.sort(key=lambda u: (u["active"] != "failed", ["app", "server", "infra", "system", "desktop"].index(
        u["category"]), u["name"]))

    # ------------------------------------------------ 경보
    alerts = []

    def alert(level, source, message, target=None, key=None):
        alerts.append({"level": level, "source": source, "message": message, "target": target,
                       "key": key or f"{source}:{target or message}"})

    for s in services:
        if s.get("kind") == "expected":
            alert("warn", "inventory", f"기대 서비스가 없음: {s['id']}", s["id"])
    for u in units:
        if u["name"] in config.EXPECTED_UNITS and u["active"] not in ("active", "activating", "reloading"):
            alert("warn", "systemd", f"기대 유닛 {u['name']} {u['active']}", u["name"])
    if config.CONFIG_RESTART_REQUIRED:
        alert("warn", "config", "설정 변경 적용에 재시작 필요: " + ", ".join(config.CONFIG_RESTART_REQUIRED), "config-restart")

    prev_keys = raw.get("prev_alert_keys") or set()
    cpu = fast.get("cpu") or {}
    mem = fast.get("memory") or {}
    therm = fast.get("thermal") or {}
    temp = therm.get("cpu_temp_c")
    if temp is not None:
        # 한 번 뜬 온도 경보는 3°C 내려가야 풀린다(깜빡임 방지)
        held = "system:cpu_temp" in prev_keys or "system:cpu_temp:crit" in prev_keys
        if temp >= 80 or ("system:cpu_temp:crit" in prev_keys and temp >= 77):
            alert("crit", "system", f"CPU 온도 {temp}°C — 스로틀링 임계(85°C) 근접", "cpu_temp")
            alerts[-1]["key"] = "system:cpu_temp:crit"
        elif temp >= 70 or (held and temp >= 67):
            alert("warn", "system", f"CPU 온도 {temp}°C", "cpu_temp")
    th = med.get("throttle") or {}
    if th.get("now"):
        alert("crit", "system", "현재 " + ", ".join(th["now"]) + f" ({th.get('raw')})", "throttle")
    elif th.get("since_boot"):
        alert("info", "system", "부팅 이후 발생: " + ", ".join(th["since_boot"]), "throttle")
    if mem.get("usage_pct") is not None:
        if mem["usage_pct"] >= 90:
            alert("crit", "system", f"메모리 사용률 {mem['usage_pct']}%", "memory")
        elif mem["usage_pct"] >= 80:
            alert("warn", "system", f"메모리 사용률 {mem['usage_pct']}%", "memory")
    if mem.get("swap_total") and mem.get("swap_pct", 0) >= 90:
        avail_pct = mem["available"] / mem["total"] * 100 if mem.get("total") else 100
        swin = mem.get("swap_in_ps") or 0
        used = f"{mem['swap_used'] // 2**20}/{mem['swap_total'] // 2**20} MiB"
        if swin > 50 or avail_pct < 15:
            alert("warn", "system", f"메모리 압박: 스왑 {mem['swap_pct']}% ({used}), 스왑 인 {swin:.0f}페이지/s, "
                                    f"가용 {avail_pct:.0f}%", "swap")
        else:
            alert("info", "system", f"스왑 {mem['swap_pct']}% 찼지만 가용 메모리 {avail_pct:.0f}%·스왑 인 없음 — "
                                    f"압박 아님", "swap")
    for d in med.get("disks") or []:
        if d["usage_pct"] is None:
            continue
        if d["usage_pct"] >= 95:
            alert("crit", "system", f"디스크 {d['mount']} {d['usage_pct']}% 사용", f"disk:{d['mount']}")
        elif d["usage_pct"] >= 85:
            alert("warn", "system", f"디스크 {d['mount']} {d['usage_pct']}% 사용", f"disk:{d['mount']}")
    load5 = (cpu.get("load") or [0, 0])[1]
    if st.get("cores") and load5 > st["cores"] * 1.5:
        alert("warn", "system", f"5분 부하 평균 {load5} (코어 {st['cores']}개)", "load")
    pw = med.get("power") or {}
    if pw.get("input_v") and pw["input_v"] < 4.8:
        alert("warn", "system", f"5V 입력 전압 {pw['input_v']:.2f}V — 전원 어댑터 확인", "input_v")

    for c in containers:
        if c["state"] in ("exited", "dead"):
            lvl = "crit" if c["restart_policy"] not in (None, "", "no") else "warn"
            alert(lvl, "container", f"{c['name']} 중지됨 (exit {c['exit_code']})", c["name"],
                  f"container:state:{c['name']}")
        elif c["state"] == "restarting":
            alert("crit", "container", f"{c['name']} 재시작 반복", c["name"], f"container:state:{c['name']}")
        elif c["health"] == "unhealthy":
            alert("crit", "container", f"{c['name']} 헬스체크 실패: {c['health_last'] or ''}"[:200], c["name"],
                  f"container:state:{c['name']}")
        elif c["restart_count"] and c["uptime_s"] is not None and c["uptime_s"] < 3600:
            alert("warn", "container", f"{c['name']} 최근 재시작 — 누적 {c['restart_count']}회, "
                                       f"{c['uptime_s'] // 60}분째 가동", c["name"], f"container:state:{c['name']}")
        if c.get("oom_killed"):
            alert("warn", "container", f"{c['name']} OOM 으로 종료된 적 있음", c["name"], f"container:oom:{c['name']}")
    for s in services:
        if s["category"] == "infra" and s["status"] in ("down", "unknown"):
            alert("crit", "infra", f"{s['title']} {STATUS_KO.get(s['status'])} — {s['status_reason'] or ''}",
                  s["id"])
    for a in pm2.get("apps") or []:
        if a.get("status") in ("errored", "stopped") and a.get("autorestart") is not False:
            alert("warn", "pm2", f"pm2 앱 {a['name']} {a['status']}", a["name"], f"pm2:state:{a['name']}")
        elif a.get("unstable_restarts"):
            alert("warn", "pm2", f"pm2 앱 {a['name']} 불안정 재시작 {a['unstable_restarts']}회", a["name"],
                  f"pm2:unstable:{a['name']}")
    for u in unit_rows:
        if u["active"] == "failed":
            # macOS launchd 의 '실패'는 떠 있지 않고 마지막 종료 코드가 0 이 아닌 것이라, 필요할 때만 도는 서드파티 작업
            # (업데이터·헬퍼)에 흔하다: 감시 대상(WATCH_UNITS)이 아니면 참고로만. systemd 의 failed 는 실제 실패라 그대로.
            quiet = u.get("manager") == "launchd" and u["name"] not in config.WATCH_UNITS and u["name"] not in MONITOR_RUNTIME_NAMES
            lvl = "info" if u["category"] == "desktop" or quiet else "warn"
            alert(lvl, "systemd", f"{u['name']} ({u['scope']}) 실패 상태", u["name"])

    certs_low = {}
    public_route_hosts = {h for h, r in routes.items() if r["http"]}
    private_route_hosts = {h for h, r in routes.items() if not r["http"]}
    for s in services:
        if s["category"] not in ("public", "tailnet"):
            continue
        host = s["hostnames"][0]
        pr_ = s.get("probes") or {}
        streak = max((pr_.get(k) or {}).get("fail_streak") or 0 for k in ("internal", "public"))
        if s.get("status_source") == "runtime" and (s.get("runtime") or {}).get("type") == "container":
            pass  # 원인이 컨테이너 상태라면 컨테이너 경보가 이미 말한다
        elif s["status"] in ("down", "degraded") and streak >= 2:  # 한 번 튄 실패로는 경보를 띄우지 않는다
            lvl = "crit" if s["status"] == "down" else "warn"
            alert(lvl, "route", f"{host} {'다운' if lvl == 'crit' else '성능 저하'} — {s['status_reason']} "
                                f"({streak}회 연속)", host)
        tls = (pr_.get("internal") or {}).get("tls_error")
        if tls:
            alert("crit", "cert", f"{host} 오리진 인증서 검증 실패: {tls}", host, f"cert:tls:{host}")
        tp = pr_.get("tunnel") or {}
        if s["category"] == "tailnet" and tp.get("ok") and not tp.get("caddy_fallback"):
            alert("crit", "exposure", f"비공개 {host} 가 터널 경로(:80)에서 Caddy 기본 응답이 아닌 HTTP {tp.get('code')} 로 "
                                      f"응답 — 공개 노출 위험", host, f"exposure:leak:{host}")
        if s.get("snippet") == "private" and s["category"] == "public":
            alert("crit", "exposure", f"{host} 는 Caddyfile (private) 스니펫인데 :80(터널 경로)에 걸려 있음", host,
                  f"exposure:snippet:{host}")
        d = s.get("dns") or {}
        if d.get("points_to") in ("cloudflare", "tailnet", "none", "other"):
            if s["category"] == "public" and d["points_to"] != "cloudflare":
                alert("warn", "dns", f"공개 {host} 의 공개 DNS 가 Cloudflare 가 아님 ({d['points_to']})", host)
            if s["category"] == "tailnet" and d["points_to"] == "cloudflare":
                alert("crit", "dns", f"비공개 {host} 가 Cloudflare 프록시로 공개 DNS 에 걸려 있음", host)
            if s["category"] == "tailnet" and d["points_to"] == "none":
                alert("warn", "dns", f"비공개 {host} 의 DNS 레코드가 없음 — tailnet 에서 이름으로 못 들어감", host)
        cert = s.get("cert") or {}
        if cert.get("days_left") is not None and cert["days_left"] < 21:
            g = certs_low.setdefault((cert.get("subject"), cert.get("not_after")), {"days": cert["days_left"],
                                                                                   "hosts": []})
            g["hosts"].append(host)
    for (subject, _na), g in certs_low.items():  # 와일드카드 인증서 하나가 호스트 수만큼 경보를 만들지 않게
        where = f"{subject or '?'} ({len(g['hosts'])}개 호스트: {', '.join(g['hosts'][:3])}{'…' if len(g['hosts']) > 3 else ''})"
        if g["days"] < 7:
            alert("crit", "cert", f"인증서 {where} {g['days']}일 남음", subject, f"cert:{subject}")
        else:
            alert("warn", "cert", f"인증서 {where} {g['days']}일 남음 — 자동 갱신 실패 의심", subject, f"cert:{subject}")
    if tunnel_hosts and routes:
        missing = sorted(public_route_hosts - tunnel_hosts)
        extra = sorted(tunnel_hosts - public_route_hosts)
        if missing:
            alert("warn", "tunnel", "Caddy 공개 라우트가 터널 ingress 에 없음: " + ", ".join(missing), "tunnel-drift")
        if extra:
            alert("warn", "tunnel", "터널 ingress 에 있으나 Caddy 공개 라우트 없음: " + ", ".join(extra), "tunnel-drift2")
    script_hosts = raw.get("tunnel_script_hosts")
    if script_hosts is not None and tunnel_hosts and set(script_hosts) != tunnel_hosts:
        alert("info", "tunnel", "create-public-tunnel.sh HOSTS 와 실제 터널 ingress 가 다름", "script-drift")
    desired = (caddy.get("caddyfile") or {}).get("routes")
    if desired is not None:
        def signature(r):
            return (tuple(sorted(r.get("upstreams") or [])), tuple(sorted(r.get("listen") or [])),
                    bool(r.get("header_inject")), bool(r.get("static")))
        diff = sorted(set(desired) ^ set(routes) | {h for h in set(desired) & set(routes)
                                                  if signature(desired[h]) != signature(routes[h])})
    else:
        diff = sorted(set(cf_hosts) ^ set(routes)) if cf_hosts else []
    if diff:
        alert("warn", "caddy", "Caddyfile 과 활성 설정이 다름(reload 필요?): " + ", ".join(diff), "caddyfile-drift")
    if caddy.get("caddyfile_error"):
        alert("warn", "caddy", "Caddyfile 해석 실패 — 드리프트 확인 불가", "caddyfile-parse")
    wild = [h for h in tunnel_hosts if "*" in h]
    if wild:
        alert("crit", "tunnel", "터널 ingress 에 와일드카드 호스트: " + ", ".join(wild) + " — 비공개 서비스까지 공개될 수 있음",
              "tunnel-wildcard")
    tun_ctr = ctr_by_name.get(config.TUNNEL_CONTAINER)
    if tun_ctr and tun_ctr["state"] == "running" and tunnel and not tunnel.get("metrics_ok"):
        alert("warn", "tunnel", "cloudflared metrics(20241-20245)에 닿지 않음 — 터널 연결 상태를 확인할 수 없음",
              "tunnel-metrics")
    elif tunnel.get("version") is not None or tunnel.get("ready") is not None:
        if tunnel.get("ready") is False or tunnel.get("connections") == 0:
            alert("crit", "tunnel", "Cloudflare 터널 연결 없음 — 공개 서비스 전체 불통", "tunnel")
        elif tunnel.get("connections") is not None and tunnel["connections"] < 2:
            alert("warn", "tunnel", f"터널 엣지 연결 {tunnel['connections']}개", "tunnel")
    if tunnel.get("outdated"):
        alert("info", "tunnel", f"cloudflared {tunnel['outdated']['current']} → {tunnel['outdated']['latest']} "
                                f"업데이트 가능", "tunnel-version")
    l24 = traffic.get("last_24h") or {}
    known_norm = {h.lower().rstrip(".") for h in routes}
    for window in ("last_1h", "last_24h"):
        for h in (traffic.get(window) or {}).get("hosts") or []:
            if not h.get("folded"):
                h["known"] = h["host"].lower().rstrip(".") in known_norm
    unknown = [h for h in l24.get("hosts") or [] if not h["known"] and h["host"]] if routes else []
    if unknown:
        tot = sum(h["requests"] for h in unknown)
        alert("info", "traffic", f"등록되지 않은 호스트로 24시간 {tot}건 요청: " +
              ", ".join(f"{h['host']}({h['requests']})" for h in unknown[:4]), "unknown-hosts")
    l1 = traffic.get("last_1h") or {}
    if l1.get("requests", 0) >= 20:
        e5 = (l1.get("by_status") or {}).get("5xx", 0)
        if e5 / l1["requests"] > 0.05:
            alert("warn", "traffic", f"최근 1시간 5xx 비율 {e5 / l1['requests'] * 100:.1f}% ({e5}건)", "5xx-rate")

    if ts:
        if ts.get("backend_state") != "Running":
            alert("crit", "tailscale", f"Tailscale 상태 {ts.get('backend_state')} — tailnet 접근 불가", "tailscale")
        for h in ts.get("health") or []:
            alert("warn", "tailscale", f"Tailscale: {h}"[:200], "ts-health:" + h[:40])
        if ts.get("funnel"):
            alert("warn", "tailscale", "Tailscale Funnel 이 켜져 있음 — 운영 원칙(VPN 전용) 위반", "funnel")
        if ts.get("serve"):
            alert("info", "tailscale", "tailscale serve 설정이 있음", "serve")
        ke = ts_self.get("key_expiry")
        if ke and ke - now < 14 * 86400:
            alert("warn", "tailscale", f"노드 키 만료 {max(0, (ke - now) / 86400):.0f}일 전", "key-expiry")
    if hermes and hermes["status"] != "up" and (hermes.get("bad_streak") or 0) >= 2:
        # 예전 인프라 항목일 때처럼 멈추면 crit(어댑터 이상 등 degraded 는 warn)
        alert("crit" if hermes["status"] == "down" else "warn", "hermes", f"Hermes 게이트웨이: {hermes['status_reason']}", "hermes")

    lan_open = [l for l in listeners if l["proto"] == "tcp" and l["scope"] in ("all", "lan")
                and l["port"] not in (22, 80, 443, config.PORT)]
    if lan_open:
        alert("info", "exposure", "LAN 전체에 열린 포트: " + ", ".join(
            f"{l['port']}({l['process'] or l['owner']})" for l in lan_open), "lan-open")
    for name, msg in (raw.get("errors") or {}).items():
        alert("warn", "monitor", f"수집 실패 [{name}]: {msg}"[:220], "err:" + name)
    for c in monitor["collectors"]:
        if c["stale"] and not c["error"]:
            alert("warn", "monitor", f"수집 지연 [{c['name']}]: " + (f"마지막 수집 {int(c['age_s'])}초 전" if c["age_s"] is not None else "한 번도 수집 못 함")
                  + f" (기준 {c['limit_s']}초)",
                  "stale:" + c["name"])
    alerts.sort(key=lambda a: -LEVEL_RANK[a["level"]])

    # ------------------------------------------------ 요약
    worst = max((LEVEL_RANK[a["level"]] for a in alerts), default=0)
    overall = "crit" if worst == 3 else "warn" if worst == 2 else "ok"
    sv_count = lambda cat: [s for s in services if s["category"] == cat]
    summary = {
        "status": overall,
        "alerts": {lv: sum(a["level"] == lv for a in alerts) for lv in ("crit", "warn", "info")},
        "services": {"total": len(services), "up": sum(s["status"] == "up" for s in services),
                     "degraded": sum(s["status"] == "degraded" for s in services),
                     "down": sum(s["status"] == "down" for s in services)},
        "public": len(sv_count("public")),
        "tailnet": len(sv_count("tailnet")),
        "host": len(sv_count("host")),
        "hermes": len(sv_count("hermes")),
        "containers": {"total": len(containers), "running": sum(c["state"] == "running" for c in containers),
                       "healthy": sum(c["health"] == "healthy" for c in containers),
                       "unhealthy": sum(c["health"] == "unhealthy" for c in containers)},
        "listeners": {"total": len([l for l in listeners if l["proto"] == "tcp"]),
                      "lan": len([l for l in listeners if l["proto"] == "tcp" and l["scope"] in ("all", "lan")]),
                      "tailnet_only": len([l for l in listeners if l["proto"] == "tcp" and l["scope"] == "tailnet"]),
                      "loopback": len([l for l in listeners if l["proto"] == "tcp" and l["scope"] == "loopback"])},
        "requests_24h": l24.get("requests", 0),
        "requests_1h": l1.get("requests", 0),
    }

    # ------------------------------------------------ 토폴로지
    topo = _topology(services, tunnel, ts, lan_ips)
    topo_phys = _topology_physical(st, med, fast, containers, raw.get("docker_networks") or [], services, ts,
                                   tunnel, summary["status"], raw.get("neighbors") or [])

    # ------------------------------------------------ 시스템
    nets = []
    addrs = med.get("iface_addrs") or {}
    for name, n in ((fast.get("net") or {}).get("interfaces") or {}).items():
        a = addrs.get(name) or {}
        nets.append({**n, "ipv4": a.get("ipv4") or [], "ipv6": a.get("ipv6") or [], "up": a.get("up"),
                     "speed_mbps": a.get("speed_mbps"), "mac": a.get("mac"), "wifi": a.get("wifi")})
    kind_order = {"wired": 0, "wifi": 1, "tailnet": 2, "docker": 3, "other": 4}
    nets.sort(key=lambda n: (kind_order.get(n["kind"], 9), n["name"]))
    gpu = dict(fast.get("gpu") or {})
    gpu["freq_mhz"] = (med.get("clocks_mhz") or {}).get("v3d")
    gpu["name"] = st.get("gpu_name")
    gpu["mem_mb"] = st.get("gpu_mem_mb")
    system = {
        "host": st,
        "uptime_s": fast.get("uptime_s"),
        "boot_time": now - fast["uptime_s"] if fast.get("uptime_s") else None,
        "cpu": {**cpu, "temp_c": temp, "cores": st.get("cores"), "max_mhz": st.get("cpu_max_mhz")},
        "gpu": gpu,
        "memory": mem,
        "thermal": {"cpu_temp_c": temp, "pmic_temp_c": med.get("pmic_temp_c"), "fan": therm.get("fan"),
                    "throttle": med.get("throttle")},
        "power": {**pw, "core_volts": med.get("core_volts")},
        "clocks_mhz": med.get("clocks_mhz") or {},
        "disks": med.get("disks") or [],
        "disk_io": fast.get("disk_io") or {},
        "network": {
            "interfaces": nets,
            **{k: v for k, v in (fast.get("net") or {}).items() if k != "interfaces"},
            "lan_ips": lan_ips,
            "tailnet_ip": ts_ip,
            "neighbors": raw.get("neighbors") or [],
        },
        "processes": procs,
    }

    access = [{"url": f"http://{ip}:{config.PORT}/", "audience": "lan"} for ip in lan_ips]
    if ts_ip:
        access.append({"url": f"http://{ts_short}:{config.PORT}/", "audience": "tailnet"})
    own = set(lan_ips) | {ts_ip}
    mon_route = next((h for h in routes if self_upstream((routes[h]["upstreams"] or [""])[0], own)), None)
    if mon_route:
        access.insert(0, {"url": f"https://{mon_route}/", "audience": "tailnet"})

    return {
        "meta": {
            "host": st.get("hostname"),
            "version": __version__,
            "generated_at": now,
            "started_at": raw.get("started_at"),
            "intervals_s": {"fast": config.FAST_S, "medium": config.MEDIUM_S, "slow": config.SLOW_S,
                            "dns": config.DNS_S},
            "collected_at": raw.get("collected_at") or {},
            "errors": raw.get("errors") or {},
            "access": access,
            "domain": domain,
            "listeners_have_process": raw.get("listeners_have_proc"),
        },
        "summary": summary,
        "alerts": alerts,
        "categories": cat_rows,
        "services": services,
        "system": system,
        "containers": containers,
        "projects": raw.get("projects") or [],
        "units": unit_rows,
        "listeners": listeners,
        "ingress": {
            "domain": domain,
            "caddy": {
                "container": config.CADDY_CONTAINER, "version": caddy.get("version"), "config_ok": caddy.get("config_ok"),
                "caddyfile_error": caddy.get("caddyfile_error"),
                "error": caddy.get("error"), "routes": [
                    {"host": h, "upstreams": r["upstreams"], "public": r["http"], "listen": r["listen"],
                     "header_inject": r["header_inject"], "snippet": (cf_hosts.get(h) or {}).get("snippet"),
                     "comment": (cf_hosts.get(h) or {}).get("comment")} for h, r in sorted(routes.items())],
                "caddyfile_mtime": (caddy.get("caddyfile") or {}).get("mtime"),
                "public_hosts": sorted(public_route_hosts), "private_hosts": sorted(private_route_hosts),
            },
            "tunnel": {**{k: v for k, v in tunnel.items() if k != "ingress"}, "name": config.TUNNEL_NAME,
                       "container": config.TUNNEL_CONTAINER,
                       "ingress_version": (tunnel.get("ingress") or {}).get("version"),
                       "ingress_updated": (tunnel.get("ingress") or {}).get("ts"),
                       "script_hosts": script_hosts},
            "traffic": traffic,
        },
        "tailscale": ts,
        "topology": topo,
        "topology_physical": topo_phys,
        "pm2": pm2 or {"available": False, "apps": []},
        "monitor": monitor,
        "changes": raw.get("changes") or [],
    }


def _topology(services, tunnel, ts, lan_ips):
    nodes, edges = [], []

    def node(id, label, type, layer, status="up", **kw):
        nodes.append({"id": id, "label": label, "type": type, "layer": layer, "status": status, **kw})

    def edge(a, b, kind, label=None):
        edges.append({"from": a, "to": b, "kind": kind, "label": label})

    tun_ok = tunnel.get("ready") is not False and (tunnel.get("connections") or 0) > 0
    node("zone:internet", "Internet", "zone", 0)
    node("zone:tailnet", "Tailnet", "zone", 0, "up" if ts.get("backend_state") == "Running" else "down",
         detail=f"{ts.get('peers_online', 0)}/{ts.get('peers_total', 0)} online")
    node("zone:lan", "LAN", "zone", 0, detail=", ".join(lan_ips))
    by_id = {s["id"]: s for s in services}
    if config.TUNNEL_CONTAINER:  # 설정에서 터널을 뺀 기기는 Cloudflare 경로가 없다
        node("edge:cloudflare", "Cloudflare Edge", "edge", 1, "up" if tun_ok else "down",
             detail=", ".join(tunnel.get("edge_locations") or []))
        edge("zone:internet", "edge:cloudflare", "public", "HTTPS")
        cf = by_id.get("infra:cloudflared")
        node("infra:cloudflared", "cloudflared", "infra", 2, cf["status"] if cf else "unknown",
             detail=f"{tunnel.get('connections') or 0} conns", service_id="infra:cloudflared")
        edge("edge:cloudflare", "infra:cloudflared", "public", "QUIC")
        edge("infra:cloudflared", "infra:caddy", "public", ":80")
    cd = by_id.get("infra:caddy")
    node("infra:caddy", "Caddy", "infra", 3, cd["status"] if cd else "unknown", service_id="infra:caddy")
    edge("zone:tailnet", "infra:caddy", "tailnet", ":443")
    ssh = by_id.get("infra:ssh")
    if ssh:
        node("infra:ssh", "SSH", "infra", 3, ssh["status"], service_id="infra:ssh")
        edge("zone:lan", "infra:ssh", "lan", ":22")
        edge("zone:tailnet", "infra:ssh", "tailnet", ":22")
    for s in services:
        if s["category"] in ("public", "tailnet"):
            node(s["id"], s["name"], "service", 4, s["status"], category=s["category"], service_id=s["id"],
                 detail=s["hostnames"][0])
            edge("infra:caddy", s["id"], s["category"], None)
        elif s["category"] == "internal":
            node(s["id"], s["name"], "internal", 5, s["status"], category="internal", service_id=s["id"],
                 detail=s["title"])
        elif s["category"] in ("host", "hermes") and (s["runtime"] or {}).get("type") != "remote":
            node(s["id"], s["name"], "host", 4, s["status"], category=s["category"], service_id=s["id"],
                 detail=", ".join(str(l["port"]) for l in s["listeners"] if l["proto"] == "tcp"))
            ex = s["exposure"]
            if ex["lan"]:
                edge("zone:lan", s["id"], "lan", None)
            if "direct-tailnet" in ex["via"]:
                edge("zone:tailnet", s["id"], "tailnet", None)
    for s in services:
        for dep in s.get("depends_on") or []:
            if any(n["id"] == dep for n in nodes):
                edge(s["id"], dep, "depends", None)
    return {"nodes": nodes, "edges": edges}


# 수집기별 '오래됐다'고 볼 나이(초). 주기의 3~4배.
STALE_LIMIT_S = {"system_fast": 10, "system": 45, "processes": 45, "docker": 45, "systemd": 45, "listeners": 45,
                 "tailscale": 45, "tunnel": 45, "caddy_log": 45, "caddy": 200, "probes": 200,
                 "hermes": 200, "dns": 900, "http_ports": 900, "config": 45, "inventory": 45, "neighbors": 45,
                 "pm2": 120}


def _monitor_health(raw, now):
    me = raw.get("self") or {}
    errors = raw.get("errors") or {}
    collected = raw.get("collected_at") or {}
    started = me.get("started_at") or now
    cols = []
    for name, limit in STALE_LIMIT_S.items():
        if name == "tunnel" and not config.TUNNEL_CONTAINER:
            continue  # 설정에서 터널을 뺀 기기
        at = collected.get(name)
        age = now - at if at else None
        # 기동 직후에는 아직 한 번도 못 돈 수집기가 있을 수 있다(주기만큼 기다린다)
        stale = (age is not None and age > limit) or (at is None and now - started > limit)
        cols.append({"name": name, "age_s": round(age, 1) if age is not None else None, "limit_s": limit,
                     "stale": bool(stale), "error": errors.get(name)})
    problems = [c["name"] for c in cols if c["stale"] or c["error"]]
    rss = psutil.Process().memory_info().rss
    return {
        "version": me.get("version"), "commit": me.get("commit"), "pid": me.get("pid"),
        "started_at": me.get("started_at"), "uptime_s": round(now - started) if me.get("started_at") else None,
        "rss_bytes": rss, "threads": threading_count(),
        "snapshot_ms": me.get("snapshot_ms"), "snapshot_bytes": me.get("snapshot_bytes"),
        "snapshot_gz_bytes": me.get("snapshot_gz_bytes"),
        "loops": me.get("loops") or {}, "collectors": cols, "problems": problems, "ok": not problems,
        "bind": f"{config.BIND}:{config.PORT}", "config_path": config.CONFIG_PATH,
    }


def threading_count():
    import threading
    return threading.active_count()


def _topology_physical(st, med, fast, containers, docker_nets, services, ts, tunnel, overall, neighbors=()):
    """물리 관점: 기기 → 인터페이스 → Docker 엔진·네트워크 → 컨테이너, 호스트 프로세스, LAN·tailnet 의 다른 기기.
    노드의 parent 는 '안에 들어 있음'(중첩), edges 는 실제 연결(선·무선·오버레이·터널·바인딩)이다."""
    import ipaddress
    nodes, edges, seen = [], [], set()
    rank = {"down": 3, "degraded": 2, "unknown": 1, "up": 0}

    def node(nid, label, typ, parent=None, status="up", **kw):
        if nid in seen:
            return
        seen.add(nid)
        nodes.append({"id": nid, "label": label, "type": typ, "parent": parent, "status": status, **kw})

    def edge(a, b, kind, label=None):
        if a in seen and b in seen:
            edges.append({"from": a, "to": b, "kind": kind, "label": label})

    def worst(ss, default="up"):
        return max((s["status"] for s in ss), key=lambda x: rank.get(x, 0), default=default)

    by_rt = {}
    for s in services:
        rt = s.get("runtime") or {}
        if rt.get("type") == "container":
            by_rt.setdefault(("container", rt["name"]), []).append(s)
        elif rt.get("type") == "systemd":
            by_rt.setdefault(("systemd", rt["name"]), []).append(s)
        elif rt.get("type") in ("process", "pm2") and rt.get("pid"):
            by_rt.setdefault(("pid", rt["pid"]), []).append(s)
        elif rt.get("type") == "remote":
            key = ("remote-id", rt["peer_id"]) if rt.get("peer_id") else ("remote", rt["name"])
            by_rt.setdefault(key, []).append(s)

    # 바깥
    node("ext:internet", "인터넷", "external")
    tun_ok = tunnel.get("ready") is not False and (tunnel.get("connections") or 0) > 0
    if config.TUNNEL_CONTAINER:
        node("ext:cloudflare", "Cloudflare 엣지", "external", status="up" if tun_ok else "down",
             detail=", ".join(tunnel.get("edge_locations") or []) or None)
        edge("ext:cloudflare", "ext:internet", "edge")

    # LAN 세그먼트·tailnet 오버레이
    addrs = med.get("iface_addrs") or {}
    lan_net = None
    for name, a in addrs.items():
        if name.startswith(("eth", "wlan", "en", "wl")):
            for c in a.get("ipv4_cidr") or []:
                try:
                    lan_net = lan_net or ipaddress.ip_interface(c).network
                except ValueError:
                    pass
    gw = next((r.get("gateway") for r in med.get("routes_default") or [] if r.get("gateway")), None)
    node("seg:lan", f"LAN {lan_net}" if lan_net else "LAN", "segment", detail=f"게이트웨이 {gw}" if gw else None,
         cidr=str(lan_net) if lan_net else None, gateway=gw)
    edge("seg:lan", "ext:internet", "uplink", f"NAT · {gw}" if gw else "NAT")
    peers = ts.get("peers") or []
    node("seg:tailnet", "Tailnet", "segment", status="up" if ts.get("backend_state") == "Running" else "down",
         detail=f"{ts.get('peers_online', 0)}/{ts.get('peers_total', 0)} 기기 온라인")

    # 이 기기
    host = st.get("hostname") or "host"
    node(f"dev:{host}", host, "device", status={"ok": "up", "warn": "degraded", "crit": "down"}.get(overall, "up"),
         detail=st.get("model"), self=True, os=st.get("os"), cores=st.get("cores"),
         mem_total=(fast.get("memory") or {}).get("total"))
    dev = f"dev:{host}"
    rates = (fast.get("net") or {}).get("interfaces") or {}
    ip_to_iface = {}
    for name, a in sorted(addrs.items()):
        kind = "wired" if name.startswith(("eth", "en")) else "wifi" if name.startswith(("wlan", "wl")) else \
            "tailnet" if name.startswith("tailscale") else None
        if not kind:
            continue
        ips = a.get("ipv4") or []
        for ip in ips + (a.get("ipv6") or []):
            ip_to_iface[ip] = f"iface:{name}"
        r = rates.get(name) or {}
        node(f"iface:{name}", name, "iface", dev, "up" if a.get("up") else "down", kind=kind,
             detail=", ".join(ips) or None, ips=ips, speed_mbps=a.get("speed_mbps"),
             signal_dbm=(a.get("wifi") or {}).get("signal_dbm"), rx_bps=r.get("rx_bps"), tx_bps=r.get("tx_bps"))
        if kind in ("wired", "wifi"):
            lbl = f"{a['speed_mbps']} Mbps" if a.get("speed_mbps") else (
                f"{(a.get('wifi') or {}).get('signal_dbm'):.0f} dBm" if (a.get("wifi") or {}).get("signal_dbm") is not None else None)
            edge(f"iface:{name}", "seg:lan", kind, lbl)
        else:
            edge(f"iface:{name}", "seg:tailnet", "overlay", ", ".join(ips) or None)

    # Docker 엔진·네트워크·컨테이너
    dk = by_rt.get(("systemd", config.INFRA_UNITS["docker"])) or []
    node("engine:docker", "Docker", "engine", dev, worst(dk), service_ids=[s["id"] for s in dk])
    used = {n for c in containers for n in c["networks"]}
    for n in docker_nets:
        if n["name"] in used and n["driver"] not in ("host", "null"):
            node(f"net:{n['name']}", n["name"], "network", "engine:docker", detail=n.get("subnet"),
                 subnet=n.get("subnet"), driver=n.get("driver"))
    for c in containers:
        nets = [n for n in c["networks"] if f"net:{n}" in seen]
        ss = by_rt.get(("container", c["name"])) or []
        state_status = "up" if c["state"] == "running" else "down"
        node(f"ctr:{c['name']}", c["name"], "container", f"net:{nets[0]}" if nets else "engine:docker",
             worst(ss, state_status) if ss else state_status, service_ids=[s["id"] for s in ss],
             detail=c.get("image"), networks=c["networks"], host_network=c.get("network_mode") == "host",
             ports=[f"{p.get('ip') or '*'}:{p['public']}→{p['private']}" for p in c["ports"] if p.get("public")],
             project=c.get("project"))
        for n in nets[1:]:
            edge(f"ctr:{c['name']}", f"net:{n}", "bridge")
    tc = f"ctr:{config.TUNNEL_CONTAINER}"
    edge(tc, "ext:cloudflare", "tunnel", f"QUIC ×{tunnel.get('connections') or 0}")

    # 호스트 프로세스(systemd·일반 프로세스) — 서비스로 잡힌 것만
    for key, ss in by_rt.items():
        if key[0] not in ("systemd", "pid") or key == ("systemd", config.INFRA_UNITS["docker"]):
            continue
        rt = ss[0]["runtime"]
        label = _unit_short(rt["name"]) if key[0] == "systemd" else (ss[0]["name"] or rt.get("name"))
        if key[0] == "systemd":
            nid = f"proc:{rt['name']}"
        elif rt["type"] == "pm2":
            nid = f"proc:pm2:{rt['name']}"
        else:  # pid 는 재시작마다 바뀌므로 서비스 id(프로세스명:포트)로 만든다
            nid = f"proc:{ss[0]['id'].split(':', 1)[1]}"
        ports = sorted({l["port"] for s in ss for l in s.get("listeners") or [] if l["proto"] == "tcp"})
        node(nid, label, "process", dev, worst(ss), service_ids=[s["id"] for s in ss],
             detail=" ".join(f":{p}" for p in ports[:4]) or None, unit=rt["name"] if key[0] == "systemd" else None,
             manager="pm2" if rt["type"] == "pm2" else ("systemd" if key[0] == "systemd" else None))
        for s in ss:
            for l in s.get("listeners") or []:
                if l["scope"] in ("lan", "tailnet"):
                    for a in l["addrs"]:
                        if a in ip_to_iface:
                            edge(nid, ip_to_iface[a], "bind", f":{l['port']}")

    # tailnet 의 다른 기기. 같은 LAN 주소(CurAddr IP)를 쓰는 노드는 같은 물리 기기 안의 노드로 묶는다.
    by_ip = {}
    for p in peers:
        ip = (p.get("cur_addr") or "").rsplit(":", 1)[0].strip("[]") or p.get("lan_ip_last")
        if ip and is_lan_ip(ip) and ip != gw:
            by_ip.setdefault(ip, []).append(p)
    parent_of = {}
    for ip, group in by_ip.items():
        if len(group) > 1:
            group.sort(key=lambda p: (p.get("created") or 0))
            for p in group[1:]:
                parent_of[p["id"]] = group[0]
    for p in sorted(peers, key=lambda p: (p["id"] in parent_of, not p["online"], p.get("dns_short") or "")):
        pid = f"peer:{p.get('dns_short') or p['hostname']}"
        par = parent_of.get(p["id"])
        ss = (by_rt.get(("remote-id", p.get("id"))) or []) + (by_rt.get(("remote", p["hostname"])) or [])
        ip = (p.get("cur_addr") or "").rsplit(":", 1)[0].strip("[]")
        on_lan = bool(ip and lan_net and ipaddress.ip_address(ip) in lan_net) if ip and ip[0].isdigit() else False
        via_gateway = bool(on_lan and gw and ip == gw)  # 공유기 NAT 주소로만 보이면 같은 LAN 기기라고 할 수 없다
        on_lan = on_lan and not via_gateway
        node(pid, p.get("dns_short") or p["hostname"], "peer",
             f"peer:{par.get('dns_short') or par['hostname']}" if par else None,
             worst(ss) if ss else ("up" if p["online"] else "unknown"), online=p["online"], os=p.get("os"),
             hostname=p["hostname"], ip=p.get("ip"), lan_ip=ip or None, on_lan=on_lan,
             connection=p.get("connection"), relay=p.get("relay"), last_seen=p.get("last_seen"),
             tags=p.get("tags") or [], same_device=bool(par), via_gateway=via_gateway,
             service_ids=[s["id"] for s in ss],
             detail=" · ".join(filter(None, [p.get("os"), p.get("ip")])))
    for n in list(nodes):
        if n["type"] != "peer":
            continue
        if n["online"]:
            if n["connection"] == "direct":
                edge(n["id"], "seg:tailnet", "tailnet-direct", "직결")
            elif n["connection"] == "relay":
                edge(n["id"], "seg:tailnet", "tailnet-relay", f"DERP {n['relay']}" if n.get("relay") else "중계")
            else:  # 온라인이지만 지금 오가는 트래픽이 없어 경로가 정해지지 않았다
                edge(n["id"], "seg:tailnet", "tailnet-idle", "유휴")
        else:
            edge(n["id"], "seg:tailnet", "offline", None)
        if n["on_lan"] and not n["same_device"]:
            edge(n["id"], "seg:lan", "lan", n["lan_ip"])
    known_lan = {p.get("lan_ip_last") for p in peers} | {
        (p.get("cur_addr") or "").rsplit(":", 1)[0].strip("[]") for p in peers}
    for n in neighbors:
        if n["ip"] in known_lan or n["ip"] in ip_to_iface:
            continue
        nid = f"lan:{n['mac']}"
        node(nid, n["ip"], "peer", status="up" if n["online"] else "unknown",
             source="neighbor-cache", ip=n["ip"], lan_ip=n["ip"], mac=n["mac"], online=n["online"],
             on_lan=True, same_device=False, connection="lan", hostname=n["ip"], service_ids=[],
             detail="LAN 이웃 테이블 · " + n["state"], observed_at=n["observed_at"])
        edge(nid, "seg:lan", "lan", n["ip"])
    return {"nodes": nodes, "edges": edges}
