"""이 기기 Orca 가 아는 AI 구독 사용량(프로바이더 → 계정 → 한도 창). GET /api/orca/usage 로 core 에게만 준다.

Orca 로컬 RPC accounts.list 의 rateLimits 를 그대로 정규화한다(사용량 조회·토큰 갱신은 Orca 가 한다).
refreshUsage=true 는 계정마다 프로바이더 API 를 한 번씩 부르고 Orca 의 조회 간격·Retry-After 를 건너뛴다:
REFRESH_S 마다 한 번만(버튼으로 강제해도 FORCE_S 간격) 쓰고, 그 사이에는 Orca 가 들고 있는 값을 읽는다.
자동 갱신은 뒤에서 돌리고 보관값을 바로 준다(계정이 많으면 45초까지 걸린다). 시도한 시각부터 간격을 센다(실패·지연이 반복 호출이 되지 않게).
계정은 이메일만 내보낸다. 홈 경로·조직 UUID·프로바이더 계정 ID·자격 출처는 넣지 않는다.

공식 계약: src/main/runtime/rpc/methods/accounts.ts, src/shared/rate-limit-types.ts (stablyai/orca)
"""
import math
import re
import threading
import time

from . import orca, orca_rpc

REFRESH_S = 300
FORCE_S = 30
# rateLimits 키 → (id, 화면 이름). 모르는 키는 무시한다(새 Orca 가 늘려도 깨지지 않게).
PROVIDERS = (("claude", "Claude"), ("codex", "Codex"), ("grok", "Grok"), ("antigravity", "Antigravity"),
             ("gemini", "Gemini"), ("cursor", "Cursor"), ("opencodeGo", "OpenCode Go"), ("kimi", "Kimi"),
             ("minimax", "MiniMax"), ("zcode", "ZCode"))
WINDOWS = (("session", "session"), ("weekly", "weekly"), ("fableWeekly", "fable"), ("monthly", "monthly"))
NOT_SET_UP = {"missing-credentials", "cli-unavailable"}

_lock = threading.Lock()
_refreshed = 0.0  # 마지막 refreshUsage=true 의 monotonic 시각


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _sec(ms):
    v = _num(ms)
    return v / 1000 if v and v > 0 else None


def _text(v, n=300):
    return v[:n] if isinstance(v, str) and v else None


def _scrub(v):
    """오류 문장에 섞일 수 있는 사용자 홈 경로의 계정 이름을 가린다."""
    return re.sub(r"(/home/|/Users/|[A-Za-z]:\\Users\\)[^/\\\s]+", r"\1~", v) if v else v


def _dict(v):
    return v if isinstance(v, dict) else {}


def _list(v):
    return v if isinstance(v, list) else []


def _pct(v):
    v = _num(v)
    return None if v is None else max(0, min(100, round(v)))


def _window(w, key):
    if not isinstance(w, dict) or _pct(w.get("usedPercent")) is None:
        return None
    return {"key": key, "used": _pct(w["usedPercent"]), "resets_at": _sec(w.get("resetsAt"))}


def _extra(e):
    if not isinstance(e, dict):
        return None
    out = {"unit": e.get("unit") if e.get("unit") in ("currency", "credits") else None,
           "enabled": e.get("enabled") is True, "reason": _text(e.get("disabledReason"), 80)}
    if out["unit"] == "currency":
        out.update(spent=_num(e.get("spent")), limit=_num(e.get("spendLimit")),
                   currency=_text(e.get("currencyCode"), 8), balance=_num(e.get("balance")))
    elif out["unit"] == "credits":
        out.update(balance=_num(e.get("balance")), unlimited=e.get("unlimited") is True)
    return out


def limits(r):
    """ProviderRateLimits → {status, error, plan, updated_at, windows, buckets, extra, credits}. 형식이 이상하면 None."""
    if not isinstance(r, dict):
        return None
    status = r.get("status") if r.get("status") in ("idle", "fetching", "ok", "error", "unavailable") else "error"
    credits = r.get("rateLimitResetCredits")
    return {"status": status, "error": _scrub(_text(r.get("error"))),
            "failure": _text(_dict(r.get("usageMetadata")).get("failureKind"), 60),
            "plan": _text(r.get("planType"), 60), "updated_at": _sec(r.get("updatedAt")),
            "windows": [w for k, key in WINDOWS if (w := _window(r.get(k), key))],
            "buckets": [{"name": _text(b.get("name"), 80), "used": _pct(b.get("usedPercent")), "resets_at": _sec(b.get("resetsAt"))}
                        for b in _list(r.get("buckets")) if isinstance(b, dict) and _pct(b.get("usedPercent")) is not None][:12],
            "extra": _extra(r.get("extraUsage")),
            "credits": _num(credits.get("availableCount")) if isinstance(credits, dict) else None}


