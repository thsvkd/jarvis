"""hswap 이 재는 Claude 밖 하네스(Codex·Grok·OpenCode)의 계정별 사용량을 읽기만 한다.

하네스마다 `hswap list <하네스> --json` 을 한 번 부른다(Claude 는 claude_accounts 가 `hswap claude list` 로 따로 읽어 같은 하네스를 두 번 재지 않는다).
설정(usage.remote)·계정 등록·전환·자동 회전은 하지 않는다. 측정 간격과 실패 백오프는 hswap 이 정한다.
원격 측정은 하네스 CLI 를 띄워 수십 초가 걸릴 수 있어 뒤에서 읽고, 기다리는 시간 안에 끝나지 않으면 마지막 답을 준다.
계정은 이메일만 내보낸다. identity·details(계정 ID)·hswap 의 detail 원문은 내보내지 않는다.

공식 계약: https://github.com/thsvkd/harness-swap/tree/v0.2.1 (src/harness_swap/cli.py cmd_list, core/models.py UsageSnapshot·QuotaWindow·UsageState)
"""
import json
import math
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from . import caam_usage, claude_accounts, cli_login, provider_usage
from .util import local_bin

WAIT_S = 20  # 한 번의 읽기가 hswap 을 기다리는 시간. 넘기면 마지막 답이나 기존 수집기로 답한다(수집 대기 55초 안)
TIMEOUT_S = 240  # hswap 프로세스 한도. Grok 은 RPC 120초, OpenCode 는 목록 120초 + 사용량 60초까지 쓴다
# UsageState → (화면 문구, 사람이 손봐야 하는가). detail 원문은 오류 경로가 섞일 수 있어 쓰지 않는다
STATES = {
    "stale": ("이전 값입니다(hswap 이 지금은 다시 재지 않았습니다).", False),
    "unknown": ("사용률을 확인하지 못했습니다.", False),
    "unsupported": ("hswap 이 이 계정의 사용량을 재지 않습니다(usage.remote 설정을 확인하세요).", False),
    "authentication_required": ("다시 로그인해야 합니다.", True),
    "rate_limited": ("프로바이더가 요청을 제한하고 있습니다.", True),
    "error": ("사용량을 읽지 못했습니다.", False),
}
# 알려진 detail(hswap 0.2.1 adapters/grok.py)만 한국어로 바꿔 쓴다
DETAILS = {"Grok reported no included-credit usage": "포함 크레딧 사용량이 없습니다(요금제·잔액만 확인)."}
WINDOW_NAMES = {"weekly": "weekly", "monthly": "monthly"}  # 길이를 모르는 창(OpenCode 월간 등)은 이름으로 정한다
_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="jarvis-hswap")
_lock = threading.Lock()
_jobs = {}  # 하네스 → 마지막으로 띄운 Future
_last = {}  # 하네스 → 마지막으로 끝난 읽기의 행(없으면 None)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _extra(balance):
    """hswap balance → 화면의 extra. Codex {balance, hasCredits, unlimited}, Grok {currency, onDemandCap, onDemandUsed, ...}."""
    b = balance if isinstance(balance, dict) else {}
    if "hasCredits" in b or "unlimited" in b:
        value = b.get("balance")
        return {"unit": "credits", "balance": str(value)[:20] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None,
                "unlimited": b.get("unlimited") is True, "enabled": b.get("hasCredits") is True}
    if "onDemandCap" in b or "onDemandUsed" in b:
        cap = _num(b.get("onDemandCap"))
        return {"unit": "currency", "spent": _num(b.get("onDemandUsed")), "limit": cap,
                "currency": str(b.get("currency") or "USD")[:8], "enabled": bool(cap)}
    return None


