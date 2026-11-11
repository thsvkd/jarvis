"""AI 사용량 화면의 '다시 로그인': 이 기기에서 `<cli> login --device-auth` 를 띄우고, 출력의 로그인 URL·일회용 코드를 준다.
사람이 아무 기기의 브라우저로 그 URL 을 열어 코드를 넣으면 CLI 가 자격을 저장하고 끝난다.
Claude 는 device-code 가 없다: `claude auth login` 이 준 URL 로 로그인한 사람이 받은 인증 코드를 화면에 붙여 넣으면
submit() 이 그 CLI 의 표준 입력으로 넘기고, 끝나면 `hswap claude add` 로 슬롯에 저장한다.
"""
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time

from . import caam_usage, claude_accounts
from .util import _ENV, _NO_WINDOW, run, which_local

WAIT_S = 30  # URL·코드가 나올 때까지
STATUS_S = 6  # codex login status 는 보통 0.1초 안에 답한다(늦으면 알 수 없음으로 본다)
LIFE_S = 16 * 60  # 코드는 15분 유효하다. 그 뒤에도 남아 있으면 끝낸다
URL_RE = re.compile(r"https://\S+")
CODE_RE = re.compile(r"^\s*([A-Z0-9]{4}-[A-Z0-9]{4,5})\s*$", re.M)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PASTE_RE = re.compile(r"[A-Za-z0-9_#.~-]{8,256}")  # Claude 인증 코드(`코드#상태`). 헤더로 오가므로 이 글자만 받는다

_lock = threading.Lock()
_jobs = {}  # provider → {"proc", "out", "url", "code", "started_at"}


def _grok_home():
    """grok 홈 디렉터리(공식 `GROK_HOME`, 없으면 ~/.grok). 자격과 CLI 를 같은 곳에서 찾는다.

    `GROK_HOME` 을 재정의한 기기에서 `~/.grok` 만 보면 자격을 없는 쪽으로 오판해
    '로그인' 버튼이 멀쩡한 자격을 지운다(공식 Settings 문서: `$GROK_HOME` 가 홈 디렉터리).
    """
    return os.environ.get("GROK_HOME") or os.path.join(os.path.expanduser("~"), ".grok")


def _grok():
    p = os.path.join(_grok_home(), "bin", "grok" + (".exe" if sys.platform == "win32" else ""))
    return which_local("grok") or (p if os.path.isfile(p) else None)


CLIS = {"codex": caam_usage._codex_cli, "grok": _grok, "claude": lambda: which_local("claude")}
ARGS = {"claude": ["auth", "login", "--claudeai"]}  # 그 밖은 login --device-auth
PASTE = {"claude"}  # 코드를 화면에서 CLI 로 붙여 넣는 프로바이더(출력에 코드가 없다)
HSWAP_ADD = "0.2.8"  # `hswap <하네스> add --login --json`(JSON Lines 이벤트)을 아는 hswap
# hswap add --login 의 error.type → 화면 문구. message 원문은 내보내지 않는다
ADD_ERRORS = {"login_failed": "로그인이 끝나지 않았습니다", "wrong_account": "다른 계정으로 로그인했습니다",
              "unsupported": "이 하네스는 hswap 으로 계정을 추가하지 못합니다", "not_installed": "하네스 CLI 가 없습니다",
              "keychain_unavailable": "키체인에 접근할 수 없습니다", "error": "hswap 계정 추가 실패"}


def _claude_ready():
    """macOS 는 hswap 파일 저장소이거나, 키체인 모드에서 이 게이트웨이가 키체인에 닿는다고 확인된 기기만 쓴다(claude_accounts.login_ready)."""
    return sys.platform != "darwin" or claude_accounts.login_ready()


def _hswap_add():
    """계정 추가를 맡길 hswap(고정 버전 이상). 버전을 아직 모르거나 낮으면 None."""
    exe, v = claude_accounts.binary(), claude_accounts._version
    try:
        return exe if exe and v and claude_accounts._key(v) >= claude_accounts._key(HSWAP_ADD) else None
    except ValueError:
        return None


