"""Claude 계정의 모든 규칙을 한 곳에 둔다: 신선도, 만료 경고, 상태 문구, 사용 중 판정, 창·Fable·버킷 이름, 기기 로그인 상태.

바깥 인터페이스는 read(force)와 switch(slot)·remove(slot)이다. 사용량 경로(independent_usage)가 read 를 거친다.
저장값이 지금 로그인에 아직 맞는지는 판정 함수(_judge_shape, _judge_ask, _judge_rows, _judge_login)가 값만 받아 정한다. 새 신선도·유효성 규칙은 그 함수와 표 테스트만 고친다.
파일과 프로세스를 읽는 순서는 _read 가, 기억(_state)을 고치는 것은 live·switch·login 이 맡는다.
안에 어댑터가 둘 있다.
- hswap CLI(live, _json): `hswap claude list --json`(cswap 과 같은 schemaVersion 1)의 실값. 토큰 갱신·사용량 조회 간격은 hswap 이 맡는다.
  쓰기는 core 의 '이 계정 사용' 버튼이 부르는 `hswap claude switch <슬롯> --json` 과 '계정 삭제' 버튼이 부르는 `hswap claude remove <슬롯>` 이다(자격 교체·삭제는 hswap 이 한다).
- 저장소 캐시(_store_read, _keepalive): hswap(Claude 엔진 claude-swap)과 기존 cswap 이 함께 쓰는 저장소의 계정·마지막 사용량·keepalive.json. 대체값이다.
jarvis 는 자격 증명 파일을 열지 않는다(갱신 토큰을 따로 돌리면 hswap·Claude Code 의 로그인이 풀릴 수 있다). 계정은 이메일·조직 이름·슬롯 번호만 내보낸다.
이미 깔린 cswap 은 지우지 않는다.

공식 계약: https://github.com/thsvkd/harness-swap (claude 하위 명령은 claude-swap 의 CLI, src/claude_swap/json_output.py)
https://github.com/realiti4/claude-swap/blob/v0.28.0/src/claude_swap/usage_store.py
https://github.com/realiti4/claude-swap/blob/v0.28.0/src/claude_swap/switcher.py
사용 중 계정: https://github.com/thsvkd/harness-swap/blob/9a1e5d1/src/claude_swap/switcher.py (current_account_number)·paths.py
"""
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
import time

from . import config, macos_daemon, opencode_usage, provider_usage
from .util import Installer, org, parse_iso, run, scrub_home, which_local, which_tool

CONFIG_MAX = 32 << 20  # Claude Code 전역 설정은 프로젝트 기록이 쌓여 수 MB 까지 큰다
KNOWN_BUCKETS = {"opus", "sonnet", "haiku"}  # 첫 단어가 이것인 이름만 그대로 내보낸다. 나머지는 번호로 적는다
# keepalive error.type → 화면 문구. 파일의 자유 문구(error.message)는 토큰·조직 UUID 가 들어 있어도 새지 않게 내보내지 않는다
KEEPALIVE_ERRORS = {"keychain_unavailable": "키체인 접근 불가", "relogin_required": "재로그인 필요", "split_brain": "키체인과 파일의 로그인이 갈라짐",
                    "refresh_failed": "토큰 갱신 실패", "no_credentials": "자격 증명 없음", "locked": "다른 프로세스가 잠금을 쥐고 있음", "unmanaged": "저장 슬롯에 없는 로그인",
                    "keychain_item_present": "키체인에 Claude 로그인이 있거나 확인하지 못해 자동 갱신을 멈췄습니다", "config_conflict": "hswap 설정 충돌(파일 저장소와 키체인 필수 모드)"}
# hswap 최상위 오류 type → 화면 문구(hswap 의 json_type 또는 예외 클래스 이름, claude_swap/exceptions.py). error.message 는 토큰·경로가 들어 있어도 새지 않게 쓰지 않는다
HSWAP_ERRORS = {**KEEPALIVE_ERRORS, "stores_disagree": KEEPALIVE_ERRORS["split_brain"], "required_mode_unsupported": "hswap 이 이 저장소 모드에서 지원하지 않는 명령",
                "stash_manifest_corrupt": "hswap 보관 목록 손상", "AccountNotFoundError": "저장 슬롯에 없는 계정", "LockError": KEEPALIVE_ERRORS["locked"],
                "ClaudeCodeLockTimeout": KEEPALIVE_ERRORS["locked"], "SwitchError": "계정 전환 실패",
                # hswap 0.3.0(파일 저장 방식의 세션 밖 동작·reconcile). CredentialReadError 는 키체인을 읽지 못한 예외 클래스 이름이다
                "CredentialReadError": "hswap 이 자격 증명을 읽지 못했습니다",
                "keychain_item_present_locked": "키체인에 Claude 로그인이 있는데 키체인이 잠겨 정리하지 못했습니다. 로그인 세션에서 reconcile 이 정리합니다",
                "reconcile_ambiguous": f"키체인과 파일 중 어느 쪽이 새 로그인인지 알 수 없어 정리하지 않았습니다. 그 기기의 로그인 세션에서 한쪽을 고르세요({macos_daemon.MIGRATE} 또는 --keep file)"}
FATAL_ERRORS = ("config_conflict", "keychain_item_present", "relogin_required")  # keepalive 기록이 옛것이어도 기기 상태에 바로 보이는 type
LOGIN_WARN_S = 3 * 86400  # 로그인(갱신 토큰) 만료 3일 전부터 '주의 필요'. 새 /login 만이 기한을 늘린다
CACHE_S = 30  # hswap 자체가 캐시하므로 짧게만 묶는다(요청마다 프로세스를 띄우지 않게)
READ_BUDGET_S = 40  # 읽기 한 번의 전체 시한. 재시도의 잠금 대기와 hswap 호출이 함께 쓴다. 수집기의 대기(55초)보다 짧아야 한다
TIMEOUT_S = 15  # 다른 수집기와 동시에 돈다. core 의 기기당 시간 제한(70초) 안에 들어와야 한다
SWITCH_TIMEOUT_S = 30  # switch 는 현재 로그인을 백업하고 macOS 는 Keychain 도 고친다. core 는 40초를 기다린다
# usageStatus → (화면 문구, 사람이 손봐야 하는가). ok·api_key·unavailable 은 따로 다룬다
STATUS = {
    "token_expired": ("토큰 갱신을 기다리는 중입니다(hswap 이 다시 시도합니다).", False),
    # hswap 은 죽은 갱신 토큰을 슬롯 백업 기준으로 격리한다. Claude Code 에서 다시 로그인만 하면 격리가 남는다
    "relogin_required": ("다시 로그인해야 합니다: 그 계정으로 Claude Code 에서 /login 한 뒤 hswap claude add 를 실행하세요.", True),
    "keychain_unavailable": ("Keychain 을 읽지 못했습니다.", True),
    "keychain_item_present": ("키체인에 Claude 로그인이 있거나 확인하지 못해 자동 갱신을 멈췄습니다.", True),  # 뒷문장은 item_text 가 예약 상태로 붙인다
    "config_conflict": ("hswap 설정 충돌(파일 저장소와 키체인 필수 모드).", True),
    "foreign_credential": ("지금 로그인이 다른 계정입니다. hswap claude switch 로 맞추세요.", True),
    "no_credentials": ("자격 증명이 없습니다.", True),
    "no_saved_usage": ("저장된 사용량이 없습니다.", False),  # 저장소 캐시 어댑터만 쓴다
}
# macOS 로그인 세션 밖(LaunchDaemon·SSH)인데 hswap 이 키체인 저장 방식이면 모든 슬롯이 실패한다. 슬롯마다의 실패 대신 이 한 문구를 보인다
HEADLESS = f"로그인 세션 밖에서 실행 중 — 키체인을 읽을 수 없습니다. 파일 저장 방식으로 바꾸세요({macos_daemon.MIGRATE})"
HEADLESS_KINDS = ("CredentialReadError", "keychain_unavailable", "no_credentials")  # 세션 밖 키체인 저장 방식에서 HEADLESS 로 바꾸는 hswap 오류
_lock = threading.Lock()