def _set_up(lim):
    return lim is not None and lim["status"] != "unavailable" and lim["failure"] not in NOT_SET_UP \
        and (lim["status"] != "idle" or lim["windows"])


def _managed(snap, pid, rl):
    """claude·codex: 사용 중 계정(관리 계정 또는 시스템 기본) + 다른 관리 계정(inactive*Accounts)."""
    st = _dict(snap.get(pid))
    accs = [a for a in _list(st.get("accounts")) if isinstance(a, dict) and isinstance(a.get("id"), str)]
    by_runtime = _dict(st.get("activeAccountIdsByRuntime"))
    target = _dict(rl.get(pid + "Target"))  # rateLimits.<pid> 는 이 대상(host 또는 WSL 배포판)의 사용 중 계정이다
    if target.get("runtime") == "wsl":
        active_id = _dict(by_runtime.get("wsl")).get(target.get("wslDistro"))
    else:
        active_id = by_runtime.get("host", st.get("activeAccountId"))
    inactive = _list(rl.get("inactive" + pid.capitalize() + "Accounts"))
    usage = {u["accountId"]: u for u in inactive if isinstance(u, dict) and isinstance(u.get("accountId"), str)}
    out = []
    active = limits(rl.get(pid))
    act = next((a for a in accs if a["id"] == active_id), None)
    if act or _set_up(active):
        # 시스템 기본 계정의 이메일: Claude 는 systemDefaultEmail, Codex 는 systemDefault.email
        default = st.get("systemDefaultEmail") or _dict(st.get("systemDefault")).get("email")
        # 시스템 기본 계정의 조직 이름은 같은 조직 UUID 의 관리 계정에서 빌린다(UUID 는 내보내지 않는다)
        org = (act or next((a for a in accs if st.get("systemDefaultOrganizationUuid")
                            and a.get("organizationUuid") == st["systemDefaultOrganizationUuid"]), {})).get("organizationName")
        out.append({"label": _text((act or {}).get("email"), 200) or _text(default, 200) or "시스템 기본 계정",
                    "org": _text(org, 80), "active": True, **(active or limits({"status": "idle"}))})
    for a in accs:
        if a is act:
            continue
        u = usage.get(a["id"]) or {}
        lim = limits(u.get("rateLimits")) or limits({"status": "fetching" if u.get("isFetching") else "idle"})
        if lim["updated_at"] is None:
            lim["updated_at"] = _sec(u.get("updatedAt"))
        out.append({"label": _text(a.get("email"), 200) or "이름 없는 계정", "org": _text(a.get("organizationName"), 80),
                    "active": False, **lim})
    return out


def normalize(snap):
    snap = snap if isinstance(snap, dict) else {}
    rl = snap.get("rateLimits") if isinstance(snap.get("rateLimits"), dict) else {}
    providers = []
    for key, name in PROVIDERS:
        if key in ("claude", "codex"):
            accounts = _managed(snap, key, rl)
        else:
            lim = limits(rl.get(key))
            accounts = [{"label": "기본 계정", "org": None, "active": True, **lim}] if _set_up(lim) else []
        if accounts:
            providers.append({"id": key, "name": name, "accounts": accounts})
    return providers


def _refresh_behind():
    try:
        orca_rpc.call("accounts.list", {"refreshUsage": True}, timeout=45)
    except Exception:  # 다음 간격에 다시 시도한다. 보관값은 이미 주었다
        pass


def read(force=False):
    """(http 상태, 응답). force: 사용자가 누른 새로고침. 프로바이더에서 다시 읽은 뒤 답한다(FORCE_S 간격 안이면 보관값)."""
    global _refreshed
    d = orca.find_dir(orca.DIRS)
    if not d:
        return 200, {"installed": False, "running": False, "providers": []}
    with _lock:  # 결정과 시각 기록만 잠근다. RPC 동안 잡고 있으면 다른 요청이 core 의 시간 제한을 넘긴다
        age = time.monotonic() - _refreshed
        now = force and (not _refreshed or age > FORCE_S)
        behind = not now and (not _refreshed or age > REFRESH_S)
        if now or behind:
            _refreshed = time.monotonic()
    if behind:
        threading.Thread(target=_refresh_behind, name="jarvis-orca-usage", daemon=True).start()
    try:
        try:
            snap = orca_rpc.call("accounts.list", {"refreshUsage": now}, timeout=45 if now else 12)
        except Exception:
            if not now:
                raise
            snap = orca_rpc.call("accounts.list", {"refreshUsage": False}, timeout=12)  # 다시 읽기가 늦으면 보관값이라도
        providers = normalize(snap)
    except Exception as e:
        return 200, {"installed": True, "running": False, "providers": [], "error": _scrub(str(e)[:200] or type(e).__name__)}
    return 200, {"installed": True, "running": True, "fetched_at": time.time(), "refreshed": now, "refreshing": behind,
                 "providers": providers}
