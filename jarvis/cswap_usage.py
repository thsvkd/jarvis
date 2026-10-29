"""이 기기 cswap(claude-swap)이 관리하는 Claude 계정별 사용량. orca_usage 가 Claude 계정 목록으로 쓴다.

`cswap list --json`(schemaVersion 1)만 읽는다: 토큰 갱신·사용량 조회 간격·캐시는 cswap 이 맡고, jarvis 는 자격 증명 파일을
열지 않는다(갱신 토큰을 따로 돌리면 cswap·Claude Code 의 로그인이 풀릴 수 있다). 계정은 이메일·조직 이름·슬롯 번호만 내보낸다.
쓰기는 core 의 '이 계정 사용' 버튼이 부르는 `cswap switch <슬롯> --json` 하나다(자격 교체는 cswap 이 한다).

공식 계약: https://github.com/realiti4/claude-swap (README 의 JSON 출력, claude_swap/json_output.py)
"""
import json
import math
import subprocess
import threading
import time

from . import config
from .util import Installer, parse_iso, scrub_home, which_local

LOGIN_WARN_S = 3 * 86400  # 로그인(갱신 토큰) 만료 3일 전부터 '주의 필요'. 새 /login 만이 기한을 늘린다
CACHE_S = 30  # cswap 자체가 캐시하므로 짧게만 묶는다(요청마다 프로세스를 띄우지 않게)
TIMEOUT_S = 15  # Orca 와 동시에 돈다. core 의 기기당 시간 제한(70초) 안에 들어와야 한다
SWITCH_TIMEOUT_S = 30  # switch 는 현재 로그인을 백업하고 macOS 는 Keychain 도 고친다. core 는 40초를 기다린다
# usageStatus → (화면 문구, 사람이 손봐야 하는가). ok·api_key·unavailable 은 따로 다룬다
STATUS = {
    "token_expired": ("토큰 갱신을 기다리는 중입니다(cswap 이 다시 시도합니다).", False),
    "relogin_required": ("다시 로그인해야 합니다(cswap).", True),
    "keychain_unavailable": ("Keychain 을 읽지 못했습니다.", True),
    "foreign_credential": ("지금 로그인이 다른 계정입니다. cswap switch 로 맞추세요.", True),
    "no_credentials": ("자격 증명이 없습니다.", True),
}
_lock = threading.Lock()
_cache = None  # (monotonic, 결과)
# 자동 설치: 검증한 버전으로 고정한다(서비스가 PyPI 의 최신판을 저절로 받아 실행하지 않게). 올리기는 cswap upgrade 로 사람이 한다
PACKAGE = "claude-swap==0.28.0"
INSTALL_TIMEOUT_S = 600  # 처음이면 uv 가 Python 3.12+ 까지 받는다


def binary():
    return which_local("cswap")


def _install():
    """uv tool install(없으면 pipx)."""
    uv, pipx = which_local("uv"), which_local("pipx")
    if not (uv or pipx):
        raise RuntimeError("uv·pipx 가 없어 cswap 을 설치할 수 없습니다")
    cmd = [uv, "tool", "install", PACKAGE] if uv else [pipx, "install", PACKAGE]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=INSTALL_TIMEOUT_S,
                           stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"cswap 설치가 {INSTALL_TIMEOUT_S}초 안에 끝나지 않았습니다")
    if p.returncode or not binary():
        last = (p.stderr.strip().splitlines() or ["원인 모름"])[-1]
        raise RuntimeError(f"cswap 설치 실패(종료 코드 {p.returncode}): {last}")


installer = Installer("cswap", _install)


def ensure():
    """cswap 이 없으면 뒤에서 설치를 시작한다. Claude Code 가 없는 기기는 cswap 이 쓸모없어 설치하지 않는다.
    None(설치하지 않음) 또는 화면에 붙일 문구."""
    if not config.CSWAP_INSTALL or not which_local("claude"):
        return None
    return installer.ensure("Claude 계정별 사용량")


def _sec(iso):
    return parse_iso(iso) if isinstance(iso, str) else None


def _pct(v):
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    return max(0, min(100, round(v))) if ok else None


def _windows(u):
    """cswap usage → (windows, buckets). Fable 은 화면의 'fable' 창으로, 그 밖의 모델별 창은 bucket 으로."""
    u = u if isinstance(u, dict) else {}
    wins, buckets = [], []
    for k, key in (("fiveHour", "session"), ("sevenDay", "weekly")):
        w = u.get(k)
        if isinstance(w, dict) and _pct(w.get("pct")) is not None:
            wins.append({"key": key, "used": _pct(w["pct"]), "resets_at": _sec(w.get("resetsAt"))})
    for s in u.get("scoped") if isinstance(u.get("scoped"), list) else []:
        if not isinstance(s, dict) or _pct(s.get("pct")) is None:
            continue
        name = str(s.get("name") or "")[:80]
        if name.lower() == "fable":
            wins.append({"key": "fable", "used": _pct(s["pct"]), "resets_at": _sec(s.get("resetsAt"))})
        else:
            buckets.append({"name": name, "used": _pct(s["pct"]), "resets_at": _sec(s.get("resetsAt"))})
    return wins, buckets


