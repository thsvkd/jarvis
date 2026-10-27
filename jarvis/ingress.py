"""인그레스: Caddy 라우트·인증서·접근 로그, Cloudflare 터널, 경로별 프로브, 공개 DNS."""

import fnmatch
import ipaddress
import json
import os
import re
import threading
import socket
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from . import config
from .util import PROBE_UA, UnixConn, client_kind, fetch_text, http_probe, ip_obj, is_tailnet_ip, parse_iso, read, run

# https://www.cloudflare.com/ips-v4 · ips-v6
_CF_NETS = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18 "
    "190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 "
    "104.24.0.0/14 172.64.0.0/13 131.0.72.0/22 2400:cb00::/32 2606:4700::/32 2803:f800::/32 2405:b500::/32 "
    "2405:8100::/32 2a06:98c0::/29 2c0f:f248::/32").split()]
PROBE_UA_PREFIX = "jarvis/"


def _is_cloudflare(a):
    ip = ip_obj(a)
    return bool(ip) and any(ip.version == n.version and ip in n for n in _CF_NETS)


def norm_host(h):
    return (h or "").split(":")[0].strip().lower().rstrip(".")[:80]


# ---------------------------------------------------------------- Caddy 설정
def caddy_live_config():
    """활성 설정(autosave.json). admin API 를 두드리면 Caddy 로그에 줄이 쌓이므로 파일을 읽는다."""
    rc, out, err = run(["docker", "exec", config.CADDY_CONTAINER, "cat", "/config/caddy/autosave.json"], timeout=8)
    if rc != 0:
        raise RuntimeError(f"autosave.json 읽기 실패: {(err or out).strip()[:160]}")
    return json.loads(out)


def caddy_file_routes():
    """실제 Caddy adapter와 컨테이너 환경으로 파일을 해석한다. 설정 적용은 하지 않는다."""
    rc, out, _ = run(["docker", "exec", config.CADDY_CONTAINER, "caddy", "adapt", "--config",
                      config.CADDYFILE_CONTAINER_PATH, "--adapter", "caddyfile"], timeout=10)
    if rc:
        raise RuntimeError("Caddyfile adapter failed")
    return extract_routes(json.loads(out))


STATIC_HANDLERS = ("static_response", "file_server", "static_redirect")


def extract_routes(cfg):
    """host → {listen:[...], upstreams:[...], header_inject:bool, static:bool}.
    reverse_proxy 라우트와, 호스트 매처가 따로 붙은 정적 응답 라우트(예: 루트 도메인의 ads.txt·플레이스홀더)를 모은다.
    사이트 주소 자체(*.domain, domain)에 걸린 기본 404 는 라우트가 아니다."""
    routes = {}
    servers = (((cfg or {}).get("apps") or {}).get("http") or {}).get("servers") or {}
    for sname, srv in servers.items():
        listen = srv.get("listen") or []

        def rec(host, dials=(), inject=False, static=False):
            if "*" in host:
                return
            e = routes.setdefault(host.lower(), {"host": host.lower(), "listen": [], "upstreams": [],
                                                 "header_inject": False, "servers": [], "static": False})
            for l in listen:
                if l not in e["listen"]:
                    e["listen"].append(l)
            for d in dials:
                if d not in e["upstreams"]:
                    e["upstreams"].append(d)
            e["header_inject"] = e["header_inject"] or inject
            e["static"] = e["static"] or static
            if sname not in e["servers"]:
                e["servers"].append(sname)

        def walk(rs, hosts, specific):
            for r in rs or []:
                cur, spec = hosts, specific
                for m in r.get("match") or []:
                    if m.get("host"):
                        cur, spec = m["host"], specific is not None
                for h in r.get("handle") or []:
                    hd = h.get("handler")
                    if hd == "subroute":
                        walk(h.get("routes"), cur, spec if spec is not None else False)
                    elif hd == "reverse_proxy":
                        dials = [u.get("dial") for u in h.get("upstreams") or [] if u.get("dial")]
                        inject = bool(((h.get("headers") or {}).get("request") or {}).get("set"))
                        for host in cur:
                            rec(host, dials, inject)
                    elif hd in STATIC_HANDLERS and spec:
                        for host in cur:
                            rec(host, static=True)

        # 최상위(사이트 주소) 호스트는 specific=None 으로 시작해 한 단계 안쪽 매처부터 '특정 호스트'로 본다
        walk(srv.get("routes"), [], None)
    for e in routes.values():
        e["https"] = any(l.endswith(":443") for l in e["listen"])
        e["http"] = any(l.endswith(":80") for l in e["listen"])
    return routes