class _State:
    """읽기 경로의 기억. 고치는 곳은 live·switch·login 뿐이고 판정 함수는 값으로 받는다."""
    cache = None  # (monotonic, 결과, 물을 때의 저장소 모습 _store_shape). 잠금 안에서 한 값으로 바꾼다. 모습이 지금 저장소와 같을 때만 다시 쓴다
    discovered = False  # hswap 이 마지막으로 답한 때 계정을 주었다. 저장소를 읽지 못해도 기준이 지나면 다시 묻는 근거다
    device = None  # (monotonic, 키체인에 닿지 못함). hswap 이 답한 기기 상태. 계정 행과 따로 두어 계정이 없거나 다음 조회가 실패해도 남는다
    fatal = None  # (monotonic, type). hswap 이 최상위 오류로 답한 FATAL_ERRORS. hswap 이 다시 답하면 지운다
    unconfirmed = None  # 파일 저장소에서 갱신을 확인하지 못한 기록을 처음 본 시각. 기록의 checkedAt 은 실패해도 새로워져 기간을 알 수 없다
    switched = 0  # switch() 가 끝난 횟수. 이것이 바뀌면 그 전에 시작한 읽기의 값은 전환 전 값일 수 있다


_state = _State()
# 자동 설치·올리기: 검증한 버전으로 고정한다(서비스가 최신판을 저절로 받아 실행하지 않게). PyPI 배포본이 그 태그의 소스와 같은지
# 대조한 뒤 여기 버전을 올려 릴리스하면, 게이트웨이(hswap_usage.maintain)가 이보다 낮은 hswap 을 이 버전으로 올린다
# ponytail: 버전만 고정한다. 같은 버전에 나중에 더해진 배포 파일은 대조 없이 받을 수 있다. 막으려면 파일 해시로 고정한다
VERSION = "0.3.0"  # 릴리스 전 미대조(태그·PyPI 배포본을 태그 소스 빌드와 아직 대조하지 않았다)
PACKAGE = f"harness-swap=={VERSION}"
INSTALL_TIMEOUT_S = 600  # 처음이면 uv 가 Python 3.12+ 까지 받는다
CLIS = ("claude", "codex", "grok", "opencode")  # hswap 이 다루는 하네스. 하나라도 있으면 hswap 을 설치한다
_version = None  # 마지막으로 확인한 설치 버전("0.2.7"). 화면의 기기 행에 보인다
service = None  # macOS: hswap claude keepalive --service-status 의 마지막 답(hswap_usage.schedule 이 쓴다). 모르면 None
doctor = None  # macOS: hswap claude doctor 의 요약(macos_daemon.summary, hswap_usage.inspect 가 쓴다). 모르면 None
_aqua = None  # macOS: 로그인 세션(Aqua) 안에서 도는가. 한 번 답을 받으면 다시 묻지 않는다
_aqua_failed = None  # 마지막으로 묻지 못한 때(monotonic). AQUA_RETRY_S 동안은 다시 묻지 않는다
AQUA_RETRY_S = 60


def binary():
    return which_local("hswap")


def in_session():
    """macOS: 이 게이트웨이가 로그인 세션 안에서 도는가(launchctl managername 이 Aqua. LaunchDaemon 은 System, SSH 는 Background).
    묻지 못하면 세션 안으로 본다(지금까지의 동작). 일시적 실패는 굳히지 않되 AQUA_RETRY_S 동안은 다시 묻지 않는다(읽기마다 띄우지 않게)."""
    global _aqua, _aqua_failed
    if _aqua is None:
        now = time.monotonic()
        if _aqua_failed is not None and now - _aqua_failed < AQUA_RETRY_S:
            return True
        rc, out, _ = run(["launchctl", "managername"], timeout=8)
        if rc != 0:
            _aqua_failed = now
            return True
        _aqua = out.strip() == "Aqua"
    return _aqua


def gateway_ticks():
    """게이트웨이 tick(hswap_usage.tick)이 keepalive·auto 를 부르는가. 화면의 keepalive 표시도 같은 판정을 쓴다. macOS 밖은 늘 그렇다. macOS 는 로그인 세션 밖이고 파일 저장 방식이며
    적재된 keepalive 예약(gui·system)이 없을 때만이다(세션 안에서는 hswap 예약이 돌고, 키체인 저장 방식은 세션 밖에서 읽지 못한다)."""
    if sys.platform != "darwin":
        return True
    d = doctor or {}
    loaded = d.get("keepalive_loaded")
    if loaded is None:  # doctor 를 모르는 옛 hswap: 예약 상태(schedule)의 답을 쓴다
        loaded = (service or {}).get("loaded") is True
    return d.get("store") == "file" and not loaded and not in_session()


def item_text():
    """keychain_item_present 의 화면 문구. 정리할 길은 hswap doctor 가 답한 reconcile 예약 상태로 정한다: 설치·적재됐으면 reconcile 이 정리하고,
    아니면 예약 설치를 권한다. 지난 reconcile 이 어느 쪽이 새것인지 몰랐으면(reconcile_ambiguous) 사람이 골라야 한다. doctor 를 모르면(옛 hswap) 붙이지 않는다."""
    base = KEEPALIVE_ERRORS["keychain_item_present"]
    if not doctor:
        return base
    if "reconcile_ambiguous" in (doctor.get("problems") or []):  # hswap 이 본 사정(영문, macos_daemon.summary 가 자르고 거른 것)을 함께 보인다
        return " ".join(x for x in (f"{base}. {HSWAP_ERRORS['reconcile_ambiguous']}", doctor.get("last_reconcile_hint")) if x)
    ready = doctor.get("reconcile_installed") is True and doctor.get("reconcile_loaded") is True
    return f"{base}. " + ("로그인 세션에서 reconcile 이 정리합니다" if ready else "hswap claude reconcile --install-service 로 정리 예약을 설치하세요")


def headless():
    """macOS 의 로그인 세션 밖에서 돌고, hswap 저장 방식이 파일이 아니라서 키체인을 읽지 못하는가. 저장 방식을 모르면 아니라고 본다."""
    return sys.platform == "darwin" and (doctor or {}).get("store") not in (None, "file") and not in_session()


def installed_version():
    """설치된 hswap 의 버전("0.2.1"). 없거나 읽지 못하면 None."""
    exe = binary()
    if not exe:
        return None
    try:
        p = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S,
                           stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.fullmatch(r"hswap (\d+\.\d+\.\d+)", p.stdout.strip())
    return m.group(1) if m else None


def _key(v):
    return tuple(int(x) for x in v.split("."))