def account(row):
    """cswap 계정 한 줄 → 화면 계정. 조회가 실패해도 마지막으로 읽은 값(lastGoodUsage)과 그 이유를 함께 준다.
    attention: 사람이 손봐야 다시 읽히는 상태(재로그인 등). 화면이 '주의 필요'로 센다."""
    status = row.get("usageStatus")
    usage, fetched = row.get("usage"), row.get("usageFetchedAt")
    if status != "ok" and isinstance(row.get("lastGoodUsage"), dict):
        usage, fetched = row["lastGoodUsage"], row.get("lastGoodFetchedAt")
    reason = str(row.get("usageError") or "")[:60]
    note, attention = STATUS.get(status, (None, False))
    if status == "api_key":
        note = "API 키 계정이라 구독 한도가 없습니다."
    elif status != "ok" and not note:
        note = "사용량을 읽지 못했습니다" + (f"({reason})" if reason else "") + "."
    left = (parse_iso(row.get("loginExpiresAt") or "") or 0) - time.time()
    if status not in ("relogin_required", "api_key") and 0 < left <= LOGIN_WARN_S:
        days = f"{left / 86400:.0f}일" if left >= 86400 else f"{max(1, left // 3600):.0f}시간"
        soon = f"로그인 만료 {days} 남음. 그 계정으로 다시 /login 하세요."
        note, attention = (note + " " + soon if note else soon), True
    windows, buckets = _windows(usage)
    plan = " · ".join(x for x in (str(row.get("alias") or "")[:40], "자동 전환 제외" if row.get("disabled") is True else "") if x)
    email = str(row.get("email") or "")[:200]
    num = row.get("number")
    return {"label": email or "이름 없는 계정", "org": str(row.get("organizationName") or "")[:80] or None,
            "active": row.get("active") is True,
            # 슬롯 번호: 기기마다 다르다. 같은 이메일이 조직별로 여러 슬롯일 수 있어 이메일 대신 이것으로 바꾼다
            "slot": str(num) if isinstance(num, int) and not isinstance(num, bool) and num > 0 else None,
            # 값이 있으면 그 값을 보이고 note 로 이유를 붙인다. 값도 없으면 오류로 보인다
            "status": "ok" if windows or buckets or status in ("ok", "api_key") else "error",
            "error": None if windows or buckets or status == "api_key" else note,
            "note": note if windows or buckets or status == "api_key" else None, "attention": attention,
            "failure": None, "plan": plan or None, "updated_at": _sec(fetched),
            "windows": windows, "buckets": buckets[:12], "extra": None, "credits": None, "source": "cswap"}


def _json(exe, *args, timeout=TIMEOUT_S):
    try:
        p = subprocess.run([exe, *args, "--json"], capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:  # 메시지에 실행 경로(홈 디렉터리)가 들어 있어 그대로 내보내지 않는다
        raise RuntimeError(f"cswap 이 {timeout}초 안에 답하지 않았습니다")
    except OSError as e:
        raise RuntimeError(f"cswap 을 실행하지 못했습니다({type(e).__name__})")
    try:
        body = json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(f"cswap {args[0]} --json 응답을 읽지 못했습니다(종료 코드 {p.returncode})")
    if not isinstance(body, dict) or body.get("error"):
        err = body.get("error") if isinstance(body, dict) else None
        raise RuntimeError(scrub_home(str((err.get("message") if isinstance(err, dict) else err) or "알 수 없는 응답"))[:200])
    return body


def _run(exe):
    return [account(r) for r in _json(exe, "list").get("accounts") or [] if isinstance(r, dict)]


def switch(slot):
    """'이 계정 사용' 버튼: Claude Code 가 쓰는 계정을 cswap 슬롯 slot 으로 바꾼다. (http 상태, 응답)."""
    global _cache
    if not (isinstance(slot, str) and slot.isdigit() and len(slot) <= 4):
        return 400, {"error": "slot 은 cswap 슬롯 번호여야 합니다"}
    exe = binary()
    if not exe:
        return 404, {"error": "이 기기에는 cswap 이 없습니다"}
    if not _lock.acquire(timeout=TIMEOUT_S + 5):  # list 와 겹치지 않게
        return 503, {"error": "cswap 이 응답하지 않습니다"}
    try:
        body = _json(exe, "switch", slot, timeout=SWITCH_TIMEOUT_S)
        return 200, {"switched": body.get("switched") is True}
    except Exception as e:
        return 409, {"error": scrub_home(str(e))[:200] or type(e).__name__}
    finally:
        _cache = None  # 실패해도 다음 읽기는 cswap 에 다시 묻는다
        _lock.release()


def read(force=False):
    """None(cswap 없음·계정 없음) 또는 {"accounts": [...], "fetched_at": 초} 또는 {"error": "..."}.
    cswap 이 없으면 ensure() 가 설치를 시작하고 그 진행·실패를 error 로 준다.
    force 는 jarvis 의 30초 묶음만 건너뛴다(프로바이더 조회 간격은 cswap 이 정한다)."""
    global _cache
    exe = binary()
    if not exe:
        msg = ensure()
        return {"error": msg} if msg else None
    asked = time.monotonic()
    # 동시에 여러 cswap 을 띄우지 않는다. 앞 요청이 끝나지 않으면(멈춘 cswap) 기다리지 않고 마지막 값을 준다
    if not _lock.acquire(timeout=TIMEOUT_S + 5):
        return _cache[1] if _cache else {"error": "cswap 이 응답하지 않습니다"}
    try:
        # 기다리는 동안 앞 요청이 새로 읽었으면 강제라도 그 값을 쓴다
        if _cache and (_cache[0] >= asked or (not force and asked - _cache[0] < CACHE_S)):
            return _cache[1]
        try:
            accounts = _run(exe)
            out = {"accounts": accounts, "fetched_at": time.time()} if accounts else None
        except Exception as e:
            out = {"error": scrub_home(str(e))[:200] or type(e).__name__}
        _cache = (time.monotonic(), out)
        return out
    finally:
        _lock.release()