def parse_caddyfile(path, domain):
    """Caddyfile 원문에서 매처별 주석(설명)과 스니펫 소속을 뽑는다.
    한 줄 매처(@x host a.b)와 블록 매처(@x { host a.b ... }) 둘 다 읽는다."""
    text = read(path)
    if text is None:
        return None
    info, comments, snippet, pending = {}, [], None, None

    def expand(h):
        return h.replace("{$GATEWAY_DOMAIN}", domain or "").replace("{env.GATEWAY_DOMAIN}", domain or "").lower()

    for raw in text.splitlines():
        s = raw.strip()
        m = re.match(r"^\((\w[\w-]*)\)\s*\{", s)
        if m:
            snippet, comments = m.group(1), []
            continue
        if s.startswith("#"):
            comments.append(s.lstrip("#").strip())
            continue
        m = re.match(r"^@([\w-]+)\s+host\s+(\S+)", s)
        if m:
            info[expand(m.group(2))] = {"matcher": m.group(1), "snippet": snippet, "comment": " ".join(comments) or None}
            comments = []
            continue
        m = re.match(r"^@([\w-]+)\s*\{$", s)
        if m:
            pending = {"matcher": m.group(1), "snippet": snippet, "comment": " ".join(comments) or None}
            comments = []
            continue
        if pending:
            m = re.match(r"^host\s+(\S+)", s)
            if m:
                info[expand(m.group(1))] = dict(pending)
            if s == "}":
                pending = None
            continue
        if s:
            comments = []
    return {"hosts": info, "mtime": os.path.getmtime(path)}


def tunnel_script_hosts(domain):
    s = read(os.path.join(config.INGRESS_DIR, "create-public-tunnel.sh")) or "" if config.INGRESS_DIR else ""
    m = re.search(r'^HOSTS="([^"]*)"', s, re.M)
    if not m:
        return None
    return [domain if h == "@" else f"{h}.{domain}" for h in m.group(1).split()]


# ---------------------------------------------------------------- 터널
def _prom(text):
    rows = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([a-zA-Z_:][\w:]*)(\{[^}]*\})?\s+(\S+)", line)
        if not m:
            continue
        labels = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2) or ""))
        try:
            rows.append((m.group(1), labels, float(m.group(3))))
        except ValueError:
            pass
    return rows


