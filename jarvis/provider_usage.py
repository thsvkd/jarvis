"""caam 이 제공하는 프로바이더 계정별 사용량을 Orca 없이 읽는다.

https://github.com/Dicklesworthstone/coding_agent_account_manager/blob/v0.1.24/cmd/caam/cmd/limits.go
"""
import math
import re

from . import caam_usage
from .util import parse_iso

EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
SUPPORTED = ("codex", "grok", "cursor")


def _email(ident):
    ident = ident if isinstance(ident, dict) else {}
    email = ident.get("email")
    return email if isinstance(email, str) and len(email) <= 200 and EMAIL.fullmatch(email) else "이름 없는 계정"


def _time(value):
    return parse_iso(value) if isinstance(value, str) else None


def _account(row, ident, provider):
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    windows = []
    for index, name in enumerate(("primary_window", "secondary_window", "tertiary_window")):
        window = usage.get(name)
        if not isinstance(window, dict) or window.get("unmeasured") is True:
            continue
        used = window.get("used_percent")
        if not isinstance(used, (int, float)) or isinstance(used, bool) or not math.isfinite(used):
            continue
        duration = window.get("window_duration")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0:
            key = caam_usage._key(duration / 1e9)
        else:
            key = ("session", "weekly", "monthly")[index] if provider == "codex" else "기간 미확인"
        windows.append({"key": key, "used": max(0, min(100, round(used))),
                        "resets_at": _time(window.get("resets_at"))})
    health = ident.get("health") if isinstance(ident.get("health"), dict) else {}
    login = health.get("login_required") is True
    restricted = bool(usage.get("limit_stage")) or usage.get("quota_status") in ("degraded", "unavailable")
    label = _email(ident.get("identity"))
    observed = _email({"email": usage.get("account_id")})
    mismatch = label != "이름 없는 계정" and observed != "이름 없는 계정" and label.lower() != observed.lower()
    if mismatch:
        label = "이름 없는 계정"
    if provider == "grok" and label == "이름 없는 계정":
        label = "기본 계정"
    attention = login or restricted or mismatch
    note = ("사용량과 계정 정보가 일치하지 않습니다." if mismatch else "다시 로그인이 필요할 수 있습니다." if login
            else "프로바이더가 한도 상태를 완전히 확인하지 못했습니다.")
    return {"label": label, "org": None, "active": ident.get("active") is True,
            "status": "ok" if windows else "error", "error": None if windows else "사용량을 확인할 수 없습니다.",
            "note": note if attention and windows else None, "attention": attention,
            "failure": None, "plan": None, "updated_at": _time(usage.get("fetched_at")),
            "windows": windows, "buckets": [], "extra": None, "credits": None, "source": "caam"}


def read_provider(exe, provider):
    """caam 에 저장된 vault 프로필에 대해서만 사용량을 조회한다."""
    if provider not in SUPPORTED:
        raise ValueError("지원하지 않는 caam 프로바이더")
    listing = caam_usage._json(exe, "ls", provider, "--json", timeout=caam_usage.LS_TIMEOUT_S)
    profiles = listing.get("profiles") if isinstance(listing, dict) else None
    vault = {p["name"]: p for p in profiles or [] if isinstance(p, dict) and p.get("tool") == provider
             and p.get("type") == "vault" and isinstance(p.get("name"), str)}
    if not vault:
        return []
    data = caam_usage._json(exe, "limits", provider, "--format", "json")
    rows = data if isinstance(data, list) else []
    by_name = {r["profile_name"]: r for r in rows if isinstance(r, dict) and r.get("provider") == provider
               and isinstance(r.get("profile_name"), str) and r["profile_name"] in vault}
    return [_account(by_name.get(name, {}), ident, provider) for name, ident in vault.items()]