def _install():
    """uv tool install(없으면 pipx). 이미 깔린 낮은 버전도 같은 명령으로 올린다: uv 는 기존 도구 환경의 Python 을 그대로 쓴다
    (thsvkd-home 처럼 Python 경로를 고정해 둔 기기도 유지된다). pipx 는 --force 가 있어야 다시 깐다."""
    global _version
    uv, pipx = which_local("uv"), which_local("pipx")
    if not (uv or pipx):
        raise RuntimeError("uv·pipx 가 없어 hswap 을 설치할 수 없습니다")
    cmd = [uv, "tool", "install", PACKAGE] if uv else [pipx, "install", "--force", PACKAGE]
    # 환경을 바꾸는 동안 jarvis 의 list·switch 가 hswap 을 띄우지 않게 한다(자격을 바꾸던 switch 가 중간에 끊기지 않게)
    with _lock:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=INSTALL_TIMEOUT_S,
                               stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"hswap 설치가 {INSTALL_TIMEOUT_S}초 안에 끝나지 않았습니다")
    if p.returncode or not binary():
        last = (p.stderr.strip().splitlines() or ["원인 모름"])[-1]
        raise RuntimeError(f"hswap 설치 실패(종료 코드 {p.returncode}): {last}")
    _version = installed_version()
    if _version is None or _key(_version) < _key(VERSION):  # PATH 앞의 다른 hswap 이 가리는 경우 등. 실패로 보여 6시간 간격을 지킨다
        raise RuntimeError(f"hswap {VERSION} 을 설치했지만 실행되는 hswap 은 {_version or '버전을 읽지 못하는 것'}입니다")


installer = Installer("hswap", _install)


def ensure():
    """hswap 이 없거나 고정 버전보다 낮으면 뒤에서 고정 버전을 설치한다. hswap 이 다루는 하네스 CLI(Claude Code·Codex·Grok·OpenCode)가
    하나도 없는 기기는 hswap 이 쓸모없어 설치하지 않는다. 설치 실패는 Installer 가 6시간 뒤에 다시 시도한다.
    버전을 아직 모르거나 읽지 못했거나 더 높으면(사람이 먼저 올린 것) 그대로 둔다. None(할 일 없음) 또는 화면에 붙일 문구."""
    if not config.CSWAP_INSTALL or not any(which_tool(n) for n in CLIS):
        return None
    if binary() and (_version is None or _key(_version) >= _key(VERSION)):
        return None
    return installer.ensure("AI 계정별 사용량·로그인 유지")


def check():
    """설치된 버전을 다시 읽고 ensure() 로 맞춘다."""
    global _version
    _version = installed_version()
    return ensure()


def status():
    """화면의 기기 행: 확인한 hswap 버전(없으면 None)과 고정 버전, 설치 중이거나 실패했으면 그 문구. 확인 전이면 None."""
    if _version is None and installer.state is None:
        return None
    behind = _version is None or _key(_version) < _key(VERSION)  # 맞은 뒤에는 지난 실패 문구를 보이지 않는다
    note = {"running": "설치하는 중", "failed": installer.error}.get(installer.state) if behind else None
    out = {"version": _version, "pinned": VERSION, "note": note}
    if doctor:  # macOS: 저장 방식(file·auto)과 hswap doctor 의 문제. 모르는 문제 코드는 코드 그대로(소문자·밑줄만) 보인다
        # 세션 안의 키체인 저장 방식은 흔한 정상이라 not_file_store 는 세션 밖에서만 문제로 보인다.
        # keepalive_service_missing 은 hswap 이 jarvis tick 을 보지 못해 나온다: tick 이 대신 도는 기기에서는 문제가 아니다
        ticks = "keepalive_service_missing" in (doctor.get("problems") or []) and gateway_ticks()
        codes = [p for p in doctor.get("problems") or [] if (p != "not_file_store" or (sys.platform == "darwin" and not in_session()))
                 and not (p == "keepalive_service_missing" and ticks)]
        text = lambda p: f"{macos_daemon.PROBLEMS[p]}({p})" if p in macos_daemon.PROBLEMS else p
        hint = doctor.get("last_reconcile_hint")
        out.update(store=doctor.get("store"), problems=[f"{text(p)}: {hint}" if p == "reconcile_ambiguous" and hint else text(p) for p in codes],
                   keychain_backups=doctor.get("keychain_backups"), **({"keepalive": "jarvis 가 5분마다 keepalive 를 돌립니다"} if ticks else {}))
    return out


def _sec(v):
    """hswap 의 ISO 시각이나 저장소의 epoch 초 → epoch 초. 형식이 이상하면 None."""
    if isinstance(v, str):
        return parse_iso(v)
    # 범위 비교는 inf·nan·아주 큰 정수에서도 예외가 나지 않는다
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1e11 else None


def _pct(v):
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    return max(0, min(100, round(v))) if ok else None


def _windows(u):
    """hswap claude usage·저장소 lastGood → (windows, buckets). 두 쪽의 키 이름(fiveHour/five_hour, resetsAt/resets_at)을 여기서 함께 받는다.
    Fable 은 화면의 'fable' 창으로, 그 밖의 모델별 창은 bucket 으로. 모르는 모델 이름은 내보내지 않고 번호로 적는다."""
    u = u if isinstance(u, dict) else {}
    reset = lambda w: _sec(w.get("resetsAt", w.get("resets_at")))
    wins, buckets = [], []
    for k, alt, key in (("fiveHour", "five_hour", "session"), ("sevenDay", "seven_day", "weekly")):
        w = u.get(k, u.get(alt))
        if isinstance(w, dict) and _pct(w.get("pct")) is not None:
            wins.append({"key": key, "used": _pct(w["pct"]), "resets_at": reset(w)})
    for s in u.get("scoped") if isinstance(u.get("scoped"), list) else []:
        if not isinstance(s, dict) or _pct(s.get("pct")) is None:
            continue
        name = str(s.get("name") or "")[:80]
        if name.lower() == "fable":
            wins.append({"key": "fable", "used": _pct(s["pct"]), "resets_at": reset(s)})
        else:
            buckets.append({"name": name if name.lower().split(" ")[0] in KNOWN_BUCKETS else f"모델별 한도 {len(buckets) + 1}",
                            "used": _pct(s["pct"]), "resets_at": reset(s)})
    return wins, buckets


