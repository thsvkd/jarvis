"""hswap 이 재는 Claude 밖 하네스(Codex·Grok·OpenCode)의 계정별 사용량을 읽고(USAGE-8), 게이트웨이가 도는 동안 로그인을 유지한다(USAGE-9).

하네스마다 `hswap list <하네스> --json` 을 한 번 부른다(Claude 는 claude_accounts 가 `hswap claude list` 로 따로 읽어 같은 하네스를 두 번 재지 않는다).
읽기는 설정·등록·전환을 하지 않는다. 측정 간격과 실패 백오프는 hswap 이 정한다. 등록·usage.remote·refresh 는 maintain() 만 한다(전환·자동 회전은 하지 않는다).
원격 측정은 하네스 CLI 를 띄워 수십 초가 걸릴 수 있어 뒤에서 읽고, 기존 수집기와 동시에 돌려 기다리는 시간 안에 끝나지 않으면 마지막 답을 준다.
계정은 이메일만 내보낸다. identity·details(계정 ID)·hswap 의 detail 원문은 내보내지 않는다.

공식 계약: https://github.com/thsvkd/harness-swap/tree/v0.2.1 (src/harness_swap/cli.py cmd_list, core/models.py UsageSnapshot·QuotaWindow·UsageState)
"""
import json
import math
import os
import re
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from . import caam_usage, claude_accounts, provider_usage
from .util import _NO_WINDOW, scrub_home, tool_path, which_tool

WAIT_S = 8  # 읽기를 시작한 뒤 hswap 을 기다리는 시간. 기존 수집기가 그보다 오래 걸리면 그동안 함께 기다린다(수집 대기 55초 안)
LAST_MAX_S = 1800  # 이보다 오래된 마지막 답은 버리고 기존 수집기 값을 쓴다
TIMEOUT_S = 240  # hswap 프로세스 한도. Grok 은 RPC 120초, OpenCode 는 목록 120초 + 사용량 60초까지 쓴다
KEEP_S = 3600  # 로그인 유지 주기. refresh 는 기한이 된 토큰만 갱신하고 하네스가 실행 중이면 건너뛴다
# 로그인 유지(add·refresh) 대상. OpenCode 는 스스로 로그인을 갱신하고(0.2.1 CREDENTIAL_REFRESH unsupported), 네이티브 모드의 add 는
# 지금 로그인을 등록하지 않고 대화형 `opencode auth login` 을 띄워 뺀다(hswap 이 OpenCode 의 저장 로그인을 목록에서 스스로 등록한다)
KEEP = ("codex", "grok")
REMOTE = "codex,grok,opencode"  # usage.remote 가 비어 있을 때 정하는 값. Antigravity 는 약관 위험이 커서 넣지 않는다(2026-11-08 사용자 결정)
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
# 쉬는 OpenCode Console 계정은 활성일 때만 재진다(hswap adapters/opencode.py). 값 없이 이 답이면 오류가 아니라 보일 것이 없는 행이다
INACTIVE_CONSOLE = "measured only while it is the active OpenCode Console account"
WINDOW_NAMES = {"weekly": "weekly", "monthly": "monthly"}  # 길이를 모르는 창(OpenCode 월간 등)은 이름으로 정한다
_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="jarvis-hswap")
_lock = threading.Lock()
_jobs = {}  # 하네스 → 마지막으로 띄운 Future
_last = {}  # 하네스 → (monotonic, 마지막으로 끝난 읽기의 행 또는 None)


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


def _str(v):
    """hswap 의 문자열 필드. 다른 형식이면 없는 것으로 본다."""
    return v if isinstance(v, str) else None


def account(row):
    """hswap 계정 한 줄 → 화면 계정. 값(창·잔액, 또는 요금제와 측정 시각)이 있으면 그것을 보이고 상태는 note 로 붙인다."""
    u = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    windows = []
    for w in u.get("windows") if isinstance(u.get("windows"), list) else []:
        used = claude_accounts._pct(w.get("usedPercent")) if isinstance(w, dict) else None
        if used is None:
            continue
        seconds = _num(w.get("windowSeconds"))
        key = caam_usage._key(seconds) if seconds and seconds > 0 else WINDOW_NAMES.get(_str(w.get("name")), "기간 미확인")
        windows.append({"key": key, "used": used, "resets_at": claude_accounts._sec(w.get("resetsAt"))})
    extra, at = _extra(u.get("balance")), claude_accounts._sec(u.get("measuredAt"))
    state = _str(u.get("state"))
    note, attention = STATES.get(state, (None, False))
    note = DETAILS.get(_str(u.get("detail")), note)
    plan = (_str(u.get("plan")) or "")[:40]  # 별칭(alias)은 사람이 붙인 이름이라 내보내지 않는다
    has = bool(windows or extra or (plan and at))
    if not has and state == "stale":  # 이전 값을 줄 측정이 아직 한 번도 없었다(토큰 갱신 대기 등)
        note = "아직 잰 값이 없습니다(hswap 이 지금은 재지 않았습니다)."
    label = row.get("label")
    return {"label": label if isinstance(label, str) and len(label) <= 200 and provider_usage.EMAIL.fullmatch(label)
            else "API 키 계정" if row.get("authMode") in ("api_key", "key") else "이름 없는 계정",
            "org": None, "active": row.get("active") is True,
            "status": "ok" if has else "error", "error": None if has else note or "사용량을 확인할 수 없습니다.",
            "note": note if has else None, "attention": attention, "failure": None, "plan": plan or None, "updated_at": at,
            "windows": windows, "buckets": [], "extra": extra, "credits": None, "source": "hswap",
            # stale·error 행은 마지막 측정값을 싣고 온다. merge 가 판정에만 쓰고 지운다
            "_prior": state in ("stale", "error")}