def adds():
    """화면의 '계정 추가' 버튼을 낼 프로바이더. Claude 는 다시 로그인과 같은 일이라 늘 되고, Codex·Grok 은 hswap 이 지금 로그인을
    바꾸지 않고 더할 수 있을 때만 된다. 이 목록을 모르는 옛 게이트웨이에는 버튼을 내지 않는다(mode=add 를 몰라 로그인을 바꾼다)."""
    have = available()
    return [k for k in have if k == "claude" or _hswap_add()]


def _unmask():
    """메뉴 막대 모드(JARVIS_TRAY)의 게이트웨이는 SIGTERM·SIGINT 를 막아 둔 채 자식을 띄운다. 자식은 그 마스크를 물려받아
    _stop 의 SIGTERM 을 받지 못하므로, hswap 이 정리할 수 있게 자식에서 다시 연다(fork 뒤 exec 전, 비동기 시그널 안전 호출 하나)."""
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT})


def _stop(proc):
    """hswap 은 SIGTERM 에만 하네스 CLI 와 임시 로그인 홈을 정리한다. 끝나지 않으면 죽인다."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


def available():
    """이 기기에서 로그인 버튼을 낼 프로바이더."""
    return [k for k, f in CLIS.items() if f() and (k != "claude" or _claude_ready())]


def _codex_auth():
    """codex 자격 파일(CODEX_HOME 기본 ~/.codex)."""
    return os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "auth.json")


def _grok_auth():
    """grok 자격 파일(공식 `GROK_HOME` 우선). grok 은 OS 키링을 쓰지 않고 이 파일에만 자격을 둔다(공식 문서·실험 확인)."""
    return os.path.join(_grok_home(), "auth.json")


def _codex_keyring_allowed(exe, timeout):
    """CLI의 관리 요건을 읽어 -c keyring이 덮어써지지 않는지 확인한다. 불명이면 False.

    공식 rust-v0.166.0 app-server-protocol의 configRequirements/read:
    requirements.cliAuthCredentialsStore는 CLI -c보다 우선한다. 자체 stdio 프로세스만 띄워
    설정을 읽고 끝내며, 기존 데몬·세션에는 연결하지 않는다. 응답 원문은 출력하지 않는다.
    """
    proc = None
    reader = None
    end = time.monotonic() + timeout
    replies = queue.Queue()
    try:
        proc = subprocess.Popen([exe, 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                                cwd=os.path.expanduser('~'), env=_ENV, creationflags=_NO_WINDOW,
                                start_new_session=os.name != 'nt')

        def read():
            try:
                while line := proc.stdout.readline((1 << 20) + 1):
                    if len(line) > 1 << 20:
                        break
                    replies.put(line)
            finally:
                proc.stdout.close()
                replies.put(None)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()

        def send(body):
            proc.stdin.write(json.dumps(body) + '\n')
            proc.stdin.flush()

        def ask(number, method, params):
            send({'id': number, 'method': method, 'params': params})
            while True:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return None
                line = replies.get(timeout=remaining)
                if line is None:
                    return None
                body = json.loads(line)
                if isinstance(body, dict) and body.get('id') == number:
                    return body.get('result')

        if not isinstance(ask(1, 'initialize', {'clientInfo': {'name': 'jarvis_usage', 'version': '1'}}), dict):
            return False
        send({'method': 'initialized', 'params': {}})
        result = ask(2, 'configRequirements/read', {})
        if not isinstance(result, dict) or 'requirements' not in result:
            return False
        requirements = result['requirements']
        return requirements is None or (isinstance(requirements, dict)
                                        and requirements.get('cliAuthCredentialsStore') in (None, 'keyring'))
    except (OSError, ValueError, queue.Empty):
        return False
    finally:
        if proc:
            if os.name != 'nt':
                try:
                    os.killpg(proc.pid, signal.SIGKILL)  # 이 조회의 런처와 stdout을 상속한 자식만
                except ProcessLookupError:
                    pass
            elif proc.poll() is None:
                try:
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=.3, creationflags=_NO_WINDOW)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=.3)
            except subprocess.TimeoutExpired:
                pass
            try:
                proc.stdin.close()
            except OSError:
                pass
            if reader:
                reader.join(timeout=.1)
            else:
                proc.stdout.close()


def _codex_stored():
    """codex 에 저장한 자격가 있나.

    파일이 있으면 보존한다. 파일이 없을 때는 CLI와 명시적인 keyring 조회가 모두 정확히
    'Not logged in'/exit 1을 답해야 없는 쪽으로 본다. auto 저장소는 키링 오류를 삼키고
    파일로 대체하므로 기본 status만으로 키링 부재를 확정할 수 없다(공식 rust-v0.166.0
    codex-rs/login/src/auth/storage.rs AutoAuthStorage::load). 관리 요건으로 keyring 옵션을
    덮어쓰지 않는지도 CLI에서 확인한다. 키링 불가·오류는 불명으로 보존한다.
    모든 조회는 STATUS_S 예산을 공유한다(`_ENV`의 LC_ALL=C로 영문 응답).
    """
    exe = CLIS["codex"]()
    if not exe:
        return True
    if os.path.isfile(_codex_auth()):
        return True  # 파일이 있으면 codex 도 'Logged in' 으로 답한다(빈 파일도). 매번 CLI 를 부르지 않는다
    end = time.monotonic() + STATUS_S
    rc, out, err = run([exe, "login", "status"], STATUS_S)
    if rc != 1 or ((out or "") + (err or "")).strip() != 'Not logged in':
        return True
    remaining = end - time.monotonic()
    if remaining <= 0 or not _codex_keyring_allowed(exe, remaining):
        return True
    remaining = end - time.monotonic()
    if remaining <= 0:
        return True
    rc, out, err = run([exe, '-c', 'cli_auth_credentials_store="keyring"', 'login', 'status'], remaining)
    return rc != 1 or ((out or "") + (err or "")).strip() != 'Not logged in'


def _grok_stored():
    """grok 에 저장한 자격가 있나. 파일이 없거나 비어 있으면 없다(못 읽으면 있는 쪽으로 본다)."""
    try:
        with open(_grok_auth(), encoding="utf-8") as f:
            body = json.load(f)
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError):
        return True  # 잠겨·깨져 있으면 자격이 있는 쪽으로
    return True if not isinstance(body, dict) else bool(body)


STORED = {"codex": _codex_stored, "grok": _grok_stored}  # provider → 저장한 자격이 있나


def fresh():
    """저장한 자격이 없어 새로 로그인해도 잃을 게 없는 프로바이더(화면의 '로그인' 버튼). 알 수 없으면 넣지 않는다."""
    return [k for k in available() if k in STORED and not STORED[k]()]


def _event(job, line):
    """hswap add --login --json 의 한 줄. 이벤트가 아닌 줄은 버린다(하네스 출력은 hswap 이 이미 버린다)."""
    try:
        e = json.loads(line)
    except (ValueError, RecursionError):
        return
    if not isinstance(e, dict):
        return
    if e.get("type") == "login_url" and isinstance(e.get("url"), str) and e["url"].startswith("https://"):
        job["url"] = job["url"] or e["url"]
    elif e.get("type") == "device_code" and isinstance(e.get("url"), str) and e["url"].startswith("https://") and isinstance(e.get("userCode"), str):
        job["url"], job["code"] = job["url"] or e["url"], job["code"] or e["userCode"]
        if isinstance(e.get("expiresInSeconds"), int) and not isinstance(e.get("expiresInSeconds"), bool):
            job["expires_at"] = time.time() + e["expiresInSeconds"]
    elif e.get("type") == "added":
        job["added"] = e
    elif e.get("type") == "error":
        err = e.get("error") if isinstance(e.get("error"), dict) else {}
        job["error"] = ADD_ERRORS.get(err.get("type"), "hswap 계정 추가 실패(알 수 없는 종류)")


def _read_events(job):
    """hswap add --login --json: 로그인·슬롯 저장·(macOS Claude) keepalive 동기화를 hswap 이 한 번에 한다."""
    for line in iter(job["proc"].stdout.readline, ""):
        _event(job, line)
    rc, added = job["proc"].wait(), job.get("added")
    ka = added.get("keepalive") if added else None
    # macOS 의 Claude 는 동기화 보고가 정상이어야 끝난다(hswap 이 동기화에 실패하면 보고가 null 이다). 실패해도 hswap 은 추가를 되돌리지 않는다
    must = job.get("provider") == "claude" and sys.platform == "darwin"
    job["unsynced"] = bool(added) and (ka is not None or must) and not (isinstance(ka, dict) and ka.get("ok") is True and not ka.get("error"))
    job["rc"] = 0 if rc == 0 and added and not job["unsynced"] else 1


def _read(job):
    if job.get("events"):
        return _read_events(job)
    for line in iter(job["proc"].stdout.readline, ""):
        job["out"] += ANSI_RE.sub("", line)
        job["url"] = job["url"] or next(iter(URL_RE.findall(job["out"])), None)
        job["code"] = job["code"] or next(iter(CODE_RE.findall(job["out"])), None)
    rc = job["proc"].wait()
    if job["paste"]:  # 로그인이 끝나야 저장한다. 저장까지 마친 뒤에 done 으로 본다
        hswap = claude_accounts.binary()
        saved = run([hswap, "claude", "add"], 60)[0] if rc == 0 and hswap else rc
        job["unsaved"] = rc == 0 and saved != 0  # 로그인은 바뀌었고 슬롯 저장만 실패했다
        if saved == 0 and hswap and sys.platform == "darwin":
            # add 가 종료 0 이어도 키체인·파일은 다른 로그인을 가질 수 있다.
            # hswap 이 검증·동기화한다. 어느 저장소가 새 것인지 추측해 adopt 하지는 않는다.
            checked, out, _ = run([hswap, "claude", "keepalive", "--json"], 60)
            try:
                report = json.loads(out)
            except (ValueError, RecursionError):
                report = None
            job["unsynced"] = not (checked == 0 and isinstance(report, dict) and type(report.get("schemaVersion")) is int and report.get("schemaVersion") == 1
                                   and report.get("ok") is True and not report.get("error"))
            if job["unsynced"]:
                saved = 1
        job["rc"] = saved  # 검증과 실패 이유를 갖춘 뒤에 완료 상태를 공개한다


def _alive(job):
    return job and job["proc"].poll() is None and time.time() - job["started_at"] < LIFE_S


def _ready(job):
    return job["url"] and (job["code"] or job["paste"])


def start(provider, add=False):
    """(http 상태, 응답). 같은 프로바이더 로그인이 아직 기다리는 중이면 그 URL·코드를 다시 준다.
    add: 계정 추가. Codex·Grok 은 hswap 이 격리 홈에서 로그인해 지금 로그인을 바꾸지 않고 슬롯만 더한다.
    Claude 는 다시 로그인과 추가가 같다(지금 로그인이 바뀐다): hswap 이 있으면 관리되지 않던 지금 로그인을 먼저 저장하는 hswap 에 맡긴다."""
    if provider not in CLIS:
        return 400, {"error": "provider 는 " + " 또는 ".join(CLIS)}
    exe = CLIS[provider]()
    if not exe or (provider == "claude" and not _claude_ready()):
        return 404, {"error": f"이 기기에서는 {provider} 로그인을 화면에서 할 수 없습니다"}
    hswap = _hswap_add() if add or provider == "claude" else None
    if add and not hswap and provider != "claude":  # Claude 의 추가는 다시 로그인과 같아 옛 hswap 에서도 된다
        return 404, {"error": f"이 기기의 hswap 은 화면에서 계정을 추가하지 못합니다(hswap {HSWAP_ADD} 이상 필요)"}
    with _lock:
        job = _jobs.get(provider)
        saving = job and job["paste"] and job["rc"] is None and job["proc"].poll() is not None
        if not ((_alive(job) or saving) and _ready(job) and job.get("add", False) == add):
            if job and job["proc"].poll() is None:
                (_stop if job.get("events") else subprocess.Popen.kill)(job["proc"])
            cmd = [hswap, provider, "add", "--login", "--json"] if hswap else [exe, *ARGS.get(provider, ["login", "--device-auth"])]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if provider in PASTE else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    # 이벤트 모드의 stderr 는 hswap 의 진단 문구라 이벤트 줄과 섞지 않는다
                                    stderr=subprocess.DEVNULL if hswap else subprocess.STDOUT, text=True, errors="replace", cwd=os.path.expanduser("~"),
                                    env={**_ENV, "NO_COLOR": "1", "TERM": "dumb"}, creationflags=_NO_WINDOW,
                                    preexec_fn=_unmask if hswap and hasattr(signal, "pthread_sigmask") else None)
            job = _jobs[provider] = {"proc": proc, "out": "", "url": None, "code": None, "started_at": time.time(),
                                     "paste": provider in PASTE, "rc": None, "unsaved": False, "unsynced": False,
                                     "events": bool(hswap), "add": add, "added": None, "error": None, "provider": provider}
            job["reader"] = threading.Thread(target=_read, args=(job,), name="jarvis-login-" + provider, daemon=True)
            job["reader"].start()
            t = threading.Timer(LIFE_S, lambda p=proc, ev=bool(hswap): p.poll() is None and (_stop(p) if ev else p.kill()))
            t.daemon = True  # 기다리는 로그인이 jarvis 종료를 붙잡지 않게
            t.start()
    end = time.monotonic() + WAIT_S
    while time.monotonic() < end and not _ready(job) and job["proc"].poll() is None:
        time.sleep(0.2)
    if _ready(job):
        return 200, {"provider": provider, "url": job["url"], "code": job["code"], "paste": job["paste"],
                     "expires_at": job.get("expires_at") or job["started_at"] + 15 * 60}
    if job["proc"].poll() is None:
        (_stop if job.get("events") else subprocess.Popen.kill)(job["proc"])
    if job.get("events"):
        job["reader"].join(1)  # 바로 끝난 hswap 의 error 줄을 읽을 때까지 잠깐 기다린다
        return 502, {"error": f"hswap {provider} add --login 이 로그인 주소를 주지 않았습니다" + (f": {job['error']}" if job.get("error") else "")}
    return 502, {"error": f"{provider} login 이 URL·코드를 주지 않았습니다: " + job["out"].strip()[-300:]}


def submit(provider, code):
    """(http 상태, 응답). 붙여 넣는 로그인이 기다리는 중이면 그 CLI 의 표준 입력으로 인증 코드를 넘긴다. 코드는 기록하지 않는다."""
    if provider not in PASTE:
        return 400, {"error": "provider 는 " + " 또는 ".join(sorted(PASTE))}
    if not (isinstance(code, str) and PASTE_RE.fullmatch(code)):
        return 400, {"error": "인증 코드의 모양이 맞지 않습니다"}
    with _lock:
        job = _jobs.get(provider)
        if not (_alive(job) and job["url"]):
            return 409, {"error": "기다리는 로그인이 없습니다. 로그인을 다시 시작하세요"}
        try:
            job["proc"].stdin.write(code + "\n")
            job["proc"].stdin.flush()
        except (OSError, ValueError):
            return 409, {"error": "로그인이 이미 끝났습니다. 로그인을 다시 시작하세요"}
    return 200, {"submitted": True}


def status(provider):
    """로그인 진행 상태: none(시작 안 함)·waiting(코드 입력을 기다림)·done(자격 저장)·failed(실패·코드 만료)."""
    job = _jobs.get(provider)
    if not job:
        return {"state": "none"}
    if job["paste"] or job.get("events"):  # CLI 가 끝난 뒤 hswap 저장이 남았으면 아직 기다리는 중이다
        if job["rc"] is None:
            return {"state": "waiting" if _alive(job) or job["proc"].poll() is not None else "failed"}
        if job.get("unsynced"):
            return {"state": "failed", "unsynced": True}
        if job["rc"] == 0:
            return {"state": "done"}
        return {"state": "failed", "unsaved": True} if job["unsaved"] else {"state": "failed", **({"error": job["error"]} if job.get("error") else {})}
    rc = job["proc"].poll()
    if rc is None:
        return {"state": "waiting" if _alive(job) else "failed"}
    return {"state": "done" if rc == 0 else "failed"}