def _plan_label(row):
    """hswap 0.2.7 의 plan("max"·"team"…)과 rateLimitTier("default_claude_max_20x")를 "Max 20x" 로. 모양이 다르면 빈 문자열(Max 5x/20x 를 모르는 티어는 배수를 붙이지 않는다)."""
    plan, tier = row.get("plan"), row.get("rateLimitTier")
    if not (isinstance(plan, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,23}", plan)):
        return ""
    times = re.fullmatch(r"default_claude_max_(\d{1,3})x", tier) if isinstance(tier, str) else None
    return plan.capitalize() + (f" {times.group(1)}x" if times else "")


def account(row, source="cswap", headless=False):
    """hswap claude 계정 한 줄(저장소 어댑터도 같은 모양으로 넘긴다) → 화면 계정. 조회가 실패해도 마지막으로 읽은 값(lastGoodUsage)과 그 이유를 함께 준다.
    attention: 사람이 손봐야 다시 읽히는 상태(재로그인 등). 화면이 '주의 필요'로 센다.
    headless: 로그인 세션 밖의 키체인 저장 방식(headless()). 자격 증명이 없다·키체인을 읽지 못했다는 슬롯 실패를 그 까닭(HEADLESS)으로 바꾼다."""
    status = row.get("usageStatus")
    usage, fetched = row.get("usage"), row.get("usageFetchedAt")
    if status != "ok" and isinstance(row.get("lastGoodUsage"), dict):
        usage, fetched = row["lastGoodUsage"], row.get("lastGoodFetchedAt")
    raw_reason = row.get("usageError")
    reason = raw_reason if isinstance(raw_reason, str) and re.fullmatch(r"http-[1-5][0-9]{2}", raw_reason) else ""
    note, attention = STATUS.get(status, (None, False))
    if headless and status in ("no_credentials", "keychain_unavailable"):
        note, attention = HEADLESS, True
    elif status == "keychain_item_present":
        note = item_text() + "."
    if status == "api_key":
        note = "API 키 계정이라 구독 한도가 없습니다."
    elif status != "ok" and not note:
        note = "사용량을 읽지 못했습니다" + (f"({reason})" if reason else "") + "."
    left = (parse_iso(row.get("loginExpiresAt") or "") or 0) - time.time()
    if status not in ("relogin_required", "api_key") and 0 < left <= LOGIN_WARN_S:
        soon = f"로그인 만료 {_span(left)} 남음. 그 계정으로 다시 /login 하세요."
        note, attention = (note + " " + soon if note else soon), True
    windows, buckets = _windows(usage)
    plan = " · ".join(x for x in (_plan_label(row), str(row.get("alias") or "")[:40], "자동 전환 제외" if row.get("disabled") is True else "") if x)
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
            "windows": windows, "buckets": buckets[:12], "extra": None, "credits": None, "source": source}


class _HswapError(RuntimeError):
    """hswap 이 최상위 오류로 답했다. 문구는 고정 문구이고 kind 는 응답의 값 그대로라 비교에만 쓴다."""

    def __init__(self, kind):
        known = HSWAP_ERRORS.get(kind) if isinstance(kind, str) else None
        if not known:  # 종류 원문은 화면에 내지 않는다. 진단용으로 거른 값만 게이트웨이 로그에 남긴다
            print(f"[jarvis] hswap 의 모르는 오류 종류: {re.sub(r'[^A-Za-z_]', '', kind if isinstance(kind, str) else '')[:40]}", flush=True)
        super().__init__(known or "hswap 오류(알 수 없는 종류)")
        self.kind = kind


