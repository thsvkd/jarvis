"""hswap(Claude 엔진 claude-swap)과 기존 cswap 이 함께 쓰는 저장소의 계정과 마지막 사용량만 읽는다. 명령 실행과 토큰 갱신은 하지 않는다.

https://github.com/realiti4/claude-swap/blob/v0.28.0/src/claude_swap/usage_store.py
https://github.com/realiti4/claude-swap/blob/v0.28.0/src/claude_swap/switcher.py
사용 중 계정: https://github.com/thsvkd/harness-swap/blob/9a1e5d1/src/claude_swap/switcher.py (current_account_number)·paths.py
"""
import json
import math
import os
import stat
import sys

from . import cswap_usage, opencode_usage, orca_usage, provider_usage

CONFIG_MAX = 32 << 20  # Claude Code 전역 설정은 프로젝트 기록이 쌓여 수 MB 까지 큰다


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


def read():
    root = _root()
    try:
        roster = opencode_usage._private_json(os.path.join(root, "sequence.json"))
        try:
            cache = opencode_usage._private_json(os.path.join(root, "cache", "usage.json"))
        except (OSError, ValueError):
            cache = {}
        accounts = roster.get("accounts", {}) if isinstance(roster, dict) else {}
        sequence = roster.get("sequence", []) if isinstance(roster, dict) else []
        live = _live_identity()
        cached = cache.get("accounts", {}) if isinstance(cache, dict) and cache.get("schemaVersion") == 2 else {}
        if not isinstance(accounts, dict) or not isinstance(sequence, list) or not isinstance(cached, dict):
            return None
        result = []
        for number in sequence:
            if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
                continue
            ident = accounts.get(str(number))
            row = cached.get(str(number))
            if not isinstance(ident, dict):
                continue
            valid = (isinstance(row, dict) and row.get("email") == ident.get("email")
                     and row.get("organizationUuid", "") == ident.get("organizationUuid", ""))
            usage = row.get("lastGood") if valid else None
            usage = usage if isinstance(usage, dict) else {}
            windows = []
            for field, key in (("five_hour", "session"), ("seven_day", "weekly")):
                w = usage.get(field)
                used = cswap_usage._pct(w.get("pct")) if isinstance(w, dict) else None
                if used is not None:
                    windows.append({"key": key, "used": used, "resets_at": cswap_usage._sec(w.get("resets_at"))})
            buckets = []
            scoped = usage.get("scoped") if isinstance(usage.get("scoped"), list) else []
            for item in scoped[:12]:
                used = cswap_usage._pct(item.get("pct")) if isinstance(item, dict) else None
                if used is None:
                    continue
                reset = cswap_usage._sec(item.get("resets_at"))
                if item.get("name") == "Fable":
                    windows.append({"key": "fable", "used": used, "resets_at": reset})
                else:
                    buckets.append({"name": f"모델별 한도 {len(buckets) + 1}", "used": used, "resets_at": reset})
            fetched = row.get("fetchedAt") if valid else None
            if isinstance(fetched, bool) or not isinstance(fetched, (float, int)) or not math.isfinite(fetched):
                fetched = None
            email = provider_usage._email(ident)
            has_usage = bool(windows or buckets)
            # 화면은 이메일+조직 이름으로 같은 계정을 묶는다. Orca 경로와 같은 규칙으로 개인 조직("<email>'s Organization")은 None.
            # 조직 UUID 는 내보내지 않는다
            org = ident.get("organizationName")
            org = orca_usage.org(org[:80], email) if isinstance(org, str) and org else None
            # slot: '이 계정 사용' 버튼이 cswap switch 에 넘기는 슬롯 번호(기기마다 다르다)
            # 사용 중: 실제 로그인과 이메일·조직 UUID 가 같은 슬롯(엔진의 _find_account_slot 과 같은 비교)
            active = live is not None and (ident.get("email"), ident.get("organizationUuid", "")) == live
            result.append({"label": email, "org": org, "active": active, "slot": str(number),
                           "status": "ok" if has_usage else "error",
                           "error": None if has_usage else "저장된 사용량이 없습니다.", "note": None, "attention": False,
                           "failure": None, "plan": None, "updated_at": fetched, "windows": windows, "buckets": buckets,
                           "extra": None, "credits": None, "source": "cswap-cache"})
        return {"accounts": result} if result else None
    except (OSError, ValueError, TypeError):
        return None