def account(row):
    """hswap 계정 한 줄 → 화면 계정. 값(창·잔액·측정 시각)이 있으면 그것을 보이고 상태는 note 로 붙인다."""
    u = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    windows = []
    for w in u.get("windows") if isinstance(u.get("windows"), list) else []:
        used = claude_accounts._pct(w.get("usedPercent")) if isinstance(w, dict) else None
        if used is None:
            continue
        seconds = _num(w.get("windowSeconds"))
        key = caam_usage._key(seconds) if seconds and seconds > 0 else WINDOW_NAMES.get(w.get("name"), "기간 미확인")
        windows.append({"key": key, "used": used, "resets_at": claude_accounts._sec(w.get("resetsAt"))})
    extra, at = _extra(u.get("balance")), claude_accounts._sec(u.get("measuredAt"))
    note, attention = STATES.get(u.get("state"), (None, False)) if u.get("state") != "available" else (None, False)
    note = DETAILS.get(u.get("detail"), note)
    has = bool(windows or extra or at)
    label = row.get("label")
    plan = " · ".join(str(x)[:40] for x in (row.get("alias"), u.get("plan")) if isinstance(x, str) and x)
    return {"label": label if isinstance(label, str) and len(label) <= 200 and provider_usage.EMAIL.fullmatch(label)
            else "API 키 계정" if row.get("authMode") in ("api_key", "key") else "이름 없는 계정",
            "org": None, "active": row.get("active") is True,
            "status": "ok" if has else "error", "error": None if has else note or "사용량을 확인할 수 없습니다.",
            "note": note if has else None, "attention": attention, "failure": None, "plan": plan or None, "updated_at": at,
            "windows": windows, "buckets": [], "extra": extra, "credits": None, "source": "hswap"}


def _fetch(exe, harness):
    """hswap list <하네스> --json → 계정 행. 하네스가 없거나 계정이 없거나 읽지 못하면 None."""
    try:
        # hswap 은 하네스 CLI(codex·grok·opencode)를 PATH 로 찾는다. 서비스의 PATH 에는 사용자 설치 위치가 없어 "not installed" 로 답한다
        path = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
        tools = [local_bin(), os.path.join(cli_login._grok_home(), "bin"), os.path.join(os.path.expanduser("~"), ".opencode", "bin"),
                 *(("/opt/homebrew/bin", "/usr/local/bin") if sys.platform == "darwin" else ())]
        env = {**os.environ, "PATH": os.pathsep.join([d for d in tools if d not in path] + path)}
        p = subprocess.run([exe, "list", harness, "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S,
                           env=env, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        body = json.loads(p.stdout)  # 하네스 하나라도 실패하면 종료 코드 1 이지만 JSON 은 정상이다
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if not isinstance(body, dict) or body.get("schemaVersion") != 1 or not isinstance(body.get("harnesses"), list):
        return None
    rows = [a for h in body["harnesses"] if isinstance(h, dict) and h.get("harness") == harness
            for a in (h.get("accounts") if isinstance(h.get("accounts"), list) else []) if isinstance(a, dict)
            # OpenCode 는 여러 프로바이더를 앞에 둔다. Go 한도는 OpenCode Console 로그인만 잰다(adapters/opencode.py CONSOLE)
            and (harness != "opencode" or a.get("provider") == "opencode")]
    return [account(a) for a in rows] or None


def _job(exe, harness):
    out = _fetch(exe, harness)
    _last[harness] = out
    return out


def read(harness):
    """계정 행 목록 또는 None(hswap 없음·하네스 없음·계정 없음·아직 답 없음). 읽기가 WAIT_S 안에 끝나지 않으면 뒤에서 계속 읽고 마지막 답을 준다."""
    exe = claude_accounts.binary()
    if not exe:
        return None
    with _lock:
        job = _jobs.get(harness)
        if job is None or job.done():
            job = _jobs[harness] = _pool.submit(_job, exe, harness)
    try:
        return job.result(timeout=WAIT_S)
    except FutureTimeout:
        return _last.get(harness)


def merge(rows, fallback):
    """hswap 행에 값이 있으면 그것만 쓴다(기존 수집기가 같은 계정을 다시 재지 않게). 없으면 기존 수집기 행에
    그것이 모르는 이메일의 hswap 행(재로그인 안내 등)을 더한다."""
    if any(r["status"] == "ok" for r in rows or []):
        return rows
    try:
        old = fallback()
    except Exception:  # 기존 수집기가 없어도(caam 없음 등) hswap 행은 보인다
        old = []
    seen = {str(r.get("label")).lower() for r in old if isinstance(r, dict)}
    return old + [r for r in rows or [] if r["label"].lower() not in seen]
