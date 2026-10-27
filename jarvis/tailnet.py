"""Tailscale 상태와 Hermes 게이트웨이."""

import json
import time
import urllib.request

from . import config
from .util import PROBE_UA, parse_iso, run


LAN_MEMO_S = 1800   # 유휴가 된 피어의 마지막 LAN 주소를 이만큼 기억한다
_lan_memo = {}      # peer id → (ip, 마지막으로 본 시각)


def tailscale_status():
    rc, out, err = run([config.TAILSCALE_BIN, "status", "--json"], timeout=8)
    if rc != 0:
        return {"backend_state": "Error", "error": (err or out).strip()[:200]}
    d = json.loads(out)
    now = time.time()
    s = d.get("Self") or {}

    def peer(p):
        last = parse_iso(p.get("LastSeen"))
        cur = p.get("CurAddr") or ""
        ip_now = cur.rsplit(":", 1)[0].strip("[]") if cur else ""
        if ip_now:
            _lan_memo[p.get("ID")] = (ip_now, now)
        memo = _lan_memo.get(p.get("ID"))
        lan_ip_last = memo[0] if memo and now - memo[1] < LAN_MEMO_S else None
        return {
            "id": p.get("ID"),
            "hostname": p.get("HostName"),
            "dns_name": (p.get("DNSName") or "").rstrip("."),
            "dns_short": (p.get("DNSName") or "").split(".")[0] or p.get("HostName"),
            "created": parse_iso(p.get("Created")),
            "ip": (p.get("TailscaleIPs") or [None])[0],
            "ips": p.get("TailscaleIPs") or [],
            "os": p.get("OS"),
            "online": bool(p.get("Online")),
            "active": bool(p.get("Active")),
            "last_seen": last,
            "last_seen_ago_s": round(now - last) if last else None,
            "connection": "direct" if cur else ("relay" if p.get("Relay") and p.get("Active") else None),
            "cur_addr": cur or None,
            "lan_ip_last": lan_ip_last,  # 지금 경로가 없어도 최근(30분) 직결했던 주소 — 같은 기기 판정용
            "relay": p.get("Relay") or None,
            "rx_bytes": p.get("RxBytes", 0),
            "tx_bytes": p.get("TxBytes", 0),
            "tags": p.get("Tags") or [],
            "exit_node": bool(p.get("ExitNode")),
            "exit_node_option": bool(p.get("ExitNodeOption")),
            "key_expiry": parse_iso(p.get("KeyExpiry")),
        }

    # ShareeNode: 다른 tailnet 에서 공유받은 노드. 내 기기가 아니므로 뺀다.
    raw = [p for p in (d.get("Peer") or {}).values() if not p.get("ShareeNode")]
    peers = [peer(p) for p in raw]
    peers.sort(key=lambda p: (not p["online"], p["hostname"] or ""))
    rc, sv, _ = run([config.TAILSCALE_BIN, "serve", "status", "--json"], timeout=6)
    try:
        serve = json.loads(sv) if rc == 0 and sv.strip() else {}
    except ValueError:
        serve = {}
    funnel = any(bool(v) for v in (serve.get("AllowFunnel") or {}).values())
    return {
        "backend_state": d.get("BackendState"),
        "version": (d.get("Version") or "").split("-")[0] or None,
        "tailnet": (d.get("CurrentTailnet") or {}).get("Name"),
        "magic_dns_suffix": d.get("MagicDNSSuffix"),
        "health": d.get("Health") or [],
        "self": {
            "hostname": s.get("HostName"),
            "dns_name": (s.get("DNSName") or "").rstrip("."),
            "ips": s.get("TailscaleIPs") or [],
            "os": s.get("OS"),
            "online": bool(s.get("Online")),
            "relay": s.get("Relay"),
            "rx_bytes": s.get("RxBytes", 0),
            "tx_bytes": s.get("TxBytes", 0),
            "key_expiry": parse_iso(s.get("KeyExpiry")),
            "exit_node_option": bool(s.get("ExitNodeOption")),
        },
        "serve": serve or None,
        "funnel": funnel,
        "peers": peers,
        "peers_online": sum(p["online"] for p in peers),
        "peers_total": len(peers),
    }


def hermes_judge(d):
    """Hermes /api/status 응답 → (status, reason). 게이트웨이가 멈췄으면 down, 어댑터(텔레그램 봇·Slack·webhook·api_server)
    하나라도 연결이 아니거나 needs_attention 이면 degraded."""
    if not d.get("gateway_running"):
        return "down", f"게이트웨이 {d.get('gateway_state') or '멈춤'}"
    bad = sorted(k for k, p in (d.get("gateway_platforms") or {}).items()
                 if p.get("needs_attention") or p.get("state") != "connected")
    if bad:
        return "degraded", "어댑터 이상: " + ", ".join(bad[:6]) + (f" 외 {len(bad) - 6}" if len(bad) > 6 else "")
    return "up", None


def hermes_status(url):
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": PROBE_UA}), timeout=8) as r:
            d = json.load(r)
    except Exception as e:
        return {"url": url, "ok": False, "ms": None, "status": "down", "status_reason": f"응답 없음: {e}"[:160],
                "version": None, "gateway_state": None, "platforms": []}
    status, reason = hermes_judge(d)
    return {"url": url, "ok": True, "ms": round((time.monotonic() - t0) * 1000), "status": status,
            "status_reason": reason, "version": d.get("version"), "gateway_state": d.get("gateway_state"),
            "platforms": [{"name": k, "state": p.get("state"), "needs_attention": bool(p.get("needs_attention")),
                           "error_code": p.get("error_code")}
                          for k, p in sorted((d.get("gateway_platforms") or {}).items())]}
