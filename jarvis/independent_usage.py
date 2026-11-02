"""Orca 상태와 별개로 사용할 수 있는 사용량 수집기를 묶는다. Orca 가 있으면 독립 수집기가 못 읽은 계정만 보충한다."""
from concurrent.futures import ThreadPoolExecutor, wait
import os
import sys
import threading
import time

from . import antigravity_usage, caam_usage, cli_login, codex_native_usage, cswap_cached_usage, cswap_usage, gemini_usage, kimi_usage, minimax_usage, opencode_usage, orca_usage, provider_usage, zcode_usage
from .util import which_local

_pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="jarvis-usage")
_lock = threading.Lock()
_tasks = {}  # 이름 → (시작 시각, Future). 늦은 수집을 중복 실행하지 않는다.
_names = {"claude": "Claude", "codex": "Codex", "grok": "Grok", "cursor": "Cursor", "opencodeGo": "OpenCode Go", "antigravity": "Antigravity", "kimi": "Kimi", "zcode": "ZCode", "gemini": "Gemini", "minimax": "MiniMax"}


def _caam(name):
    exe = caam_usage.binary()
    if not exe:
        raise RuntimeError("caam 없음")
    # caam 은 실행 때 기존 데이터를 마이그레이션할 수 있다. 기존 Codex 수집과도 직렬화한다.
    if not caam_usage._lock.acquire(timeout=caam_usage.BUDGET_S):
        raise RuntimeError("caam 대기 시간 초과")
    try:
        return provider_usage.read_provider(exe, name)
    finally:
        caam_usage._lock.release()


def _opencode():
    exe = which_local("opencode")
    if not exe:
        candidate = os.path.expanduser("~/.opencode/bin/opencode" + (".exe" if sys.platform == "win32" else ""))
        exe = candidate if os.path.isfile(candidate) else None
    return opencode_usage.read(exe) if exe else {"status": "unavailable", "accounts": []}


def _codex():
    try:
        rows = _caam("codex")
    except Exception:
        rows = []
    return rows + codex_native_usage.read()


def _claude(force):
    return cswap_cached_usage.read()


def _orca(force):
    """Orca 로컬 RPC 의 계정만 읽는다(cswap·caam 은 실행하지 않는다). Orca 가 없거나 실패하면 빈 목록."""
    return orca_usage._orca(force).get("providers", [])


def _merge(mine, orca):
    """독립 수집 계정에 Orca 계정을 보충한다. 이메일 모르는 기본 계정만 있으면 Orca 의 이메일 계정이 같은 로그인이다."""
    emails = {a["label"].lower() for a in mine if "@" in a["label"]}
    extra = [o for o in orca if "@" in o["label"] and o["label"].lower() not in emails]
    if not mine:
        return list(orca)
    return extra if extra and not emails else mine + extra


def read(force=False):
    """독립 수집 결과만 반환한다. 실패 원문이나 자격 증명은 내보내지 않는다."""
    jobs = {"claude": lambda: _claude(force), "codex": _codex,
            "grok": lambda: _caam("grok"), "cursor": lambda: _caam("cursor"), "opencodeGo": _opencode,
            "antigravity": antigravity_usage.read, "kimi": kimi_usage.read, "zcode": zcode_usage.read,
            "gemini": gemini_usage.read, "minimax": minimax_usage.read}
    now = time.monotonic()
    selected = {}
    with _lock:
        for name, job in jobs.items():
            previous = _tasks.get(name)
            ttl = 30 if name == "claude" else 120
            if previous is None or (previous[1].done() and (force or now - previous[0] >= ttl)):
                previous = _tasks[name] = (now, _pool.submit(job))
            selected[name] = previous[1]
        previous = _tasks.get("orca")
        if previous is None or (previous[1].done() and (force or now - previous[0] >= 30)):
            previous = _tasks["orca"] = (now, _pool.submit(_orca, force))
        orca = previous[1]
    wait([*selected.values(), orca], timeout=55)
    try:
        extra = {p["id"]: p["accounts"] for p in orca.result(0)} if orca.done() else {}
    except Exception:
        extra = {}
    providers, unavailable = [], []
    for name, future in selected.items():
        accounts, failed = [], True
        try:
            value = future.result(0) if future.done() else None
            if name == "claude":
                accounts = value.get("accounts", []) if isinstance(value, dict) else []
                failed = not isinstance(value, dict) or bool(value.get("error"))
            elif name in ("opencodeGo", "antigravity", "kimi", "zcode", "gemini", "minimax"):
                accounts = value.get("accounts", []) if isinstance(value, dict) else []
                failed = not isinstance(value, dict) or value.get("status") != "ok"
            else:
                accounts = value if isinstance(value, list) else []
                failed = not future.done()
        except Exception:
            pass
        if extra.get(name):
            accounts = _merge(accounts, extra[name])
        if failed and not extra.get(name) or not accounts:
            unavailable.append(name)
        if accounts:
            providers.append({"id": name, "name": _names[name], "accounts": accounts})
    # Orca 경로와 같은 후속 처리: Grok 토큰 위임 갱신(grok CLI), 화면의 '다시 로그인' 버튼을 낼 CLI 목록
    if any(a.get("failure") == "delegated-refresh-required" for p in providers if p["id"] == "grok" for a in p["accounts"]):
        cli_login.refresh_grok()
    body = {"mode": "independent", "providers": providers, "unavailable": unavailable, "fetched_at": time.time()}
    if logins := cli_login.available():
        body["logins"] = logins
    # 저장한 자격가 없어 새로 로그인해도 잃을 게 없는 프로바이더(기기 행의 '로그인' 버튼). 알 수 없으면 넣지 않는다
    if fresh_logins := cli_login.fresh():
        body["fresh_logins"] = fresh_logins
    return body
