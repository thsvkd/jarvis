"""AI 사용량 화면의 '다시 로그인': 이 기기에서 `<cli> login --device-auth` 를 띄우고, 출력의 로그인 URL·일회용 코드를 준다.
사람이 아무 기기의 브라우저로 그 URL 을 열어 코드를 넣으면 CLI 가 자격을 저장하고 끝난다(Orca 는 그 자격을 읽는다).
Grok 은 Orca 가 토큰을 스스로 갱신하지 않는다(delegated-refresh-required): grok CLI 를 한 번 돌리면 refresh token 으로 갱신된다.
refresh token 마저 죽었으면 grok 은 자격을 지운다(그 뒤로는 로그인 버튼으로 다시 받는다).
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

from . import caam_usage
from .util import _ENV, _NO_WINDOW, run, which_local

WAIT_S = 30  # URL·코드가 나올 때까지
STATUS_S = 6  # codex login status 는 보통 0.1초 안에 답한다(늦으면 알 수 없음으로 본다)
LIFE_S = 16 * 60  # 코드는 15분 유효하다. 그 뒤에도 남아 있으면 끝낸다
REFRESH_S = 600  # grok 자동 갱신 간격
URL_RE = re.compile(r"https://\S+")
CODE_RE = re.compile(r"^\s*([A-Z0-9]{4}-[A-Z0-9]{4,5})\s*$", re.M)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_lock = threading.Lock()
_jobs = {}  # provider → {"proc", "out", "url", "code", "started_at"}
_grok_at = 0.0


def _grok_home():
    """grok 홈 디렉터리(공식 `GROK_HOME`, 없으면 ~/.grok). 자격과 CLI 를 같은 곳에서 찾는다.

    `GROK_HOME` 을 재정의한 기기에서 `~/.grok` 만 보면 자격을 없는 쪽으로 오판해
    '로그인' 버튼이 멀쩡한 자격을 지운다(공식 Settings 문서: `$GROK_HOME` 가 홈 디렉터리).
    """
    return os.environ.get("GROK_HOME") or os.path.join(os.path.expanduser("~"), ".grok")


def _grok():
    p = os.path.join(_grok_home(), "bin", "grok" + (".exe" if sys.platform == "win32" else ""))
    return which_local("grok") or (p if os.path.isfile(p) else None)


CLIS = {"codex": caam_usage._codex_cli, "grok": _grok}


def available():
    """이 기기에서 로그인 버튼을 낼 프로바이더."""
    return [k for k, f in CLIS.items() if f()]


def _codex_auth():
    """codex 자격 파일(CODEX_HOME 기본 ~/.codex)."""
    return os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "auth.json")


def _grok_auth():
    """grok 자격 파일(공식 `GROK_HOME` 우선). grok 은 OS 키링을 쓰지 않고 이 파일에만 자격을 둔다(공식 문서·실험 확인)."""
    return os.path.join(_grok_home(), "auth.json")


def _codex_stored():
    """codex 에 저장한 자격가 있나.

    파일 백엔드는 파일 유무로, keyring·환경변수 백엔드(`secret_auth_storage`)는 codex 자체의
    답으로 확인한다: 파일만 보면 keyring 보관 자격을 놓친다. `codex login status` 가
    'Not logged in' 을 답했을 때만 없는 쪽으로 본다. 없다고 오판하면 '로그인' 버튼이
    멀쩡한 자격을 지운다(`_ENV` 는 LC_ALL=C 라 영문으로 답한다).
    """
    exe = CLIS["codex"]()
    if not exe:
        return True
    if os.path.isfile(_codex_auth()):
        return True  # 파일이 있으면 codex 도 'Logged in' 으로 답한다(빈 파일도). 매번 CLI 를 부르지 않는다
    _, out, err = run([exe, "login", "status"], STATUS_S)
    text = (out or "") + (err or "")
    return "Not logged in" not in text  # 그 밖(로그인 중·오류·시간 초과·구버전)은 있는 쪽으로


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


def _read(job):
    for line in iter(job["proc"].stdout.readline, ""):
        job["out"] += ANSI_RE.sub("", line)
        job["url"] = job["url"] or next(iter(URL_RE.findall(job["out"])), None)
        job["code"] = job["code"] or next(iter(CODE_RE.findall(job["out"])), None)
    job["proc"].wait()


def _alive(job):
    return job and job["proc"].poll() is None and time.time() - job["started_at"] < LIFE_S


def start(provider):
    """(http 상태, 응답). 같은 프로바이더 로그인이 아직 기다리는 중이면 그 URL·코드를 다시 준다."""
    if provider not in CLIS:
        return 400, {"error": "provider 는 " + " 또는 ".join(CLIS)}
    exe = CLIS[provider]()
    if not exe:
        return 404, {"error": f"이 기기에는 {provider} CLI 가 없습니다"}
    with _lock:
        job = _jobs.get(provider)
        if not (_alive(job) and job["url"] and job["code"]):
            if job and job["proc"].poll() is None:
                job["proc"].kill()
            proc = subprocess.Popen([exe, "login", "--device-auth"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace", cwd=os.path.expanduser("~"),
                                    env={**_ENV, "NO_COLOR": "1", "TERM": "dumb"}, creationflags=_NO_WINDOW)
            job = _jobs[provider] = {"proc": proc, "out": "", "url": None, "code": None, "started_at": time.time()}
            threading.Thread(target=_read, args=(job,), name="jarvis-login-" + provider, daemon=True).start()
            t = threading.Timer(LIFE_S, lambda p=proc: p.poll() is None and p.kill())
            t.daemon = True  # 기다리는 로그인이 jarvis 종료를 붙잡지 않게
            t.start()
    end = time.monotonic() + WAIT_S
    while time.monotonic() < end and not (job["url"] and job["code"]) and job["proc"].poll() is None:
        time.sleep(0.2)
    if job["url"] and job["code"]:
        return 200, {"provider": provider, "url": job["url"], "code": job["code"],
                     "expires_at": job["started_at"] + 15 * 60}
    if job["proc"].poll() is None:
        job["proc"].kill()
    return 502, {"error": f"{provider} login 이 URL·코드를 주지 않았습니다: " + job["out"].strip()[-300:]}


def status(provider):
    """로그인 진행 상태: none(시작 안 함)·waiting(코드 입력을 기다림)·done(자격 저장)·failed(실패·코드 만료)."""
    job = _jobs.get(provider)
    if not job:
        return {"state": "none"}
    rc = job["proc"].poll()
    if rc is None:
        return {"state": "waiting" if _alive(job) else "failed"}
    return {"state": "done" if rc == 0 else "failed"}


def refresh_grok():
    """Orca 가 Grok 을 delegated-refresh-required 로 볼 때 뒤에서 grok 을 한 번 돌려 토큰을 갱신한다(REFRESH_S 에 한 번)."""
    global _grok_at
    exe = _grok()
    with _lock:
        if not exe or (_grok_at and time.monotonic() - _grok_at < REFRESH_S):
            return
        _grok_at = time.monotonic()
    threading.Thread(target=lambda: run([exe, "models"], 60), name="jarvis-grok-refresh", daemon=True).start()
