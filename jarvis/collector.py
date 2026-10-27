"""주기 수집기. 느린 수집이 빠른 수집을 막지 않도록 주기별로 스레드를 나눈다."""

import gzip
import json
import os
import threading
import time
import traceback
from collections import deque

from . import __version__, config, discovery, ingress, model, runtime, svc, tailnet, util
from .changes import InventoryJournal
from .system import SystemCollector
from .util import http_probe

HIST_FINE_S = 5          # 1시간 창: 5초 간격 720점
HIST_COARSE_S = 60       # 24시간 창: 1분 간격 1440점
SPARK_POINTS = 60        # 스냅샷에 싣는 최근 5분
HIST_KEYS = ("cpu", "gpu", "mem", "swap", "temp", "load", "rx", "tx", "trx", "ttx", "dr", "dw", "pw", "fan", "mhz")


def _listener_shape(rows):
    ports = {}
    for r in rows:
        if r["proto"] == "tcp":
            fields = tuple(str(r.get(k)) for k in ("pid", "process", "owner_kind", "owner", "scope", "cwd"))
            ports.setdefault(r["port"], []).append((fields, tuple(sorted(r.get("addrs") or []))))
    return {p: sorted(v) for p, v in ports.items()}


def _git_commit():
    """배포된 코드의 커밋(짧은 해시, 커밋 안 된 수정이 있으면 -dirty). 저장소가 아니면 None."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    from .util import run
    rc, out, _ = run(["git", "-C", root, "describe", "--always", "--dirty", "--abbrev=10"], timeout=5)
    if rc == 0 and out.strip():
        return out.strip()
    try:
        head = open(os.path.join(root, ".git", "HEAD")).read().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            p = os.path.join(root, ".git", ref)
            if os.path.exists(p):
                return open(p).read().strip()[:10]
            for line in open(os.path.join(root, ".git", "packed-refs")):
                if line.strip().endswith(" " + ref):
                    return line.split()[0][:10]
            return None
        return head[:10]
    except OSError:
        return None


class Collector:
    def __init__(self):
        self.lock = threading.Lock()
        self.raw = {"started_at": time.time(), "errors": {}, "collected_at": {}}
        self.sys = SystemCollector()
        self.raw["static"] = self.sys.static
        self.docker = runtime.DockerCollector()
        self.units = svc.UnitCollector()
        self.tunnel = ingress.TunnelCollector()
        self.caddy_log = ingress.CaddyLog()
        self.fine = deque(maxlen=3600 // HIST_FINE_S)
        self.coarse = deque(maxlen=86400 // HIST_COARSE_S)
        self._acc = []
        self._bucket = None
        self._streak = {}          # (host, 경로) → 연속 실패 횟수
        self._hermes_streak = 0    # Hermes 게이트웨이 연속 비정상 횟수
        self._alert_since = {}     # 경보 key → 처음 뜬 시각
        self._slow_n = 0
        self._snap = None
        self._snap_gz = None
        self._snap_at = 0
        self._dirty = True
        self.stop = threading.Event()
        self._slow_wake = threading.Event()
        self._dns_wake = threading.Event()
        self.journal = InventoryJournal(os.path.join(config.STATE_DIR, "inventory.json"))
        self._journal_saved = 0
        self.self_info = {"pid": os.getpid(), "version": __version__, "commit": _git_commit(),
                          "started_at": time.time(), "loops": {}, "snapshot_ms": None, "snapshot_bytes": None}
        self.raw["self"] = self.self_info
        if config.CONFIG_ERROR:
            self.raw["errors"]["config"] = config.CONFIG_ERROR[:200]
        self._load_history()

    # ------------------------------------------------ 상태 저장
    def _set(self, **kw):
        with self.lock:
            self.raw.update(kw)
            self._dirty = True

    def _ok(self, name):
        with self.lock:
            self.raw["errors"].pop(name, None)
            self.raw["collected_at"][name] = time.time()

    def _fail(self, name, e):
        msg = f"{type(e).__name__}: {e}"[:200]
        with self.lock:
            same = self.raw["errors"].get(name) == msg
            self.raw["errors"][name] = msg
            self._dirty = True
        if not same:  # 같은 실패가 이어지면 한 번만 찍는다(Docker 가 없는 기기는 10초마다 실패한다)
            print(f"[jarvis] {name} 실패: {msg}", flush=True)
        if config.env("DEBUG"):
            traceback.print_exc()

    def _guard(self, name, fn):
        try:
            fn()
            self._ok(name)
        except Exception as e:
            self._fail(name, e)

    # ------------------------------------------------ 주기 작업
    def _fast(self):
        f = self.sys.fast()
        self._set(fast=f)
        now = f["t"]
        with self.lock:
            med = self.raw.get("medium") or {}
        pw = (med.get("power") or {}).get("total_w")
        fan = ((f.get("thermal") or {}).get("fan") or {}).get("rpm")
        net = f.get("net") or {}
        point = {
            "cpu": f["cpu"]["usage_pct"], "gpu": f["gpu"]["usage_pct"], "mem": f["memory"]["usage_pct"],
            "swap": f["memory"]["swap_pct"], "temp": f["thermal"]["cpu_temp_c"], "load": (f["cpu"]["load"] or [None])[0],
            "rx": round(net.get("rx_bps", 0)), "tx": round(net.get("tx_bps", 0)),
            "trx": round(net.get("tailnet_rx_bps", 0)), "ttx": round(net.get("tailnet_tx_bps", 0)),
            "dr": round((f.get("disk_io") or {}).get("read_bps", 0)),
            "dw": round((f.get("disk_io") or {}).get("write_bps", 0)),
            "pw": pw, "fan": fan, "mhz": f["cpu"]["freq_mhz"],
        }
        if point["cpu"] is None:
            return
        bucket = int(now // HIST_FINE_S)
        if self._bucket is not None and bucket != self._bucket and self._acc:
            # 2초 틱이 5초 경계를 넘으면 직전 버킷을 닫는다(벽시계 기준이라 간격이 정확히 5초)
            avg = self._avg(self._acc)
            avg["t"] = self._bucket * HIST_FINE_S
            self._acc = []
            with self.lock:
                self.fine.append(avg)
                if not self.coarse or avg["t"] - self.coarse[-1]["t"] >= HIST_COARSE_S:
                    window = [p for p in self.fine if p["t"] > avg["t"] - HIST_COARSE_S]
                    c = self._avg(window)
                    c["t"] = avg["t"]
                    self.coarse.append(c)
        self._bucket = bucket
        self._acc.append(point)

    @staticmethod
    def _avg(points):
        out = {}
        for k in HIST_KEYS:
            vals = [p[k] for p in points if p.get(k) is not None]
            if k in ("rx", "tx", "trx", "ttx", "dr", "dw"):
                out[k] = round(sum(vals) / len(vals)) if vals else None
            else:
                out[k] = round(sum(vals) / len(vals), 2) if vals else None
        return out

    def _medium(self):
        self._guard("config", self._refresh_config)
        self._guard("system", lambda: self._set(medium=self.sys.medium()))
        self._guard("processes", lambda: self._set(procs=self.sys.processes(), rss_by_pid=self.sys.rss_by_pid))
        rss = self.sys.rss_by_pid

        def dock():
            cs = self.docker.collect(rss)
            self._set(containers=cs, projects=runtime.compose_projects(cs), docker_networks=runtime.docker_networks())
            shape = [(c["full_id"], c["name"], c["networks"], c["aliases"]) for c in cs]
            if getattr(self, "_docker_shape", shape) != shape:
                self._slow_wake.set()
            self._docker_shape = shape
        # 설정에서 빈 값이면 Docker 가 없는 기기다: 빈 목록을 '수집함'으로 둔다(수집 지연으로 보이지 않게)
        self._guard("docker", dock if config.DOCKER_SOCK else
                    lambda: self._set(containers=[], projects=runtime.compose_projects([]), docker_networks=[]))
        self._guard("systemd", lambda: self._set(units=self.units.collect(rss)))

        def lst():
            with self.lock:
                addrs = (self.raw.get("medium") or {}).get("iface_addrs")
                cs = self.raw.get("containers")
                us = self.raw.get("units")
            ls, has = runtime.collect_listeners(addrs, cs, us)
            self._publish_listeners(ls, has)
        self._guard("listeners", lst)
        self._guard("neighbors", lambda: self._set(neighbors=discovery.neighbors(
            (self.raw.get("medium") or {}).get("iface_addrs") or {})))
        self._guard("tailscale", lambda: self._set(tailscale=tailnet.tailscale_status()))
        if config.TUNNEL_CONTAINER:  # 설정에서 빈 값이면 이 기기에 터널이 없는 것이다
            self._guard("tunnel", lambda: self._set(tunnel=self.tunnel.collect()))

        def logs():
            with self.lock:
                known = set((self.raw.get("caddy") or {}).get("routes") or {})
            self._set(traffic=self.caddy_log.summary(known))
            if not self.caddy_log.connected and self.caddy_log.stream_error:
                raise RuntimeError("Caddy 로그 스트림 끊김: " + self.caddy_log.stream_error)
        self._guard("caddy_log", logs)

        if time.time() - getattr(self, "_pm2_t", 0) >= config.PM2_EVERY_S:
            self._pm2_t = time.time()
            self._guard("pm2", lambda: self._set(pm2=runtime.pm2_apps()))

        if config.INGRESS_DIR:
            try:
                st = os.stat(os.path.join(config.INGRESS_DIR, "Caddyfile"))
                stamp = st.st_mtime_ns, st.st_size
            except OSError:
                stamp = None
            if getattr(self, "_caddyfile_stamp", stamp) != stamp:
                self._slow_wake.set()
            self._caddyfile_stamp = stamp
        self._guard("inventory", self._record_changes)

    def _publish_listeners(self, rows, has_proc):
        with self.lock:
            old = _listener_shape(self.raw.get("listeners") or [])
            new = _listener_shape(rows)
            changed = {p for p in set(old) | set(new) if old.get(p) != new.get(p)}
            self.raw.update(listeners=rows, listeners_have_proc=has_proc)
            self.raw["http_ports"] = {p: v for p, v in (self.raw.get("http_ports") or {}).items() if p not in changed}
            self._dirty = True
        if changed:
            self._dns_wake.set()

    def _refresh_config(self):
        changed = config.reload_if_changed()
        if changed:
            self._hosts = None
            if "CADDY_CONTAINER" in changed:
                self.caddy_log.reconnect()
                with self.lock:
                    self.raw.update(caddy={}, probes={}, dns={}, traffic={})
                    self.raw["collected_at"].pop("caddy", None)
                    self._dirty = True
            if "TUNNEL_CONTAINER" in changed:
                self.tunnel = ingress.TunnelCollector()
                self._set(tunnel={})
            self._slow_wake.set()
            self._dns_wake.set()

    def _record_changes(self):
        with self.lock:
            raw = {**self.raw, "collected_at": dict(self.raw["collected_at"]), "errors": dict(self.raw["errors"])}
        self.journal.observe(raw)
        self._set(changes=self.journal.events())
        if time.monotonic() - self._journal_saved >= 30:
            self.journal.save()
            self._journal_saved = time.monotonic()

    def _slow(self):
        def caddy():
            if not config.CADDY_CONTAINER:  # 설정에서 빈 값이면 이 기기에 Caddy 가 없는 것이다
                return
            cfg = ingress.caddy_live_config()
            routes = ingress.extract_routes(cfg)
            domain = None
            for h in routes:
                parts = h.split(".")
                if len(parts) >= 3:
                    domain = ".".join(parts[-2:])
                    break
            domain = domain or config.DOMAIN
            cf = ingress.parse_caddyfile(os.path.join(config.INGRESS_DIR, "Caddyfile"), domain) if config.INGRESS_DIR else None
            cf_error = None
            try:
                cf = cf or {"hosts": {}}
                cf["routes"] = ingress.caddy_file_routes()
            except Exception:
                cf_error = "Caddyfile adapter failed"
            version = ingress.caddy_version()
            with self.lock:
                previous = (self.raw.get("caddy") or {}).get("routes") or {}
                changed = {h for h in set(previous) | set(routes) if previous.get(h) != routes.get(h)}
                if changed:
                    self.raw["probes"] = {h: p for h, p in (self.raw.get("probes") or {}).items() if h not in changed}
                    self.raw["dns"] = {h: d for h, d in (self.raw.get("dns") or {}).items() if h not in changed}
                    self._streak = {k: v for k, v in self._streak.items() if k[0] not in changed}
            self._set(caddy={"routes": routes, "domain": domain, "caddyfile": cf, "config_ok": True, "error": None,
                             "caddyfile_error": cf_error, "version": version},
                      tunnel_script_hosts=ingress.tunnel_script_hosts(domain))
            if changed:
                self._dns_wake.set()

        def caddy_guarded():
            try:
                caddy()
            except Exception as e:
                with self.lock:
                    prev = dict(self.raw.get("caddy") or {})
                prev.update(config_ok=False, error=str(e)[:200])
                self._set(caddy=prev)
                raise
        self._guard("caddy", caddy_guarded)

        self._slow_n += 1

        def probes():
            with self.lock:
                routes = ingress.probe_targets((self.raw.get("caddy") or {}).get("routes") or {}, config.PROBE_SKIP)
                domain = (self.raw.get("caddy") or {}).get("domain") or config.DOMAIN
                prev = self.raw.get("probes") or {}
                ts_ips = ((self.raw.get("tailscale") or {}).get("self") or {}).get("ips") or []
            pub = {h for h, r in routes.items() if r["http"]}
            with_public = time.time() - getattr(self, "_public_at", 0) >= 120 or any(
                "public" not in (prev.get(h) or {}) for h in pub)
            if with_public:
                self._public_at = time.time()
            internal_ip = next((ip for ip in ts_ips if ":" not in ip), "127.0.0.1")
            res = ingress.probe_routes(routes, pub, domain, with_public, internal_ip)
            for host, kinds in res.items():
                if not with_public and "public" in (prev.get(host) or {}):
                    kinds["public"] = prev[host]["public"]
                for kind, r in kinds.items():
                    if kind == "public" and not with_public:
                        continue
                    bad = not r.get("ok") or (r.get("code") or 0) >= 500 or bool(r.get("tls_error")) or bool(r.get("caddy_fallback"))
                    k = (host, kind)
                    self._streak[k] = self._streak.get(k, 0) + 1 if bad else 0
            for host, kinds in res.items():
                for kind, r in kinds.items():
                    r["fail_streak"] = self._streak.get((host, kind), 0)
            self._set(probes=res)
        self._guard("probes", probes)

        def hermes():
            if not config.HERMES_URL:
                self._set(hermes=None)
                return
            h = tailnet.hermes_status(config.HERMES_URL)
            self._hermes_streak = self._hermes_streak + 1 if h["status"] != "up" else 0
            h["bad_streak"] = self._hermes_streak
            self._set(hermes=h)
        self._guard("hermes", hermes)

    def _very_slow(self):
        def dns():
            with self.lock:
                routes = (self.raw.get("caddy") or {}).get("routes") or {}
            if routes:
                result = ingress.dns_lookup(sorted(routes))
                with self.lock:
                    if routes == ((self.raw.get("caddy") or {}).get("routes") or {}):
                        self.raw["dns"] = result
                        self._dirty = True
        self._guard("dns", dns)

        def http_ports():
            """직접 열린 포트가 HTTP 를 말하는지 한 번씩 확인한다(링크를 달기 위해)."""
            with self.lock:
                ls = list(self.raw.get("listeners") or [])
                prev = dict(self.raw.get("http_ports") or {})
            res = {}
            for l in ls:
                if l["proto"] != "tcp" or l["scope"] in ("loopback", "docker") or l["process"] in model.NON_HTTP_PROCS:
                    continue
                if l["owner_kind"] == "container" or l["port"] in res:
                    continue
                ip = "127.0.0.1" if l["scope"] == "all" else l["addrs"][0]
                r = http_probe(ip, "http", ip, l["port"], "/", 2)
                if not r["ok"] and "BadStatusLine" not in (r.get("error") or "") and "RemoteDisconnected" not in (
                        r.get("error") or ""):
                    res[l["port"]] = {"http": False}
                elif r["ok"]:
                    res[l["port"]] = {"http": True, "code": r["code"], "scheme": "http"}
                else:
                    r2 = http_probe(ip, "https", ip, l["port"], "/", 2)
                    res[l["port"]] = {"http": bool(r2["ok"]), "code": r2.get("code"), "scheme": "https"}
            with self.lock:
                old, current = _listener_shape(ls), _listener_shape(self.raw.get("listeners") or [])
                self.raw["http_ports"] = {p: v for p, v in res.items() if old.get(p) == current.get(p)}
                self._dirty = True
        self._guard("http_ports", http_ports)

    # ------------------------------------------------ 루프
    def _loop(self, name, interval, fn, first_delay=0.0):
        if first_delay:
            self.stop.wait(first_delay)
        while not self.stop.is_set():
            t0 = time.monotonic()
            try:
                fn()
            except Exception as e:
                self._fail(name, e)
            took = time.monotonic() - t0
            with self.lock:
                self.self_info["loops"][name] = {"last_ms": round(took * 1000), "last_at": time.time(),
                                                 "interval_s": interval}
            wake = {"slow": self._slow_wake, "dns": self._dns_wake}.get(name)
            if wake:
                wake.wait(max(0.5, interval - took))
                wake.clear()
            else:
                self.stop.wait(max(0.5, interval - took))

    def start(self):
        self.caddy_log.start(self.stop)
        # 첫 스냅샷이 비지 않게 한 바퀴를 동기로 돈다(빠른 것부터).
        self._guard("system_fast", self._fast)
        self._medium()
        threads = [
            ("fast", config.FAST_S, lambda: self._guard("system_fast", self._fast), config.FAST_S),
            ("medium", config.MEDIUM_S, self._medium, config.MEDIUM_S),
            ("slow", config.SLOW_S, self._slow, 0),
            ("dns", config.DNS_S, self._very_slow, 3),
            ("persist", 300, self._save_history, 300),
        ]
        for name, iv, fn, delay in threads:
            t = threading.Thread(target=self._loop, args=(name, iv, fn, delay), name=f"jarvis-{name}", daemon=True)
            t.start()

    # ------------------------------------------------ 접근 통제용
    def allowed_hosts(self):
        """Host 헤더로 받아 줄 이름: 이 기기의 IP·호스트명·tailnet 이름·모니터로 가는 Caddy 라우트."""
        now = time.time()
        cached = getattr(self, "_hosts", None)
        if cached and now - self._hosts_at < 30:
            return cached
        with self.lock:
            med = self.raw.get("medium") or {}
            ts = self.raw.get("tailscale") or {}
            routes = (self.raw.get("caddy") or {}).get("routes") or {}
        name = (self.sys.static.get("hostname") or "").lower()
        hs = {"localhost", "127.0.0.1", "::1", name, name + ".local"}
        for a in (med.get("iface_addrs") or {}).values():
            hs.update(a.get("ipv4") or [])
            hs.update(a.get("ipv6") or [])
        me = ts.get("self") or {}
        hs.update(me.get("ips") or [])
        if me.get("dns_name"):
            hs.add(me["dns_name"].lower())
        if me.get("hostname"):
            hs.add(me["hostname"].lower())
        for h, r in routes.items():
            if any(util.self_upstream(u, hs) for u in r.get("upstreams") or []):
                hs.add(h.lower())
        hs.update(config.EXTRA_HOSTS)
        hs.discard("")
        self._hosts, self._hosts_at = hs, now
        return hs

    def caddy_ips(self):
        with self.lock:
            cs = self.raw.get("containers") or []
        return {ip for c in cs if c["name"] == config.CADDY_CONTAINER for ip in c.get("ips") or []}

    def tailnet_v4(self):
        with self.lock:
            ips = ((self.raw.get("tailscale") or {}).get("self") or {}).get("ips") or []
        return next((ip for ip in ips if ":" not in ip), None)

    # ------------------------------------------------ 스냅샷
    def snapshot(self):
        """(json bytes, gzip bytes). 1초 이내 재요청은 캐시."""
        with self.lock:
            fresh = self._snap is not None and (not self._dirty or time.time() - self._snap_at < 1.0)
            if fresh:
                return self._snap, self._snap_gz
            raw = dict(self.raw)
            raw["errors"] = dict(self.raw["errors"])
            raw["collected_at"] = dict(self.raw["collected_at"])
            raw["prev_alert_keys"] = set(self._alert_since)
            raw["self"] = {**self.self_info, "loops": {k: dict(v) for k, v in self.self_info["loops"].items()}}
            spark = list(self.fine)[-SPARK_POINTS:]
            self._dirty = False
        t_build = time.monotonic()
        snap = model.build(raw)
        now = time.time()
        live = set()
        for a in snap["alerts"]:
            a["since"] = self._alert_since.setdefault(a["key"], now)
            live.add(a["key"])
        for k in list(self._alert_since):
            if k not in live:
                del self._alert_since[k]
        snap["spark"] = {"interval_s": HIST_FINE_S, "t": [p["t"] for p in spark],
                         **{k: [p.get(k) for p in spark] for k in HIST_KEYS}}
        body = json.dumps(snap, ensure_ascii=False, separators=(",", ":"), default=str).encode()
        gz = gzip.compress(body, 5)
        with self.lock:
            self._snap, self._snap_gz, self._snap_at = body, gz, time.time()
            self.self_info["snapshot_ms"] = round((time.monotonic() - t_build) * 1000)
            self.self_info["snapshot_bytes"] = len(body)
            self.self_info["snapshot_gz_bytes"] = len(gz)
        return body, gz

    def history(self, rng):
        with self.lock:
            pts = list(self.fine) if rng == "1h" else list(self.coarse)
        iv = HIST_FINE_S if rng == "1h" else HIST_COARSE_S
        return {"range": rng, "interval_s": iv, "t": [p["t"] for p in pts],
                **{k: [p.get(k) for p in pts] for k in HIST_KEYS}}

    # ------------------------------------------------ 히스토리 보존
    def _hist_path(self):
        return os.path.join(config.STATE_DIR, "history.json")

    def _save_history(self):
        try:
            os.makedirs(config.STATE_DIR, exist_ok=True)
            with self.lock:
                data = {"coarse": list(self.coarse), "fine": list(self.fine)}
            tmp = self._hist_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._hist_path())
        except OSError as e:
            print(f"[jarvis] 히스토리 저장 실패: {e}", flush=True)

    def _load_history(self):
        paths = [self._hist_path()]
        data = {}
        for p in paths:
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                break
            except (OSError, ValueError):
                continue
        now = time.time()
        for p in data.get("coarse") or []:
            if p.get("t", 0) > now - 86400:
                self.coarse.append(p)
        for p in data.get("fine") or []:
            if p.get("t", 0) > now - 3600:
                self.fine.append(p)

    def shutdown(self):
        self.stop.set()
        self._slow_wake.set()
        self._dns_wake.set()
        self.caddy_log.reconnect()
        self._save_history()
        self._guard("inventory", self.journal.save)
