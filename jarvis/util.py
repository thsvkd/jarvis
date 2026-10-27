"""파일·명령·도커 소켓·HTTP 프로브 공용 도구."""

import hashlib
import http.client
import ipaddress
import tempfile
import json
import os
import re
import socket
import ssl
import subprocess
import time
from datetime import datetime

from . import config


def read(path, default=None):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def read_int(path, default=None):
    s = read(path)
    if s is None:
        return default
    try:
        return int(s.strip())
    except ValueError:
        return default


# 프로브 User-Agent. pit5 Caddyfile 의 log_skip @jarvis_probe 가 이 접두어로 모니터 요청을 거른다.
PROBE_UA = "jarvis/1"

_ENV = dict(os.environ)
_ENV["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/homebrew/bin:" + _ENV.get("PATH", "")
_ENV.setdefault("XDG_RUNTIME_DIR", f"/run/user/{config.USER_UID}")
_ENV["LC_ALL"] = "C"
# Windows: 창 없는 pythonw 가 콘솔 프로그램을 부를 때마다 콘솔 창이 떴다 사라지지 않게 한다.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(args, timeout=6, sudo=False):
    """(returncode, stdout, stderr). 실패해도 예외를 올리지 않는다."""
    if sudo:
        args = ["sudo", "-n"] + list(args)
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=_ENV, errors="replace",
                           creationflags=_NO_WINDOW)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


class UnixConn(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def docker_api(path, timeout=6):
    conn = UnixConn(config.DOCKER_SOCK, timeout)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read()
        if r.status >= 400:
            raise RuntimeError(f"docker {path} -> HTTP {r.status}")
        return json.loads(body)
    finally:
        conn.close()


def parse_iso(ts):
    """도커·Go 형식 RFC3339(나노초 포함)을 epoch 초로. 0001-01-01 은 None."""
    if not ts or ts.startswith("0001-"):
        return None
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)?", ts)
    if not m:
        return None
    base, frac, tz = m.group(1), m.group(2) or "", m.group(3) or "Z"
    frac = (frac + "000000")[:7] if frac else ""
    tz = "+00:00" if tz == "Z" else tz
    try:
        return datetime.fromisoformat(base + frac + tz).timestamp()
    except ValueError:
        return None


class Rate:
    """누적 카운터의 초당 변화량."""

    def __init__(self):
        self.prev = {}

    def per_s(self, key, value, now):
        prev = self.prev.get(key)
        self.prev[key] = (value, now)
        if prev is None:
            return None
        dv, dt = value - prev[0], now - prev[1]
        if dt <= 0 or dv < 0:
            return None
        return dv / dt

    def prune(self, keep):
        for k in list(self.prev):
            if k not in keep:
                del self.prev[k]


TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def ip_obj(s):
    try:
        return ipaddress.ip_address(s.split("%")[0])
    except ValueError:
        return None


def is_tailnet_ip(s):
    ip = ip_obj(s)
    return bool(ip) and (ip in TAILNET_V4 if ip.version == 4 else ip in TAILNET_V6)


def self_upstream(u, own):
    """Caddy upstream(host:port)이 이 기기의 jarvis 인가. 다른 기기의 :9300(예: pit5 Caddy → s2 core)은 아니다."""
    host, _, port = (u or "").rpartition(":")
    host = host.strip("[]")
    return port == str(config.PORT) and (host in ("host.docker.internal", "localhost") or host.startswith("127.") or host in own)


def client_kind(ip_s):
    """접속 주체 분류: tailnet / lan / docker / local / public."""
    ip = ip_obj(ip_s or "")
    if ip is None:
        return "unknown"
    if ip.is_loopback:
        return "local"
    if is_tailnet_ip(ip_s):
        return "tailnet"
    if ip.version == 4 and ip in ipaddress.ip_network("172.16.0.0/12"):
        return "docker"
    if ip.is_private:
        return "lan"
    return "public"