class TunnelCollector:
    LOG_EVERY_S = 60

    def __init__(self):
        self.metrics_port = None
        self.req_samples = deque(maxlen=64)  # (t, total_requests)
        self.last_ingress = None  # 로그에서 본 가장 최신 ingress 설정(로그가 밀려나도 유지)
        self.outdated = None
        self.errors = []
        self._log_t = 0
        self.process_start = None

    def _metrics(self):
        ports = [self.metrics_port] if self.metrics_port else []
        ports += [p for p in range(20241, 20246) if p != self.metrics_port]
        for p in ports:
            try:
                text = fetch_text(f"http://127.0.0.1:{p}/metrics", timeout=2)
                if "cloudflared_" in text:
                    self.metrics_port = p
                    ready = None
                    try:
                        ready = json.loads(fetch_text(f"http://127.0.0.1:{p}/ready", timeout=2))
                    except Exception as e:  # /ready 는 연결이 없으면 503 을 준다
                        ready = {"status": 503, "error": str(e)[:120]}
                    return text, ready
            except Exception:
                continue
        return None, None

    def _read_logs(self):
        rc, out, err = run(["docker", "logs", "--tail", "2000", config.TUNNEL_CONTAINER], timeout=10)
        errors = []
        for line in ((out or "") + (err or "")).splitlines():
            ts = parse_iso(line.split()[0]) if line.strip() else None
            if self.process_start and ts and ts < self.process_start - 1:
                continue
            if "Updated to new configuration" in line:
                m = re.search(r'config="(.*)" version=(\d+)', line)
                if not m:
                    continue
                try:
                    cfg = json.loads(json.loads('"' + m.group(1) + '"'))
                except (ValueError, TypeError):
                    continue
                rules = cfg.get("ingress") if isinstance(cfg, dict) else None
                if isinstance(rules, list):
                    ver = int(m.group(2))
                    if not self.last_ingress or ver >= self.last_ingress["version"]:
                        self.last_ingress = {"version": ver, "ts": parse_iso(line.split()[0]),
                                             "rules": [r for r in rules if isinstance(r, dict)]}
            elif "is outdated" in line:
                m = re.search(r"version (\S+) is outdated\. We recommend upgrading it to (\S+)", line)
                if m:
                    self.outdated = {"current": m.group(1), "latest": m.group(2)}
            elif re.search(r"\s(ERR|WRN)\s", line):
                errors.append({"ts": parse_iso(line.split()[0]), "level": "error" if " ERR " in line else "warn",
                               "msg": line.split(" ", 2)[-1][:220]})
        self.errors = errors[-8:]

    def collect(self):
        now = time.time()
        text, ready = self._metrics()
        res = {"metrics_port": self.metrics_port, "metrics_ok": bool(text), "ready": None, "connections": None,
               "edge_locations": [], "total_requests": None, "request_errors": None, "responses_by_code": {},
               "version": None, "concurrent_requests": None, "rtt_ms": [], "req_per_min": None,
               "config_version": None, "process_start": None}
        if text:
            rtts = {}
            for name, labels, v in _prom(text):
                if name == "cloudflared_tunnel_ha_connections":
                    res["connections"] = int(v)
                elif name == "cloudflared_tunnel_server_locations" and v > 0:
                    res["edge_locations"].append(labels.get("edge_location"))
                elif name == "cloudflared_tunnel_total_requests":
                    res["total_requests"] = int(v)
                elif name == "cloudflared_tunnel_request_errors":
                    res["request_errors"] = int(v)
                elif name == "cloudflared_tunnel_response_by_code":
                    res["responses_by_code"][labels.get("status_code", "?")] = int(v)
                elif name == "build_info" and labels.get("version"):
                    res["version"] = labels["version"]
                elif name == "cloudflared_tunnel_concurrent_requests_per_tunnel":
                    res["concurrent_requests"] = int(v)
                elif name == "quic_client_smoothed_rtt":
                    rtts[labels.get("conn_index", str(len(rtts)))] = v
                elif name == "cloudflared_orchestration_config_version":
                    res["config_version"] = int(v)
                elif name == "process_start_time_seconds":
                    res["process_start"] = v
            res["rtt_ms"] = [round(v, 1) for _, v in sorted(rtts.items())]
            res["edge_locations"] = sorted(set(filter(None, res["edge_locations"])))
            tot = res["total_requests"]
            if tot is not None:
                if self.req_samples and tot < self.req_samples[-1][1]:
                    self.req_samples.clear()  # cloudflared 재시작
                self.req_samples.append((now, tot))
                # 최소 60초 창으로 계산해야 10초 간격 샘플의 들쭉날쭉함이 사라진다
                old = [s for s in self.req_samples if now - s[0] >= 60]
                if old:
                    t0, v0 = old[-1]
                    res["req_per_min"] = round((tot - v0) / (now - t0) * 60, 1)
        if ready is not None:
            res["ready"] = ready.get("status") == 200
            res["ready_connections"] = ready.get("readyConnections")
        start = res.get("process_start")
        if start and start != self.process_start:
            self.process_start = start
            self.last_ingress, self.outdated, self.errors = None, None, []
            self._log_t = 0
        if now - self._log_t >= self.LOG_EVERY_S:
            self._log_t = now
            self._read_logs()
        res["ingress"] = self.last_ingress
        res["hostnames"] = [r["hostname"].lower() for r in (self.last_ingress or {}).get("rules", [])
                            if r.get("hostname")]
        res["outdated"] = self.outdated if (self.outdated and self.outdated["current"] == res["version"]) or (
            self.outdated and not res["version"]) else None
        res["recent_errors"] = self.errors
        return res


# ---------------------------------------------------------------- 접근 로그
# :80 전용 사이트를 둔 구성이라 기동할 때마다 나오는 정상 경고.
_BENIGN = ("HTTP/2 skipped because it requires TLS", "HTTP/3 skipped because it requires TLS",
           "server is listening only on the HTTP port")