def _json(exe, *args, timeout=TIMEOUT_S):
    try:
        p = subprocess.run([exe, "claude", *args, "--json"], capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:  # 메시지에 실행 경로(홈 디렉터리)가 들어 있어 그대로 내보내지 않는다
        raise RuntimeError(f"hswap 이 {timeout:.0f}초 안에 답하지 않았습니다")
    except OSError as e:
        raise RuntimeError(f"hswap 을 실행하지 못했습니다({type(e).__name__})")
    try:
        body = json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(f"hswap claude {args[0]} --json 응답을 읽지 못했습니다(종료 코드 {p.returncode})")
    if not isinstance(body, dict) or body.get("error"):
        err = body.get("error") if isinstance(body, dict) else None
        raise _HswapError(err.get("type") if isinstance(err, dict) else None)
    return body


def _identity(email, org_uuid=None):
    """계정 신원 비교의 유일한 규칙. 개인 계정의 UUID 는 null·누락·빈 문자열로 온다: 모두 같은 값이고, 앞뒤 공백과 대소문자는 보지 않는다."""
    return str(email or "").strip().lower(), str(org_uuid or "").strip().lower()


def _fingerprint(email, org_uuid):
    """슬롯의 계정 신원 지문(이메일 + 조직 UUID). 비교에만 쓰고 응답으로 내보내지 않는다."""
    return hashlib.sha256("\0".join(_identity(email, org_uuid)).encode("utf-8", "replace")).hexdigest()


def switch(slot):
    """'이 계정 사용' 버튼: Claude Code 가 쓰는 계정을 hswap 슬롯 slot 으로 바꾼다. (http 상태, 응답)."""
    if not (isinstance(slot, str) and slot.isdigit() and len(slot) <= 4):
        return 400, {"error": "slot 은 hswap 슬롯 번호여야 합니다"}
    exe = binary()
    if not exe:
        return 404, {"error": "이 기기에는 hswap 이 없습니다"}
    if not _lock.acquire(timeout=TIMEOUT_S + 5):  # list 와 겹치지 않게
        return 503, {"error": "hswap 이 응답하지 않습니다"}
    try:
        body = _json(exe, "switch", slot, timeout=SWITCH_TIMEOUT_S)
        return 200, {"switched": body.get("switched") is True}
    except Exception as e:
        return 409, {"error": scrub_home(str(e))[:200] or type(e).__name__}
    finally:
        _state.cache = None  # 실패해도 다음 읽기는 hswap 에 다시 묻는다
        _state.switched += 1  # 묶음을 비운 뒤에 는다: 새 번호를 본 읽기는 옛 묶음을 볼 수 없다
        _lock.release()


def remove(slot):
    """'계정 삭제' 버튼: hswap 슬롯 slot 에 저장한 Claude 계정을 지운다(`hswap claude remove <슬롯>`). (http 상태, 응답).
    remove 는 --json 이 없고 확인을 표준 입력으로 묻는다: y 를 넘기고, 종료 코드 0 과 'Removed' 줄로 지운 것을 확인한다.
    실행 중인 그 계정의 세션 등으로 hswap 이 거절하면 그 이유(홈 경로를 지운 마지막 줄)를 409 로 준다."""
    if not (isinstance(slot, str) and slot.isdigit() and len(slot) <= 4):
        return 400, {"error": "slot 은 hswap 슬롯 번호여야 합니다"}
    exe = binary()
    if not exe:
        return 404, {"error": "이 기기에는 hswap 이 없습니다"}
    if not _lock.acquire(timeout=TIMEOUT_S + 5):  # list·switch 와 겹치지 않게
        return 503, {"error": "hswap 이 응답하지 않습니다"}
    try:
        p = subprocess.run([exe, "claude", "remove", slot], input="y\n", capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=SWITCH_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return 409, {"error": f"hswap 이 {SWITCH_TIMEOUT_S}초 안에 답하지 않았습니다"}
    except OSError as e:
        return 409, {"error": f"hswap 을 실행하지 못했습니다({type(e).__name__})"}
    finally:
        _state.cache = None
        _state.switched += 1  # 지우기 전에 시작한 읽기가 지운 계정을 다시 내지 않게 한다
        _lock.release()
    if p.returncode == 0 and "Removed" in p.stdout:
        return 200, {"removed": True}
    last = re.sub(r"\x1b\[[0-9;]*m", "", (p.stderr.strip().splitlines() or [""])[-1]).removeprefix("Error: ")
    return 409, {"error": scrub_home(last)[:200] or f"hswap 이 계정을 지우지 않았습니다(종료 코드 {p.returncode})"}


def live(force=False, shape=None, deadline=None):
    """hswap CLI 어댑터. None(hswap 없음·계정 없음) 또는 {"accounts": [...], "fetched_at": 초, "ids": {슬롯: 신원 지문}} 또는 {"error": "..."}. ids 는 read() 안에서만 쓴다.
    hswap 이 답하면 최상위 keychainUnavailable(0.2.2 의 추가 필드. 없으면 아니라고 본다)을 _state.device 에 적는다. 실패한 조회는 건드리지 않는다.
    force 는 jarvis 의 30초 묶음만 건너뛴다(프로바이더 조회 간격은 hswap 이 정한다).
    shape: 이 호출을 정한 저장소 모습. 잠금을 기다린 뒤든 포기한 뒤든 모습이 같은 결과만 다시 쓴다(hswap 은 한 번에 하나만 도니 옛 답은 새 답보다 먼저 온다).
    deadline: 잠금 대기와 hswap 호출이 함께 쓰는 시한(monotonic). 넘기면 hswap 을 부르지 않고 오류를 준다."""
    exe = binary()
    if not exe:
        return None
    asked = time.monotonic()
    # 동시에 여러 hswap 을 띄우지 않는다. 앞 요청이 끝나지 않으면(멈춘 hswap) 기다리지 않고 마지막 값을 준다
    left = lambda: float("inf") if deadline is None else deadline - time.monotonic()
    if not _lock.acquire(timeout=max(0, min(TIMEOUT_S + 5, left()))):
        held = _state.cache
        return held[1] if held and held[2] == shape else {"error": "hswap 이 응답하지 않습니다"}
    try:
        held = _state.cache
        # 기다리는 동안 앞 요청이 같은 저장소를 보고 새로 읽었으면 강제라도 그 값을 쓴다
        if held and held[2] == shape and (held[0] >= asked or (not force and asked - held[0] < CACHE_S)):
            return held[1]
        if left() <= 0:
            return {"error": "hswap 을 기다리다 읽기 시한을 넘겼습니다"}
        blind = headless()
        try:
            body = _json(exe, "list", timeout=min(TIMEOUT_S, left()))
            raw = [r for r in body.get("accounts") or [] if isinstance(r, dict)]
            accounts = [account(r, headless=blind) for r in raw]
            _state.device = (time.monotonic(), body.get("keychainUnavailable") is True)
            _state.discovered = bool(accounts)  # 실패한 조회는 건드리지 않는다: 일시 실패로 계정을 잊지 않게
            _state.fatal = None
            out = {"accounts": accounts, "fetched_at": time.time(),
                   "ids": {str(r.get("number")): _fingerprint(r.get("email"), r.get("organizationUuid")) for r in raw}} if accounts else None
        except Exception as e:
            if isinstance(e, _HswapError):  # 시간 초과처럼 답이 없는 실패는 이전 판정을 지우지 않는다
                _state.fatal = (time.monotonic(), e.kind) if e.kind in FATAL_ERRORS else None
            # 세션 밖의 키체인 저장 방식이면 hswap 의 오류(CredentialReadError 등)는 그 결과다. 까닭과 고칠 명령을 보인다
            kind = getattr(e, "kind", None)
            out = {"error": HEADLESS if blind and kind in HEADLESS_KINDS else item_text() if kind == "keychain_item_present"
                   else scrub_home(str(e))[:200] or type(e).__name__}
        _state.cache = (time.monotonic(), out, shape)
        return out
    finally:
        _lock.release()


def _root():
    if sys.platform.startswith("linux"):
        data = os.path.expanduser(os.environ.get("XDG_DATA_HOME", ""))
        if not os.path.isabs(data):
            data = os.path.expanduser("~/.local/share")
        return os.path.join(data, "claude-swap")
    return os.path.expanduser("~/.claude-swap-backup")


def _live_identity():
    """Claude Code 가 지금 쓰는 로그인의 (이메일, 조직 UUID). 로그인이 없거나 읽지 못하면 None.
    엔진과 같은 규칙이다: sequence.json 의 activeAccountNumber 는 계정을 더할 때도 바뀌어 실제 로그인과 어긋날 수 있다.
    파일은 CLAUDE_CONFIG_DIR(없으면 홈)의 .claude.json, 단 Claude Code 가 예전 <설정 홈>/.config.json 을 쓸 조건이면 그것.
    이 파일에 토큰은 없다. 이메일·조직 UUID 는 계정을 맞추는 데만 쓰고 내보내지 않는다."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    path = os.path.join(env or os.path.expanduser("~"), ".claude.json")
    legacy = os.path.join(env or os.path.expanduser(os.path.join("~", ".claude")), ".config.json")
    try:
        st = os.stat(legacy)
        if stat.S_ISREG(st.st_mode) and (st.st_size > 0 or (st.st_mode & 0o222 and not os.path.exists(path))):
            path = legacy
    except OSError:
        pass
    try:
        if os.path.getsize(path) > CONFIG_MAX:
            return None
        with open(path, encoding="utf-8") as f:
            acct = json.load(f).get("oauthAccount")
    except (OSError, ValueError, AttributeError):
        return None
    email = acct.get("emailAddress") if isinstance(acct, dict) else None
    return (email, acct.get("organizationUuid", "") or "") if isinstance(email, str) and email else None


def _store_read():
    """저장소 캐시 어댑터. 명령 실행과 토큰 갱신은 하지 않는다. 계정이 없거나 읽지 못하면 None."""
    root = _root()
    try:
        roster = opencode_usage._private_json(os.path.join(root, "sequence.json"))
        try:
            cache = opencode_usage._private_json(os.path.join(root, "cache", "usage.json"))
        except (OSError, ValueError):
            cache = {}
        accounts = roster.get("accounts", {}) if isinstance(roster, dict) else {}
        sequence = roster.get("sequence", []) if isinstance(roster, dict) else []
        identity = _live_identity()
        cached = cache.get("accounts", {}) if isinstance(cache, dict) and cache.get("schemaVersion") == 2 else {}
        if not isinstance(accounts, dict) or not isinstance(sequence, list) or not isinstance(cached, dict):
            return None
        result, ids, orgs = [], {}, {}
        for number in sequence:
            if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
                continue
            ident = accounts.get(str(number))
            row = cached.get(str(number))
            if not isinstance(ident, dict):
                continue
            ids[str(number)] = _fingerprint(ident.get("email"), ident.get("organizationUuid"))
            who = _identity(ident.get("email"), ident.get("organizationUuid"))
            valid = isinstance(row, dict) and _identity(row.get("email"), row.get("organizationUuid")) == who
            usage = row.get("lastGood") if valid else None
            usage = usage if isinstance(usage, dict) else {}
            fetched = row.get("fetchedAt") if valid else None
            # hswap 이 갱신 토큰을 죽었다고 본 슬롯(AUTH_DEAD_STRIKES=1)은 다시 읽지 않는다. 남은 값은 격리 전의 것이다.
            # 새 자격으로 고쳐지면 hswap 이 이 기록을 지운다(harness-swap src/claude_swap/usage_store.py token_dead)
            strikes = row.get("authDeadStrikes") if valid else None
            dead = isinstance(strikes, int) and strikes >= 1
            name = ident.get("organizationName")
            # 저장소 값도 hswap 한 줄 모양으로 바꿔 account() 의 규칙을 그대로 태운다. 조직 UUID·별칭은 넘기지 않는다
            a = account({"number": number, "email": provider_usage._email(ident), "organizationName": name if isinstance(name, str) else None,
                         # 사용 중: 실제 로그인과 이메일·조직 UUID 가 같은 슬롯(엔진의 _find_account_slot 과 같은 비교)
                         "active": identity is not None and _identity(*identity) == who,
                         "usageStatus": "relogin_required" if dead else "ok" if any(_windows(usage)) else "no_saved_usage",
                         "usage": usage, "usageFetchedAt": fetched, "lastGoodUsage": usage, "lastGoodFetchedAt": fetched}, "cswap-cache")
            # 화면은 이메일+조직 이름으로 같은 계정을 묶는다. 개인 조직("<email>'s Organization")은 None
            result.append({**a, "org": org(a["org"], a["label"])})
            org_uuid = _identity(None, ident.get("organizationUuid"))[1]
            if org_uuid:  # hswap 은 조직 이름을 등록할 때만 저장한다: added 가 그 이름의 시점이다
                orgs[str(number)] = {**({"org": result[-1]["org"]} if isinstance(name, str) and name else {}), "org_key": hashlib.sha256(org_uuid.encode("utf-8", "replace")).hexdigest(), "org_at": _sec(ident.get("added"))}
        # signed_in: 로그인을 읽었다. 그런데 active 인 행이 없으면 지금 로그인은 어느 저장 슬롯과도 맞지 않는다
        return {"accounts": result, "signed_in": identity is not None, "ids": ids, "orgs": orgs} if result else None
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return None


def _keepalive():
    """hswap claude keepalive 가 남긴 저장소의 cache/keepalive.json(토큰 없음). 없거나 안전하지 않거나 모르는 형식이면 None."""
    try:
        body = opencode_usage._private_json(os.path.join(_root(), "cache", "keepalive.json"))
    except (OSError, ValueError, RecursionError):
        return None
    return body if isinstance(body, dict) and body.get("schemaVersion") == 1 else None


def _keychain_open():
    """이 프로세스가 로그인 키체인에 닿는가(비밀값은 읽지 않는다). 확인하지 못하면 닿지 않는 쪽으로 본다."""
    try:
        return subprocess.run(["security", "show-keychain-info"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def login_ready():
    """macOS 에서 화면 로그인(claude auth login → hswap claude add)을 낼 수 있는 기기인가.
    파일 저장소이면서 이 게이트웨이가 키체인에 닿지 않거나(닿으면 claude 가 로그인을 키체인에 써서 hswap 이 파일에서 찾지 못한다), 키체인 모드(store 가 file 이 아님)에서 이 게이트웨이의 hswap 조회가 최근에 키체인에 닿았고(keychainUnavailable 아님)
    설정 충돌·키체인 항목 문제가 없을 때다. keepalive 기록이 없거나 게이트웨이의 키체인 접근을 최근에 확인하지 못했으면 아니다."""
    k = _keepalive()
    if k is None:
        return False
    if k.get("store") == "file":
        return not _keychain_open()
    mono = time.monotonic()
    err = k.get("error") if isinstance(k.get("error"), dict) else {}
    blocked = ("config_conflict", "keychain_item_present")
    return _recent(_state.device, mono) is False and err.get("type") not in blocked and _recent(_state.fatal, mono) not in blocked


def _unscheduled(svc):
    """macOS 에서 hswap 이 keepalive 예약이 없다고 답했다."""
    return isinstance(svc, dict) and svc.get("platform") == "launchd" and svc.get("installed") is False


def _span(s):
    s = max(0, s)
    return f"{s / 86400:.0f}일" if s >= 86400 else f"{s // 3600:.0f}시간" if s >= 3600 else f"{max(1, s // 60):.0f}분"


def _is_relogin(a):
    return STATUS["relogin_required"][0] in (a.get("note"), a.get("error"))


def _judge_login(k, rows, keychain, signed_in, fatal, first_seen, now, svc=None, headless=False):
    """로그인 판정: 값만 받아 (기기 단위 로그인 상태 {state, text, note, attention, slot, other, store} 또는 None, 새 first_seen). 알릴 것이 없으면 None(keepalive.json 이 없으면 이 기능을 쓰지 않는 기기다).
    k: keepalive 기록(없으면 {}). first_seen: 파일 저장소에서 갱신을 확인하지 못한 기록을 처음 본 시각. now: time.time().
    순서: 세션 밖의 키체인 저장 방식(headless) > 설정 충돌·키체인 항목(있거나 확인 실패) > 사람이 다시 로그인해야 하는 것 > 키체인 > 만료 임박 > 멈춤 > 이전 계정의 기록 > 그 밖의 실패 > 정상.
    headless: macOS 로그인 세션 밖인데 hswap 이 키체인 저장 방식이다(headless()). 키체인을 읽지 못해 다른 판정이 모두 그 결과라 기록이 없어도 먼저 보인다.
    svc: macOS 의 keepalive 서비스 상태(hswap --service-status). 기록이 오래됐거나 없을 때 그 까닭을 가른다: 예약이 없으면 '미설정',
    hswap 이 만든 예약이 적재되지 않았으면 '중지됨'(GUI 세션이 없으면 다음 로그인 때 시작). 사람이 만든 예약이나 모르는 상태는 '멈춤' 그대로다.
    store: 파일 저장소 기기면 "file", 아니면 None(옛 형식 포함). 파일의 다른 값은 내보내지 않는다. 키체인을 쓰지 않는 기기라 키체인에 닿지 못하는 것은 문제가 아니다.
    rows: 사용 중 계정의 재로그인 판정(저장소의 authDeadStrikes 와 hswap 의 relogin_required 가 같은 문구가 된다)과 사용 중 슬롯.
    keychain: hswap 의 keychainUnavailable. signed_in: 로그인을 읽었다(rows 에 active 가 없으면 저장되지 않은 로그인이다).
    fatal: hswap 이 최상위 오류로 답한 FATAL_ERRORS. keepalive 기록보다 새로운 사실이라 기록과 상관없이 쓴다.
    unconfirmed: 파일 저장소인데 keychain_unavailable 로 실패한 기록. 키체인 경고는 아니지만 갱신이 확인되지 않았으므로 성공 문구를 쓰지 않고, CLAUDE_KEEPALIVE_STALE_S 동안 계속되면 attention 으로 올린다.
    other: keepalive 기록의 activeSlot 이 지금 로그인의 슬롯과 다르다고 확인됐다. 만료·오류·접근 토큰은 그 계정의 사실이라 쓰지 않고, 키체인·멈춤·자격 부재는 기기의 사실이라 그대로 쓴다.
    activeSlot 이 없으면 슬롯을 모르는 것이지 다른 계정이라는 증거가 아니다: 판정은 그대로 두고 slot 만 뺀다.
    슬롯은 맞다고 확인될 때만 준다(다른 계정의 슬롯에 add 하면 저장 로그인을 덮을 수 있다). 파일이 없으면 기록과 비교할 것이 없어 사용 중 슬롯을 준다."""
    err = k.get("error") if isinstance(k.get("error"), dict) else {}
    kind = err.get("type") if isinstance(err.get("type"), str) else None
    checked, expires = _sec(k.get("checkedAt")), _sec(k.get("loginExpiresAt"))
    current = next((a.get("slot") for a in rows if a.get("active") and a.get("slot")), None)
    recorded = k.get("activeSlot")
    recorded = str(recorded) if isinstance(recorded, int) and not isinstance(recorded, bool) and recorded > 0 else None
    # 사용 중 슬롯을 모르고 저장되지 않은 로그인도 아니면 맞는지 알 수 없다: 경고는 남기고 슬롯만 권하지 않는다
    other = recorded is not None and bool(current or signed_in) and current != recorded
    file_store = k.get("store") == "file"
    blocked = next((x for x in (fatal, kind) if x in ("config_conflict", "keychain_item_present")), "keychain_item_present" if file_store and k.get("keychainItemPresent") is True else None)
    # shortcut: 처음 본 시각은 메모리에만 있어 게이트웨이를 재시작하면 처음부터 센다. hswap 이 마지막 성공 시각을 적으면 그것으로 바꾼다
    first_seen = (first_seen or now) if file_store and kind == "keychain_unavailable" and k.get("ok") is not True else None
    quiet = False
    if headless:
        state, text = "headless", HEADLESS
    elif blocked:  # 기기의 사실이다. 항목이 남아 있으면 SSH 의 /login 이 저장되지 않을 수 있어 재로그인보다 먼저 보인다
        state, text = blocked, KEEPALIVE_ERRORS[blocked]
    elif fatal == "relogin_required" or (kind == "relogin_required" and not other) or any(a.get("active") and _is_relogin(a) for a in rows):
        state, text = "relogin", "재로그인 필요"
    elif not file_store and (kind == "keychain_unavailable" or keychain):
        state, text = "keychain", "키체인 접근 불가: 키체인에 접근할 수 없는 곳에서 실행됨"
    elif not k and not _unscheduled(svc):
        return None, first_seen
    elif not other and expires is not None and expires - now <= LOGIN_WARN_S:
        state, text = "expiring", f"로그인 만료 임박({_span(expires - now)})" if expires > now else "로그인 기한이 지났습니다"
    elif (checked is None or now - checked > config.CLAUDE_KEEPALIVE_STALE_S) and _unscheduled(svc):
        state, text = "unscheduled", "자동 갱신 미설정(예약 실행 없음)"
    elif (checked is None or now - checked > config.CLAUDE_KEEPALIVE_STALE_S) and isinstance(svc, dict) and svc.get("managed") is True and svc.get("loaded") is False:
        state, text = "stopped", "자동 갱신 중지됨" + ("(GUI 로그인 뒤 시작)" if svc.get("domainAvailable") is False else "")
    elif checked is None or now - checked > config.CLAUDE_KEEPALIVE_STALE_S:
        state, text = "stalled", "자동 갱신 멈춤" + (f"(마지막 {_span(now - checked)} 전)" if checked else "")
    elif other and kind != "no_credentials":  # 자격 부재는 add 로 고칠 수 없어 /login 안내로 이어지는 실패로 둔다
        state, text = "other_account", "자동 갱신 기록이 이전 계정의 것입니다"
    elif first_seen:
        quiet = now - first_seen <= config.CLAUDE_KEEPALIVE_STALE_S
        state, text = "unconfirmed", "자동 갱신 확인되지 않음" + ("" if quiet else f"({_span(now - first_seen)}째)")
    elif k.get("ok") is not True:
        state, text = "failed", f"자동 갱신 실패({KEEPALIVE_ERRORS.get(kind, '알 수 없는 오류')})"
    else:
        state, text = "ok", f"로그인됨{' · 파일 저장소' if file_store else ''}, 자동 갱신 {_span(now - checked)} 전"
    stale = checked is None or now - checked > config.CLAUDE_KEEPALIVE_STALE_S  # 만료 임박처럼 먼저 고른 상태여도 갱신이 돌지 않으면 같다
    access = None if other or blocked or stale or state in ("unconfirmed", "headless") else _sec(k.get("accessExpiresAt"))  # 갱신이 멈췄거나 확인되지 않았는데 "자동 갱신"을 말하지 않는다
    return {"state": state, "text": text, "attention": state != "ok" and not quiet, "slot": current if not k or current == recorded else None, "other": other,
            "store": "file" if file_store else None,
            "note": None if access is None else f"접근 토큰 {_span(access - now)} 뒤 만료(자동 갱신)" if access > now else "접근 토큰 만료됨(자동 갱신 대기)"}, first_seen


def login(rows=(), keychain=False, signed_in=False, fatal=None):
    """지금 keepalive 기록으로 로그인을 판정한다(_judge_login). 파일 읽기와 처음 본 시각의 기억은 판정 밖인 여기서 한다."""
    state, _state.unconfirmed = _judge_login(_keepalive() or {}, rows, keychain, signed_in, fatal, _state.unconfirmed, time.time(), service, headless())
    if state and state["state"] == "keychain_item_present":
        state["text"] = item_text()
    return state


def _old(a, now):
    """값이 있는데 신선도 기준보다 오래됐는가. 물을지 판정과 행 판정이 같은 규칙을 쓴다."""
    at = a.get("updated_at")
    return bool(a.get("windows") or a.get("buckets")) and (not at or now - at > config.CLAUDE_STALE_S)


def _recent(fact, mono):
    """hswap 이 답한 기기 사실 (monotonic, 값) 의 값. 신선도 기준 안에 다시 확인하지 못한 옛 답은 쓰지 않는다."""
    return fact[1] if fact and mono - fact[0] < config.CLAUDE_STALE_S else None


def _judge_shape(store, k):
    """묶음이 믿을 수 있는지 가르는 값: 슬롯→신원 지문 전체, 사용 중 슬롯, 인증 실패인 슬롯, keepalive 등급(ok·실패 종류)과 loginExpiresAt.
    /login 뒤의 새 기록이 묶어 둔 CLI 의 재로그인·만료 경고를 무효로 한다. checkedAt 같은 시각만 바뀌면 달라지지 않는다. 묶음 안에만 두고 내보내지 않는다."""
    rows = (store or {}).get("accounts", [])
    err = k.get("error") if isinstance(k.get("error"), dict) else {}
    grade = None if not k else "ok" if k.get("ok") is True else err.get("type") if isinstance(err.get("type"), str) else "failed"
    return ((store or {}).get("ids", {}), {a.get("slot") for a in rows if a.get("active")}, {a.get("slot") for a in rows if _is_relogin(a)},
            grade, k.get("loginExpiresAt"))


def _store_shape(store):
    return _judge_shape(store, _keepalive() or {})


def _judge_ask(force, store, shape, held, discovered, fatal, device, mono, now):
    """묶음을 다시 쓸지 hswap 에 물을지 정한다. (물을까, 묻지 않을 때 보일 묶음의 답). 값만 받는다.
    held: (monotonic, 답, 물을 때의 모습). discovered·fatal·device: 지난 답의 기억. mono·now: time.monotonic()·time.time().
    계정을 찾은 적도 저장소도 없는 기기는 묻지 않는다. CLI 로 계정을 찾았거나 경고가 있던 기기는 기준이 지나면 다시 묻는다.
    묶음의 모습은 hswap 답이 아니라 물을 때의 저장소다: 답이 저장소와 계속 어긋나도 변경 한 번에 한 번만 다시 부른다."""
    recent = held[1] if held and mono - held[0] < config.CLAUDE_STALE_S else None
    recent = recent if recent and recent.get("accounts") else None
    recheck = discovered or bool(fatal) or (bool(device and device[1]) and not _recent(device, mono))
    if store is None and recent is None and not force and not recheck:
        return False, None
    changed = recent is not None and shape != held[2]
    return force or changed or recent is None or any(_old(a, now) for a in recent["accounts"]), recent


def _judge_rows(out, store, hswap, now):
    """행 판정: 저장값이 지금 로그인에 아직 맞는지 정해 화면 행을 만든다. 값만 받고, 묶음이 들고 있는 dict 는 고치지 않는다.
    out: live() 의 답이나 묶음. store: _store_read() 의 값. hswap: 실행 파일이 있는가. now: time.time().
    (login 판정에 넘길 행, 응답 행). 보일 행이 없으면 ([], None).
    CLI 행과 저장소의 같은 슬롯은 신원 지문까지 같아야 같은 계정이다: 슬롯 번호만 같으면 다른 계정의 상태를 옮기게 된다.
    사용 중: 실제 로그인과 같은 저장 슬롯이고 신원도 같은 행(엔진과 같은 규칙. 맞는 슬롯이 없으면 사용 중도 없다). 로그인이나 저장소를 읽지 못하면 hswap 의 값.
    인증 실패(authDeadStrikes): 슬롯과 신원이 같은 CLI 행에 붙인다. 묶어 둔 행이 그 뒤에 적힌 실패를 가리지 않게 읽을 때마다 보고, 사용량 값은 건드리지 않는다.
    조직: core 가 기기 사이에서 같은 조직을 알아볼 값(org_key·org_at)과 그 시각의 이름. 묶어 둔 hswap 답의 이름은 재등록보다 오래됐을 수 있어 이름도 저장소 값을 쓴다.
    슬롯과 신원이 저장소와 같은 행에만 붙고, core 가 키와 시각을 지운 뒤 화면으로 보낸다(USAGE-1).
    신선도: 값이 있는 행에 대체 이유와 "N 전 값"을 붙인다. login 판정은 이 문구가 붙기 전의 행을 본다."""
    stored = store or {}
    cli = (out or {}).get("accounts")
    ids = out.get("ids", {}) if cli else stored.get("ids", {})
    current = {a.get("slot") for a in stored.get("accounts", []) if a.get("active")}
    dead = {a.get("slot") for a in stored.get("accounts", []) if _is_relogin(a)}
    relogin = STATUS["relogin_required"][0]

    def same(a):
        fingerprint = ids.get(a.get("slot"))
        return fingerprint is not None and fingerprint == stored.get("ids", {}).get(a.get("slot"))

    def marked(a):
        if stored.get("signed_in"):
            a = {**a, "active": a.get("slot") in current and same(a)}
        a = {**a, "org": org(a.get("org"), a.get("label"))}
        if a.get("slot") not in dead or _is_relogin(a) or not same(a):
            return a
        has = a.get("windows") or a.get("buckets")
        return {**a, "attention": True, **({"note": relogin, "error": None} if has else {"status": "error", "note": None, "error": relogin})}

    def shown(a, why):
        org_id = stored.get("orgs", {}).get(a.get("slot"), {}) if same(a) else {}
        old, at = _old(a, now), a.get("updated_at")
        if not ((a.get("windows") or a.get("buckets")) and (old or why)):
            return {**a, **org_id}
        age = (f"{_span(now - at)} 전 값입니다." if at else "저장 시각을 모르는 값입니다.") if old else None
        return {**a, "note": " ".join(x for x in (a.get("note"), why, age) if x), "attention": a.get("attention") or old, **org_id}

    if cli:
        rows, why = [marked(a) for a in cli], None
    elif store:
        reason = (out or {}).get("error") or ("hswap 이 없습니다" if not hswap else "hswap 이 계정을 주지 않았습니다")
        rows, why = store["accounts"], reason.rstrip(".") + ". 저장된 값을 보입니다."
    else:
        return [], None
    return rows, [shown(a, why) for a in rows]


def read(force=False):
    """사용량 읽기. None(저장소도 실값도 없고 알릴 것도 없음) 또는 {"error": "..."} 또는 {"accounts": [...], "login": {...}}.
    login 은 알릴 것이 있을 때만이며 계정 목록과 상관없다: 계정이 없어도 {"accounts": [], "login": {...}}(hswap 오류가 있으면 error 도)를 준다.
    force 이거나, hswap 에 물은 지 신선도 기준이 지났거나, 보일 실값 중 기준보다 오래된 것이 있으면 hswap list 를 실행한다
    (저장소에는 로그인 기한·상태가 없어 만료 경고는 실값에서만 나온다. 예약 실행이 저장소를 늘 새로 써도 경고가 사라지지 않게 한다).
    그 사이에는 마지막 실값을 그대로 준다. 오래된 저장소 값은 실값이 없을 때만 보이므로 따로 보지 않는다. hswap 이 없거나 실패하면 저장소 값을 대체값으로 쓰고 이유를 붙인다.
    읽기는 설치하지 않는다(USAGE-3). hswap 설치는 hswap_usage.maintain() 이 맡는다.
    읽는 사이 switch() 가 끝나거나 저장소 모습이 바뀌면 옛 값을 내지 않고 다시 읽는다(진행 중인 이 읽기를 함께 기다리는 쪽도 옛 값을 받지 않는다).
    재시도는 2번까지이고 전체 시한은 READ_BUDGET_S 다. 그 안에 확정하지 못하면 CLI 값 대신 지금 저장소 값과 로그인 상태를 준다."""
    deadline = time.monotonic() + READ_BUDGET_S
    for _ in range(3):
        seen = _state.switched
        out, settled = _read(force, deadline)
        if settled and seen == _state.switched:
            return out
        if time.monotonic() >= deadline:
            break
        force = False  # 강제는 첫 시도로 지켰다. 다시 읽을 때는 같은 모습의 새 결과를 쓴다
    return _read(False, deadline, give_up=True)[0]


def _read(force, deadline, give_up=False):
    """(결과, 답을 받는 동안 저장소 모습이 그대로였는가). give_up: hswap 을 부르지 않고 지금 저장소로 답한다."""
    settled = True
    store = _store_read()
    if give_up:
        out = {"error": "저장소가 계속 바뀌어 hswap 값을 확정하지 못했습니다"}
    else:
        memory = (_state.cache, _state.discovered, _state.fatal, _state.device)  # switch() 가 도중에 비워도 같은 값을 본다
        mono = time.monotonic()
        shape = _store_shape(store)
        asked, out = _judge_ask(force, store, shape, *memory, mono, time.time())
        if asked:
            out = live(force, shape=shape, deadline=deadline)
            store = _store_read()  # 사용 중 판정은 hswap 이 답한 뒤의 로그인으로 한다(잠금을 기다리는 사이 로그인이 바뀌었을 수 있다)
            settled = _store_shape(store) == shape
    rows, accounts = _judge_rows(out, store, bool(binary()), time.time())
    mono = time.monotonic()  # hswap 이 방금 답했으면 그 기억으로 판정한다
    state = login(rows, _recent(_state.device, mono), bool(store and store.get("signed_in")), _recent(_state.fatal, mono))
    if accounts is None:
        return ({**(out or {}), "accounts": [], "login": state} if state else out), settled
    result = {"accounts": accounts}
    if (out or {}).get("fetched_at"):
        result["fetched_at"] = out["fetched_at"]
    return ({**result, "login": state} if state else result), settled