class _PinnedHTTPS(http.client.HTTPSConnection):
    """SNI·Host 는 도메인으로, TCP 는 지정 IP 로 붙는다(curl --resolve 와 같음)."""

    def __init__(self, host, ip, port, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


_VERIFY = ssl.create_default_context()
_NOVERIFY = ssl.create_default_context()
_NOVERIFY.check_hostname = False
_NOVERIFY.verify_mode = ssl.CERT_NONE


def _cert_info(cert):
    if not cert:
        return None

    def rdn(seq):
        out = {}
        for group in seq or ():
            for k, v in group:
                out[k] = v
        return out

    not_after = ssl.cert_time_to_seconds(cert["notAfter"]) if cert.get("notAfter") else None
    not_before = ssl.cert_time_to_seconds(cert["notBefore"]) if cert.get("notBefore") else None
    issuer = rdn(cert.get("issuer"))
    return {
        "subject": rdn(cert.get("subject")).get("commonName"),
        "issuer": issuer.get("organizationName") or issuer.get("commonName"),
        "issuer_cn": issuer.get("commonName"),
        "sans": [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"],
        "serial": cert.get("serialNumber"),
        "not_before": not_before,
        "not_after": not_after,
        "days_left": round((not_after - time.time()) / 86400, 1) if not_after else None,
    }


def _decode_der(der):
    """검증을 끈 연결에서는 getpeercert() 가 비므로 DER 을 직접 디코드한다(만료·발급자 확인용)."""
    if not der:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=True) as f:
        f.write(ssl.DER_cert_to_PEM_cert(der))
        f.flush()
        return ssl._ssl._test_decode_cert(f.name)


def http_probe(url_host, scheme="https", ip=None, port=None, path="/", timeout=6, host_header=None):
    """GET 한 번. {ok, code, ms, error, cert, server, cf_ray}.
    ok 는 '서버가 HTTP 로 응답했다'는 뜻이고, 상태 판정은 호출하는 쪽이 code 로 한다."""
    port = port or (443 if scheme == "https" else 80)
    t0 = time.monotonic()
    res = {"ok": False, "code": None, "ms": None, "error": None}
    conn = None
    try:
        if not ip:
            # tailnet 노드의 IPv6 가 443 을 안 열어 둔 경우가 있어 IPv4 를 먼저 쓴다(curl 의 happy eyeballs 대용).
            infos = socket.getaddrinfo(url_host, port, type=socket.SOCK_STREAM)
            v4 = [i[4][0] for i in infos if i[0] == socket.AF_INET]
            ip = (v4 or [infos[0][4][0]])[0]
            res["ip"] = ip
        if scheme == "https":
            ctx = _VERIFY
            try:
                if ip:
                    conn = _PinnedHTTPS(url_host, ip, port, timeout, ctx)
                else:
                    conn = http.client.HTTPSConnection(url_host, port, timeout=timeout, context=ctx)
                conn.connect()
            except ssl.SSLCertVerificationError as e:
                res["tls_error"] = str(e.verify_message or e)[:160]
                conn = (
                    _PinnedHTTPS(url_host, ip, port, timeout, _NOVERIFY)
                    if ip
                    else http.client.HTTPSConnection(url_host, port, timeout=timeout, context=_NOVERIFY)
                )
                conn.connect()
            try:
                res["cert"] = _cert_info(conn.sock.getpeercert()) or _cert_info(_decode_der(
                    conn.sock.getpeercert(binary_form=True)))
            except Exception:
                res["cert"] = None
        else:
            conn = http.client.HTTPConnection(ip or url_host, port, timeout=timeout)
        conn.request(
            "GET",
            path,
            headers={
                "Host": host_header or url_host,
                "User-Agent": PROBE_UA + " (+probe)",
                "Accept": "*/*",
                "Connection": "close",
            },
        )
        r = conn.getresponse()
        res["ms"] = round((time.monotonic() - t0) * 1000, 1)
        res["code"] = r.status
        res["ok"] = True
        res["server"] = r.getheader("Server")
        if r.getheader("CF-RAY"):
            res["cf_ray"] = r.getheader("CF-RAY")
        body = r.read(2048)
        res["body_hash"] = hashlib.sha1(body).hexdigest()[:16]
        if r.status == 404 and "등록되지 않은 서비스".encode() in body:
            res["caddy_fallback"] = True
        drained = len(body)
        while drained < (1 << 20):
            chunk = r.read(65536)
            if not chunk:
                break
            drained += len(chunk)
    except Exception as e:  # 연결 거부·타임아웃·TLS 실패 모두 여기로 온다
        res["ms"] = round((time.monotonic() - t0) * 1000, 1)
        res["error"] = f"{type(e).__name__}: {e}"[:200]
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return res


def fetch_text(url, timeout=4, headers=None):
    import urllib.request

    req = urllib.request.Request(url, headers=headers or {"User-Agent": PROBE_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")
