"""Claude 계정의 모든 규칙을 한 곳에 둔다: 신선도, 만료 경고, 상태 문구, 사용 중 판정, 창·Fable·버킷 이름, 기기 로그인 상태.

바깥 인터페이스는 read(force)와 switch(slot)이다. 독립 경로와 호환 경로(orca_usage.read)가 같은 read 를 거치고, 그 뒤에 같은 merge_orca 로 Orca 계정을 보탠다.
안에 어댑터가 둘 있다.
- hswap CLI(live, _json): `hswap claude list --json`(cswap 과 같은 schemaVersion 1)의 실값. 토큰 갱신·사용량 조회 간격은 hswap 이 맡는다.
  쓰기는 core 의 '이 계정 사용' 버튼이 부르는 `hswap claude switch <슬롯> --json` 하나다(자격 교체는 hswap 이 한다).
- 저장소 캐시(_store_read, _keepalive): hswap(Claude 엔진 claude-swap)과 기존 cswap 이 함께 쓰는 저장소의 계정·마지막 사용량·keepalive.json. 대체값이다.
jarvis 는 자격 증명 파일을 열지 않는다(갱신 토큰을 따로 돌리면 hswap·Claude Code 의 로그인이 풀릴 수 있다). 계정은 이메일·조직 이름·슬롯 번호만 내보낸다.
이미 깔린 cswap 은 지우지 않는다. 옛 이름 cswap_usage·cswap_cached_usage 는 이 모듈로 넘기는 호환 이름표다(alias).

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
import types

from . import config, opencode_usage, provider_usage
from .util import Installer, org, parse_iso, scrub_home, which_local

CONFIG_MAX = 32 << 20  # Claude Code 전역 설정은 프로젝트 기록이 쌓여 수 MB 까지 큰다
KNOWN_BUCKETS = {"opus", "sonnet", "haiku"}  # 첫 단어가 이것인 이름만 그대로 내보낸다. 나머지는 번호로 적는다
# keepalive error.type → 화면 문구. 파일의 자유 문구(error.message)는 토큰·조직 UUID 가 들어 있어도 새지 않게 내보내지 않는다
KEEPALIVE_ERRORS = {"keychain_unavailable": "키체인 접근 불가", "relogin_required": "재로그인 필요", "split_brain": "키체인과 파일의 로그인이 갈라짐",
                    "refresh_failed": "토큰 갱신 실패", "no_credentials": "자격 증명 없음", "locked": "다른 프로세스가 잠금을 쥐고 있음", "unmanaged": "저장 슬롯에 없는 로그인",
                    "keychain_item_present": "키체인에 Claude 로그인이 있거나 확인하지 못해 자동 갱신을 멈췄습니다", "config_conflict": "hswap 설정 충돌(파일 저장소와 키체인 필수 모드)"}
# hswap 최상위 오류 type → 화면 문구(hswap 의 json_type 또는 예외 클래스 이름, claude_swap/exceptions.py). error.message 는 토큰·경로가 들어 있어도 새지 않게 쓰지 않는다
HSWAP_ERRORS = {**KEEPALIVE_ERRORS, "stores_disagree": KEEPALIVE_ERRORS["split_brain"], "required_mode_unsupported": "hswap 이 이 저장소 모드에서 지원하지 않는 명령",
                "stash_manifest_corrupt": "hswap 보관 목록 손상", "AccountNotFoundError": "저장 슬롯에 없는 계정", "LockError": KEEPALIVE_ERRORS["locked"],
                "ClaudeCodeLockTimeout": KEEPALIVE_ERRORS["locked"], "SwitchError": "계정 전환 실패"}
FATAL_ERRORS = ("config_conflict", "keychain_item_present", "relogin_required")  # keepalive 기록이 옛것이어도 기기 상태에 바로 보이는 type
LOGIN_WARN_S = 3 * 86400  # 로그인(갱신 토큰) 만료 3일 전부터 '주의 필요'. 새 /login 만이 기한을 늘린다
CACHE_S = 30  # hswap 자체가 캐시하므로 짧게만 묶는다(요청마다 프로세스를 띄우지 않게)
READ_BUDGET_S = 40  # 읽기 한 번의 전체 시한. 재시도의 잠금 대기와 hswap 호출이 함께 쓴다. 수집기의 대기(독립 55초, 호환 60초)보다 짧아야 한다
TIMEOUT_S = 15  # Orca 와 동시에 돈다. core 의 기기당 시간 제한(70초) 안에 들어와야 한다
SWITCH_TIMEOUT_S = 30  # switch 는 현재 로그인을 백업하고 macOS 는 Keychain 도 고친다. core 는 40초를 기다린다
# usageStatus → (화면 문구, 사람이 손봐야 하는가). ok·api_key·unavailable 은 따로 다룬다
STATUS = {
    "token_expired": ("토큰 갱신을 기다리는 중입니다(hswap 이 다시 시도합니다).", False),
    # hswap 은 죽은 갱신 토큰을 슬롯 백업 기준으로 격리한다. Claude Code 에서 다시 로그인만 하면 격리가 남는다
    "relogin_required": ("다시 로그인해야 합니다: 그 계정으로 Claude Code 에서 /login 한 뒤 hswap claude add 를 실행하세요.", True),
    "keychain_unavailable": ("Keychain 을 읽지 못했습니다.", True),
    "keychain_item_present": ("키체인에 Claude 로그인이 있거나 확인하지 못해 자동 갱신을 멈췄습니다.", True),
    "config_conflict": ("hswap 설정 충돌(파일 저장소와 키체인 필수 모드).", True),
    "foreign_credential": ("지금 로그인이 다른 계정입니다. hswap claude switch 로 맞추세요.", True),
    "no_credentials": ("자격 증명이 없습니다.", True),
    "no_saved_usage": ("저장된 사용량이 없습니다.", False),  # 저장소 캐시 어댑터만 쓴다
}
_lock = threading.Lock()
_cache = None  # (monotonic, 결과, 물을 때의 저장소 모습 _store_shape). 잠금 안에서 한 값으로 바꾼다. 모습이 지금 저장소와 같을 때만 다시 쓴다
_discovered = False  # hswap 이 마지막으로 답한 때 계정을 주었다. 저장소를 읽지 못해도 기준이 지나면 다시 묻는 근거다
_device = None  # (monotonic, 키체인에 닿지 못함). hswap 이 답한 기기 상태. 계정 행과 따로 두어 계정이 없거나 다음 조회가 실패해도 남는다
_fatal = None  # (monotonic, type). hswap 이 최상위 오류로 답한 FATAL_ERRORS. hswap 이 다시 답하면 지운다
_unconfirmed = None  # 파일 저장소에서 갱신을 확인하지 못한 기록을 처음 본 시각. 기록의 checkedAt 은 실패해도 새로워져 기간을 알 수 없다
_switched = 0  # switch() 가 끝난 횟수. 이것이 바뀌면 그 전에 시작한 읽기의 값은 전환 전 값일 수 있다
# 자동 설치·올리기: 검증한 버전으로 고정한다(서비스가 최신판을 저절로 받아 실행하지 않게). PyPI 배포본이 그 태그의 소스와 같은지
# 대조한 뒤 여기 버전을 올려 릴리스하면, 게이트웨이(maintain)가 이보다 낮은 hswap 을 이 버전으로 올린다
# ponytail: 버전만 고정한다. 같은 버전에 나중에 더해진 배포 파일은 대조 없이 받을 수 있다. 막으려면 파일 해시로 고정한다
VERSION = "0.2.1"  # 태그 v0.2.1 = 38be330
PACKAGE = f"harness-swap=={VERSION}"
INSTALL_TIMEOUT_S = 600  # 처음이면 uv 가 Python 3.12+ 까지 받는다
CHECK_S = 3600  # 버전 확인 주기. 설치에 실패하면 Installer 가 6시간 뒤에 다시 시도한다
_version = None  # 마지막으로 확인한 설치 버전("0.2.1"). 화면의 기기 행에 보인다


def binary():
    return which_local("hswap")


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
    """hswap 이 없거나 고정 버전보다 낮으면 뒤에서 고정 버전을 설치한다. Claude Code 가 없는 기기는 hswap 이 쓸모없어 설치하지 않는다.
    버전을 아직 모르거나 읽지 못했거나 더 높으면(사람이 먼저 올린 것) 그대로 둔다. None(할 일 없음) 또는 화면에 붙일 문구."""
    if not config.CSWAP_INSTALL or not which_local("claude"):
        return None
    if binary() and (_version is None or _key(_version) >= _key(VERSION)):
        return None
    return installer.ensure("Claude 계정별 사용량")


def check():
    """설치된 버전을 다시 읽고 ensure() 로 맞춘다."""
    global _version
    _version = installed_version()
    return ensure()


def maintain():
    """게이트웨이가 도는 동안 hswap 버전을 CHECK_S 마다 맞춘다. 독립 조회 경로는 설치하지 않으므로(USAGE-3) 여기서 한다."""
    while True:
        try:
            check()
        except Exception as e:
            print(f"[jarvis] hswap 버전 확인 실패: {scrub_home(str(e))[:200]}", flush=True)
        time.sleep(CHECK_S)


def status():
    """화면의 기기 행: 확인한 hswap 버전(없으면 None)과 고정 버전, 설치 중이거나 실패했으면 그 문구. 확인 전이면 None."""
    if _version is None and installer.state is None:
        return None
    behind = _version is None or _key(_version) < _key(VERSION)  # 맞은 뒤에는 지난 실패 문구를 보이지 않는다
    note = {"running": "설치하는 중", "failed": installer.error}.get(installer.state) if behind else None
    return {"version": _version, "pinned": VERSION, "note": note}


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


def account(row, source="cswap"):
    """hswap claude 계정 한 줄(저장소 어댑터도 같은 모양으로 넘긴다) → 화면 계정. 조회가 실패해도 마지막으로 읽은 값(lastGoodUsage)과 그 이유를 함께 준다.
    attention: 사람이 손봐야 다시 읽히는 상태(재로그인 등). 화면이 '주의 필요'로 센다."""
    status = row.get("usageStatus")
    usage, fetched = row.get("usage"), row.get("usageFetchedAt")
    if status != "ok" and isinstance(row.get("lastGoodUsage"), dict):
        usage, fetched = row["lastGoodUsage"], row.get("lastGoodFetchedAt")
    raw_reason = row.get("usageError")
    reason = raw_reason if isinstance(raw_reason, str) and re.fullmatch(r"http-[1-5][0-9]{2}", raw_reason) else ""
    note, attention = STATUS.get(status, (None, False))
    if status == "api_key":
        note = "API 키 계정이라 구독 한도가 없습니다."
    elif status != "ok" and not note:
        note = "사용량을 읽지 못했습니다" + (f"({reason})" if reason else "") + "."
    left = (parse_iso(row.get("loginExpiresAt") or "") or 0) - time.time()
    if status not in ("relogin_required", "api_key") and 0 < left <= LOGIN_WARN_S:
        soon = f"로그인 만료 {_span(left)} 남음. 그 계정으로 다시 /login 하세요."
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
            "windows": windows, "buckets": buckets[:12], "extra": None, "credits": None, "source": source}


class _HswapError(RuntimeError):
    """hswap 이 최상위 오류로 답했다. 문구는 고정 문구이고 kind 는 응답의 값 그대로라 비교에만 쓴다."""

    def __init__(self, kind):
        known = HSWAP_ERRORS.get(kind) if isinstance(kind, str) else None
        if not known:  # 종류 원문은 화면에 내지 않는다. 진단용으로 거른 값만 게이트웨이 로그에 남긴다
            print(f"[jarvis] hswap 의 모르는 오류 종류: {re.sub(r'[^a-z_]', '', kind if isinstance(kind, str) else '')[:40]}", flush=True)
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
    global _cache, _switched
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
        _cache = None  # 실패해도 다음 읽기는 hswap 에 다시 묻는다
        _switched += 1  # 묶음을 비운 뒤에 는다: 새 번호를 본 읽기는 옛 묶음을 볼 수 없다
        _lock.release()


def live(force=False, install=True, shape=None, deadline=None):
    """hswap CLI 어댑터. None(hswap 없음·계정 없음) 또는 {"accounts": [...], "fetched_at": 초, "ids": {슬롯: 신원 지문}} 또는 {"error": "..."}. ids 는 read() 안에서만 쓴다.
    install 이면 hswap 이 없을 때 ensure() 가 설치를 시작하고 그 진행·실패를 error 로 준다(호환 경로만. 독립 경로는 설치하지 않는다).
    hswap 이 답하면 최상위 keychainUnavailable(0.2.2 의 추가 필드. 없으면 아니라고 본다)을 _device 에 적는다. 실패한 조회는 건드리지 않는다.
    force 는 jarvis 의 30초 묶음만 건너뛴다(프로바이더 조회 간격은 hswap 이 정한다).
    shape: 이 호출을 정한 저장소 모습. 잠금을 기다린 뒤든 포기한 뒤든 모습이 같은 결과만 다시 쓴다(hswap 은 한 번에 하나만 도니 옛 답은 새 답보다 먼저 온다).
    deadline: 잠금 대기와 hswap 호출이 함께 쓰는 시한(monotonic). 넘기면 hswap 을 부르지 않고 오류를 준다."""
    global _cache, _device, _discovered, _fatal
    exe = binary()
    if not exe:
        msg = ensure() if install else None
        return {"error": msg} if msg else None
    asked = time.monotonic()
    # 동시에 여러 hswap 을 띄우지 않는다. 앞 요청이 끝나지 않으면(멈춘 hswap) 기다리지 않고 마지막 값을 준다
    left = lambda: float("inf") if deadline is None else deadline - time.monotonic()
    if not _lock.acquire(timeout=max(0, min(TIMEOUT_S + 5, left()))):
        held = _cache
        return held[1] if held and held[2] == shape else {"error": "hswap 이 응답하지 않습니다"}
    try:
        held = _cache
        # 기다리는 동안 앞 요청이 같은 저장소를 보고 새로 읽었으면 강제라도 그 값을 쓴다
        if held and held[2] == shape and (held[0] >= asked or (not force and asked - held[0] < CACHE_S)):
            return held[1]
        if left() <= 0:
            return {"error": "hswap 을 기다리다 읽기 시한을 넘겼습니다"}
        try:
            body = _json(exe, "list", timeout=min(TIMEOUT_S, left()))
            raw = [r for r in body.get("accounts") or [] if isinstance(r, dict)]
            accounts = [account(r) for r in raw]
            _device = (time.monotonic(), body.get("keychainUnavailable") is True)
            _discovered = bool(accounts)  # 실패한 조회는 건드리지 않는다: 일시 실패로 계정을 잊지 않게
            _fatal = None
            out = {"accounts": accounts, "fetched_at": time.time(),
                   "ids": {str(r.get("number")): _fingerprint(r.get("email"), r.get("organizationUuid")) for r in raw}} if accounts else None
        except Exception as e:
            if isinstance(e, _HswapError):  # 시간 초과처럼 답이 없는 실패는 이전 판정을 지우지 않는다
                _fatal = (time.monotonic(), e.kind) if e.kind in FATAL_ERRORS else None
            out = {"error": scrub_home(str(e))[:200] or type(e).__name__}
        _cache = (time.monotonic(), out, shape)
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
        result, ids = [], {}
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
            # 화면은 이메일+조직 이름으로 같은 계정을 묶는다. Orca 경로와 같은 규칙으로 개인 조직("<email>'s Organization")은 None
            result.append({**a, "org": org(a["org"], a["label"])})
        # signed_in: 로그인을 읽었다. 그런데 active 인 행이 없으면 지금 로그인은 어느 저장 슬롯과도 맞지 않는다
        return {"accounts": result, "signed_in": identity is not None, "ids": ids} if result else None
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return None


def _keepalive():
    """hswap claude keepalive 가 남긴 저장소의 cache/keepalive.json(토큰 없음). 없거나 안전하지 않거나 모르는 형식이면 None."""
    try:
        body = opencode_usage._private_json(os.path.join(_root(), "cache", "keepalive.json"))
    except (OSError, ValueError, RecursionError):
        return None
    return body if isinstance(body, dict) and body.get("schemaVersion") == 1 else None


def _span(s):
    s = max(0, s)
    return f"{s / 86400:.0f}일" if s >= 86400 else f"{s // 3600:.0f}시간" if s >= 3600 else f"{max(1, s // 60):.0f}분"


def _is_relogin(a):
    return STATUS["relogin_required"][0] in (a.get("note"), a.get("error"))


def login(rows=(), keychain=False, signed_in=False, fatal=None):
    """기기 단위 로그인 상태 {state, text, note, attention, slot, other, store}. 알릴 것이 없으면 None(keepalive.json 이 없으면 이 기능을 쓰지 않는 기기다).
    순서: 설정 충돌·키체인 항목(있거나 확인 실패) > 사람이 다시 로그인해야 하는 것 > 키체인 > 만료 임박 > 멈춤 > 이전 계정의 기록 > 그 밖의 실패 > 정상.
    store: 파일 저장소 기기면 "file", 아니면 None(옛 형식 포함). 파일의 다른 값은 내보내지 않는다. 키체인을 쓰지 않는 기기라 키체인에 닿지 못하는 것은 문제가 아니다.
    rows: 사용 중 계정의 재로그인 판정(저장소의 authDeadStrikes 와 hswap 의 relogin_required 가 같은 문구가 된다)과 사용 중 슬롯.
    keychain: hswap 의 keychainUnavailable. signed_in: 로그인을 읽었다(rows 에 active 가 없으면 저장되지 않은 로그인이다).
    fatal: hswap 이 최상위 오류로 답한 FATAL_ERRORS. keepalive 기록보다 새로운 사실이라 기록과 상관없이 쓴다.
    unconfirmed: 파일 저장소인데 keychain_unavailable 로 실패한 기록. 키체인 경고는 아니지만 갱신이 확인되지 않았으므로 성공 문구를 쓰지 않고, CLAUDE_KEEPALIVE_STALE_S 동안 계속되면 attention 으로 올린다.
    other: keepalive 기록의 activeSlot 이 지금 로그인의 슬롯과 다르다고 확인됐다. 만료·오류·접근 토큰은 그 계정의 사실이라 쓰지 않고, 키체인·멈춤·자격 부재는 기기의 사실이라 그대로 쓴다.
    activeSlot 이 없으면 슬롯을 모르는 것이지 다른 계정이라는 증거가 아니다: 판정은 그대로 두고 slot 만 뺀다.
    슬롯은 맞다고 확인될 때만 준다(다른 계정의 슬롯에 add 하면 저장 로그인을 덮을 수 있다). 파일이 없으면 기록과 비교할 것이 없어 사용 중 슬롯을 준다."""
    global _unconfirmed
    k = _keepalive() or {}
    err = k.get("error") if isinstance(k.get("error"), dict) else {}
    kind = err.get("type") if isinstance(err.get("type"), str) else None
    now, checked, expires = time.time(), _sec(k.get("checkedAt")), _sec(k.get("loginExpiresAt"))
    current = next((a.get("slot") for a in rows if a.get("active") and a.get("slot")), None)
    recorded = k.get("activeSlot")
    recorded = str(recorded) if isinstance(recorded, int) and not isinstance(recorded, bool) and recorded > 0 else None
    # 사용 중 슬롯을 모르고 저장되지 않은 로그인도 아니면 맞는지 알 수 없다: 경고는 남기고 슬롯만 권하지 않는다
    other = recorded is not None and bool(current or signed_in) and current != recorded
    file_store = k.get("store") == "file"
    blocked = next((x for x in (fatal, kind) if x in ("config_conflict", "keychain_item_present")), "keychain_item_present" if file_store and k.get("keychainItemPresent") is True else None)
    # shortcut: 처음 본 시각은 메모리에만 있어 게이트웨이를 재시작하면 처음부터 센다. hswap 이 마지막 성공 시각을 적으면 그것으로 바꾼다
    _unconfirmed = (_unconfirmed or now) if file_store and kind == "keychain_unavailable" and k.get("ok") is not True else None
    quiet = False
    if blocked:  # 기기의 사실이다. 항목이 남아 있으면 SSH 의 /login 이 저장되지 않을 수 있어 재로그인보다 먼저 보인다
        state, text = blocked, KEEPALIVE_ERRORS[blocked]
    elif fatal == "relogin_required" or (kind == "relogin_required" and not other) or any(a.get("active") and _is_relogin(a) for a in rows):
        state, text = "relogin", "재로그인 필요"
    elif not file_store and (kind == "keychain_unavailable" or keychain):
        state, text = "keychain", "키체인 접근 불가: 키체인에 접근할 수 없는 곳에서 실행됨"
    elif not k:
        return None
    elif not other and expires is not None and expires - now <= LOGIN_WARN_S:
        state, text = "expiring", f"로그인 만료 임박({_span(expires - now)})" if expires > now else "로그인 기한이 지났습니다"
    elif checked is None or now - checked > config.CLAUDE_KEEPALIVE_STALE_S:
        state, text = "stalled", "자동 갱신 멈춤" + (f"(마지막 {_span(now - checked)} 전)" if checked else "")
    elif other and kind != "no_credentials":  # 자격 부재는 add 로 고칠 수 없어 /login 안내로 이어지는 실패로 둔다
        state, text = "other_account", "자동 갱신 기록이 이전 계정의 것입니다"
    elif _unconfirmed:
        quiet = now - _unconfirmed <= config.CLAUDE_KEEPALIVE_STALE_S
        state, text = "unconfirmed", "자동 갱신 확인되지 않음" + ("" if quiet else f"({_span(now - _unconfirmed)}째)")
    elif k.get("ok") is not True:
        state, text = "failed", f"자동 갱신 실패({KEEPALIVE_ERRORS.get(kind, '알 수 없는 오류')})"
    else:
        state, text = "ok", f"로그인됨{' · 파일 저장소' if file_store else ''}, 자동 갱신 {_span(now - checked)} 전"
    access = None if other or blocked or state == "unconfirmed" else _sec(k.get("accessExpiresAt"))  # 갱신이 멈췄거나 확인되지 않았는데 "자동 갱신"을 말하지 않는다
    return {"state": state, "text": text, "attention": state != "ok" and not quiet, "slot": current if not k or current == recorded else None, "other": other,
            "store": "file" if file_store else None,
            "note": None if access is None else f"접근 토큰 {_span(access - now)} 뒤 만료(자동 갱신)" if access > now else "접근 토큰 만료됨(자동 갱신 대기)"}


def _old(a):
    """값이 있는데 신선도 기준보다 오래됐는가."""
    at = a.get("updated_at")
    return bool(a.get("windows") or a.get("buckets")) and (not at or time.time() - at > config.CLAUDE_STALE_S)


def _same_login(a, ids, store):
    """CLI 행과 저장소의 같은 슬롯이 같은 계정인가. 슬롯 번호만 같으면 다른 계정의 상태를 옮기게 된다."""
    fingerprint = ids.get(a.get("slot"))
    return fingerprint is not None and fingerprint == store.get("ids", {}).get(a.get("slot"))


def _store_shape(store):
    """묶음이 믿을 수 있는지 가르는 값: 슬롯→신원 지문 전체, 사용 중 슬롯, 인증 실패인 슬롯, keepalive 등급(ok·실패 종류)과 loginExpiresAt.
    /login 뒤의 새 기록이 묶어 둔 CLI 의 재로그인·만료 경고를 무효로 한다. checkedAt 같은 시각만 바뀌면 달라지지 않는다. 묶음 안에만 두고 내보내지 않는다."""
    rows = (store or {}).get("accounts", [])
    k = _keepalive() or {}
    err = k.get("error") if isinstance(k.get("error"), dict) else {}
    grade = None if not k else "ok" if k.get("ok") is True else err.get("type") if isinstance(err.get("type"), str) else "failed"
    return ((store or {}).get("ids", {}), {a.get("slot") for a in rows if a.get("active")}, {a.get("slot") for a in rows if _is_relogin(a)},
            grade, k.get("loginExpiresAt"))


def _mark_current(rows, store, ids):
    """사용 중 판정: 실제 로그인과 같은 저장 슬롯이고 신원도 같은 행(엔진과 같은 규칙. 맞는 슬롯이 없으면 사용 중도 없다). 로그인이나 저장소를 읽지 못하면 hswap 의 값."""
    if not (store and store.get("signed_in")):
        return rows
    current = {a.get("slot") for a in store["accounts"] if a.get("active")}
    return [{**a, "active": a.get("slot") in current and _same_login(a, ids, store)} for a in rows]


def _mark_dead(rows, store, ids):
    """저장소의 인증 실패(authDeadStrikes)를 슬롯과 신원이 같은 CLI 행에 붙인다. 묶어 둔 행이 그 뒤에 적힌 실패를 가리지 않게 읽을 때마다 본다.
    사용량 값은 건드리지 않고, 묶음의 dict 도 고치지 않는다."""
    dead = {a.get("slot") for a in (store or {}).get("accounts", []) if _is_relogin(a)}
    relogin = STATUS["relogin_required"][0]

    def mark(a):
        if a.get("slot") not in dead or _is_relogin(a) or not _same_login(a, ids, store):
            return a
        has = a.get("windows") or a.get("buckets")
        return {**a, "attention": True, **({"note": relogin, "error": None} if has else {"status": "error", "note": None, "error": relogin})}
    return [mark(a) for a in rows]


def _aged(a, why=None):
    """값이 있는 계정에 대체 이유와 신선도를 붙인다. hswap 묶음이 들고 있는 dict 는 고치지 않는다."""
    if not (a.get("windows") or a.get("buckets")):
        return a
    old = _old(a)
    at = a.get("updated_at")
    if not (old or why):
        return a
    age = (f"{_span(time.time() - at)} 전 값입니다." if at else "저장 시각을 모르는 값입니다.") if old else None
    return {**a, "note": " ".join(x for x in (a.get("note"), why, age) if x), "attention": a.get("attention") or old}


def read(force=False, install=False):
    """두 사용량 경로의 공용 읽기. None(저장소도 실값도 없고 알릴 것도 없음) 또는 {"error": "..."} 또는 {"accounts": [...], "login": {...}}.
    login 은 알릴 것이 있을 때만이며 계정 목록과 상관없다: 계정이 없어도 {"accounts": [], "login": {...}}(hswap 오류가 있으면 error 도)를 준다.
    force 이거나, hswap 에 물은 지 신선도 기준이 지났거나, 보일 실값 중 기준보다 오래된 것이 있으면 hswap list 를 실행한다
    (저장소에는 로그인 기한·상태가 없어 만료 경고는 실값에서만 나온다. 예약 실행이 저장소를 늘 새로 써도 경고가 사라지지 않게 한다).
    그 사이에는 마지막 실값을 그대로 준다. 오래된 저장소 값은 실값이 없을 때만 보이므로 따로 보지 않는다. hswap 이 없거나 실패하면 저장소 값을 대체값으로 쓰고 이유를 붙인다.
    install: 호환 경로만 켠다. 저장소가 없어도 묻고, hswap 이 없으면 설치를 시작한다. 독립 경로는 설치하지 않는다(USAGE-3).
    읽는 사이 switch() 가 끝나거나 저장소 모습이 바뀌면 옛 값을 내지 않고 다시 읽는다(진행 중인 이 읽기를 함께 기다리는 쪽도 옛 값을 받지 않는다).
    재시도는 2번까지이고 전체 시한은 READ_BUDGET_S 다. 그 안에 확정하지 못하면 CLI 값 대신 지금 저장소 값과 로그인 상태를 준다."""
    deadline = time.monotonic() + READ_BUDGET_S
    for _ in range(3):
        seen = _switched
        out, settled = _read(force, install, deadline)
        if settled and seen == _switched:
            return out
        if time.monotonic() >= deadline:
            break
        force = False  # 강제는 첫 시도로 지켰다. 다시 읽을 때는 같은 모습의 새 결과를 쓴다
    return _read(False, False, deadline, give_up=True)[0]


def _keychain_down():
    """hswap 이 신선도 기준 안에 키체인에 닿지 못한다고 답했는가. 다시 확인하지 못한 옛 답은 쓰지 않는다."""
    d = _device
    return bool(d and d[1] and time.monotonic() - d[0] < config.CLAUDE_STALE_S)


def _fatal_kind():
    """hswap 이 신선도 기준 안에 답한 치명적 오류 type. 다시 확인하지 못한 옛 답은 쓰지 않는다."""
    f = _fatal
    return f[1] if f and time.monotonic() - f[0] < config.CLAUDE_STALE_S else None


def _read(force, install, deadline, give_up=False):
    """(결과, 답을 받는 동안 저장소 모습이 그대로였는가). give_up: hswap 을 부르지 않고 지금 저장소로 답한다."""
    settled = True
    store = _store_read()
    held = _cache  # switch() 가 도중에 비워도 같은 값을 본다
    recent = held[1] if held and time.monotonic() - held[0] < config.CLAUDE_STALE_S else None
    recent = recent if recent and recent.get("accounts") else None
    device = _device
    # 계정을 찾은 적도 저장소도 없는 기기는 묻지 않는다. CLI 로 계정을 찾았거나 경고가 있던 기기는 기준이 지나면 다시 묻는다
    recheck = _discovered or bool(_fatal) or (bool(device and device[1]) and not _keychain_down())
    if give_up:
        out = {"error": "저장소가 계속 바뀌어 hswap 값을 확정하지 못했습니다"}
    elif store is None and recent is None and not force and not install and not recheck:
        out = None
    else:
        shape = _store_shape(store)
        # 묶음의 모습은 hswap 답이 아니라 물을 때의 저장소다: 답이 저장소와 계속 어긋나도 변경 한 번에 한 번만 다시 부른다
        changed = recent is not None and shape != held[2]
        asked = force or changed or recent is None or any(_old(a) for a in recent["accounts"])
        out = recent
        if asked:
            out = live(force, install=install, shape=shape, deadline=deadline)
            store = _store_read()  # 사용 중 판정은 hswap 이 답한 뒤의 로그인으로 한다(잠금을 기다리는 사이 로그인이 바뀌었을 수 있다)
            settled = _store_shape(store) == shape
    rows = (out or {}).get("accounts")
    if rows:
        ids = out.get("ids", {})
        rows = _mark_dead([{**a, "org": org(a.get("org"), a.get("label"))} for a in _mark_current(rows, store, ids)], store, ids)
        why = None
    elif store:
        rows = store["accounts"]
        reason = (out or {}).get("error") or ("hswap 이 없습니다" if not binary() else "hswap 이 계정을 주지 않았습니다")
        why = reason.rstrip(".") + ". 저장된 값을 보입니다."
    else:
        state = login((), _keychain_down(), fatal=_fatal_kind())
        return ({**(out or {}), "accounts": [], "login": state} if state else out), settled
    state = login(rows, _keychain_down(), bool(store and store.get("signed_in")), _fatal_kind())
    result = {"accounts": [_aged(a, why) for a in rows]}
    if (out or {}).get("fetched_at"):
        result["fetched_at"] = out["fetched_at"]
    return ({**result, "login": state} if state else result), settled


def merge_orca(mine, orca_rows):
    """두 경로의 공용 병합: read() 의 계정 + Orca 에만 있는 관리 계정(같은 이메일의 다른 조직 포함)."""
    email = lambda a: _identity(a["label"])[0]
    seen = {(email(a), a.get("org")) for a in mine}
    emails = {email(a) for a in mine}
    # Orca 의 '시스템 기본 계정'은 hswap 의 사용 중 계정과 같은 로그인이다(hswap 에 사용 중 계정이 있을 때만 뺀다).
    # 조직을 모르는 Orca 줄(시스템 기본 계정이 조직 이름을 못 빌린 경우)은 같은 이메일이면 같은 로그인으로 본다
    extra = [a for a in orca_rows if (email(a), a.get("org")) not in seen
             and not (a.get("org") is None and email(a) in emails)
             and not (a["label"] == "시스템 기본 계정" and any(m.get("active") for m in mine))]
    return mine + extra


def alias(name, names):
    """옛 모듈 name 을 이 모듈로 넘기는 이름표로 바꾼다. 읽기뿐 아니라 쓰기(테스트의 패치 포함)도 그대로 이 모듈에 닿는다. names: 옛 이름 → 지금 이름."""
    target = sys.modules[__name__]

    class Alias(types.ModuleType):
        def __getattr__(self, k):
            return getattr(target, names.get(k, k))

        def __setattr__(self, k, v):
            setattr(target, names.get(k, k), v)

        def __delattr__(self, k):
            delattr(target, names.get(k, k))
    sys.modules[name].__class__ = Alias
