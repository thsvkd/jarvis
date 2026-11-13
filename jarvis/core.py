"""core: 설정 devices 의 게이트웨이(각 기기의 jarvis)를 10초마다 pull 해서 모은다.

- 스냅샷은 마지막 성공본을 들고 있다가 /api/devices(요약)·/api/devices/<id>/snapshot(그대로) 로 준다.
- history·changes 는 크기가 커서 모아 두지 않고 요청이 올 때 전달한다.
- 사용량·계정 동작은 이메일·로그인 코드가 있어 모아 두지 않고 누를 때 그 기기 하나만 전달한다.
- 게이트웨이는 기기마다 pull 시점이 달라 버전이 섞인다: 모르는 필드는 무시하고, 없는 API(404)는 '미지원'으로 본다.
- SSH 로만 닿는 기기(devices 의 ssh)는 `ssh -W` 로 조회하고, AI 셋업 모듈과 jarvis 갱신은 원격 CLI 로, 사용량·로그인·계정
  조작은 원격 bridge(jarvis.ipc)로 그 게이트웨이에 전달한다(docs/SPEC.md 의 "SSH 기기"). 갱신 진행 조회만 501 이다.
"""

import gzip
import http.client
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor

from . import launch
from .cli_login import PASTE_RE

EVERY_S = 10
TIMEOUT_S = 5  # 연결·읽기 한 번
PULL_TIMEOUT_S = 150  # jarvis 갱신은 게이트웨이가 manifest(30초)와 묶음(60초, 소켓 동작마다)을 받는다
USAGE_TIMEOUT_S = 70  # 사용량 새로고침은 게이트웨이가 프로바이더 API 를 부른다(수집기 대기 55초)
LOGIN_TIMEOUT_S = 40  # 게이트웨이가 CLI 의 URL·코드를 30초까지 기다린다
SWITCH_TIMEOUT_S = 40  # 게이트웨이가 hswap claude switch 를 30초까지 기다린다
DEADLINE_S = 15  # 응답 하나 전체(조금씩 흘려 보내는 응답이 스레드를 붙잡지 못하게)
STALE_S = 3 * EVERY_S  # 이보다 오래된 스냅샷은 주지 않는다(기기 화면이 '응답 없음'을 띄우게)
MAX_BYTES = 16 << 20  # 압축을 푼 뒤 기준. 24h history 가 수 MB 다
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# SSH 기기. Homebrew openssh 는 ~/.ssh/config 의 UseKeychain 을 거절하므로 macOS 는 시스템 ssh 로 고정한다
SSH_BIN = "/usr/bin/ssh" if sys.platform == "darwin" else (shutil.which("ssh") or "ssh")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
            "-o", "LogLevel=ERROR"]
# 연결 공유는 조회에만. 조작이 공유하면 다른 호출의 시간 초과(프로세스 그룹 kill)가 진행 중인 apply 를 끊는다
SSH_MUX = [] if os.name == "nt" else ["-o", "ControlMaster=auto", "-o", "ControlPath=~/.ssh/jarvis-%C", "-o", "ControlPersist=60"]
SSH_NOMUX = ["-o", "ControlMaster=no", "-o", "ControlPath=none", "-a", "-x", "-o", "ClearAllForwardings=yes"]  # 사용자 ssh_config 의 에이전트·X11·포트 전달을 대상 기기로 넘기지 않는다
SSH_DEADLINE_S = DEADLINE_S + 10  # 조회 하나 전체(접속 포함)
SSH_PATH_RE = re.compile(r"/api/(snapshot|changes|history\?range=(1h|24h))")
ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
TOKEN_RE = re.compile(r"[A-Za-z0-9._-]+")
SSH_MIN = "1.7.8"  # 시험한 최저 판
BRIDGE_MIN = "1.13.0"  # 게이트웨이가 jarvis.ipc 를 여는 첫 판
SSH_ONLY = "SSH 로 닿는 기기는 이 조작을 지원하지 않습니다(그 기기에서 직접)"
NO_RESTART = "받았지만 재시작하지 못했습니다(그 기기에서 재시작하세요)"
SSH_EXIT = {90: "install.sh 배치(~/.local/share/jarvis/app 또는 ~/Workspace/jarvis, ~/.local/share/jarvis/venv)가 아닙니다",
            91: NO_RESTART}
