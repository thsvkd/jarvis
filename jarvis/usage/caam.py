"""이 기기 caam(coding_agent_account_manager)을 부르는 공용 도우미. 프로바이더 계정별 사용량 해석도 여기서 한다.

`caam ls <프로바이더> --json`(프로필 → 이메일·상태)과 `caam limits <프로바이더> --format json`(한도)만 부른다.
limits 는 caam 금고(vault)의 접근 토큰으로 사용량 API 를 읽기만 하고 토큰을 갱신하지 않는다(v0.1.24 cmd/caam/cmd/limits.go).
caam refresh·daemon·activate 는 부르지 않는다: Codex 갱신 토큰은 쓸 때마다 바뀌어 같은 로그인을 두 곳이 갱신하면 계정이 막힐 수 있다.
jarvis 는 caam 을 설치하지 않는다.

공식 계약: https://github.com/Dicklesworthstone/coding_agent_account_manager (internal/usage/usage.go 의 UsageInfo)
라이선스(MIT + OpenAI/Anthropic 추가 조항)는 사용자가 확인하고 도입을 정했다(2026-10-28).
"""
import json
import math
import os
import subprocess
import threading

from .common import email
from ..util import parse_iso, which_local

LS_TIMEOUT_S = 10
TIMEOUT_S = 35  # limits: caam 의 프로필당 HTTP 제한(30초)보다 길게. 한 프로필이 느려도 나머지를 잃지 않게
BUDGET_S = 50  # 잠금 대기 + ls + limits. 독립 사용량 수집이 이 안에서 기다린다
_lock = threading.Lock()

SUPPORTED = ("codex", "grok", "cursor")
# 사용량 계정 행의 failure 닫힌 코드(USAGE-17). 뜻은 static/shared/API.md 의 "사용량 계정 행의 failure" 표, 화면은 usage.js 의 CODES 가 같은 키를 쓴다
FAILURES = ("auth", "expiring", "rate", "limit", "wait", "keychain", "perm", "net", "server", "unsupported", "unknown")
LIMIT_NOTE = "사용 한도에 도달했습니다(리셋 시각에 풀립니다)."  # 한도 창 소진(limit)은 리셋 시각에 풀려 attention 이 아니다


def binary():
    return which_local("caam")


def _codex_cli():
    """Codex CLI 는 npm·Homebrew 로도 깔린다: 서비스의 짧은 PATH 에 없을 수 있어 흔한 위치도 본다."""
    return which_local("codex") or next((p for p in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex")
                                         if os.path.isfile(p)), None)


def _key(seconds):
    m = (seconds or 0) / 60
    return "session" if 0 < m <= 360 else "monthly" if m >= 40000 else "weekly"


def _json(exe, *args, timeout=TIMEOUT_S):
    try:
        p = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"caam 이 {timeout}초 안에 답하지 않았습니다")
    except OSError as e:
        raise RuntimeError(f"caam 을 실행하지 못했습니다({type(e).__name__})")
    try:
        return json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(f"caam {args[0]} 응답을 읽지 못했습니다(종료 코드 {p.returncode})")


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
            key = _key(duration / 1e9)
        else:
            key = ("session", "weekly", "monthly")[index] if provider == "codex" else "기간 미확인"
        windows.append({"key": key, "used": max(0, min(100, round(used))),
                        "resets_at": _time(window.get("resets_at"))})
    health = ident.get("health") if isinstance(ident.get("health"), dict) else {}
    login = health.get("login_required") is True
    limited = bool(usage.get("limit_stage"))
    degraded = usage.get("quota_status") in ("degraded", "unavailable")
    label = email(ident.get("identity"))
    observed = email({"email": usage.get("account_id")})
    mismatch = label != "이름 없는 계정" and observed != "이름 없는 계정" and label.lower() != observed.lower()
    if mismatch:
        label = "이름 없는 계정"
    if provider == "grok" and label == "이름 없는 계정":
        label = "기본 계정"
    attention = login or degraded or mismatch
    note = ("사용량과 계정 정보가 일치하지 않습니다." if mismatch else "다시 로그인이 필요할 수 있습니다." if login
            else "프로바이더가 한도 상태를 완전히 확인하지 못했습니다." if degraded else LIMIT_NOTE if limited else None)
    failure = ("auth" if login else "unknown" if mismatch else "limit" if limited else "unknown" if degraded or not windows else None)
    return {"label": label, "org": None, "active": ident.get("active") is True,
            "status": "ok" if windows else "error", "error": None if windows else "사용량을 확인할 수 없습니다.",
            "note": note if windows else None, "attention": attention,
            "failure": failure, "plan": None, "updated_at": _time(usage.get("fetched_at")),
            "windows": windows, "buckets": [], "extra": None, "credits": None, "source": "caam"}


def read_provider(exe, provider):
    """caam 에 저장된 vault 프로필에 대해서만 사용량을 조회한다."""
    if provider not in SUPPORTED:
        raise ValueError("지원하지 않는 caam 프로바이더")
    listing = _json(exe, "ls", provider, "--json", timeout=LS_TIMEOUT_S)
    profiles = listing.get("profiles") if isinstance(listing, dict) else None
    vault = {p["name"]: p for p in profiles or [] if isinstance(p, dict) and p.get("tool") == provider
             and p.get("type") == "vault" and isinstance(p.get("name"), str)}
    if not vault:
        return []
    data = _json(exe, "limits", provider, "--format", "json")
    rows = data if isinstance(data, list) else []
    by_name = {r["profile_name"]: r for r in rows if isinstance(r, dict) and r.get("provider") == provider
               and isinstance(r.get("profile_name"), str) and r["profile_name"] in vault}
    return [_account(by_name.get(name, {}), ident, provider) for name, ident in vault.items()]
