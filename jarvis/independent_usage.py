"""Orca 상태와 별개로 사용할 수 있는 사용량 수집기를 묶는다."""
from concurrent.futures import ThreadPoolExecutor, wait
import os
import sys
import threading
import time

from . import antigravity_usage, caam_usage, codex_native_usage, cswap_cached_usage, cswap_usage, gemini_usage, kimi_usage, minimax_usage, opencode_usage, provider_usage, zcode_usage
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
    wait(selected.values(), timeout=55)
    providers, unavailable = [], []
    for name, future in selected.items():
        if not future.done():
            unavailable.append(name)
            continue
        try:
            value = future.result()
            if name == "claude":
                accounts = value.get("accounts", []) if isinstance(value, dict) else []
                if isinstance(value, dict) and value.get("error"):
                    unavailable.append(name)
            elif name in ("opencodeGo", "antigravity", "kimi", "zcode", "gemini", "minimax"):
                accounts = value.get("accounts", []) if isinstance(value, dict) else []
                if not isinstance(value, dict) or value.get("status") != "ok":
                    unavailable.append(name)
            else:
                accounts = value if isinstance(value, list) else []
            if not accounts and name not in unavailable:
                unavailable.append(name)
            if accounts:
                providers.append({"id": name, "name": _names[name], "accounts": accounts})
        except Exception:
            unavailable.append(name)
    return {"mode": "independent", "providers": providers, "unavailable": unavailable, "fetched_at": time.time()}