class CaddyLog:
    """Caddy 컨테이너 로그를 docker API follow 스트림 하나로 받아 24시간 창을 유지한다.
    (docker logs --since 를 주기마다 부르면 dockerd 가 로그 파일 전체를 매번 훑는다.)"""

    WINDOW = 24 * 3600

    def __init__(self):
        self.lock = threading.Lock()
        self.entries = deque(maxlen=50000)  # 공개 트래픽 폭주에도 메모리가 묶이게
        self.errors = deque(maxlen=40)
        self.last_ts = None
        self.connected = False
        self.stream_error = None
        self.connected_at = None
        self._conn = None

    def reconnect(self):
        """설정의 컨테이너가 바뀌면 기존 follow 읽기를 깨워 새 대상으로 연결한다."""
        with self.lock:
            conn = self._conn
        if conn and conn.sock:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # ---- 스트림
    def start(self, stop):
        threading.Thread(target=self._run, args=(stop,), name="jarvis-caddylog", daemon=True).start()

    def _run(self, stop):
        backoff = 2
        while not stop.is_set():
            if not config.CADDY_CONTAINER:
                self.connected, self.stream_error = False, None
                stop.wait(2)
                continue
            since = self.last_ts if self.last_ts else time.time() - self.WINDOW
            try:
                self._stream(since, stop)
                backoff = 2
            except Exception as e:
                self.stream_error = f"{type(e).__name__}: {e}"[:200]
            self.connected = False
            stop.wait(backoff)
            backoff = min(60, backoff * 2)

    @staticmethod
    def _readn(r, n):
        buf = b""
        while len(buf) < n:
            chunk = r.read(n - len(buf))
            if not chunk:
                return buf
            buf += chunk
        return buf

    def _stream(self, since, stop):
        conn = UnixConn(config.DOCKER_SOCK, timeout=None)
        try:
            ins = None
            try:
                conn.request("GET", f"/containers/{config.CADDY_CONTAINER}/json")
                r = conn.getresponse()
                ins = json.loads(r.read()) if r.status == 200 else None
            finally:
                conn.close()
            if ins is None:
                raise RuntimeError("caddy 컨테이너 없음")
            tty = bool((ins.get("Config") or {}).get("Tty"))
            conn = UnixConn(config.DOCKER_SOCK, timeout=None)
            with self.lock:
                self._conn = conn
            conn.request("GET", f"/containers/{config.CADDY_CONTAINER}/logs?follow=1&stdout=1&stderr=1"
                                f"&since={since:.6f}")
            r = conn.getresponse()
            if r.status != 200:
                raise RuntimeError(f"logs HTTP {r.status}")
            self.connected, self.stream_error, self.connected_at = True, None, time.time()
            watermark = since  # 재접속 때 겹치는 줄은 버린다(스트림 안에서는 순서대로 온다)
            pend = b""
            while not stop.is_set():
                if tty:
                    data = r.read1(65536) if hasattr(r, "read1") else r.read(4096)
                    if not data:
                        return
                else:
                    hdr = self._readn(r, 8)
                    if len(hdr) < 8:
                        return  # 컨테이너 정지·dockerd 재시작 → 재접속
                    data = self._readn(r, int.from_bytes(hdr[4:8], "big"))
                pend += data
                *lines, pend = pend.split(b"\n")
                for raw in lines:
                    self._ingest(raw.decode("utf-8", "replace"), watermark)
        finally:
            conn.close()
            with self.lock:
                if self._conn is conn:
                    self._conn = None

    def _ingest(self, line, watermark):
        if not line.startswith("{"):
            return
        try:
            d = json.loads(line)
        except ValueError:
            return
        if not isinstance(d, dict):
            return
        ts = d.get("ts") or 0
        if not isinstance(ts, (int, float)) or ts <= watermark:
            return
        logger = d.get("logger", "")
        with self.lock:
            self.last_ts = max(self.last_ts or 0, ts)
            if logger.startswith("http.log.access"):
                req = d.get("request") or {}
                hdr = req.get("headers") or {}
                if (hdr.get("User-Agent") or [""])[0].startswith(PROBE_UA_PREFIX):
                    return  # 모니터 자신의 프로브는 트래픽이 아니다
                peer = req.get("remote_ip") or req.get("client_ip")
                peer_kind = client_kind(peer)
                cf_ip = (hdr.get("Cf-Connecting-Ip") or [None])[0]
                # 터널 요청: 평문(:80) + cloudflared(호스트 루프백 → docker-proxy) 에서 옴 + CF 헤더.
                # LAN 에서 :80 으로 CF 헤더를 위조해도 터널로 세지 않는다.
                tunnel = not req.get("tls") and peer_kind in ("docker", "local") and bool(cf_ip)
                self.entries.append({
                    "ts": ts,
                    "host": norm_host(req.get("host")),
                    "method": str(req.get("method") or "")[:10],
                    "path": str(req.get("uri") or "").split("?")[0][:120],
                    "status": d.get("status") if isinstance(d.get("status"), int) else 0,
                    "ms": round((d.get("duration") or 0) * 1000, 1) if isinstance(d.get("duration"), (int, float)) else None,
                    "bytes": d.get("size"),
                    "proto": req.get("proto"),
                    "client": cf_ip if tunnel else (req.get("client_ip") or peer),
                    "country": (hdr.get("Cf-Ipcountry") or [None])[0] if tunnel else None,
                    "via": "tunnel" if tunnel else peer_kind,
                })
            elif d.get("level") in ("warn", "error", "fatal") or "error" in d:
                if logger == "admin.api" or any(b in (d.get("msg") or "") for b in _BENIGN):
                    return
                self.errors.append({"ts": ts, "level": d.get("level"), "logger": logger,
                                    "msg": str(d.get("msg") or "")[:160], "error": str(d.get("error") or "")[:240]})

    # ---- 집계
    @staticmethod
    def _pct(sorted_vals, q):
        if not sorted_vals:
            return None
        i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
        return sorted_vals[i]

    def summary(self, known_hosts):
        now = time.time()
        with self.lock:
            cutoff = now - self.WINDOW
            while self.entries and self.entries[0]["ts"] < cutoff:
                self.entries.popleft()
            es = list(self.entries)
            errors = list(self.errors)[-15:][::-1]
        known_hosts = {norm_host(h) for h in known_hosts}

        def agg(window):
            sub = [e for e in es if e["ts"] >= now - window]
            by_status = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
            by_via = {}
            hosts = {}
            for e in sub:
                cls = f"{(e['status'] or 0) // 100}xx"
                if cls in by_status:
                    by_status[cls] += 1
                by_via[e["via"]] = by_via.get(e["via"], 0) + 1
                h = hosts.setdefault(e["host"], {"host": e["host"], "requests": 0, "errors_5xx": 0, "errors_4xx": 0,
                                                 "ms": [], "known": e["host"] in known_hosts, "last_ts": 0})
                h["requests"] += 1
                h["errors_5xx"] += cls == "5xx"
                h["errors_4xx"] += cls == "4xx"
                if e["ms"] is not None:
                    h["ms"].append(e["ms"])
                h["last_ts"] = max(h["last_ts"], e["ts"])
            for h in hosts.values():
                ms = sorted(h.pop("ms"))
                h["p50_ms"], h["p95_ms"] = self._pct(ms, 0.5), self._pct(ms, 0.95)
            # 미등록 호스트(임의 Host 헤더)는 상위 8개만 이름을 남기고 나머지는 한 줄로 묶는다.
            unknown = sorted((h for h in hosts.values() if not h["known"]), key=lambda h: -h["requests"])
            if len(unknown) > 8:
                rest = unknown[8:]
                for h in rest:
                    del hosts[h["host"]]
                hosts["(기타 미등록)"] = {
                    "host": "(기타 미등록)", "requests": sum(h["requests"] for h in rest),
                    "errors_5xx": sum(h["errors_5xx"] for h in rest), "errors_4xx": sum(h["errors_4xx"] for h in rest),
                    "known": False, "last_ts": max(h["last_ts"] for h in rest), "p50_ms": None, "p95_ms": None,
                    "folded": len(rest)}
            all_ms = sorted(e["ms"] for e in sub if e["ms"] is not None)
            return {
                "window_s": window, "requests": len(sub), "by_status": by_status, "by_via": by_via,
                "p50_ms": self._pct(all_ms, 0.5), "p95_ms": self._pct(all_ms, 0.95),
                "hosts": sorted(hosts.values(), key=lambda h: -h["requests"]),
            }

        # 분 단위(최근 60분)·시간 단위(최근 24시간) 요청 수. 시계가 뒤로 가도 인덱스가 넘치지 않게.
        per_min = [0] * 60
        per_min_5xx = [0] * 60
        per_hour = [0] * 24
        for e in es:
            age = now - e["ts"]
            if age < 0:
                continue
            if age < 3600:
                i = 59 - int(age // 60)
                per_min[i] += 1
                per_min_5xx[i] += (e["status"] or 0) >= 500
            if age < 86400:
                per_hour[23 - int(age // 3600)] += 1
        return {
            "last_1h": agg(3600),
            "last_24h": agg(86400),
            "per_minute": per_min,
            "per_minute_5xx": per_min_5xx,
            "per_hour": per_hour,
            "recent": [dict(e) for e in es[-40:]][::-1],
            "errors": errors,
            "stream": {"connected": self.connected, "since": self.connected_at, "error": self.stream_error},
        }


# ---------------------------------------------------------------- 프로브
def probe_targets(routes, skip):
    """프로브할 라우트. skip 의 glob 에 맞는 호스트는 뺀다(config.PROBE_SKIP)."""
    return {h: r for h, r in routes.items() if not any(fnmatch.fnmatch(h, p) for p in skip)}


def probe_routes(routes, public_hosts, domain, with_public=True, internal_ip="127.0.0.1"):
    """각 호스트를 세 경로로 찔러 본다.
    internal: <tailnet IP>:443 — tailnet 사용자와 같은 경로(Caddy 가 보는 출발지도 tailnet 대역이라
              remote_ip 로 tailnet 만 받는 라우트도 실제 앱까지 닿는다), 인증서 확인 포함
    tunnel:   127.0.0.1:80 Host 헤더 (터널이 도달하는 경로. 비공개 호스트는 Caddy 기본 404 여야 한다)
    public:   공개 DNS → Cloudflare 엣지 → 터널 (공개 호스트만)
    터널 경로의 'Caddy 기본 응답' 여부는 없는 호스트로 보낸 대조 요청과 코드·본문을 비교해 정한다
    (앱이 / 에서 404 를 줘도 오판하지 않게)."""
    jobs = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        ctrl_f = ex.submit(http_probe, f"jarvis-nx.{domain}", "http", "127.0.0.1", 80, "/", 6)
        for host in routes:
            jobs[(host, "internal")] = ex.submit(http_probe, host, "https", internal_ip, 443, "/", 6)
            jobs[(host, "tunnel")] = ex.submit(http_probe, host, "http", "127.0.0.1", 80, "/", 6)
            if with_public and host in public_hosts:
                jobs[(host, "public")] = ex.submit(http_probe, host, "https", None, 443, "/", 10)
        try:
            ctrl = ctrl_f.result()
        except Exception:
            ctrl = {}
        out = {}
        for (host, kind), fut in jobs.items():
            try:
                r = fut.result()
            except Exception as e:
                r = {"ok": False, "error": str(e)[:160]}
            r["t"] = time.time()
            if kind == "tunnel" and r.get("ok") and ctrl.get("ok"):
                same = r.get("code") == ctrl.get("code") and r.get("body_hash") == ctrl.get("body_hash")
                r["caddy_fallback"] = bool(r.get("caddy_fallback") or same)
            out.setdefault(host, {})[kind] = r
    return out


def dns_lookup(hosts):
    """공개 리졸버(Cloudflare DoH)가 보는 레코드. 공개 호스트는 CF 엣지, 비공개는 tailnet IP 여야 정상.
    판정은 A 레코드 IP 로만 한다(CNAME 만 있고 IP 가 없으면 none)."""
    res = {}

    def one(h):
        try:
            d = json.loads(fetch_text(f"https://cloudflare-dns.com/dns-query?name={h}&type=A", timeout=5,
                                      headers={"accept": "application/dns-json", "User-Agent": PROBE_UA}))
            ans = d.get("Answer") or []
            ips = [a["data"] for a in ans if a.get("type") == 1 and ip_obj(a.get("data", ""))]
            cnames = [a["data"].rstrip(".") for a in ans if a.get("type") == 5]
            if not ips:
                kind = "none"
            elif all(is_tailnet_ip(a) for a in ips):
                kind = "tailnet"
            elif all(_is_cloudflare(a) for a in ips):
                kind = "cloudflare"
            else:
                kind = "other"
            return h, {"answers": ips, "cnames": cnames, "points_to": kind, "t": time.time()}
        except Exception as e:
            return h, {"answers": [], "cnames": [], "points_to": "error", "error": str(e)[:120], "t": time.time()}

    with ThreadPoolExecutor(max_workers=6) as ex:
        for h, r in ex.map(one, hosts):
            res[h] = r
    return res


def caddy_version():
    rc, out, _ = run(["docker", "exec", config.CADDY_CONTAINER, "caddy", "version"], timeout=6)
    return out.split()[0] if rc == 0 and out.strip() else None
