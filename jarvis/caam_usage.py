"""이 기기 caam(coding_agent_account_manager)을 부르는 공용 도우미. 사용량 해석은 provider_usage 가 한다.

`caam ls <프로바이더> --json`(프로필 → 이메일·상태)과 `caam limits <프로바이더> --format json`(한도)만 부른다.
limits 는 caam 금고(vault)의 접근 토큰으로 사용량 API 를 읽기만 하고 토큰을 갱신하지 않는다(v0.1.24 cmd/caam/cmd/limits.go).
caam refresh·daemon·activate 는 부르지 않는다: Codex 갱신 토큰은 쓸 때마다 바뀌어 같은 로그인을 두 곳이 갱신하면 계정이 막힐 수 있다.
jarvis 는 caam 을 설치하지 않는다.

공식 계약: https://github.com/Dicklesworthstone/coding_agent_account_manager (internal/usage/usage.go 의 UsageInfo)
라이선스(MIT + OpenAI/Anthropic 추가 조항)는 사용자가 확인하고 도입을 정했다(2026-10-28).
"""
import json
import os
import subprocess
import threading

from .util import which_local

LS_TIMEOUT_S = 10
TIMEOUT_S = 35  # limits: caam 의 프로필당 HTTP 제한(30초)보다 길게. 한 프로필이 느려도 나머지를 잃지 않게
BUDGET_S = 50  # 잠금 대기 + ls + limits. 독립 사용량 수집이 이 안에서 기다린다
_lock = threading.Lock()


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
