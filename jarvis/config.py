"""설정. 우선순위: 환경 변수(JARVIS_*) > 기기별 설정 파일 > 아래 기본값.

설정 파일은 TOML: $JARVIS_CONFIG 또는 ~/.config/jarvis/config.toml. 키는 환경 변수 이름에서 접두어를 뗀 소문자
(JARVIS_INGRESS_DIR → ingress_dir). 목록은 TOML 배열이나 쉼표로 구분한 문자열. 예시는 deploy/pit5.toml·macstudio.toml.
"""

import os
import getpass
try:
    import pwd
except ImportError:  # Windows
    pwd = None
import re
import sys
import runpy

CONFIG_PATH = os.environ.get("JARVIS_CONFIG") or os.path.expanduser("~/.config/jarvis/config.toml")
CONFIG_SOURCE = CONFIG_PATH
CONFIG_ERROR = None  # 설정 파일을 못 읽었을 때의 이유(모니터 자기 진단에 올린다)
_FILE = {}
if os.path.exists(CONFIG_PATH):
    try:
        import tomllib  # 3.11+

        with open(CONFIG_PATH, "rb") as _f:
            _FILE = tomllib.load(_f)
    except ImportError:
        CONFIG_ERROR = f"{CONFIG_PATH} 를 읽으려면 Python 3.11 이상이 필요하다"
    except (OSError, ValueError) as _e:
        CONFIG_ERROR = f"{CONFIG_PATH}: {_e}"
else:
    CONFIG_PATH = None
if CONFIG_ERROR:
    print(f"[jarvis] 설정 파일 오류, 기본값으로 간다: {CONFIG_ERROR}", file=sys.stderr, flush=True)


def env(name, default=None):
    v = os.environ.get("JARVIS_" + name)
    if v is not None:
        return v
    v = _FILE.get(name.lower())
    return default if v is None else v


def env_list(name, default=()):
    v = env(name, list(default))
    items = v.split(",") if isinstance(v, str) else v
    return [str(x).strip() for x in items if str(x).strip()]


