"""HTTP 서버: /api/* JSON 과 static/ 정적 파일.

접근 통제(인증이 없으므로 여기가 전부다):
- 접속 IP: 루프백·사설망·tailnet 만. 도커 대역(172.16/12)은 Caddy 컨테이너에서 온 요청만, 그것도
  X-Forwarded-For 의 원래 클라이언트가 허용 대역일 때만 받는다(인터넷을 향한 다른 컨테이너 차단).
- Host 헤더: 이 기기의 주소·이름만(DNS 리바인딩 차단).
- Cloudflare 가 붙이는 헤더가 있으면 거부(터널로 잘못 노출돼도 열리지 않게).
"""

import gzip
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import posixpath
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__, config, orca, orca_issue, orca_update, orca_usage, self_update
from .core import why
from .util import client_kind

ALLOW = [ipaddress.ip_network(n.strip()) for n in config.ALLOW_NETS.split(",") if n.strip()]
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".jpg": "image/jpeg", ".webp": "image/webp", ".woff2": "font/woff2", ".ico": "image/x-icon",
         ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8"}
COMPRESSIBLE = (".html", ".js", ".mjs", ".css", ".json", ".svg", ".txt", ".md")
# 본체(Atlas)는 인라인 스크립트·이벤트 핸들러를 허용하지 않는다(주입된 문자열이 실행될 길을 막는다).
# 스타일은 SVG·차트가 style 속성을 쓰므로 인라인을 허용한다.
CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
       "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
# 보관용 시안(/d/*)·갤러리(/gallery/)는 만들 때의 느슨한 정책을 그대로 쓴다.
CSP_ARCHIVE = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
               "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
               "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; img-src 'self' data:; "
               "connect-src 'self'; object-src 'none'; frame-ancestors 'self'; base-uri 'self'; form-action 'none'")
ARCHIVE_PREFIXES = ("/d/", "/gallery/")
MAX_CONNS = 48
PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP", "Via", "X-Forwarded-Host", "X-Forwarded-Proto",
                 "Tailscale-User-Login", "Tailscale-Funnel-Request")


def allowed(addr):
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip.version == n.version and ip in n for n in ALLOW)


def _host_only(h):
    h = (h or "").strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else ""
    if h.count(":") == 1:
        h = h.rsplit(":", 1)[0]
    return h.rstrip(".")