# ponytail: install.sh 배치 고정. 수동 설치 기기가 생기면 devices 항목에 python·root 를 더한다
# 코드는 1.8.0 배치(app)를 먼저, 없으면 옛 배치·개발 체크아웃(~/Workspace/jarvis)을 쓴다
PRE = ('D="$HOME/.local/share/jarvis/app"; [ -f "$D/jarvis/__init__.py" ] || D="$HOME/Workspace/jarvis"\n'
       '[ -f "$D/jarvis/__init__.py" ] && cd "$D" && P="$HOME/.local/share/jarvis/venv/bin/python" && [ -x "$P" ] || exit 90\n'
       'PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"; export PATH\n'  # deploy/jarvis.plist
       'PYTHONIOENCODING=utf-8; export PYTHONIOENCODING\n')
# 사용량·로그인·계정 조작을 그 기기 게이트웨이의 IPC 로 나르는 원격 명령(jarvis/ipc.py). Windows 는 게이트웨이가 쓴 bridge.cmd
# POSIX 는 런처를 거친다: PRE 의 저장소는 다시 pull 하지 않으므로 받아 둔 릴리스의 코드로 넘어가야 한다
BRIDGE = "sh -c " + shlex.quote(PRE + 'exec "$P" -m jarvis bridge')
BRIDGE_WIN = 'cmd /c "%LOCALAPPDATA%\\jarvis\\bridge.cmd"'
VERSION_RE = re.compile(r"^jarvis (\d+\.\d+\.\d+)", re.M)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트는 따르지 않는다(HTTPError 가 된다). 따르면 장악된 게이트웨이가 core 를 루프백이나 다른 게이트웨이의
    core 전용 경로로 보내, 그 응답이 viewers 제한이 없는 changes·history·snapshot 경로로 새어 나간다."""

    def redirect_request(self, *a, **k):
        return None


# 환경 변수 http_proxy 가 있어도 tailnet 으로 바로 간다
_open = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect).open


def devices_from(items):
    """설정 devices = [{id, url} 또는 {id, ssh}, …] 중 형식이 맞는 것만. ssh 기기의 url 은 ssh://<별칭> 이다."""
    out, seen = [], set()
    for d in items if isinstance(items, list) else []:
        if not isinstance(d, dict):
            print(f"[jarvis] core: devices 항목은 {{id, url}} 테이블이어야 한다: {d!r}"[:300], flush=True)
            continue
        i, u, s = str(d.get("id") or ""), str(d.get("url") or "").rstrip("/"), d.get("ssh")
        if "ssh" in d:  # url 과 함께 쓰면 어느 쪽으로 닿을지 모호하다
            ok = "url" not in d and isinstance(s, str) and ALIAS_RE.fullmatch(s)
        else:
            ok = re.match(r"^https?://[^/?#]+$", u)
        if ID_RE.match(i) and ok and i not in seen:
            seen.add(i)
            out.append({"id": i, "url": "ssh://" + s, "ssh": s} if "ssh" in d else {"id": i, "url": u})
        else:
            print(f"[jarvis] core: 잘못된 devices 항목은 건너뛴다: {d!r}"[:300], flush=True)
    return out


def fetch(url):
    """(json bytes, gzip bytes 또는 None). 크기·시간 상한을 넘기면 예외."""
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with _open(req, timeout=TIMEOUT_S) as r:
        return _read(r, time.monotonic() + DEADLINE_S)


