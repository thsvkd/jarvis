"""core 전용: 다른 기기의 Caddy 라우트가 이 기기의 포트로 들어오는 길(MAP-6).

게이트웨이는 자기 기기만 안다. 예를 들어 s2 Caddy 는 127.0.0.1:28899 로 보내고, s2 의 ssh -L 이 그 포트를
macmini 127.0.0.1:8899 로 넘긴다. macmini 는 이 사실을 모른다. core 가 모은 스냅샷을 맞대어 이 길을 만든다.
기기는 url 이 아니라 각 스냅샷의 tailnet 이름·IP(와 설정 id·ssh 별칭)로 맞춘다. SSH 기기의 url 은 ssh://<별칭> 이다.
"""
import json
import time

LOOP = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}


def _aliases(dev_id, dev_cfg, snap):
    me = ((snap.get("tailscale") or {}).get("self")) or {}
    dns = (me.get("dns_name") or "").rstrip(".")
    names = {dev_id, dev_cfg.get("ssh"), (snap.get("meta") or {}).get("host"), me.get("hostname"), dns,
             *(me.get("ips") or [])}
    out = set()
    for n in filter(None, names):
        n = str(n).lower()
        out.add(n)
        if not n.replace(".", "").isdigit():
            out.add(n.split(".")[0])  # thsvkdui-macstudio.taile69c22.ts.net → thsvkdui-macstudio
    return out


def _split(upstream):
    host, _, port = str(upstream).strip().rpartition(":")
    return host.strip("[]").lower(), int(port) if port.isdigit() else None


# 컨테이너 대신 포트를 여는 프로세스(macOS 의 host 네트워크 컨테이너). 포트만으로는 어느 컨테이너인지 모른다.
PROXY_OWNERS = {"OrbStack Helper", "com.docker.backend", "vpnkit-bridge"}


def _owner(snap, port, host):
    """(그 기기에서 port 를 연 서비스 id, 추정 여부). 포트 주인이 OrbStack 같은 대리 프로세스면
    주소 첫 칸(yt.hermes.example → yt)이 이름에 든 가장 짧은 컨테이너로 추정한다."""
    svcs = [sv for sv in snap.get("services") or [] if not sv["id"].startswith("route:")]
    for sv in svcs:
        if any(l.get("proto") == "tcp" and l.get("port") == port for l in sv.get("listeners") or []):
            if sv.get("name") in PROXY_OWNERS and host:
                label = str(host).split(".")[0].lower()
                cands = [c for c in svcs if (c.get("runtime") or {}).get("type") == "container"
                         and label and label in str(c.get("name") or "").lower()]
                if cands:
                    return min(cands, key=lambda c: len(c["name"]))["id"], True
            return sv["id"], False
    return None, False


def inbound_paths(target, devices, snaps):
    """devices: {id: 설정 항목}, snaps: {id: 스냅샷 dict}. target 으로 들어오는 다른 기기의 길 목록."""
    if target not in snaps:
        return []
    # 두 기기 이상이 같은 이름을 가지면(예: OS 호스트 이름 raspberrypi 둘) 그 이름으로는 맞추지 않는다
    owners = {}
    for i, s in snaps.items():
        for a in _aliases(i, devices.get(i) or {}, s):
            owners.setdefault(a, set()).add(i)
    who = {a: next(iter(o)) for a, o in owners.items() if len(o) == 1}
    out = []
    for src, s in snaps.items():
        if src == target:
            continue
        tun = {t.get("port"): t for t in s.get("ssh_tunnels") or []}
        status = {sv["id"]: sv.get("status") for sv in s.get("services") or []}
        for r in ((s.get("ingress") or {}).get("caddy") or {}).get("routes") or []:
            for up in r.get("upstreams") or []:
                host, port = _split(up)
                if port is None:
                    continue
                if host in LOOP and port in tun:
                    t = tun[port]
                    if who.get(str(t.get("host")).lower()) != target:
                        continue
                    via, to_port = "ssh", t.get("to_port")
                elif who.get(host) == target or who.get(host.split(".")[0]) == target:
                    via, to_port = "tailnet", port
                else:
                    continue
                sid, guess = _owner(snaps[target], to_port, r.get("host"))
                out.append({"from": src, "host": r.get("host"), "public": bool(r.get("public")), "via": via,
                            "port": port if via == "ssh" else None, "to_port": to_port,
                            "service_id": sid, "guess": guess,
                            "status": status.get("route:" + str(r.get("host"))) or "unknown"})
    return sorted(out, key=lambda p: (p["from"], p["host"] or ""))


def inbound(core, target):
    snaps = {}
    for i in core.devices:
        raw = core.snapshot(i)[0]
        if raw:
            try:
                snaps[i] = json.loads(raw)
            except ValueError:
                pass
    return {"generated_at": time.time(), "device": target, "paths": inbound_paths(target, core.devices, snaps)}
