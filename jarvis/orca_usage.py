"""이 기기 Orca 가 아는 AI 구독 사용량(프로바이더 → 계정 → 한도 창). GET /api/orca/usage 로 core 에게만 준다.
Claude 는 cswap(cswap_usage)이 있으면 그 계정 목록을 쓴다: Orca 는 cswap 이 바꿔 끼운 로그인을 '시스템 기본 계정' 하나로만 본다.

Orca 로컬 RPC accounts.list 의 rateLimits 를 그대로 정규화한다(사용량 조회·토큰 갱신은 Orca 가 한다).
refreshUsage=true 는 계정마다 프로바이더 API 를 한 번씩 부르고 Orca 의 조회 간격·Retry-After 를 건너뛴다:
REFRESH_S 마다 한 번만(버튼으로 강제해도 FORCE_S 간격) 쓰고, 그 사이에는 Orca 가 들고 있는 값을 읽는다.
자동 갱신은 뒤에서 돌리고 마지막 값을 바로 준다(계정이 많으면 45초까지 걸린다). 시도한 시각부터 간격을 센다(실패·지연이 반복 호출이 되지 않게).
RPC 는 한 번에 하나만 부른다: Windows 명명 파이프는 다른 연결이 열려 있는 동안 열리지 않는다.
계정은 이메일만 내보낸다. 홈 경로·조직 UUID·프로바이더 계정 ID·자격 출처는 넣지 않는다.

공식 계약: src/main/runtime/rpc/methods/accounts.ts, src/shared/rate-limit-types.ts (stablyai/orca)
"""
import math
import threading
import time

from . import caam_usage, cli_login, cswap_usage, orca, orca_rpc
from .util import scrub_home

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
_rpc = threading.Lock()  # Orca RPC 는 하나씩
_last = None  # (fetched_at, providers): 마지막으로 읽은 값. 뒤에서 다시 읽는 동안 바로 준다


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _sec(ms):
    v = _num(ms)
    return v / 1000 if v and v > 0 else None


def _text(v, n=300):
    return v[:n] if isinstance(v, str) and v else None


def _dict(v):
    return v if isinstance(v, dict) else {}


def _list(v):
    return v if isinstance(v, list) else []


def org(name, email):
    """조직 이름. 개인 계정에 자동으로 붙는 '<이메일>'s Organization' 은 이메일과 같은 말이라 뺀다."""
    name = _text(name, 80)
    return None if not name or (email and name == f"{email}'s Organization") else name


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
    return {"status": status, "error": scrub_home(_text(r.get("error"))),
            "failure": _text(_dict(r.get("usageMetadata")).get("failureKind"), 60),
            "plan": _text(r.get("planType"), 60), "updated_at": _sec(r.get("updatedAt")),
            "windows": [w for k, key in WINDOWS if (w := _window(r.get(k), key))],
            "buckets": [{"name": _text(b.get("name"), 80), "used": _pct(b.get("usedPercent")), "resets_at": _sec(b.get("resetsAt"))}
                        for b in _list(r.get("buckets")) if isinstance(b, dict) and _pct(b.get("usedPercent")) is not None][:12],
            "extra": _extra(r.get("extraUsage")),
            "credits": _num(credits.get("availableCount")) if isinstance(credits, dict) else None}


