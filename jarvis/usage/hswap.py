"""hswap 이 재는 Claude 밖 하네스(Codex·Grok·OpenCode)의 계정별 사용량을 읽고(USAGE-8), 게이트웨이가 도는 동안 로그인을 유지한다(USAGE-9).

하네스마다 `hswap list <하네스> --json` 을 한 번 부른다(Claude 는 claude 가 `hswap claude list` 로 따로 읽어 같은 하네스를 두 번 재지 않는다).
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

from .. import config, macos_daemon
from . import caam, claude, common
from ..util import _NO_WINDOW, scrub_home, tool_path, which_tool

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
# hswap 0.3.0 은 --ahead 가 이 값 이하인 예약 설치를 거절한다(ahead_too_short: Claude Code 의 300초 창 + 예약 간격 300초).
# 다시 설치할 때 이보다 짧은 옛 예약의 ahead 는 이어받지 않고 AHEAD_S 를 쓴다
MIN_AHEAD_S = 600
TICK_S = 300  # 게이트웨이가 keepalive 를 직접 부르는 간격(macOS 의 로그인 세션에서는 hswap 의 LaunchAgent 가 같은 간격으로 부른다)
ROTATION = "priority-consume-first"  # 계정 priority 높은 것부터, 전환 기준 아래인 계정만 쓴다. 높은 계정이 풀리면 돌아간다(USAGE-14)
ROTATION_FROM = "0.2.9"  # 이 전략과 `hswap claude priority` 를 확인한 hswap
UI = "jarvis-ui"  # 사람이 화면에서 누른 버튼이 부른 hswap 의 HSWAP_CALLER
LOG_FROM = "0.3.1"  # `hswap claude log --since … --json`(logs/events.jsonl)을 아는 hswap
LOG_MAX = 200  # 원격 로그 조회가 돌려주는 최대 줄 수(최근 것)
LOG_TIMEOUT_S = 30  # hswap log 는 파일만 읽는다. core 의 HSWAP_LOG_TIMEOUT_S(40초) 안에 답한다
_log_lock = threading.Lock()  # 원격 로그 조회는 이 기기에서 한 번에 하나만
SINCE = re.compile(r"[1-9][0-9]{0,3}[mhd]")  # 로그 조회 기간: 30m·1h·2d 꼴만 hswap 에 넘긴다
EVENT_KEYS = ("ts", "v", "cmd", "caller", "session", "store", "activeSlot", "outcome", "exit", "ms", "error", "details")  # hswap 0.3.1 events.jsonl
# 로그 응답에서 한 번 더 가리는 것: Claude 토큰 모양과 절대·홈 경로(Windows 포함)
SECRET_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]*")
PATH_RE = re.compile(r"(?:[A-Za-z]:\\|~[/\\]|/)(?:[^\s/\\\"']+[/\\])+[^\s/\\\"']*")
_said = {}  # _say: 종류 → (값, 남긴 때). 게이트웨이 로그의 같은 줄을 한 시간에 한 번만 남긴다
_rot_lock = threading.Lock()  # 자동 전환 설정 읽기·쓰기를 한 번에 하나만
REMOTE = "codex,grok,opencode"  # usage.remote 가 비어 있을 때 정하는 값. Antigravity 는 약관 위험이 커서 넣지 않는다(2026-11-08 사용자 결정)
# UsageState → (화면 문구, 사람이 손봐야 하는가). detail 원문은 오류 경로가 섞일 수 있어 쓰지 않는다
STATES = {
    "stale": ("이전 값입니다(hswap 이 지금은 다시 재지 않았습니다).", False),
    "unknown": ("사용률을 확인하지 못했습니다.", False),
    "unsupported": ("hswap 이 이 계정의 사용량을 재지 않습니다(usage.remote 설정을 확인하세요).", False),
    "authentication_required": ("다시 로그인해야 합니다.", True),
    "rate_limited": (caam.LIMIT_NOTE, False),  # 한도 창 소진. 리셋 시각에 풀린다(USAGE-17)
    "error": ("사용량을 읽지 못했습니다.", False),
}
# UsageState → failure 코드(USAGE-17, caam.FAILURES). 키 집합은 STATES 와 같다. available 은 null 이다
CODES = {"stale": "wait", "unknown": "unknown", "unsupported": "unsupported", "authentication_required": "auth", "rate_limited": "limit", "error": "unknown"}
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
_skipped = {}  # 하네스 → 마지막 refresh 가 계정을 건너뛰었는가. keep 이 그 하네스에 대해 돌았다는 표시를 겸한다(USAGE-19)


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
        used = claude._pct(w.get("usedPercent")) if isinstance(w, dict) else None
        if used is None:
            continue
        seconds = _num(w.get("windowSeconds"))
        key = caam._key(seconds) if seconds and seconds > 0 else WINDOW_NAMES.get(_str(w.get("name")), "기간 미확인")
        windows.append({"key": key, "used": used, "resets_at": claude._sec(w.get("resetsAt"))})
    extra, at = _extra(u.get("balance")), claude._sec(u.get("measuredAt"))
    state = _str(u.get("state"))
    note, attention = STATES.get(state, (None, False))
    failure = CODES.get(state)
    plan = (_str(u.get("plan")) or "")[:40]  # 별칭(alias)은 사람이 붙인 이름이라 내보내지 않는다
    if not windows and state == "unknown" and _str(u.get("detail")) == NO_CREDIT_USAGE and plan and at:
        # shortcut: hswap 이 기간 종류와 리셋 시각을 넘기지 않아 주간·리셋 미상으로 둔다. hswap 이 period 를 내보내면 그 값을 쓴다
        windows, note, failure = [{"key": "weekly", "used": 0, "resets_at": None}], None, None
    has = bool(windows or extra or (plan and at))
    if state not in STATES and state != "available" and not has:  # 모르는 상태 이름은 값이 없을 때만 오류다
        failure = "unknown"
    if not has and state == "stale":  # 이전 값을 줄 측정이 아직 한 번도 없었다(토큰 갱신 대기 등)
        note = "아직 잰 값이 없습니다(hswap 이 지금은 재지 않았습니다)."
        # 사용 중 계정을 한 번도 재지 못했으면 hswap 이 스스로 풀지 못할 수 있다(Codex 가 실행 중이면 갱신을 건너뛴다): 다시 로그인하거나 하네스를 써야 한다.
        # 쉬는 계정에 다시 로그인을 내면 지금 로그인을 바꾸게 되므로 사용 중 계정만 주의로 둔다
        attention = row.get("active") is True
    elif state == "stale" and row.get("active") is True and at and time.time() - at > claude.DEAD_S:
        # USAGE-18: 정상 stale(최장 백오프·유지 주기 1시간, Grok 의 만료 직전 거부)은 6시간을 넘지 않는다
        note, attention = (f"{claude._span(time.time() - at)}째 새로 재지 못했습니다. 다시 로그인해야 할 수 있습니다. "
                           "그 하네스가 데몬·앱으로 상주하면 먼저 끝내야 새 로그인을 씁니다."), True
    label = row.get("label")
    return {"label": label if isinstance(label, str) and len(label) <= 200 and common.EMAIL.fullmatch(label)
            else "API 키 계정" if row.get("authMode") in ("api_key", "key") else common.UNNAMED,
            "org": None, "active": row.get("active") is True,
            "status": "ok" if has else "error", "error": None if has else note or "사용량을 확인할 수 없습니다.",
            "note": note if has else None, "attention": attention, "failure": failure, "plan": plan or None, "updated_at": at,
            "windows": windows, "buckets": [], "extra": extra, "credits": None, "source": "hswap",
            # stale·error 행은 마지막 측정값을 싣고 온다. merge 가 판정에만 쓰고 지운다
            "_prior": state in ("stale", "error")}


def _run(exe, *args, caller="jarvis-gateway", timeout=None):
    """hswap 을 한 번 실행해 (종료 코드, stdout). 실행하지 못했거나 TIMEOUT_S 를 넘기면 (None, "").
    caller: hswap 에 알리는 호출 주체(HSWAP_CALLER). 사람의 버튼이 부른 것은 jarvis-ui. 결과는 claude.call_log 가 게이트웨이 로그에 남긴다."""
    start = time.monotonic()
    try:
        # hswap 은 하네스 CLI(codex·grok·opencode)를 PATH 로 찾는다. 서비스의 PATH 에는 사용자 설치 위치가 없어 "not installed" 로 답한다
        env = {**claude._env(caller), "PATH": tool_path()}
        # 새 세션으로 띄워 시간 초과 때 hswap 이 띄운 하네스 CLI 까지 함께 끝낸다
        proc = subprocess.Popen([exe, *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace", env=env, creationflags=_NO_WINDOW, start_new_session=os.name != "nt")
    except OSError:
        claude.call_log(args, None, (time.monotonic() - start) * 1000)
        return None, ""
    try:
        out, _ = proc.communicate(timeout=timeout or TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _kill(proc)
        claude.call_log(args, None, (time.monotonic() - start) * 1000)
        return None, ""
    err = (_json(out) or {}).get("error")
    claude.call_log(args, proc.returncode, (time.monotonic() - start) * 1000, err.get("type") if isinstance(err, dict) else None)
    return proc.returncode, out


def _say(kind, value, line, again_s=claude.LOG_AGAIN_S):
    """게이트웨이 로그에 `[jarvis] hswap: <line>` 한 줄. 같은 종류의 같은 값은 again_s 에 한 번만(None 이면 값이 바뀔 때만) 남긴다."""
    now, prev = time.monotonic(), _said.get(kind)
    if prev and prev[0] == value and (again_s is None or now - prev[1] < again_s):
        return
    _said[kind] = (value, now)
    print(f"[jarvis] hswap: {line}", flush=True)


def _note_tick(reason):
    """tick 의 결정(USAGE-16): None 이면 keepalive 를 불렀고, 아니면 건너뛴 이유 코드(claude.tick_reason, not_ready)."""
    _say("tick", reason, "tick keepalive 실행" if reason is None else f"tick 건너뜀({reason})")


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
    """시간 초과한 hswap 과 그 자식(하네스 CLI)을 끝낸다. 파이프를 쥔 손자가 남아도 기다리지 않는다(login._codex_keyring_allowed 와 같은 정리)."""
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
    exe = claude.binary()
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
        rows = [r for r in body.get("accounts") or [] if isinstance(r, dict)]
        bad = sum(1 for r in rows if r.get("ok") is False)
        # 건너뜀도 ok:true 다. hswap 0.3.1 harness_swap/adapters/codex.py:469,471 의 keep_alive 가 돌려주는 두 문장
        # "skipped: a running session keeps it fresh"·"skipped: {display_name} is running and refreshes the active login itself" 의 접두어로 가른다
        skipped = sum(1 for r in rows if r.get("ok") is True and str(r.get("result") or "").startswith("skipped:"))
        _skipped[h] = skipped > 0
        done[h] = (f"오류({re.sub(r'[^A-Za-z_]', '', str(err.get('type')))[:40]})" if err
                   else " · ".join(x for x in (bad and f"실패 {bad}개", skipped and f"건너뜀 {skipped}개") if x) or ("확인함" if body else "답 없음"))
    return done


def schedule():
    """Claude 로그인 자동 갱신의 예약 실행을 맞춘다(USAGE-11). macOS 는 키체인이 GUI 세션에서만 닿아 hswap 이 설치하는
    LaunchAgent 에 맡긴다: 예약이 없으면 설치하고, hswap 이 만든 예약이 적재되지 않았으면 그 설정(ahead·thenAuto) 그대로 다시 설치한다.
    적재된 예약과 hswap 이 만들지 않은 같은 이름의 서비스는 건드리지 않는다(사람이 바꾼 설정을 덮어쓰지 않는다).
    답한 상태는 로그인 판정(claude.service)에 쓴다. 할 일이 아니면 None 이고 지난 답도 지운다."""
    claude.service = claude.service_note = None
    exe = claude.binary()
    if (sys.platform != "darwin" or not config.CSWAP_INSTALL or not exe or not which_tool("claude") or claude.installer.state == "running"
            or not claude.hswap_at_least(SERVICE_FROM) or not claude._store_read()):
        return None
    st = _json(_run(exe, "claude", "keepalive", "--service-status", "--json")[1])
    want, note = _service_plan(st)
    if (claude.doctor or {}).get("keepalive_domain") == "system":  # 데몬 keepalive(--system) 옆에 gui 예약을 두지 않는다(hswap 0.3.0)
        want = False
    text = {"caller_env_only": "keepalive 예약: 다음 설치 때 갱신",
            "needs_sudo": f"keepalive 예약(LaunchDaemon)을 다시 설치해야 합니다: {_sudo_command(st or {})}"}.get(note)
    if note == "needs_sudo":
        _say("service", note, f"keepalive 예약(LaunchDaemon)이 낡았거나 적재되지 않았습니다. 그 기기에서 실행하세요: {_sudo_command(st)}")
    if want:
        ahead = _ahead(st)
        # 0 설치·갱신, 3 GUI 세션이 없어 다음 로그인 때 적재. 어느 쪽이든 다시 물은 상태를 쓴다
        # 예약이 없을 때 사람이 자동 전환을 켜 두었으면(순서 전략, USAGE-14) 그것도 다시 붙인다
        then = st.get("thenAuto") is True or (st.get("installed") is False and _settings(exe).get("autoswitch.strategy") == ROTATION)
        code, out = _run(exe, "claude", "keepalive", "--install-service", "--ahead", str(int(ahead)), *(["--then-auto"] if then else []), "--json")
        if code == 1 and _error_type(out) == "service_busy":  # hswap 0.3.1: 실행 중인 keepalive·reconcile 이 잠금을 쥐었다. 바꾼 것이 없고 다음 주기에 다시 한다
            _say("service", "busy", "keepalive 예약 다시 설치를 미뤘습니다(service_busy). 다음 주기에 다시 합니다")
        elif code not in (0, 3):
            print(f"[jarvis] hswap keepalive 예약 설치 실패(종료 코드 {code})", flush=True)
        st = _json(_run(exe, "claude", "keepalive", "--service-status", "--json")[1]) or st
    claude.service, claude.service_note = st, text
    return st


def _sudo_command(st):
    """사람이 그 기기에서 데몬 예약을 다시 설치할 명령. 지금 예약의 ahead(hswap 이 받는 값만)·thenAuto 그대로."""
    return (f"sudo hswap claude keepalive --install-service --system --ahead {int(_ahead(st))}"
            + (" --then-auto" if st.get("thenAuto") is True else ""))


def _service_plan(st):
    """keepalive 예약 상태(--service-status) → (다시 설치할까, 안내 코드 None·caller_env_only·needs_sudo). 값만 받는다.
    예약이 없으면 설치한다. hswap 이 만든 예약은 적재되지 않았거나 낡았을 때 그 설정 그대로 다시 설치한다. hswap 0.3.1 의 이유를 따른다:
    caller_env_only(0.3.1 전 plist 라 HSWAP_CALLER 만 다르다)는 동작이 같아 다시 깔지 않고, reinstallNeedsSudo(LaunchDaemon)는 사람에게 맡긴다.
    두 필드가 없는 옛 hswap 은 지금까지처럼 낡았으면 다시 설치한다."""
    if not st or st.get("platform") != "launchd":
        return False, None
    if st.get("installed") is False:
        return True, None
    if st.get("managed") is not True:
        return False, None
    outdated, reason = st.get("outdated") is True, st.get("outdatedReason")
    if st.get("loaded") is False or (outdated and reason != "caller_env_only"):
        return (False, "needs_sudo") if st.get("reinstallNeedsSudo") is True else (True, None)
    return False, "caller_env_only" if outdated else None


def _error_type(out):
    err = (_json(out) or {}).get("error")
    return err.get("type") if isinstance(err, dict) else None


def inspect():
    """macOS: `hswap claude doctor --json`(0.3.0, 키체인 없이 돈다)으로 저장 방식·문제·예약 상태를 읽어 claude.doctor 에 둔다(USAGE-15).
    doctor 를 모르는 옛 hswap(버전을 모르는 것 포함)은 doctor 를 부르지 않고 config 의 credentials.store 만 쓴다.
    할 일이 아니면(macOS 밖, hswap·Claude Code 없음, 설치 중) 지난 답도 지운다."""
    exe = claude.binary()
    if (sys.platform != "darwin" or not config.CSWAP_INSTALL or not exe or not which_tool("claude")
            or claude.installer.state == "running"):
        claude.doctor = None
        return None
    new = claude.hswap_at_least(claude.DOCTOR_FROM)
    body = _json(_run(exe, "claude", "doctor", "--json")[1]) if new else None  # 문제가 있으면 0 이 아닌 코드로 끝나도 JSON 은 정상이다
    d = claude.doctor = macos_daemon.summary(body, None if macos_daemon.answered(body) else _settings(exe))
    if d:  # 바뀔 때만 남긴다(USAGE-16). problems 는 코드뿐이다
        session = "in" if claude.in_session() else "out"
        problems = ",".join(d.get("problems") or []) or "없음"
        _say("doctor", (d.get("store"), problems, session), f"doctor store={d.get('store') or '모름'} problems={problems} session={session}", again_s=None)
    return d


def _ahead(st):
    """다시 설치할 keepalive 예약의 ahead: 지금 예약의 값(사람이 고른 것)이 MIN_AHEAD_S 보다 길면 그것, 아니면 AHEAD_S."""
    v = _num(st.get("ahead"))
    return v if v is not None and v > MIN_AHEAD_S else AHEAD_S


def _settings(exe, caller="jarvis-gateway"):
    """hswap claude config --json 의 {키: 값}. 읽지 못하면 {}."""
    body = _json(_run(exe, "claude", "config", "--json", caller=caller)[1]) or {}
    return {s["key"]: s.get("value") for s in body.get("settings") or [] if isinstance(s, dict) and isinstance(s.get("key"), str)}


def _priorities(exe, caller="jarvis-gateway"):
    """hswap claude priority(--json 이 없다)의 '  Account-<슬롯>  <이메일>  <tier>' 줄 → {슬롯: tier}. 읽지 못하면 None."""
    code, out = _run(exe, "claude", "priority", caller=caller)
    if code != 0:
        return None
    return {m[1]: int(m[2]) for m in re.finditer(r"^\s*Account-(\d+)\s+\S+\s+(\d+)\s*$", re.sub(r"\x1b\[[0-9;]*m", "", out), re.M)}


def rotation():
    """GET /api/usage/rotation(USAGE-14): 이 기기의 Claude 자동 전환 설정. (http 상태, 응답).
    {on, ordered, order: [슬롯, 우선 순서], threshold: 전환하는 사용률(%), service: macOS 예약 상태 요약(그 밖 None)}.
    on: 게이트웨이 tick 이 도는 곳(macOS 밖, macOS 의 세션 밖 파일 저장 방식)은 순서 전략(tick 이 그때만 auto 를 부른다), 그 밖의 macOS 는 hswap 예약의 then-auto.
    ordered: 순서 전략인지."""
    exe = claude.binary()
    if not exe or not claude.hswap_at_least(ROTATION_FROM):
        return 409, {"error": f"이 기기에 hswap {ROTATION_FROM} 이상이 필요합니다"}  # 404 는 core 가 'jarvis 갱신 필요'로 바꾼다
    with _rot_lock:
        s, pri = _settings(exe, UI), _priorities(exe, UI)
        st = _json(_run(exe, "claude", "keepalive", "--service-status", "--json", caller=UI)[1]) if sys.platform == "darwin" else None
    if not s or pri is None or (sys.platform == "darwin" and st is None):  # 예약 상태를 모르면 켜짐을 추측하지 않는다
        return 502, {"error": "hswap 설정을 읽지 못했습니다"}
    ordered = s.get("autoswitch.strategy") == ROTATION
    svc = None if st is None else {"managed": st.get("managed") is True, "installed": st.get("installed") is not False}
    return 200, {"on": ordered if claude.gateway_ticks() else (st or {}).get("thenAuto") is True, "ordered": ordered,
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
    if (sys.platform == "darwin" and (claude.doctor or {}).get("keepalive_domain") == "system" and not claude.gateway_ticks()
            and (on == "1") != now["on"]):  # 데몬 예약이 적재되지 않아 tick 이 도는 동안은 순서 전략만으로 켜고 끈다
        # 데몬 keepalive 는 관리자 권한으로만 다시 설치된다. jarvis 는 sudo 를 쓰지 않는다
        flag = " --then-auto" if on == "1" else ""
        return 409, {"error": f"이 기기의 keepalive 예약은 LaunchDaemon 이라 jarvis 가 자동 전환을 켜거나 끄지 않습니다. 그 기기에서 실행하세요: sudo hswap claude keepalive --install-service --system{flag}"}
    exe = claude.binary()
    pri = {s: str(len(slots) - i) for i, s in enumerate(slots)}
    steps = [(("claude", "priority", s, "--unset"), "우선순위 해제") for s in now["order"] if s not in pri]
    steps += [(("claude", "priority", s, p), "우선순위 지정") for s, p in pri.items()]
    steps.append((("claude", "config", "set", "autoswitch.threshold", f"{t:g}"), "전환 기준 저장"))
    steps.append((("claude", "config", "set", "autoswitch.strategy", ROTATION) if on == "1" else ("claude", "config", "unset", "autoswitch.strategy"), "전략 저장"))
    # 데몬 keepalive 는 gui 예약으로 바꿀 수 없다(hswap 이 ServiceConflict 로 거절한다). 적재되지 않아 tick 이 도는 동안은 전략만으로 켜고 끈다
    if sys.platform == "darwin" and (on == "1") != now["on"] and (claude.doctor or {}).get("keepalive_domain") != "system":
        st = _json(_run(exe, "claude", "keepalive", "--service-status", "--json", caller=UI)[1]) or {}
        ahead = _ahead(st)
        steps.append((("claude", "keepalive", "--install-service", "--ahead", str(int(ahead)), *(["--then-auto"] if on == "1" else []), "--json"), "예약 다시 설치"))
    with _rot_lock:
        for args, what in steps:
            rc, out = _run(exe, *args, caller=UI)
            if rc not in ((0, 3) if "--install-service" in args else (0,)):  # 예약 설치의 3 은 GUI 세션이 없어 다음 로그인 때 적재된다는 뜻이다
                if _error_type(out) == "service_busy":
                    return 409, {"error": claude.HSWAP_ERRORS["service_busy"]}
                return 409, {"error": f"hswap 이 {what}에 실패했습니다(종료 코드 {rc})"}
    return rotation()


def hswap_log(since):
    """GET /api/usage/hswap-log?since=(USAGE-16): `hswap claude log --since <since> --json` 의 최근 LOG_MAX 줄. (http 상태, 응답).
    {since, events: [EVENT_KEYS 만, 문자열은 토큰 모양·경로를 가리고 200자까지], truncated}. hswap 이 0.3.1 미만이면 409.
    hswap 의 출력은 {events: [...]}·배열·JSON Lines 중 어느 것이든 받는다(계약에 모양이 정해지지 않았다)."""
    if not isinstance(since, str) or not SINCE.fullmatch(since):
        return 400, {"error": "since 는 30m·1h·2d 꼴이어야 합니다"}
    exe = claude.binary()
    if not exe or not claude.hswap_at_least(LOG_FROM):
        return 409, {"error": f"hswap {LOG_FROM} 이상 필요"}
    if not _log_lock.acquire(blocking=False):
        return 429, {"error": "hswap 로그를 이미 읽는 중입니다. 잠시 뒤 다시 시도하세요"}
    try:
        code, out = _run(exe, "claude", "log", "--since", since, "--json", caller=UI, timeout=LOG_TIMEOUT_S)
    finally:
        _log_lock.release()
    events = _events(out, code)
    if events is None:
        return 502, {"error": f"hswap log 를 읽지 못했습니다(종료 코드 {code})"}
    return 200, {"since": since, "events": [_event(e) for e in events[-LOG_MAX:]], "truncated": len(events) > LOG_MAX}


def _events(out, code):
    """hswap log 출력 → 사건(dict) 목록. 읽지 못하면 None. hswap 0.3.1 은 JSON Lines 라 줄마다 먼저 읽는다(깨진 줄은 건너뛴다).
    한 줄도 읽지 못하면 여러 줄로 쓴 JSON 하나로 읽는다. 값마다 배열은 그 안의 객체, {events: [...]} 는 그 목록, 그 밖의 객체는 사건 하나다."""
    lines = [x for x in out.splitlines() if x.strip()]
    if not lines:
        return [] if code == 0 else None
    values = []
    for x in lines:
        try:
            values.append(json.loads(x))
        except ValueError:
            continue
    if not values:
        try:
            values = [json.loads(out)]
        except ValueError:
            return None
    rows = []
    for v in values:
        v = v.get("events") if isinstance(v, dict) and isinstance(v.get("events"), list) else v
        rows += [r for r in v if isinstance(r, dict)] if isinstance(v, list) else [v] if isinstance(v, dict) else []
    return rows


def _event(e):
    """사건 한 줄을 허용한 키만 남기고 가린다. error 는 type 만(영문자·밑줄) 남긴다."""
    err = e.get("error")
    kind = err.get("type") if isinstance(err, dict) else err if isinstance(err, str) else None
    out = {k: _clean(e.get(k)) for k in EVENT_KEYS if k in e and k != "error"}
    out["error"] = {"type": re.sub(r"[^A-Za-z_]", "", kind)[:40]} if isinstance(kind, str) else None
    return out


def _clean(v, depth=0):
    """로그 값 하나: 문자열은 홈·경로·토큰 모양을 가리고 200자까지, 객체·배열은 3단·20개까지, 그 밖의 형식은 버린다."""
    if isinstance(v, str):
        return PATH_RE.sub("[경로]", SECRET_RE.sub("[가림]", scrub_home(v)))[:200]
    if v is None or isinstance(v, bool) or (isinstance(v, (int, float)) and math.isfinite(v)):
        return v
    if depth < 3 and isinstance(v, dict):
        return {_clean(str(k))[:40]: _clean(x, depth + 1) for k, x in list(v.items())[:20]}  # 키도 값과 같이 가린다
    if depth < 3 and isinstance(v, list):
        return [_clean(x, depth + 1) for x in v[:20]]
    return None


def tick():
    """게이트웨이가 TICK_S 마다 keepalive 를 부른다(결과는 hswap 이 keepalive.json 에 남긴다). 키체인이 없는 Linux·Windows 는 늘,
    macOS 는 doctor 를 다시 읽고 로그인 세션 밖의 파일 저장 방식이며 적재된 keepalive 예약이 없을 때만(claude.gateway_ticks, USAGE-15).
    자동 전환을 켠 기기(순서 전략, USAGE-14)는 keepalive 뒤에 auto --once 로 한 번 판정한다(세션 안의 macOS 는 hswap 예약의 --then-auto 가 한다)."""
    while True:
        try:
            if sys.platform == "darwin":
                inspect()
            exe = claude.binary()
            # 저장한 계정이 없으면 유지할 로그인도 없다(로그인하지 않은 기기에 실패 기록을 만들지 않는다)
            ready = (config.CSWAP_INSTALL and exe and which_tool("claude") and claude.installer.state != "running"
                     and claude._store_read())
            reason = claude.tick_reason() or (None if ready else "not_ready")
            _note_tick(reason)
            if reason is None:
                code, _ = _run(exe, "claude", "keepalive", "--ahead", str(AHEAD_S), "--json")
                if code == 0 and _settings(exe).get("autoswitch.strategy") == ROTATION:
                    _run(exe, "claude", "auto", "--once", "--json")
        except Exception as e:
            print(f"[jarvis] hswap keepalive 실패: {scrub_home(str(e))[:200]}", flush=True)
        time.sleep(TICK_S)


def maintain():
    """게이트웨이가 도는 동안 KEEP_S 마다: hswap 버전을 맞추고(USAGE-5) macOS 의 저장 방식을 읽고(USAGE-15) Claude 자동 갱신 예약을 맞추며(USAGE-11) 로그인을 유지한다(USAGE-9).
    Claude 는 0.2.1 에 refresh 가 없어, 같은 주기로 hswap claude list 를 묻는 읽기로 hswap 이 토큰을 갱신하게 한다."""
    ticking = False
    while True:
        for step in (_check, inspect, schedule, _keep_once, lambda: which_tool("claude") and claude.read()):
            try:
                step()
            except Exception as e:
                print(f"[jarvis] hswap 유지 실패: {scrub_home(str(e))[:200]}", flush=True)
        # 첫 버전 맞추기(설치) 뒤에 시작한다. 세션 안의 macOS 는 hswap 예약이 돌아 띄우지 않는다. 세션 밖은 tick 이 매번 할 일인지 가린다(claude.gateway_ticks)
        if not ticking and (sys.platform != "darwin" or not claude.in_session()):
            ticking = True
            threading.Thread(target=tick, name="jarvis-keepalive", daemon=True).start()
        time.sleep(KEEP_S)


def _check():
    """버전을 맞추고, 지금 설치를 시작했으면 끝날 때까지(최대 설치 한도) 기다려 새 기기에서도 첫 유지를 한 시간 미루지 않는다."""
    claude.check()
    end = time.monotonic() + claude.INSTALL_TIMEOUT_S
    while claude.installer.state == "running" and time.monotonic() < end:
        time.sleep(5)


def _keep_once():
    exe = claude.binary()
    if not exe or claude.installer.state == "running":  # 설치하는 동안에는 hswap 을 띄우지 않는다
        return
    done = keep(exe)
    _say("keep", done, f"로그인 유지: {done}", again_s=None)  # 상주 하네스로 늘 건너뛰는 기기에 매시간 같은 줄이 쌓이지 않게 바뀔 때만


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
    return label.lower() if isinstance(label, str) and common.EMAIL.fullmatch(label) else id(r)


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
        same = active is not None and o.get("active") is True and o.get("label") == common.DEFAULT
        k = _email(active) if same else _email(o)
        if k in hswap:
            chosen[k] = pick(hswap.pop(k), o)
        else:
            rest.append(o)
    return list(chosen.values()) + rest
