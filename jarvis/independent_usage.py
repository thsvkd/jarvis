"""Orca 상태와 별개로 사용할 수 있는 사용량 수집기를 묶는다. Orca 가 있으면 독립 수집기가 못 읽은 계정만 보충한다."""
from concurrent.futures import ThreadPoolExecutor, wait
import os
import sys
import threading
import time

from . import antigravity_usage, caam_usage, claude_accounts, cli_login, codex_native_usage, gemini_usage, kimi_usage, minimax_usage, opencode_usage, orca_usage, provider_usage, zcode_usage
from . import cswap_cached_usage, cswap_usage  # noqa: F401 (호환 이름)
from .util import which_local

_pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="jarvis-usage")
_lock = threading.Lock()
_tasks = {}  # 이름 → (시작 시각, Future, 강제 여부). 늦은 수집을 중복 실행하지 않는다.
FORCE_WAIT_S = 10  # 새로고침이 진행 중인 보통 읽기를 기다리는 시간. 이어지는 읽기의 시한(40초)과 합쳐 read 의 대기(55초) 안이어야 한다
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
    return claude_accounts.read(force)


def _claude_after(running):
    """보통 읽기가 끝나기를 기다린 뒤 강제로 읽는다. 그 읽기는 묶음만 다시 쓸 수 있어 새로고침의 답이 될 수 없다."""
    wait([running], timeout=FORCE_WAIT_S)
    return _claude(True)


def _orca(force):
    """Orca 로컬 RPC 의 계정만 읽는다(cswap·caam 은 실행하지 않는다). Orca 가 없거나 실패하면 빈 목록."""
    return orca_usage._orca(force).get("providers", [])


def _merge(mine, orca):
    """독립 수집 계정에 Orca 계정을 보충한다. 이메일 모르는 기본 계정만 있으면 Orca 의 이메일 계정이 같은 로그인이다."""
    emails = {a["label"].lower() for a in mine if "@" in a["label"]}
    extra = [o for o in orca if "@" in o["label"] and o["label"].lower() not in emails]
    if not mine:
        return list(orca)
    if extra and not emails:
        # 내 쪽 활성 기본 계정 하나와 Orca 이메일 계정 하나는 같은 로그인이다. 사용 중 표시를 넘긴다(Orca 사본은 바꾸지 않는다)
        if len(extra) == 1 and sum(1 for a in mine if a.get("active")) == 1:
            extra = [{**extra[0], "active": True}]
        return extra
    return mine + extra


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
            # Claude 는 claude_accounts 가 hswap 을 부를 때를 정하므로 여기서 묶지 않는다. 묶으면 계정 전환 직후에도 이전 '사용 중'을 보인다
            ttl = 0 if name == "claude" else 120
            if previous is None or (previous[1].done() and (force or now - previous[0] >= ttl)):
                previous = _tasks[name] = (now, _pool.submit(job), force)
            elif force and name == "claude" and not previous[2]:  # 새로고침은 강제 작업에만 얹힌다. 보통 읽기는 어느 쪽에든 얹힌다
                previous = _tasks[name] = (now, _pool.submit(_claude_after, previous[1]), True)
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
        accounts, failed, login = [], True, None
        try:
            value = future.result(0) if future.done() else None
            if name == "claude":
                accounts = value.get("accounts", []) if isinstance(value, dict) else []
                failed = not isinstance(value, dict) or bool(value.get("error"))
                login = value.get("login") if isinstance(value, dict) else None
            elif name in ("opencodeGo", "antigravity", "kimi", "zcode", "gemini", "minimax"):
                accounts = value.get("accounts", []) if isinstance(value, dict) else []
                failed = not isinstance(value, dict) or value.get("status") != "ok"
            else:
                accounts = value if isinstance(value, list) else []
                failed = not future.done()
        except Exception:
            pass
        if extra.get(name):
            # Claude 는 이메일과 조직으로 계정을 가른다(호환 경로와 같은 규칙). 이메일만 보면 같은 이메일의 다른 조직과 그 경고를 잃는다
            accounts = (claude_accounts.merge_orca if name == "claude" else _merge)(accounts, extra[name])
        if failed and not extra.get(name) or not accounts:
            unavailable.append(name)
        if accounts or login:  # 계정이 없어도 기기 로그인 상태의 경고·복구 안내를 보일 칸은 남긴다
            providers.append({"id": name, "name": _names[name], "accounts": accounts, **({"login": login} if login else {})})
    # Orca 경로와 같은 후속 처리: Grok 토큰 위임 갱신(grok CLI), 화면의 '다시 로그인' 버튼을 낼 CLI 목록
    if any(a.get("failure") == "delegated-refresh-required" for p in providers if p["id"] == "grok" for a in p["accounts"]):
        cli_login.refresh_grok()
    body = {"mode": "independent", "providers": providers, "unavailable": unavailable, "fetched_at": time.time()}
    if logins := cli_login.available():
        body["logins"] = logins
    # 저장한 자격가 없어 새로 로그인해도 잃을 게 없는 프로바이더(기기 행의 '로그인' 버튼). 알 수 없으면 넣지 않는다
    if fresh_logins := cli_login.fresh():
        body["fresh_logins"] = fresh_logins
    # 기기 행에 보일 hswap 버전(게이트웨이가 확인해 둔 값만 쓴다. 여기서 실행·설치하지 않는다)
    if hswap := claude_accounts.status():
        body["hswap"] = hswap
    return body