def _set_up(lim):
    # 'idle'·'fetching' 은 Orca 가 아직(또는 다시) 읽는 중이라 설정 여부를 모른다: 값이 있을 때만 보인다
    return lim is not None and lim["failure"] not in NOT_SET_UP \
        and (lim["status"] in ("ok", "error") or bool(lim["windows"]))


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
        org_name = (act or next((a for a in accs if st.get("systemDefaultOrganizationUuid")
                            and a.get("organizationUuid") == st["systemDefaultOrganizationUuid"]), {})).get("organizationName")
        out.append({"label": _text((act or {}).get("email"), 200) or _text(default, 200) or "시스템 기본 계정",
                    "org": org(org_name, (act or {}).get("email") or default), "active": True, **(active or limits({"status": "idle"}))})
    for a in accs:
        if a is act:
            continue
        u = usage.get(a["id"]) or {}
        lim = limits(u.get("rateLimits")) or limits({"status": "fetching" if u.get("isFetching") else "idle"})
        if lim["updated_at"] is None:
            lim["updated_at"] = _sec(u.get("updatedAt"))
        out.append({"label": _text(a.get("email"), 200) or "이름 없는 계정", "org": org(a.get("organizationName"), a.get("email")),
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


def _call(refresh, timeout):
    global _last
    with _rpc:
        snap = orca_rpc.call("accounts.list", {"refreshUsage": refresh}, timeout=timeout)
    providers = normalize(snap)
    _last = (time.time(), providers)
    return providers


def _refresh_behind():
    try:
        _call(True, 45)
    except Exception:  # 다음 간격에 다시 시도한다. 마지막 값은 이미 주었다
        pass


def _behind():
    threading.Thread(target=_refresh_behind, name="jarvis-orca-usage", daemon=True).start()


def _codex(body, ca):
    """caam 의 Codex 계정을 Orca 의 Codex 목록에 더한다. 같은 이메일이 Orca 에 값과 함께 있으면 Orca 를 쓴다:
    Orca 는 Codex 가 실제로 쓰는 로그인을 읽고, caam 금고 사본의 접근 토큰은 Codex 가 갱신하면 낡는다."""
    cur = next((p for p in body["providers"] if p["id"] == "codex"), None)
    if cur is None:
        cur = {"id": "codex", "name": "Codex", "accounts": []}
        at = next((i + 1 for i, p in enumerate(body["providers"]) if p["id"] == "claude"), 0)
        body["providers"].insert(at, cur)
    plan = lambda a: (a.get("plan") or "").split(" · ")[0].lower()
    # 같은 이메일이라도 ChatGPT 개인·Team 워크스페이스는 한도가 따로다: 요금제가 둘 다 알려져 있고 다르면 다른 계정이다
    same = lambda o, a: o["label"].lower() == a["label"].lower() and (not plan(o) or not plan(a) or plan(o) == plan(a))
    orca_rows = list(cur["accounts"])
    if any(a["active"] for a in ca["accounts"]):  # cswap 과 같은 규칙: 이메일 모르는 시스템 기본 계정은 caam 의 사용 중 계정이다
        orca_rows = [o for o in orca_rows if o["label"] != "시스템 기본 계정"]
    accounts, added = list(orca_rows), []
    for a in ca["accounts"]:
        i = next((i for i, o in enumerate(orca_rows) if same(o, a)), None)  # caam 프로필끼리는 비교하지 않는다(둘 다 남긴다)
        if i is None:
            added.append(a)
        elif not (orca_rows[i]["windows"] or orca_rows[i]["buckets"]):
            accounts[accounts.index(orca_rows[i])] = {**a, "active": orca_rows[i]["active"] or a["active"]}
    accounts += added
    body["providers"] = [{**p, "accounts": accounts} if p is cur else p for p in body["providers"]]


def read(force=False):
    """(http 상태, 응답). Orca 값에 cswap(Claude)·caam(Codex)의 계정별 사용량을 합친다.
    셋은 동시에 읽는다(차례로 읽으면 강제 새로고침이 core 의 시간 제한을 넘을 수 있다)."""
    start = time.monotonic()
    box = {}
    ts = [threading.Thread(target=lambda m=m, k=k: box.update({k: m.read(force)}), name="jarvis-" + k, daemon=True)
          for k, m in (("cswap", cswap_usage), ("caam", caam_usage))]
    for t in ts:
        t.start()
    body = _orca(force)
    end = start + 60  # 요청이 들어온 때부터 센다: core 는 기기당 70초를 기다린다
    for t in ts:
        t.join(max(0, end - time.monotonic()))
    cs = box.get("cswap") if not ts[0].is_alive() else {"error": "cswap 이 늦게 답합니다"}
    ca = box.get("caam") if not ts[1].is_alive() else {"error": "caam 이 늦게 답합니다"}
    if cs and cs.get("accounts"):
        mine = [{**a, "org": org(a["org"], a["label"])} for a in cs["accounts"]]  # cswap 결과는 캐시라 고치지 않는다
        seen = {(a["label"].lower(), a["org"]) for a in mine}
        emails = {a["label"].lower() for a in mine}
        old = next((p for p in body["providers"] if p["id"] == "claude"), None)
        # Orca 의 '시스템 기본 계정'은 cswap 의 사용 중 계정과 같은 로그인이다(cswap 에 사용 중 계정이 있을 때만 뺀다).
        # Orca 에만 있는 관리 계정(같은 이메일의 다른 조직 포함)은 남긴다
        # 조직을 모르는 Orca 줄(시스템 기본 계정이 조직 이름을 못 빌린 경우)은 같은 이메일이면 같은 로그인으로 본다
        extra = [a for a in (old or {}).get("accounts", []) if (a["label"].lower(), a.get("org")) not in seen
                 and not (a.get("org") is None and a["label"].lower() in emails)
                 and not (a["label"] == "시스템 기본 계정" and any(m["active"] for m in mine))]
        body["providers"] = [{"id": "claude", "name": "Claude", "accounts": mine + extra}] + \
            [p for p in body["providers"] if p["id"] != "claude"]
        body.setdefault("fetched_at", cs["fetched_at"])  # Orca 를 못 읽은 기기도 수집 시각이 보이게
    elif cs:
        body["cswap_error"] = cs["error"]
    if ca and ca.get("accounts"):
        _codex(body, ca)
        body.setdefault("fetched_at", ca["fetched_at"])
    elif ca:
        body["caam_error"] = ca["error"]
    if any(a.get("failure") == "delegated-refresh-required" for p in body["providers"] if p["id"] == "grok"
           for a in p["accounts"]):
        cli_login.refresh_grok()
    if logins := cli_login.available():
        body["logins"] = logins
    return 200, body


def _orca(force):
    """force: 사용자가 누른 새로고침. 프로바이더에서 다시 읽은 뒤 답한다(FORCE_S 간격 안이면 보관값)."""
    global _refreshed
    d = orca.find_dir(orca.DIRS)
    if not d:
        return {"installed": False, "running": False, "providers": []}
    with _lock:  # 결정과 시각 기록만 잠근다. RPC 동안 잡고 있으면 다른 요청이 core 의 시간 제한을 넘긴다
        age = time.monotonic() - _refreshed
        now = force and (not _refreshed or age > FORCE_S)
        behind = not now and (not _refreshed or age > REFRESH_S)
        if now or behind:
            _refreshed = time.monotonic()
    last = _last
    if not now and last and (behind or _rpc.locked()):
        if behind:
            _behind()
        return {"installed": True, "running": True, "fetched_at": last[0], "refreshed": False, "refreshing": True,
                "providers": list(last[1])}
    try:
        if now:
            try:
                # 뒤에서 이미 다시 읽는 중이면 끝나기를 기다려 그 값을 읽는다(같은 계정을 거듭 부르지 않게)
                providers = _call(not _rpc.locked(), 45)
            except Exception:
                providers = _call(False, 12)  # 다시 읽기가 늦거나 실패하면 보관값이라도
        else:
            providers = _call(False, 12)
    except Exception as e:
        return {"installed": True, "running": False, "providers": [], "error": scrub_home(str(e)[:200] or type(e).__name__)}
    if behind:  # 처음(마지막 값이 없을 때)은 보관값을 먼저 읽고 나서 뒤에서 다시 읽는다
        _behind()
    return {"installed": True, "running": True, "fetched_at": time.time(), "refreshed": now, "refreshing": behind,
            "providers": list(providers)}