def make_handler(collector, core=None):
    static_root = os.path.realpath(config.STATIC_DIR)

    class Handler(BaseHTTPRequestHandler):
        server_version = f"jarvis/{__version__}"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = 15  # 느린 클라이언트가 스레드를 붙잡지 못하게

        def log_message(self, fmt, *args):  # API 폴링으로 저널이 넘치지 않게 오류만 남긴다
            pass

        def log_error(self, fmt, *args):
            if fmt.startswith("Request timed out"):
                return  # 유휴 keep-alive 연결(주로 Caddy)이 소켓 타임아웃으로 닫힌 것 — 정상
            msg = fmt % tuple(repr(a) if isinstance(a, str) else a for a in args)
            print(f"[jarvis] {self.client_address[0]} {msg}"[:400], flush=True)

        def _send(self, code, body, ctype, extra=None, head=False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            path = urlsplit(self.path).path
            self.send_header("Content-Security-Policy", CSP_ARCHIVE if path.startswith(ARCHIVE_PREFIXES) else CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _text(self, code, msg, head=False):
            return self._send(code, (msg + "\n").encode(), "text/plain; charset=utf-8", head=head)

        def _json(self, obj, head=False, code=200):
            body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
            extra = {"Cache-Control": "no-store"}
            if self._gz_ok() and len(body) > 1024:
                body = gzip.compress(body, 5)
                extra["Content-Encoding"] = "gzip"
                extra["Vary"] = "Accept-Encoding"
            self._send(code, body, "application/json; charset=utf-8", extra, head)

        def _gz_ok(self):
            return "gzip" in (self.headers.get("Accept-Encoding") or "")

        def _peer_ok(self):
            peer = self.client_address[0]
            if not allowed(peer):
                return False
            if client_kind(peer) == "docker":
                if peer not in collector.caddy_ips():
                    return False
                # 마지막 항목이 Caddy 가 직접 붙인 주소다. 앞쪽은 클라이언트가 보낸 값일 수 있다(trusted_proxies 설정 시).
                last = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
                return bool(last) and allowed(last) and client_kind(last) in ("tailnet", "lan", "local")
            return True

        def _trusted(self, ips, proxies=()):
            """Orca 페어링 코드는 셸 권한이다: 목록의 tailnet IP 에만 준다. 게이트웨이 /api/orca 는 설정 core,
            core 의 페어링 버튼은 설정 viewers 가 목록이다. 루프백은 받지 않는다(같은 기기의 컨테이너·프록시·다른 사용자도
            루프백으로 온다). tailnet 은 WireGuard 가 출발지를 인증한다. LAN IP 는 목록에 적어도 받지 않는다(가로채기·평문).
            프록시 헤더가 (빈 값으로라도) 있으면 받지 않는다: host 네트워크 Caddy 가 tailnet IP 로 넘겨 주면 출발지가 core 가 된다.
            core 기기 자체는 전부 믿는다(그 기기의 어떤 프로세스든 tailnet 으로 나가면 출발지가 core IP 다).
            예외는 proxies(설정, 예: 도메인을 받는 pit5 Caddy 의 tailnet IP)뿐이다: 그 프록시가 붙인 X-Forwarded-For 의
            마지막 항목을 원래 접속 IP 로 본다(Caddy 는 마지막 항목에 자기가 본 접속 IP 를 둔다). 그 기기의 다른 컨테이너도
            같은 출발지 IP 로 나가므로 Caddy 만 아는 proxy_key 헤더(X-Jarvis-Proxy-Key)도 맞아야 한다."""
            peer = self.client_address[0]  # 서버는 IPv4 로만 bind 한다
            if client_kind(peer) != "tailnet":
                return False
            if peer in proxies:
                key = self.headers.get("X-Jarvis-Proxy-Key") or ""
                if not config.PROXY_KEY or not hmac.compare_digest(key.encode(), config.PROXY_KEY.encode()):
                    return False
                xff = self.headers.get_all("X-Forwarded-For") or []
                if len(xff) != 1:  # 없거나 여러 줄이면 어느 것이 프록시가 붙인 값인지 모른다
                    return False
                last = xff[0].split(",")[-1].strip()
                return client_kind(last) == "tailnet" and last in ips and last not in proxies
            if any(h in self.headers for h in PROXY_HEADERS):
                return False
            return peer in ips

        def _usage_force(self, u):
            """core 의 ?refresh=1 은 게이트웨이마다 프로바이더 API 를 부른다. 맞춤 헤더를 요구해 다른 사이트가 이미지·링크로
            대신 부르지 못하게 한다(헤더가 있는 교차 출처 요청은 preflight 가 필요하고 이 서버는 OPTIONS 에 답하지 않는다).
            헤더 없는 refresh 는 None."""
            if parse_qs(u.query).get("refresh") != ["1"]:
                return False
            return True if self.headers.get("X-Jarvis-Action") == "usage-refresh" else None

        def _device(self, path, u, head):
            """core: /api/devices/<id>/(snapshot|history|changes|orca|usage)."""
            parts = path.split("/")
            if len(parts) != 5 or not core.has(parts[3]):
                return self._json({"error": "not found"}, head, 404)
            dev, what = parts[3], parts[4]
            if what == "snapshot":
                snap = core.snapshot(dev)
                if snap[0] is None:
                    return self._json({"error": f"{dev} 응답 없음: {snap[1]}"}, head, 503)
                gz = self._gz_ok()
                extra = {"Cache-Control": "no-store", **({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"} if gz else {})}
                return self._send(200, snap[1] if gz else snap[0], "application/json; charset=utf-8", extra, head)
            try:
                if what == "orca":
                    if not self._trusted(config.VIEWERS, config.PROXIES):
                        return self._json({"error": "페어링 코드는 허용된 기기(viewers)에서만 볼 수 있습니다"}, head, 403)
                    return self._json(core.orca(dev), head)
                if what == "usage":
                    force = self._usage_force(u)
                    if force is None:
                        return self._json({"error": "X-Jarvis-Action 헤더가 필요합니다"}, head, 403)
                    code, body = core.usage(dev, force)
                    return self._json(body, head, code)
                if what == "history":
                    rng = (parse_qs(u.query).get("range") or ["1h"])[0]
                    if rng not in ("1h", "24h"):
                        return self._json({"error": "range 는 1h 또는 24h"}, head, 400)
                    body = core.relay(dev, "/api/history?range=" + rng)
                elif what == "changes":
                    body = core.relay(dev, "/api/changes")
                else:
                    return self._json({"error": "not found"}, head, 404)
            except Exception as e:
                return self._json({"error": f"{dev} 에서 받지 못했습니다: {why(e)}"}, head, 502)
            extra = {"Cache-Control": "no-store"}
            if self._gz_ok() and len(body) > 1024:
                body = gzip.compress(body, 5)
                extra.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
            return self._send(200, body, "application/json; charset=utf-8", extra, head)

        def do_HEAD(self):
            self._route(head=True)

        def do_GET(self):
            self._route(head=False)

        def do_POST(self):
            # 쓰기 동작은 Orca 코드 발급과 Orca 업데이트뿐이다. 본문·네트워크·Host 검사는 _route 가 GET 과 똑같이 먼저 한다.
            self._route(head=False, post=True)

        def _route(self, head, post=False):
            # GET·HEAD 본문은 읽지 않는다. 그대로 두면 keep-alive 연결에서 본문이 다음 요청으로 파싱된다(프록시 뒤 요청 밀수)
            if "Transfer-Encoding" in self.headers or any(v.strip() != "0" for v in self.headers.get_all("Content-Length") or []):
                self.close_connection = True
                return self._send(400, "요청 본문은 받지 않습니다\n".encode(), "text/plain; charset=utf-8",
                                  {"Connection": "close"}, head)
            if not self._peer_ok():
                return self._text(403, "허용되지 않은 네트워크입니다 (LAN·tailnet 전용)", head)
            if self.headers.get("Cf-Ray") or self.headers.get("Cf-Connecting-Ip"):
                return self._text(403, "Cloudflare 경유 요청은 받지 않습니다", head)
            if _host_only(self.headers.get("Host")) not in collector.allowed_hosts():
                return self._text(421, "misdirected request", head)
            # 맞춤 헤더를 요구해 다른 사이트가 브라우저로 대신 보내지 못하게 한다(맞춤 헤더가 있으면 교차 출처 요청은
            # preflight 가 필요한데, 이 서버는 OPTIONS 에 답하지 않는다).
            if post and self.headers.get("X-Jarvis-Action") not in ("orca-issue", "orca-update", "jarvis-update"):
                return self._json({"error": "X-Jarvis-Action 헤더가 필요합니다"}, head, 403)
            u = urlsplit(self.path)
            path = unquote(u.path)
            # core 는 다른 망의 기기까지 모아 준다: LAN 에 그 기기들의 명령줄·포트·피어를 넘기지 않는다
            if core and path.startswith("/api/devices") and client_kind(self.client_address[0]) not in ("tailnet", "local"):
                return self._json({"error": "기기 목록은 tailnet 에서만 볼 수 있습니다"}, head, 403)
            try:
                if post:
                    scope = (parse_qs(u.query).get("scope") or [""])[0]
                    if path == "/api/orca/issue":
                        if not self._trusted(config.CORE):
                            return self._json({"error": "core 로 지정한 기기만 부를 수 있습니다"}, head, 403)
                        st = orca.status(collector.tailnet_v4())
                        port = int((st.get("endpoint") or ":6768").rsplit(":", 1)[1])
                        code, body = orca_issue.start(scope, collector.tailnet_v4(), port)
                        return self._json(body, head, code)
                    if core and path.startswith("/api/devices/") and path.endswith("/orca/issue"):
                        dev = path.split("/")[3]
                        if path.count("/") != 5 or not core.has(dev):
                            return self._json({"error": "not found"}, head, 404)
                        if not self._trusted(config.VIEWERS, config.PROXIES):
                            return self._json({"error": "페어링 코드는 허용된 기기(viewers)에서만 발급할 수 있습니다"}, head, 403)
                        code, body = core.orca_issue(dev, scope)
                        return self._json(body, head, code)
                    if path == "/api/orca/update":
                        if not self._trusted(config.CORE):
                            return self._json({"error": "core 로 지정한 기기만 부를 수 있습니다"}, head, 403)
                        code, body = orca_update.start()
                        return self._json(body, head, code)
                    if path == "/api/jarvis/update":
                        if not self._trusted(config.CORE):
                            return self._json({"error": "core 로 지정한 기기만 부를 수 있습니다"}, head, 403)
                        code, body = self_update.start()
                        if body.get("restart"):  # 응답을 보낸 뒤 끝낸다(shutdown 은 이 요청 스레드에서 부르면 안 된다)
                            threading.Thread(target=self_update.restart, args=(self.server,), daemon=True).start()
                        return self._json(body, head, code)
                    if core and path == "/api/devices/jarvis/update":
                        if not self._trusted(config.VIEWERS, config.PROXIES):
                            return self._json({"error": "업데이트는 허용된 기기(viewers)에서만 할 수 있습니다"}, head, 403)
                        return self._json({"devices": core.jarvis_update_all()}, head, 200)
                    if core and path.startswith("/api/devices/") and path.endswith("/jarvis/update"):
                        dev = path.split("/")[3]
                        if path.count("/") != 5 or not core.has(dev):
                            return self._json({"error": "not found"}, head, 404)
                        if not self._trusted(config.VIEWERS, config.PROXIES):
                            return self._json({"error": "업데이트는 허용된 기기(viewers)에서만 할 수 있습니다"}, head, 403)
                        code, body = core.jarvis_update(dev)
                        return self._json(body, head, code)
                    if core and path == "/api/devices/orca/update":
                        if not self._trusted(config.VIEWERS, config.PROXIES):
                            return self._json({"error": "업데이트는 허용된 기기(viewers)에서만 할 수 있습니다"}, head, 403)
                        return self._json({"devices": core.orca_update_all()}, head, 200)
                    if core and path.startswith("/api/devices/") and path.endswith("/orca/update"):
                        dev = path.split("/")[3]
                        if path.count("/") != 5 or not core.has(dev):
                            return self._json({"error": "not found"}, head, 404)
                        if not self._trusted(config.VIEWERS, config.PROXIES):
                            return self._json({"error": "업데이트는 허용된 기기(viewers)에서만 할 수 있습니다"}, head, 403)
                        code, body = core.orca_update(dev)
                        return self._json(body, head, code)
                    return self._json({"error": "not found"}, head, 404)
                if path == "/api/snapshot":
                    body, gz = collector.snapshot()
                    extra = {"Cache-Control": "no-store"}
                    if self._gz_ok():
                        body = gz
                        extra["Content-Encoding"] = "gzip"
                        extra["Vary"] = "Accept-Encoding"
                    return self._send(200, body, "application/json; charset=utf-8", extra, head)
                if path == "/api/history":
                    rng = (parse_qs(u.query).get("range") or ["1h"])[0]
                    if rng not in ("1h", "24h"):
                        return self._json({"error": "range 는 1h 또는 24h"}, head, 400)
                    return self._json(collector.history(rng), head)
                if path == "/api/changes":
                    return self._json({"changes": collector.journal.events()}, head)
                if path == "/api/orca/update":
                    if not self._trusted(config.CORE):
                        return self._json({"error": "core 로 지정한 기기만 받을 수 있습니다"}, head, 403)
                    return self._json(orca_update.check(), head)
                if path == "/api/orca/usage":
                    if not self._trusted(config.CORE):
                        return self._json({"error": "core 로 지정한 기기만 받을 수 있습니다"}, head, 403)
                    code, body = orca_usage.read(force=parse_qs(u.query).get("refresh") == ["1"])
                    return self._json(body, head, code)
                if path == "/api/orca":
                    if not self._trusted(config.CORE):
                        return self._json({"error": "core 로 지정한 기기만 받을 수 있습니다"}, head, 403)
                    st = orca.status(collector.tailnet_v4())
                    return self._json({**st, "issuable": orca_issue.issuable(st), "issue": orca_issue.job,
                                       "update": orca_update.snapshot()}, head)
                if core and path.startswith("/api/devices/") and path.endswith("/orca/update"):
                    dev = path.split("/")[3]
                    if path.count("/") != 5 or not core.has(dev):
                        return self._json({"error": "not found"}, head, 404)
                    if not self._trusted(config.VIEWERS, config.PROXIES):
                        return self._json({"error": "업데이트 확인은 허용된 기기(viewers)에서만 할 수 있습니다"}, head, 403)
                    code, body = core.orca_update_check(dev)
                    return self._json(body, head, code)
                if core and path == "/api/devices":
                    return self._json(core.list(), head)
                if core and path == "/api/devices/usage":
                    force = self._usage_force(u)
                    if force is None:
                        return self._json({"error": "X-Jarvis-Action 헤더가 필요합니다"}, head, 403)
                    return self._json(core.usage_all(force), head)
                if core and path.startswith("/api/devices/"):
                    return self._device(path, u, head)
                if path == "/api/health":
                    return self._json({"ok": True, "version": __version__}, head)
                if path.startswith("/api/"):
                    return self._json({"error": "not found"}, head, 404)
                return self._static(path, u, head)
            except ConnectionError:  # 클라이언트가 먼저 끊었다(Windows 는 WinError 10053 도)
                pass
            except Exception as e:  # 서버가 죽지 않게 요청 단위로 막는다
                self.log_error("요청 처리 실패 %s: %s", path, str(e))
                try:
                    self._json({"error": "internal error"}, head, 500)
                except Exception:
                    pass

        def _static(self, path, u, head):
            # 제어 문자·역슬래시·인코딩된 슬래시는 정상 경로에 나올 일이 없다(헤더 주입·오픈 리다이렉트 차단).
            if any(c < " " or c in "\x7f\\" for c in path) or "%2f" in u.path.lower() or "%5c" in u.path.lower():
                return self._text(400, "bad path", head)
            if any(seg.startswith(".") and seg not in (".", "..") for seg in path.split("/")):
                return self._text(404, "not found", head)  # 점 파일·디렉터리(.git, .omc 등)는 서빙하지 않는다
            norm = posixpath.normpath(path)
            full = os.path.realpath(os.path.join(static_root, norm.lstrip("/")))
            if not (full == static_root or full.startswith(static_root + os.sep)):
                return self._text(404, "not found", head)
            if os.path.isdir(full):
                if not path.endswith("/"):
                    rel = os.path.relpath(full, static_root)
                    loc = "/" if rel == "." else "/" + quote(rel.replace(os.sep, "/")) + "/"
                    q = u.query if all(c >= " " for c in u.query) else ""
                    return self._send(301, b"", "text/plain", {"Location": loc + ("?" + q if q else "")}, head)
                full = os.path.join(full, "index.html")
            if not os.path.isfile(full) and norm == "/favicon.ico":
                full = os.path.join(static_root, "shared", "favicon.svg")
                if not os.path.isfile(full):
                    return self._send(204, b"", "image/x-icon", head=head)
            if not os.path.isfile(full):
                return self._text(404, "페이지가 없습니다", head)
            ext = os.path.splitext(full)[1].lower()
            ctype = TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
            st = os.stat(full)
            etag = '"' + hashlib.md5(f"{st.st_mtime_ns}-{st.st_size}".encode()).hexdigest()[:16] + '"'
            extra = {"Cache-Control": "no-cache", "ETag": etag}
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                for k, v in extra.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            with open(full, "rb") as f:
                body = f.read()
            if ext in COMPRESSIBLE and self._gz_ok() and len(body) > 1024:
                body = gzip.compress(body, 6)
                extra["Content-Encoding"] = "gzip"
                extra["Vary"] = "Accept-Encoding"
            self._send(200, body, ctype, extra, head)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"  # Windows 의 SO_REUSEADDR 는 다른 프로세스가 같은 포트를 함께 bind 하게 한다
    request_queue_size = 64

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._slots = threading.BoundedSemaphore(MAX_CONNS)

    def handle_error(self, request, client_address):
        # 클라이언트가 먼저 끊은 연결(브라우저 탭 닫기·core 의 시간 초과 등)은 정상이다. Windows 는 이것이
        # WinError 10053/10054 로 와서 매번 traceback 이 로그에 쌓였다. 그 밖의 예외만 기본 처리(traceback)로.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    def verify_request(self, request, client_address):
        # 스레드를 만들기 전에 거른다: 허용 대역 밖이거나 동시 연결이 넘치면 바로 닫는다.
        return allowed(client_address[0]) and self._slots.acquire(blocking=False)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def serve(collector, core=None):
    httpd = Server((config.BIND, config.PORT), make_handler(collector, core))
    print(f"[jarvis] {config.BIND}:{config.PORT} 에서 대기 (static={config.STATIC_DIR}, "
          f"설정={config.CONFIG_PATH or '없음(기본값)'})", flush=True)
    return httpd