BIND = env("BIND", "0.0.0.0")
PORT = int(env("PORT", 9300))
STATIC_DIR = env("STATIC", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"))
STATE_DIR = os.path.expanduser(env("STATE", "~/.local/state/jarvis"))

# 인그레스(Caddy + Cloudflare Tunnel). 컨테이너가 없으면 해당 항목은 '없음'으로 보인다.
INGRESS_DIR = os.path.expanduser(env("INGRESS_DIR", ""))  # Caddyfile·create-public-tunnel.sh 가 있는 곳
CADDY_CONTAINER = env("CADDY", "caddy")
TUNNEL_CONTAINER = env("TUNNEL", "cloudflared")
TUNNEL_NAME = env("TUNNEL_NAME", "")
DOMAIN = env("DOMAIN", "")  # Caddy 라우트에서 도메인을 못 뽑을 때 쓸 값
CADDYFILE_CONTAINER_PATH = env("CADDYFILE_CONTAINER_PATH", "/etc/caddy/Caddyfile")
EXPECTED_SERVICES = env_list("EXPECTED_SERVICES")
EXPECTED_UNITS = env_list("EXPECTED_UNITS")

# Docker Engine(Linux)·OrbStack·Docker Desktop(macOS) 순으로 먼저 있는 소켓. 빈 값("")이면 Docker 가 없는 기기다(수집 안 함).
_DS = env("DOCKER_SOCK")
DOCKER_SOCK = _DS if _DS is not None else next(
    (p for p in ("/var/run/docker.sock", os.path.expanduser("~/.orbstack/run/docker.sock"),
                 os.path.expanduser("~/.docker/run/docker.sock")) if os.path.exists(p)), "/var/run/docker.sock")
USER_UID = int(env("USER_UID", os.getuid() if hasattr(os, "getuid") else 0))

# Hermes: 이름이 이 패턴에 맞는 이 기기의 컨테이너·호스트 서비스를 Hermes 카테고리로 묶는다.
HERMES_RE = re.compile(env("HERMES_RE", r"hermes"), re.I)
# Hermes 게이트웨이 상태 API(인증 없는 GET, 예: https://hermes.example.com/api/status). 빈 값이면 보지 않는다.
HERMES_URL = env("HERMES_URL", "")

# 프로브하지 않을 호스트(glob). 일부러 모든 비인증 요청을 거절하는 관리 평면(admin-gate 뒤의 *.admin.<도메인>)처럼
# 1분마다 찔러 봐야 거절 기록만 쌓이는 곳. 그 서비스 자체의 상태는 컨테이너 healthcheck 로 본다.
PROBE_SKIP = [h.lower() for h in env_list("PROBE_SKIP")]

# 이 기기 Orca 페어링 코드(GET /api/orca)를 받아 갈 core 기기의 tailnet IP. 루프백은 받지 않는다(core 가 자기 자신도
# 게이트웨이로 모으면 devices url 을 tailnet IP 로 적는다).
CORE = env_list("CORE")
# core 역할: 모을 게이트웨이 [{id, url}, …](설정 파일 전용). 있으면 core 기능이 켜진다. viewers 는 페어링 버튼(페어링 코드)을
# 누를 수 있는 tailnet IP 다(루프백은 받지 않는다).
DEVICES = _FILE.get("devices") or []
VIEWERS = env_list("VIEWERS")
# viewers 판정에서 X-Forwarded-For 를 믿을 리버스 프록시의 tailnet IP(도메인을 받아 이 core 로 넘기는 Caddy).
# 이 프록시를 장악하면 viewer 를 사칭할 수 있다. core 의 /api/orca 판정에는 쓰지 않는다.
PROXIES = env_list("PROXIES")
# 그 프록시가 X-Jarvis-Proxy-Key 로 붙이는 비밀(설정 파일은 0600). 비어 있으면 proxies 를 믿지 않는다.
PROXY_KEY = str(env("PROXY_KEY", ""))
# cswap(claude-swap)이 없고 Claude Code 가 있으면 uv/pipx 로 고정 버전을 설치한다(AI 사용량의 Claude 계정별 값). false 로 끈다
CSWAP_INSTALL = str(env("CSWAP_INSTALL", "true")).lower() not in ("0", "false", "no", "off")
# caam 이 없고 Codex CLI 가 있으면 고정 버전 릴리스(SHA256 확인)를 ~/.local/bin 에 둔다(AI 사용량의 Codex 계정별 값). false 로 끈다
CAAM_INSTALL = str(env("CAAM_INSTALL", "true")).lower() not in ("0", "false", "no", "off")

# Host 헤더 허용 목록에 더할 이름(이 기기의 IP·호스트명·tailnet 이름·모니터 라우트는 자동으로 들어간다).
EXTRA_HOSTS = [h.lower() for h in env_list("HOSTS")]

# ss -p 로 root 소유 리스너의 프로세스를 보려면 권한이 필요하다. pm2(일반 사용자)로 돌 때는
# CAP_SYS_PTRACE·CAP_DAC_READ_SEARCH 파일 능력을 준 ss 사본을 쓴다(README 의 설치 절차 참고).
SS_BIN = env("SS") or ("/usr/local/libexec/jarvis/ss" if os.access("/usr/local/libexec/jarvis/ss", os.X_OK) else "ss")
PM2_BIN = env("PM2") or next((p for p in ("/home/linuxbrew/.linuxbrew/bin/pm2", "/opt/homebrew/bin/pm2",
                                          "/usr/local/bin/pm2", "/usr/bin/pm2") if os.access(p, os.X_OK)), "pm2")
# macOS 앱(App Store·독립 설치)은 CLI 를 앱 번들 안에 둔다
TAILSCALE_BIN = env("TAILSCALE") or next((p for p in ("/usr/bin/tailscale", "/usr/local/bin/tailscale",
                                                      "/opt/homebrew/bin/tailscale",
                                                      "/Applications/Tailscale.app/Contents/MacOS/Tailscale")
                                          if os.access(p, os.X_OK)), "tailscale")
PM2_EVERY_S = 30

# 인프라 항목(model.infra_specs)의 서비스 관리자 이름. 빈 값이면 그 항목을 뺀다. 설정 파일 [infra_units] 로 덮어쓴다.
_USER = pwd.getpwuid(os.getuid()).pw_name if hasattr(os, "getuid") else getpass.getuser()  # POSIX 는 환경 변수가 아니라 uid 로
INFRA_UNITS = {"docker": "docker.service", "containerd": "containerd.service", "tailscale": "tailscaled.service",
               "ssh": "ssh.service", "pm2": f"pm2-{_USER}.service"}
if sys.platform == "win32":  # 서비스 관리자 수집이 없다
    INFRA_UNITS = {k: "" for k in INFRA_UNITS}
elif sys.platform == "darwin":  # Docker Desktop·OrbStack·Tailscale 앱은 launchd 데몬으로 보지 않는다
    INFRA_UNITS = {"docker": "", "containerd": "", "tailscale": "", "ssh": "com.openssh.sshd", "pm2": f"pm2.{_USER}"}
INFRA_UNITS.update({k: str(v) for k, v in (_FILE.get("infra_units") or {}).items() if k in INFRA_UNITS})
# 멈춰 있어도(inactive) 목록에 남겨 '다운'으로 보여야 하는 유닛
WATCH_UNITS = tuple(u for u in INFRA_UNITS.values() if u) + tuple(EXPECTED_UNITS)

# 수집 주기(초)
FAST_S = 2
MEDIUM_S = 10
SLOW_S = 60
DNS_S = 300

# 접근 허용 대역: 루프백, 사설망(LAN·도커), Tailscale CGNAT·ULA.
ALLOW_NETS = ",".join(env_list("ALLOW", (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fd7a:115c:a1e0::/48",
    "fe80::/10")))

# 서버의 bind·접근 통제·상태 저장 경로는 기존 리스너와 함께 재시작해야 바뀐다.
_LIVE_KEYS = ("INGRESS_DIR", "CADDY_CONTAINER", "TUNNEL_CONTAINER", "TUNNEL_NAME", "DOMAIN",
              "CADDYFILE_CONTAINER_PATH", "HERMES_RE", "HERMES_URL",
              "EXTRA_HOSTS", "INFRA_UNITS", "WATCH_UNITS", "EXPECTED_SERVICES", "EXPECTED_UNITS", "CORE",
              "VIEWERS", "PROXIES", "PROXY_KEY", "CSWAP_INSTALL", "CAAM_INSTALL")
_RESTART_KEYS = ("BIND", "PORT", "ALLOW_NETS", "STATIC_DIR", "STATE_DIR", "DOCKER_SOCK", "USER_UID",
                 "SS_BIN", "PM2_BIN", "TAILSCALE_BIN", "DEVICES")
CONFIG_RESTART_REQUIRED = []


def _stamp():
    try:
        s = os.stat(CONFIG_SOURCE)
        return s.st_ino, s.st_mtime_ns, s.st_size
    except FileNotFoundError:
        return None


_CONFIG_STAMP = _stamp()


def reload_if_changed():
    """10초 루프에서 호출한다. 잘못된 갱신은 마지막 정상 설정을 유지한다."""
    global _CONFIG_STAMP, CONFIG_ERROR, CONFIG_PATH, CONFIG_RESTART_REQUIRED, _FILE
    stamp = _stamp()
    if stamp == _CONFIG_STAMP:
        if CONFIG_ERROR:
            raise ValueError("Jarvis config unavailable; retaining last valid configuration")
        return set()
    _CONFIG_STAMP = stamp
    try:
        if stamp is None:
            raise ValueError("Jarvis config removed")
        candidate = runpy.run_path(__file__)
        if candidate["CONFIG_ERROR"]:
            raise ValueError("Invalid Jarvis configuration")
        for key in ("CADDY_CONTAINER", "TUNNEL_CONTAINER", "INGRESS_DIR", "DOMAIN", "CADDYFILE_CONTAINER_PATH", "HERMES_URL"):
            if not isinstance(candidate[key], str):
                raise ValueError("Invalid Jarvis configuration type")
        if not 1 <= candidate["PORT"] <= 65535:
            raise ValueError("Invalid Jarvis port")
    except Exception as e:
        CONFIG_ERROR = "Jarvis config reload failed; retaining last valid configuration"
        raise ValueError(CONFIG_ERROR) from e
    changed = {k for k in _LIVE_KEYS if globals()[k] != candidate[k]}
    CONFIG_RESTART_REQUIRED = [k for k in _RESTART_KEYS if globals()[k] != candidate[k]]
    globals().update({k: candidate[k] for k in _LIVE_KEYS})
    _FILE, CONFIG_PATH, CONFIG_ERROR = candidate["_FILE"], candidate["CONFIG_PATH"], None
    return changed