def _read(r, end):
    """HTTP 응답 r 의 본문을 (json bytes, gzip bytes 또는 None) 으로. 크기·시간 상한을 넘기면 예외."""
    gz = r.headers.get("Content-Encoding") == "gzip"
    chunks, size = [], 0
    while chunk := r.read(64 << 10):
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_BYTES:
            raise ValueError("응답이 너무 큽니다")
        if time.monotonic() > end:
            raise TimeoutError("응답이 너무 느립니다")
    body = b"".join(chunks)
    if not gz:
        return body, None
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = d.decompress(body, MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or d.unconsumed_tail:
        raise ValueError("응답이 너무 큽니다")
    if not d.eof:
        raise ValueError("gzip 응답이 잘렸습니다")
    return raw, body


def _kill(p):
    """ssh 를 프로세스 그룹째 끝낸다(ProxyJump 자식·파이프를 쥔 손자가 남지 않게)."""
    try:
        if os.name == "nt":
            p.kill()
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    p.wait()


def _last(b):
    return (b.decode("utf-8", "replace").strip().splitlines() or [""])[-1]


class _Pipe:
    """http.client.HTTPResponse 가 소켓 대신 ssh 의 표준 출력을 읽게 한다."""

    def __init__(self, f):
        self.f = f

    def makefile(self, *a, **k):
        return self.f


def ssh_fetch(alias, path):
    """조회: `ssh -W 127.0.0.1:9300` 의 표준 입출력으로 GET 하나. fetch 와 같은 (json bytes, gzip bytes 또는 None).
    실패하면 예외이고, 메시지는 ssh 표준 오류 마지막 줄이다. 원격 셸 명령은 없다."""
    if not SSH_PATH_RE.fullmatch(path):
        raise ValueError("SSH 로 보내지 않는 경로입니다")
    req = (f"GET {path} HTTP/1.0\r\nHost: 127.0.0.1:9300\r\nAccept-Encoding: gzip\r\n"
           "Connection: close\r\n\r\n").encode()
    status, headers, raw, gz = _ssh_http([*SSH_MUX, "-W", "127.0.0.1:9300", "--", alias], req, SSH_DEADLINE_S)
    if status != 200:
        raise urllib.error.HTTPError(path, status, "", headers, None)
    return raw, gz


def ssh_bridge(alias, remote, method, selector, headers, timeout):
    """조작: 원격 bridge(remote)의 표준 입력으로 HTTP 요청 하나. (상태, 본문 bytes). 연결을 공유하지 않는다.
    헤더 값은 호출자가 모양을 검사한 것만 넣는다(로그인 코드는 PASTE_RE). 실패는 ssh_fetch 와 같은 예외다."""
    if any(c in x for x in (method, selector, *(str(v) for h in headers for v in h)) for c in "\r\n\0"):
        raise ValueError("요청에 줄바꿈이 있습니다")  # IPC 는 core 로 믿으므로 헤더·요청을 끼워 넣지 못하게
    head = "".join(f"{k}: {v}\r\n" for k, v in headers)
    req = (f"{method} {selector} HTTP/1.0\r\nHost: 127.0.0.1:9300\r\nContent-Length: 0\r\n{head}"
           "Connection: close\r\n\r\n").encode()
    status, _, raw, _ = _ssh_http([*SSH_NOMUX, "-T", "--", alias, remote], req, timeout + 10)  # 10초는 접속
    return status, raw


def _ssh_http(args, request, deadline_s):
    """ssh <args> 의 표준 입력으로 request 를 쓰고 HTTP 응답을 읽는다. (상태, 헤더, json bytes, gzip bytes 또는 None)."""
    end = time.monotonic() + deadline_s
    with tempfile.TemporaryFile() as err:
        p = subprocess.Popen([SSH_BIN, *SSH_OPTS, *args], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=err, start_new_session=True)
        timer = threading.Timer(deadline_s, _kill, (p,))
        timer.start()
        r = None
        try:
            # 요청을 쓰자마자 입력을 닫는다(열어 두면 채널이 닫히지 않아 응답 끝을 기다리게 된다)
            p.stdin.write(request)
            p.stdin.close()
            r = http.client.HTTPResponse(_Pipe(p.stdout))
            r.begin()
            raw, gz = _read(r, end)  # 게이트웨이는 늘 Content-Length 를 붙이므로 그만큼만 읽는다
            return r.status, r.headers, raw, gz
        except Exception as e:
            _reap(p, 1)
            if time.monotonic() > end:
                raise TimeoutError("응답이 너무 느립니다") from None
            err.seek(0)
            last = _last(err.read())
            if last:
                raise RuntimeError(last[:200]) from None
            # 크기·시간 초과는 우리 문구다. 그 밖(BadStatusLine 등)의 메시지에는 원격이 보낸 바이트가 들어 있어 이름만
            raise (e if isinstance(e, (ValueError, TimeoutError)) else RuntimeError(type(e).__name__)) from None
        finally:
            timer.cancel()
            _reap(p, 2)
            if r:
                r.close()
            p.stdout.close()


def _reap(p, s):
    try:
        p.wait(s)
    except subprocess.TimeoutExpired:
        _kill(p)


def ssh_run(alias, script, timeout):
    """조작: 원격에서 `sh -c <script>` 하나. (종료 코드 또는 시간 초과면 None, 표준 출력, 표준 오류 마지막 줄).
    연결을 공유하지 않고, 표준 입력은 /dev/null 이다(물려받은 ssh 는 호출자의 입력을 먹는다)."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            p = subprocess.Popen([SSH_BIN, *SSH_OPTS, *SSH_NOMUX, "-T", "--", alias, "sh -c " + shlex.quote(script)],
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
        except OSError as e:  # ssh 실행 파일이 없는 등: 닿지 못한 것(255)과 같게 다룬다
            return 255, "", str(e)[:200]
        try:
            code = p.wait(timeout)
        except subprocess.TimeoutExpired:
            _kill(p)
            code = None
        out.seek(0)
        err.seek(max(0, os.fstat(err.fileno()).st_size - 4096))  # 원격 출력이 얼마든 읽는 양은 상한 안에서
        return code, out.read(MAX_BYTES).decode("utf-8", "replace"), _last(err.read())


def jarvis_line(*args, write=False):
    """원격 스크립트의 `python -m jarvis …` 한 줄(PRE 뒤에 붙인다). 하위 명령은 module·update·--version 만이고,
    모든 토큰은 끼우기 전에 다시 검사한다. 쓰기 동작은 원격 표준 오류를 버린다."""
    if (args[:1] not in (("module",), ("update",), ("--version",)) or not all(TOKEN_RE.fullmatch(a) for a in args)
            or args[:1] == ("module",) and args[2:3] not in (("status",), ("plan",), ("apply",))):  # uninstall 은 보내지 않는다
        raise ValueError("원격으로 보내지 않는 명령입니다")
    return '"$P" -m jarvis ' + " ".join(args) + " </dev/null" + (" 2>/dev/null" if write else "")


def why(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        e = e.reason
    return (str(e) or type(e).__name__)[:200]


def _summary(s):
    meta, summ, sysm = s.get("meta") or {}, s.get("summary") or {}, s.get("system") or {}
    root = next((d for m in ("/", "/System/Volumes/Data", "C:\\")  # macOS 는 사용자 데이터가 Data 볼륨, Windows 는 C:
                 for d in sysm.get("disks") or [] if isinstance(d, dict) and d.get("mount") == m), {})
    return {"host": meta.get("host"), "version": meta.get("version"), "commit": (s.get("monitor") or {}).get("commit"),
            "generated_at": meta.get("generated_at"), "os": (sysm.get("host") or {}).get("os"),
            "status": summ.get("status"), "alerts": summ.get("alerts"), "services": summ.get("services"),
            "model": (sysm.get("host") or {}).get("model"), "cpu_pct": (sysm.get("cpu") or {}).get("usage_pct"),
            "mem_pct": (sysm.get("memory") or {}).get("usage_pct"), "disk_pct": root.get("usage_pct")}


class Core:
    def __init__(self, devices):
        self.devices = {d["id"]: d for d in devices}
        self.state = {i: {"ok_at": None, "error": None, "fails": 0, "summary": None, "snap": None} for i in self.devices}
        self.lock = threading.Lock()
        self.org_names = {}  # org_key → (등록 시각, 이름). 기기 하나만 새로고침해도 같은 이름을 보이려고 들고 있다
        self.pool = ThreadPoolExecutor(max_workers=max(1, min(16, len(devices))), thread_name_prefix="jarvis-core")
        self._stop = threading.Event()
        self._busy = set()  # 지난 주기의 요청이 아직 끝나지 않은 기기(느린 기기가 다른 기기의 주기를 늦추지 않게)
        # SSH 기기의 relay(history·changes)는 요청마다 ssh 를 띄우므로 기기마다 4개까지만
        self._relays = {i: threading.BoundedSemaphore(4) for i, d in self.devices.items() if d.get("ssh")}

    def start(self):
        threading.Thread(target=self._loop, name="jarvis-core", daemon=True).start()

    def shutdown(self):
        self._stop.set()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self):
        while not self._stop.is_set():
            for i in self.devices:
                with self.lock:
                    if i in self._busy:
                        continue
                    self._busy.add(i)
                self.pool.submit(self._poll_done, i)
            self._stop.wait(EVERY_S)

    def _poll_done(self, i):
        try:
            self.poll(i)
        finally:
            with self.lock:
                self._busy.discard(i)

    def poll(self, i):
        try:
            raw, gz = self._get(i, "/api/snapshot")
            summary = _summary(json.loads(raw))
        except Exception as e:  # 한 기기의 실패가 루프를 멈추지 않게
            with self.lock:
                st = self.state[i]
                st["error"], st["fails"] = why(e), st["fails"] + 1
            return
        with self.lock:
            self.state[i].update(ok_at=time.time(), error=None, fails=0, summary=summary,
                                 snap=(raw, gz or gzip.compress(raw, 5)))

    def has(self, i):
        return i in self.devices

    def list(self):
        with self.lock:
            rows = [{"id": i, "url": self.devices[i]["url"], "reachable": st["fails"] == 0 and st["ok_at"] is not None,
                     "ok_at": st["ok_at"], "error": st["error"], "fails": st["fails"], **(st["summary"] or {})}
                    for i, st in self.state.items()]
        return {"generated_at": time.time(), "devices": rows}

    def snapshot(self, i):
        """(json bytes, gzip bytes) 또는 (None, 이유). 오래된 스냅샷은 주지 않는다."""
        with self.lock:
            st = self.state[i]
            if st["snap"] and time.time() - st["ok_at"] <= STALE_S:
                return st["snap"]
            return None, st["error"] or "아직 받은 스냅샷이 없습니다"

    def _get(self, i, path):
        d = self.devices[i]
        return ssh_fetch(d["ssh"], path) if d.get("ssh") else fetch(d["url"] + path)

    def relay(self, i, path):
        """history·changes: 그 게이트웨이 응답(json bytes)을 그대로."""
        sem = self._relays.get(i)
        if sem is None:
            return self._get(i, path)[0]
        if not sem.acquire(blocking=False):
            raise RuntimeError("이 기기로 가는 SSH 요청이 너무 많습니다")
        try:
            return self._get(i, path)[0]
        finally:
            sem.release()

    def ssh_gate(self, i, least=SSH_MIN):
        """SSH 조작 전에 스냅샷 판을 본다. (http 상태, 오류) 또는 None(통과). ssh 는 실행하지 않는다."""
        with self.lock:
            s = self.state[i]["summary"]
        if not s:
            return 502, {"error": f"{i} 의 스냅샷이 아직 없습니다"}
        k = launch.key(s.get("version"))
        if k is None or k < launch.key(least):
            return 501, {"error": f"이 기기의 jarvis {s.get('version')} 는 SSH 조작을 지원하지 않습니다(그 기기에서 직접 갱신)"}
        return None

    def ssh(self, i, script, timeout):
        """원격 조작 하나의 결과를 게이트웨이 modules_api.run() 과 같은 (종료 코드, dict) 로. 시간 초과면 코드는 None 이다."""
        code, out, err = ssh_run(self.devices[i]["ssh"], script, timeout)
        failed = self._ssh_failed(i, code, err, timeout)
        if failed:
            return code, failed
        line = next((x for x in reversed(out.splitlines()) if x.startswith("{")), "")  # 앞의 rc 배너는 건너뛴다
        try:
            body = json.loads(line)
            if isinstance(body, dict):
                return code, body
        except ValueError:
            pass
        if err:
            print(f"[jarvis] core: {i} 원격 명령 종료 코드 {code}, {err[:300]}", flush=True)
        return code or 1, {"error": f"원격 명령이 결과 없이 끝났습니다(종료 코드 {code}). core 로그를 보세요"}

    def _ssh_failed(self, i, code, err, timeout):
        """ssh·배치·시간 초과 실패의 오류 응답. 아니면 None. 표준 오류는 ssh 실패(255)일 때만 응답에 넣는다."""
        if code is None:
            return {"error": f"{timeout}초 안에 끝나지 않았습니다"}
        if code == 255:
            return {"error": f"{i} 에 닿지 못했습니다: {err[:200]}"}
        return {"error": SSH_EXIT[code]} if code in SSH_EXIT else None

    def win_ssh(self, i):
        """Windows SSH 기기: POSIX 셸이 없어 모듈·갱신 조작도 원격 명령 대신 bridge 로 게이트웨이 API 에 보낸다."""
        with self.lock:
            return bool(self.devices[i].get("ssh")) and \
                str((self.state[i]["summary"] or {}).get("os") or "").startswith("Windows")

    def _ask(self, i, req, timeout, unsupported):
        """그 게이트웨이의 동작 하나를 그대로 전달한다. (http 상태, 응답). 404 는 갱신 전 게이트웨이(501).
        SSH 기기는 원격 bridge 로 그 게이트웨이의 IPC 에 보낸다(비밀이 루프백 TCP·원격 명령줄을 지나지 않는다)."""
        d = self.devices[i]
        try:
            if d.get("ssh"):
                bad = self.ssh_gate(i, BRIDGE_MIN)
                if bad:
                    return bad
                status, raw = ssh_bridge(d["ssh"], BRIDGE_WIN if self.win_ssh(i) else BRIDGE, req.get_method(), req.selector,
                                         req.header_items(), timeout)
            else:
                with _open(req, timeout=timeout) as r:
                    return r.status, json.loads(r.read(1 << 20))
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read(1 << 16)
        except Exception as e:
            return 502, {"error": f"{i} 에 닿지 못했습니다: {why(e)}"}
        if status == 404:
            return 501, {"error": f"이 기기의 jarvis 는 {unsupported} 지원하지 않습니다(갱신 필요)"}
        try:
            return status, json.loads(raw)
        except ValueError:
            return (502, {"error": f"{i} 에 닿지 못했습니다: 응답이 JSON 이 아닙니다"}) if status < 400 else (status, {"error": f"HTTP {status}"})

    def jarvis_update(self, i):
        """jarvis 업데이트 버튼: 그 게이트웨이 POST /api/jarvis/update. 받은 코드가 다르면 게이트웨이가 스스로 재시작한다."""
        if self.devices[i].get("ssh") and not self.win_ssh(i):
            return self._ssh_update(i)
        req = urllib.request.Request(self.devices[i]["url"] + "/api/jarvis/update", data=b"", method="POST",
                                     headers={"X-Jarvis-Action": "jarvis-update"})
        return self._ask(i, req, PULL_TIMEOUT_S, "자체 갱신을")

    def jarvis_update_progress(self, i):
        """그 게이트웨이 GET /api/jarvis/update: 갱신 단계와 받은 바이트. POSIX SSH 기기는 원격 CLI 로 갱신해 진행을 모른다(501)."""
        if self.devices[i].get("ssh") and not self.win_ssh(i):
            return 501, {"error": SSH_ONLY}
        req = urllib.request.Request(self.devices[i]["url"] + "/api/jarvis/update")
        return self._ask(i, req, 5, "갱신 진행을")

    def _ssh_update(self, i):
        """SSH 기기: 원격 `update && --version` 뒤, 새 판이면 스냅샷의 게이트웨이 pid 에 SIGTERM 을 보낸다.
        다시 띄우는 것은 서비스 관리자(launchd KeepAlive, systemd Restart=always)다."""
        from . import modules_api  # 그 기기의 SSH 작업은 modules_api 가 들고 있다(core 자신의 갱신도 그것을 본다)
        bad = self.ssh_gate(i)
        if bad:
            return bad
        with modules_api._job_lock:
            if (modules_api.ssh_jobs.get(i) or {}).get("state") == "running":
                return 409, {"error": "모듈 적용이 끝난 뒤에 갱신하세요"}
            if i in modules_api.ssh_updating:
                return 409, {"error": "이미 갱신하고 있습니다"}
            modules_api.ssh_updating.add(i)
        try:
            script = PRE + jarvis_line("update", write=True) + " && " + jarvis_line("--version")
            code, out, err = ssh_run(self.devices[i]["ssh"], script, PULL_TIMEOUT_S)
            failed = self._ssh_failed(i, code, err, PULL_TIMEOUT_S)
            if failed:
                return 502, failed
            found = VERSION_RE.findall(out)
            if code != 0 or not found:
                return 502, {"error": (out.strip().splitlines() or [f"종료 코드 {code}"])[-1][:200]}
            with self.lock:
                running = self.state[i]["summary"]["version"]
            to = found[-1]
            if launch.key(to) <= launch.key(running):
                return 200, {"running": running, "to": to, "restart": False}
            raw = self.snapshot(i)[0]
            try:
                pid = (json.loads(raw).get("monitor") or {}).get("pid") if raw else None
            except (ValueError, AttributeError):
                pid = None
            if not (type(pid) is int and pid > 1):  # bool·0·1·음수(kill -1 은 그 사용자의 모든 프로세스다)는 보내지 않는다
                return 502, {"error": NO_RESTART}
            # 그 pid 가 게이트웨이 명령줄일 때만 보낸다(스냅샷 뒤 pid 가 재사용됐거나 다른 프로세스가 9300 을 잡은 경우)
            code, _, _ = ssh_run(self.devices[i]["ssh"],
                                 f'ps -o command= -p {pid} | grep -q -- "-m jarvis$" && kill -TERM {pid} || exit 91', 30)
            if code != 0:
                return 502, {"error": NO_RESTART}
            print(f"[jarvis] core: {i} jarvis {running} -> {to}, 재시작 신호를 보냈다", flush=True)
            return 200, {"running": running, "to": to, "restart": True}
        finally:
            with modules_api._job_lock:
                modules_api.ssh_updating.discard(i)

    def jarvis_update_all(self):
        """기기마다 갱신한다. core 자신은 다른 기기가 다 끝난 뒤에 한다(먼저 재시작하면 이 요청이 끊긴다).
        아직 요약을 못 받은 기기도 core 자신일 수 있어 마지막에 둔다."""
        me = socket.gethostname().split(".")[0].lower()
        with self.lock:
            mine = {i for i, st in self.state.items()
                    if ((st["summary"] or {}).get("host") or me).split(".")[0].lower() == me}

        def one(i):
            with self.lock:
                err = self.state[i]["fails"] and self.state[i]["error"]
            # 폴링이 실패 중인 기기(꺼진 tailnet 피어)는 묻지 않는다. 물으면 PULL_TIMEOUT_S 를 기다려 응답 전체가 늦는다
            code, body = (502, {"error": f"{i} 에 닿지 못했습니다: {err}"}) if err else self.jarvis_update(i)
            return {"id": i, "status": code, **(body if isinstance(body, dict) else {"error": str(body)})}

        others = [i for i in self.devices if i not in mine]
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(others)))) as pool:
            out = list(pool.map(one, others))
        return out + [one(i) for i in self.devices if i in mine]

    def usage(self, i, force=False):
        """AI 구독 사용량 한 기기: 화면으로 보내는 값이다."""
        code, body = self._usage(i, force)
        self._same_org_names([body])
        return code, body

    def _same_org_names(self, bodies):
        """같은 조직(org_key)의 이름을 가장 늦게 등록된 슬롯의 것으로 맞춘다: hswap 은 이름을 등록할 때만 저장해 개명 뒤 기기마다 다르다.
        org_key·org_at 은 여기서 지워 화면으로 내보내지 않는다(USAGE-1)."""
        items = lambda v: v if isinstance(v, list) else []
        rows = [a for b in bodies if isinstance(b, dict) for p in items(b.get("providers")) if isinstance(p, dict)
                for a in items(p.get("accounts")) if isinstance(a, dict)]
        with self.lock:
            for a in rows:
                key, at, name = a.get("org_key"), a.get("org_at"), a.get("org")
                if isinstance(key, str) and isinstance(name, str):
                    seen = (at if isinstance(at, (int, float)) and not isinstance(at, bool) and 0 <= at < 1e11 else 0, name)
                    if key not in self.org_names or seen > self.org_names[key]:
                        self.org_names[key] = seen
            for a in rows:
                key = a.pop("org_key", None)
                a.pop("org_at", None)
                if isinstance(key, str) and key in self.org_names:
                    a["org"] = self.org_names[key][1]

    def _usage(self, i, force=False):
        """그 게이트웨이 GET /api/usage/independent. 모아 두지 않는다(계정 이메일이 들어 있다)."""
        req = urllib.request.Request(self.devices[i]["url"] + "/api/usage/independent" + ("?refresh=1" if force else ""))
        return self._ask(i, req, USAGE_TIMEOUT_S, "사용량 조회를")

    def usage_login(self, i, provider, code=None, add=False):
        """다시 로그인 버튼: 그 게이트웨이 POST /api/usage/login 을 그대로 전달한다(로그인 URL·일회용 코드).
        code 가 있으면 붙여 넣는 로그인(Claude)의 인증 코드를 헤더로 넘긴다(본문은 받지 않는다)."""
        headers = {"X-Jarvis-Action": "usage-login"}
        if code is not None:
            if not PASTE_RE.fullmatch(code):
                return 400, {"error": "인증 코드의 모양이 맞지 않습니다"}
            headers["X-Jarvis-Login-Code"] = code
        req = urllib.request.Request(self.devices[i]["url"] + "/api/usage/login?provider=" + urllib.parse.quote(provider) + ("&mode=add" if add else ""),
                                     data=b"", method="POST", headers=headers)
        return self._ask(i, req, LOGIN_TIMEOUT_S, "다시 로그인을")

    def usage_login_status(self, i, provider):
        """다시 로그인 창이 완료를 알아채도록 그 게이트웨이 GET /api/usage/login 을 전달한다."""
        req = urllib.request.Request(self.devices[i]["url"] + "/api/usage/login?provider=" + urllib.parse.quote(provider))
        return self._ask(i, req, TIMEOUT_S, "다시 로그인을")

    def usage_switch(self, i, slot):
        """이 계정 사용 버튼: 그 게이트웨이 POST /api/usage/account 를 그대로 전달한다(Claude 의 hswap 슬롯)."""
        req = urllib.request.Request(self.devices[i]["url"] + "/api/usage/account?slot=" + urllib.parse.quote(slot),
                                     data=b"", method="POST", headers={"X-Jarvis-Action": "usage-account"})
        return self._ask(i, req, SWITCH_TIMEOUT_S, "계정 전환을")

    def usage_remove(self, i, slot):
        """계정 삭제 버튼: 그 게이트웨이 POST /api/usage/account/remove 를 그대로 전달한다(Claude 의 hswap 슬롯)."""
        req = urllib.request.Request(self.devices[i]["url"] + "/api/usage/account/remove?slot=" + urllib.parse.quote(slot),
                                     data=b"", method="POST", headers={"X-Jarvis-Action": "usage-account"})
        return self._ask(i, req, SWITCH_TIMEOUT_S, "계정 삭제를")

    def usage_all(self, force=False):
        """모든 기기의 사용량을 동시에 묻는다. 한 대가 느리거나 실패해도 나머지는 그대로 준다."""
        ids = list(self.devices)

        def one(i):
            with self.lock:
                host = (self.state[i]["summary"] or {}).get("host")
                err = self.state[i]["fails"] and self.state[i]["error"]
            # 폴링이 실패 중인 기기(꺼진 tailnet 피어)는 묻지 않는다. 물으면 70초를 기다려 섹션 전체가 늦게 뜬다
            code, body = (502, {"error": f"{i} 에 닿지 못했습니다: {err}"}) if err else self._usage(i, force)
            return {**(body if isinstance(body, dict) else {"error": str(body)}), "id": i, "host": host, "status": code}

        with ThreadPoolExecutor(max_workers=max(1, min(16, len(ids)))) as pool:
            devices = list(pool.map(one, ids))
        self._same_org_names(devices)
        return {"generated_at": time.time(), "devices": devices}