def _run(exe, *args):
    """hswap 을 한 번 실행해 (종료 코드, stdout). 실행하지 못했거나 TIMEOUT_S 를 넘기면 (None, "")."""
    try:
        # hswap 은 하네스 CLI(codex·grok·opencode)를 PATH 로 찾는다. 서비스의 PATH 에는 사용자 설치 위치가 없어 "not installed" 로 답한다
        env = {**os.environ, "PATH": tool_path()}
        # 새 세션으로 띄워 시간 초과 때 hswap 이 띄운 하네스 CLI 까지 함께 끝낸다
        proc = subprocess.Popen([exe, *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace", env=env, creationflags=_NO_WINDOW, start_new_session=os.name != "nt")
    except OSError:
        return None, ""
    try:
        out, _ = proc.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _kill(proc)
        return None, ""
    return proc.returncode, out


def _json(out):
    try:
        body = json.loads(out)
    except ValueError:
        return None
    return body if isinstance(body, dict) and body.get("schemaVersion") == 1 else None


def _unmeasured_inactive(row):
    """쉬는 계정이라 hswap 이 아직 한 번도 재지 못한 행(창·잔액 없음). 보이면 '알 수 없는 오류'로 센다."""
    u = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    return (row.get("active") is not True and u.get("state") == "stale" and u.get("detail") == INACTIVE_CONSOLE
            and not u.get("windows") and not u.get("balance"))


def _fetch(exe, harness):
    """hswap list <하네스> --json → 계정 행. 하네스가 없거나 계정이 없거나 읽지 못하면 None."""
    _, out = _run(exe, "list", harness, "--json")
    body = _json(out)  # 하네스 하나라도 실패하면 종료 코드 1 이지만 JSON 은 정상이다
    if body is None or not isinstance(body.get("harnesses"), list):
        return None
    rows = [a for h in body["harnesses"] if isinstance(h, dict) and h.get("harness") == harness
            for a in (h.get("accounts") if isinstance(h.get("accounts"), list) else []) if isinstance(a, dict)
            # OpenCode 는 여러 프로바이더를 앞에 둔다. Go 한도는 OpenCode Console 로그인만 잰다(adapters/opencode.py CONSOLE)
            and (harness != "opencode" or a.get("provider") == "opencode")
            and not _unmeasured_inactive(a)]
    return [account(a) for a in rows] or None


def _kill(proc):
    """시간 초과한 hswap 과 그 자식(하네스 CLI)을 끝낸다. 파이프를 쥔 손자가 남아도 기다리지 않는다(cli_login._codex_keyring_allowed 와 같은 정리)."""
    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:  # 이미 끝났거나(ProcessLookupError) macOS 가 좀비 그룹에 EPERM 을 준다
            pass
    elif proc.poll() is None:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, creationflags=_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    proc.stdout.close()


def _job(exe, harness):
    try:
        out = _fetch(exe, harness)
    except Exception:  # 형식이 어긋난 답 하나가 기존 수집기 값을 막지 않게 한다
        out = None
    _last[harness] = (time.monotonic(), out)
    return out


def _start(harness):
    """hswap 읽기를 뒤에서 시작한다(진행 중이면 그것). hswap 이 없으면 None."""
    exe = claude_accounts.binary()
    if not exe:
        return None
    with _lock:
        job = _jobs.get(harness)
        if job is None or job.done():
            job = _jobs[harness] = _pool.submit(_job, exe, harness)
    return job


def keep(exe):
    """로그인 유지 한 번(USAGE-9). usage.remote 가 비어 있으면 REMOTE 로 정하고, CLI 가 있는 하네스마다 지금 로그인이 hswap 에
    없으면 add(전환은 하지 않는다) 한 뒤 refresh 로 하네스가 기한이 된 토큰을 스스로 갱신하게 한다. 하네스 → refresh 의 답 요약."""
    code, out = _run(exe, "config", "get", "usage.remote")
    if code == 0 and not out.strip():  # 사람이 정한 값(빈 값이 아니면)은 덮어쓰지 않는다
        _run(exe, "config", "set", "usage.remote", REMOTE)
    done = {}
    for h in KEEP:
        if not which_tool(h):  # CLI 가 없으면 hswap 도 add·refresh 를 하지 못한다
            continue
        status = _json(_run(exe, h, "status", "--json")[1]) or {}
        if status.get("activeKnown") is True and status.get("active") == []:  # 로그인이 없으면 hswap 이 실패로 답할 뿐이다
            _run(exe, h, "add")
        body = _json(_run(exe, h, "refresh", "--json")[1]) or {}
        err = body.get("error") if isinstance(body.get("error"), dict) else None
        # 계정별 실패는 accounts[].ok=false 로 온다(result 는 오류 문장이라 쓰지 않는다)
        bad = sum(1 for r in body.get("accounts") or [] if isinstance(r, dict) and r.get("ok") is False)
        done[h] = (f"오류({re.sub(r'[^A-Za-z_]', '', str(err.get('type')))[:40]})" if err else f"실패 {bad}개" if bad
                   else "확인함" if body else "답 없음")
    return done


def maintain():
    """게이트웨이가 도는 동안 KEEP_S 마다: hswap 버전을 맞추고(USAGE-5) 로그인을 유지한다(USAGE-9).
    Claude 는 0.2.1 에 refresh 가 없어, 같은 주기로 hswap claude list 를 묻는 읽기로 hswap 이 토큰을 갱신하게 한다."""
    while True:
        for step in (_check, _keep_once, lambda: which_tool("claude") and claude_accounts.read()):
            try:
                step()
            except Exception as e:
                print(f"[jarvis] hswap 유지 실패: {scrub_home(str(e))[:200]}", flush=True)
        time.sleep(KEEP_S)


def _check():
    """버전을 맞추고, 지금 설치를 시작했으면 끝날 때까지(최대 설치 한도) 기다려 새 기기에서도 첫 유지를 한 시간 미루지 않는다."""
    claude_accounts.check()
    end = time.monotonic() + claude_accounts.INSTALL_TIMEOUT_S
    while claude_accounts.installer.state == "running" and time.monotonic() < end:
        time.sleep(5)


def _keep_once():
    exe = claude_accounts.binary()
    if not exe or claude_accounts.installer.state == "running":  # 설치하는 동안에는 hswap 을 띄우지 않는다
        return
    done = keep(exe)
    if any(v != "확인함" for v in done.values()):
        print(f"[jarvis] hswap 로그인 유지: {done}", flush=True)


def read(harness, fallback):
    """hswap 과 기존 수집기(fallback)를 함께 돌려 계정마다 한 행으로 합친다(merge).
    hswap 은 시작한 뒤 WAIT_S 까지(기존 수집기가 더 오래 걸리면 그것이 끝날 때까지) 기다리고, 그때까지 답이 없으면
    LAST_MAX_S 안의 마지막 답을 쓰며 뒤에서 계속 읽는다."""
    started = time.monotonic()
    job = _start(harness)
    try:
        old = fallback()
    except Exception:  # 기존 수집기가 없어도(caam 없음 등) hswap 행은 보인다
        old = []
    rows = None
    if job:
        try:
            rows = job.result(timeout=max(0, started + WAIT_S - time.monotonic()))
        except FutureTimeout:
            at, rows = _last.get(harness, (0, None))
            rows = rows if time.monotonic() - at <= LAST_MAX_S else None
    return merge(rows, old if isinstance(old, list) else [])


def _email(r):
    """같은 계정을 가르는 열쇠: 이메일(대소문자 무시). 이메일이 아닌 라벨(기본 계정 등)은 서로 다른 계정으로 본다."""
    label = r.get("label")
    return label.lower() if isinstance(label, str) and provider_usage.EMAIL.fullmatch(label) else id(r)


def merge(rows, old):
    """계정마다 한 행. 같은 이메일이 양쪽에 있으면 값이 있는 hswap 행을 쓰되, hswap 값이 이전 값(stale·error)이고
    기존 수집기 값이 더 새로우면 그것을 쓴다. hswap 에 값이 없으면 값이 있는 기존 수집기 행을 쓴다.
    한쪽에만 있는 계정은 그대로 남는다(caam 의 다른 계정, 네이티브 기본 계정, hswap 의 재로그인 안내 등)."""
    def pick(h, o):
        if o.get("status") != "ok":
            return h
        if h["status"] != "ok":
            return o
        return o if prior.get(id(h)) and (o.get("updated_at") or 0) > (h.get("updated_at") or 0) else h

    rows = [dict(h) for h in rows or []]  # 마지막 답(_last)은 다음 읽기에도 쓰므로 사본에서 판정 필드를 지운다
    prior = {id(h): h.pop("_prior", False) for h in rows}
    hswap = {_email(h): h for h in rows}
    chosen, rest = dict(hswap), []  # hswap 순서를 지키고, 짝이 없는 기존 수집기 행은 뒤에 둔다
    active = next((h for h in rows or [] if h.get("active")), None)
    for o in (o for o in old if isinstance(o, dict)):
        # 이메일을 모르는 활성 기본 계정(네이티브 수집기)은 hswap 의 활성 계정과 같은 로그인이다
        same = active is not None and o.get("active") is True and o.get("label") == "기본 계정"
        k = _email(active) if same else _email(o)
        if k in hswap:
            chosen[k] = pick(hswap.pop(k), o)
        else:
            rest.append(o)
    return list(chosen.values()) + rest
