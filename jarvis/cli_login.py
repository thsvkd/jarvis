"""AI 사용량 화면의 '다시 로그인': 이 기기에서 `<cli> login --device-auth` 를 띄우고, 출력의 로그인 URL·일회용 코드를 준다.
사람이 아무 기기의 브라우저로 그 URL 을 열어 코드를 넣으면 CLI 가 자격을 저장하고 끝난다(Orca 는 그 자격을 읽는다).
Grok 은 Orca 가 토큰을 스스로 갱신하지 않는다(delegated-refresh-required): grok CLI 를 한 번 돌리면 refresh token 으로 갱신된다.
refresh token 마저 죽었으면 grok 은 자격을 지운다(그 뒤로는 로그인 버튼으로 다시 받는다).
"""
import os
import re
import subprocess
import sys
import threading
import time

from . import caam_usage
from .util import _ENV, _NO_WINDOW, run, which_local

WAIT_S = 30  # URL·코드가 나올 때까지
LIFE_S = 16 * 60  # 코드는 15분 유효하다. 그 뒤에도 남아 있으면 끝낸다
REFRESH_S = 600  # grok 자동 갱신 간격
URL_RE = re.compile(r"https://\S+")
CODE_RE = re.compile(r"^\s*([A-Z0-9]{4}-[A-Z0-9]{4,5})\s*$", re.M)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_lock = threading.Lock()
_jobs = {}  # provider → {"proc", "out", "url", "code", "started_at"}
_grok_at = 0.0


def _grok():
    p = os.path.join(os.path.expanduser("~"), ".grok", "bin", "grok" + (".exe" if sys.platform == "win32" else ""))
    return which_local("grok") or (p if os.path.isfile(p) else None)


CLIS = {"codex": caam_usage._codex_cli, "grok": _grok}


def available():
    """이 기기에서 로그인 버튼을 낼 프로바이더."""
    return [k for k, f in CLIS.items() if f()]


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


def refresh_grok():
    """Orca 가 Grok 을 delegated-refresh-required 로 볼 때 뒤에서 grok 을 한 번 돌려 토큰을 갱신한다(REFRESH_S 에 한 번)."""
    global _grok_at
    exe = _grok()
    with _lock:
        if not exe or (_grok_at and time.monotonic() - _grok_at < REFRESH_S):
            return
        _grok_at = time.monotonic()
    threading.Thread(target=lambda: run([exe, "models"], 60), name="jarvis-grok-refresh", daemon=True).start()
