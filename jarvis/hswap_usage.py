"""hswap 이 재는 Claude 밖 하네스(Codex·Grok·OpenCode)의 계정별 사용량을 읽고(USAGE-8), 게이트웨이가 도는 동안 로그인을 유지한다(USAGE-9).

하네스마다 `hswap list <하네스> --json` 을 한 번 부른다(Claude 는 claude_accounts 가 `hswap claude list` 로 따로 읽어 같은 하네스를 두 번 재지 않는다).
읽기는 설정·등록·전환을 하지 않는다. 측정 간격과 실패 백오프는 hswap 이 정한다. 등록·usage.remote·refresh 는 maintain() 만 한다(자동 전환은 사람이 켠 Claude 만, USAGE-14).
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
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, wait

from . import caam_usage, claude_accounts, config, provider_usage
from .util import _NO_WINDOW, scrub_home, tool_path, which_tool

WAIT_S = 8  # 읽기를 시작한 뒤 hswap 을 기다리는 시간. 기존 수집기가 그보다 오래 걸리면 그동안 함께 기다린다(수집 대기 55초 안)
FORCE_WAIT_S = 40  # 새로고침(refresh + 측정)을 기다리는 시간. 수집 대기(55초)와 core 의 사용량 시한(70초) 안이어야 한다
LAST_MAX_S = 1800  # 이보다 오래된 마지막 답은 버리고 기존 수집기 값을 쓴다
TIMEOUT_S = 240  # hswap 프로세스 한도. Grok 은 RPC 120초, OpenCode 는 목록 120초 + 사용량 60초까지 쓴다
KEEP_S = 3600  # 로그인 유지 주기. refresh 는 기한이 된 토큰만 갱신하고 하네스가 실행 중이면 건너뛴다
# 로그인 유지(add·refresh) 대상. OpenCode 는 스스로 로그인을 갱신하고(0.2.1 CREDENTIAL_REFRESH unsupported), 네이티브 모드의 add 는
# 지금 로그인을 등록하지 않고 대화형 `opencode auth login` 을 띄워 뺀다(hswap 이 OpenCode 의 저장 로그인을 목록에서 스스로 등록한다)
KEEP = ("codex", "grok")
SERVICE_FROM = "0.2.8"  # `hswap claude keepalive --install-service·--service-status` 를 아는 hswap
AHEAD_S = 7200  # 만료 2시간 전부터 갱신한다. 한 번 실패해도 고칠 시간이 남는다
TICK_S = 300  # macOS 밖에서 게이트웨이가 keepalive 를 직접 부르는 간격(macOS 는 hswap 의 LaunchAgent 가 같은 간격으로 부른다)
ROTATION = "priority-consume-first"  # 계정 priority 높은 것부터, 전환 기준 아래인 계정만 쓴다. 높은 계정이 풀리면 돌아간다(USAGE-14)
ROTATION_FROM = "0.2.9"  # 이 전략과 `hswap claude priority` 를 확인한 hswap
_rot_lock = threading.Lock()  # 자동 전환 설정 읽기·쓰기를 한 번에 하나만
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
# Grok 은 포함 크레딧 사용률이 0 이면 creditUsagePercent 를 생략한다(protobuf 기본값). 요금제·측정 시각이 있는 이 답은 0% 다.
# 2026-11-09 s2 실측: 같은 답에서 Grok 의 /usage 가 "Weekly limit (SuperGrok) 0%" 를 보였다
NO_CREDIT_USAGE = "Grok reported no included-credit usage"
# 쉬는 OpenCode Console 계정은 활성일 때만 재진다(hswap adapters/opencode.py). 값 없이 이 답이면 오류가 아니라 보일 것이 없는 행이다
INACTIVE_CONSOLE = "measured only while it is the active OpenCode Console account"
WINDOW_NAMES = {"weekly": "weekly", "monthly": "monthly"}  # 길이를 모르는 창(OpenCode 월간 등)은 이름으로 정한다
_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="jarvis-hswap")
_lock = threading.Lock()
_jobs = {}  # 하네스 → 마지막으로 띄운 Future
_forced = {}  # 하네스 → refresh 를 앞세워 띄운 Future(그 하네스의 _jobs 와 같으면 새로고침이 이미 들어 있다)
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
    plan = (_str(u.get("plan")) or "")[:40]  # 별칭(alias)은 사람이 붙인 이름이라 내보내지 않는다
    if not windows and state == "unknown" and _str(u.get("detail")) == NO_CREDIT_USAGE and plan and at:
        # shortcut: hswap 이 기간 종류와 리셋 시각을 넘기지 않아 주간·리셋 미상으로 둔다. hswap 이 period 를 내보내면 그 값을 쓴다
        windows, note = [{"key": "weekly", "used": 0, "resets_at": None}], None
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


def _job(exe, harness, refresh=False, prev=None):
    """refresh: 측정 전에 `hswap <하네스> refresh` 로 기한이 된 토큰을 먼저 갱신한다(하네스가 실행 중이면 hswap 이 건너뛴다).
    prev: 아직 도는 이전 읽기. 같은 하네스의 hswap 을 겹쳐 띄우지 않으려고 끝나기를 기다린다."""
    if prev is not None:
        wait([prev], timeout=TIMEOUT_S)
    if refresh and harness in KEEP and which_tool(harness):  # CLI 가 없으면 hswap 도 갱신하지 못한다
        _run(exe, harness, "refresh", "--json")
    try:
        out = _fetch(exe, harness)
    except Exception:  # 형식이 어긋난 답 하나가 기존 수집기 값을 막지 않게 한다
        out = None
    _last[harness] = (time.monotonic(), out)
    return out


def _start(harness, refresh=False):
    """hswap 읽기를 뒤에서 시작한다(진행 중이면 그것). hswap 이 없으면 None.
    refresh 인데 진행 중인 읽기에 새로고침이 없으면 그것이 끝난 뒤 refresh 를 앞세운 읽기를 새로 띄운다."""
    exe = claude_accounts.binary()
    if not exe:
        return None
    with _lock:
        job = _jobs.get(harness)
        if job is None or job.done() or (refresh and _forced.get(harness) is not job):
            job = _jobs[harness] = _pool.submit(_job, exe, harness, refresh, job)
            if refresh:
                _forced[harness] = job
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


def schedule():
    """Claude 로그인 자동 갱신의 예약 실행을 맞춘다(USAGE-11). macOS 는 키체인이 GUI 세션에서만 닿아 hswap 이 설치하는
    LaunchAgent 에 맡긴다: 예약이 없으면 설치하고, hswap 이 만든 예약이 적재되지 않았으면 그 설정(ahead·thenAuto) 그대로 다시 설치한다.
    적재된 예약과 hswap 이 만들지 않은 같은 이름의 서비스는 건드리지 않는다(사람이 바꾼 설정을 덮어쓰지 않는다).
    답한 상태는 로그인 판정(claude_accounts.service)에 쓴다. 할 일이 아니면 None 이고 지난 답도 지운다."""
    claude_accounts.service = None
    exe = claude_accounts.binary()
    v = claude_accounts._version
    if (sys.platform != "darwin" or not config.CSWAP_INSTALL or not exe or not which_tool("claude") or claude_accounts.installer.state == "running"
            or not v or claude_accounts._key(v) < claude_accounts._key(SERVICE_FROM) or not claude_accounts._store_read()):
        return None
    st = _json(_run(exe, "claude", "keepalive", "--service-status", "--json")[1])
    # hswap 이 만든 예약은 적재되지 않았거나 지금 hswap 이 만드는 모양보다 낡았을 때(outdated)만 그 설정 그대로 다시 설치한다
    if st and st.get("platform") == "launchd" and (st.get("installed") is False or (st.get("managed") is True and (st.get("loaded") is False or st.get("outdated") is True))):
        ahead = st.get("ahead") if isinstance(st.get("ahead"), (int, float)) and not isinstance(st.get("ahead"), bool) and st.get("ahead") > 0 else AHEAD_S
        # 0 설치·갱신, 3 GUI 세션이 없어 다음 로그인 때 적재. 어느 쪽이든 다시 물은 상태를 쓴다
        # 예약이 없을 때 사람이 자동 전환을 켜 두었으면(순서 전략, USAGE-14) 그것도 다시 붙인다
        then = st.get("thenAuto") is True or (st.get("installed") is False and _settings(exe).get("autoswitch.strategy") == ROTATION)
        code, _ = _run(exe, "claude", "keepalive", "--install-service", "--ahead", str(int(ahead)), *(["--then-auto"] if then else []), "--json")
        if code not in (0, 3):
            print(f"[jarvis] hswap keepalive 예약 설치 실패(종료 코드 {code})", flush=True)
        st = _json(_run(exe, "claude", "keepalive", "--service-status", "--json")[1]) or st
    claude_accounts.service = st
    return st


def _settings(exe):
    """hswap claude config --json 의 {키: 값}. 읽지 못하면 {}."""
    body = _json(_run(exe, "claude", "config", "--json")[1]) or {}
    return {s["key"]: s.get("value") for s in body.get("settings") or [] if isinstance(s, dict) and isinstance(s.get("key"), str)}


def _priorities(exe):
    """hswap claude priority(--json 이 없다)의 '  Account-<슬롯>  <이메일>  <tier>' 줄 → {슬롯: tier}. 읽지 못하면 None."""
    code, out = _run(exe, "claude", "priority")
    if code != 0:
        return None
    return {m[1]: int(m[2]) for m in re.finditer(r"^\s*Account-(\d+)\s+\S+\s+(\d+)\s*$", re.sub(r"\x1b\[[0-9;]*m", "", out), re.M)}


def rotation():
    """GET /api/usage/rotation(USAGE-14): 이 기기의 Claude 자동 전환 설정. (http 상태, 응답).
    {on, ordered, order: [슬롯, 우선 순서], threshold: 전환하는 사용률(%), service: macOS 예약 상태 요약(그 밖 None)}.
    on: macOS 는 hswap 예약의 then-auto, 그 밖에서는 순서 전략(게이트웨이 tick 이 그때만 auto 를 부른다). ordered: 순서 전략인지."""
    exe = claude_accounts.binary()
    v = claude_accounts._version
    if not exe or not v or claude_accounts._key(v) < claude_accounts._key(ROTATION_FROM):
        return 409, {"error": f"이 기기에 hswap {ROTATION_FROM} 이상이 필요합니다"}  # 404 는 core 가 'jarvis 갱신 필요'로 바꾼다
    with _rot_lock:
        s, pri = _settings(exe), _priorities(exe)
        st = _json(_run(exe, "claude", "keepalive", "--service-status", "--json")[1]) if sys.platform == "darwin" else None
    if not s or pri is None or (sys.platform == "darwin" and st is None):  # 예약 상태를 모르면 켜짐을 추측하지 않는다
        return 502, {"error": "hswap 설정을 읽지 못했습니다"}
    ordered = s.get("autoswitch.strategy") == ROTATION
    svc = None if st is None else {"managed": st.get("managed") is True, "installed": st.get("installed") is not False}
    return 200, {"on": (st or {}).get("thenAuto") is True if sys.platform == "darwin" else ordered, "ordered": ordered,
                 "order": sorted((k for k, t in pri.items() if t > 0), key=lambda k: (-pri[k], int(k))),
                 "threshold": _num(s.get("autoswitch.threshold")), "service": svc}


def set_rotation(on, order, threshold):
    """POST /api/usage/rotation(USAGE-14): 순서 order(슬롯 쉼표 목록, 앞이 먼저)는 hswap priority 로(앞에서부터 N..1, 빠진 슬롯은 0),
    threshold(전환 사용률 50~99.9)는 autoswitch.threshold 로, 켜면 autoswitch.strategy 를 priority-consume-first 로 둔다.
    macOS 는 hswap 이 만든 keepalive 예약을 같은 ahead 에 --then-auto 를 붙이거나 떼어 다시 설치한다(사람이 만든 예약은 바꾸지 않는다).
    그 밖에서는 tick 이 순서 전략일 때만 auto --once 를 부른다. 단계마다 실패하면 거기서 멈추고 409 로 그 단계를 알린다."""
    if on not in ("0", "1"):
        return 400, {"error": "on 은 0 또는 1 이어야 합니다"}
    slots = [x for x in (order or "").split(",") if x]
    if not all(x.isdigit() and len(x) <= 4 for x in slots) or len(set(slots)) != len(slots) or len(slots) > 100:
        return 400, {"error": "order 는 겹치지 않는 hswap 슬롯 번호의 쉼표 목록이어야 합니다"}
    try:
        t = float(threshold)
    except (TypeError, ValueError):
        t = math.nan
    if not 50 <= t <= 99.9:  # hswap autoswitch.threshold 의 범위
        return 400, {"error": "threshold 는 50 이상 99.9 이하여야 합니다"}
    if on == "1" and not slots:
        return 400, {"error": "켜려면 순서에 계정이 하나 이상 있어야 합니다"}
    code, now = rotation()
    if code != 200:
        return code, now
    svc = now["service"]
    if sys.platform == "darwin" and svc and svc["installed"] and not svc["managed"] and (on == "1") != now["on"]:
        return 409, {"error": "이 기기의 keepalive 예약은 사람이 만든 것이라 jarvis 가 자동 전환을 켜거나 끄지 않습니다"}
    exe = claude_accounts.binary()
    pri = {s: str(len(slots) - i) for i, s in enumerate(slots)}
    steps = [(("claude", "priority", s, "--unset"), "우선순위 해제") for s in now["order"] if s not in pri]
    steps += [(("claude", "priority", s, p), "우선순위 지정") for s, p in pri.items()]
    steps.append((("claude", "config", "set", "autoswitch.threshold", f"{t:g}"), "전환 기준 저장"))
    steps.append((("claude", "config", "set", "autoswitch.strategy", ROTATION) if on == "1" else ("claude", "config", "unset", "autoswitch.strategy"), "전략 저장"))
    if sys.platform == "darwin" and (on == "1") != now["on"]:
        st = _json(_run(exe, "claude", "keepalive", "--service-status", "--json")[1]) or {}
        ahead = st.get("ahead") if _num(st.get("ahead")) and st.get("ahead") > 0 else AHEAD_S
        steps.append((("claude", "keepalive", "--install-service", "--ahead", str(int(ahead)), *(["--then-auto"] if on == "1" else []), "--json"), "예약 다시 설치"))
    with _rot_lock:
        for args, what in steps:
            rc, _ = _run(exe, *args)
            if rc not in ((0, 3) if "--install-service" in args else (0,)):  # 예약 설치의 3 은 GUI 세션이 없어 다음 로그인 때 적재된다는 뜻이다
                return 409, {"error": f"hswap 이 {what}에 실패했습니다(종료 코드 {rc})"}
    return rotation()


def tick():
    """macOS 밖(Linux·Windows): 키체인이 없어 서비스 없이 게이트웨이가 TICK_S 마다 keepalive 를 부른다(결과는 hswap 이 keepalive.json 에 남긴다).
    자동 전환을 켠 기기(순서 전략, USAGE-14)는 keepalive 뒤에 auto --once 로 한 번 판정한다(macOS 는 hswap 예약의 --then-auto 가 한다)."""
    while True:
        try:
            exe = claude_accounts.binary()
            # 저장한 계정이 없으면 유지할 로그인도 없다(로그인하지 않은 기기에 실패 기록을 만들지 않는다)
            if (config.CSWAP_INSTALL and exe and which_tool("claude") and claude_accounts.installer.state != "running"
                    and claude_accounts._store_read()):
                code, _ = _run(exe, "claude", "keepalive", "--ahead", str(AHEAD_S), "--json")
                if code == 0 and _settings(exe).get("autoswitch.strategy") == ROTATION:
                    _run(exe, "claude", "auto", "--once", "--json")
        except Exception as e:
            print(f"[jarvis] hswap keepalive 실패: {scrub_home(str(e))[:200]}", flush=True)
        time.sleep(TICK_S)


def maintain():
    """게이트웨이가 도는 동안 KEEP_S 마다: hswap 버전을 맞추고(USAGE-5) Claude 자동 갱신 예약을 맞추며(USAGE-11) 로그인을 유지한다(USAGE-9).
    Claude 는 0.2.1 에 refresh 가 없어, 같은 주기로 hswap claude list 를 묻는 읽기로 hswap 이 토큰을 갱신하게 한다."""
    ticking = False
    while True:
        for step in (_check, schedule, _keep_once, lambda: which_tool("claude") and claude_accounts.read()):
            try:
                step()
            except Exception as e:
                print(f"[jarvis] hswap 유지 실패: {scrub_home(str(e))[:200]}", flush=True)
        if not ticking and sys.platform != "darwin":  # 첫 버전 맞추기(설치) 뒤에 시작한다. 키체인이 없는 Linux·Windows 는 파일 저장소라 서비스가 필요 없다
            ticking = True
            threading.Thread(target=tick, name="jarvis-keepalive", daemon=True).start()
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


def read(harness, fallback, force=False):
    """hswap 과 기존 수집기(fallback)를 함께 돌려 계정마다 한 행으로 합친다(merge).
    hswap 은 시작한 뒤 WAIT_S 까지(기존 수집기가 더 오래 걸리면 그것이 끝날 때까지) 기다리고, 그때까지 답이 없으면
    LAST_MAX_S 안의 마지막 답을 쓰며 뒤에서 계속 읽는다. force(화면의 새로고침)는 측정 전에 refresh 를 하고 FORCE_WAIT_S 까지 기다린다."""
    started = time.monotonic()
    job = _start(harness, force)
    try:
        old = fallback()
    except Exception:  # 기존 수집기가 없어도(caam 없음 등) hswap 행은 보인다
        old = []
    rows = None
    if job:
        try:
            rows = job.result(timeout=max(0, started + (FORCE_WAIT_S if force else WAIT_S) - time.monotonic()))
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
