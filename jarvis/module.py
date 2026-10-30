"""모듈 공통 계약(docs/SPEC.md 의 모듈 계약): status·plan·apply·uninstall.

    python -m jarvis module <이름> <status|plan|apply|uninstall> --json [--dry-run] [--plan-id <sha256>]

apply 와 (dry-run 이 아닌) uninstall 은 미리 본 계획의 plan_id 를 받아, 지금 다시 세운 계획과 같을 때만 실행한다.
표준 출력에는 JSON 한 개만 낸다. 모듈과 그 자식 프로세스가 내는 출력은 진행 로그라 표준 오류로 돌린다.
"""

import contextlib
import hashlib
import importlib
import json
import os
import sys

MODULES = {"orca": "jarvis.orca_module"}
EXIT = {"done": 0, "current": 0, "failed": 1, "deferred": 2, "stale": 3}


def plan_id(plan):
    """plan_id 를 뺀 계획의 정규화 JSON 의 sha256. 승인은 이 값에 대해 받는다."""
    body = {k: v for k, v in plan.items() if k != "plan_id"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def seal(plan):
    return {**plan, "plan_id": plan_id(plan)}


def cli(argv):
    if len(argv) < 2 or argv[0] not in MODULES or argv[1] not in ("status", "plan", "apply", "uninstall"):
        print(f"사용법: python -m jarvis module <{'|'.join(MODULES)}> <status|plan|apply|uninstall> --json "
              "[--dry-run] [--plan-id <sha256>]", file=sys.stderr)
        return 1
    name, action, rest = argv[0], argv[1], argv[2:]
    plan_arg = rest[rest.index("--plan-id") + 1] if "--plan-id" in rest[:-1] else None
    dry_run = "--dry-run" in rest
    if action == "apply" and (not plan_arg or dry_run):
        print("apply 에는 --plan-id 가 필요하고 --dry-run 은 받지 않습니다(미리 보기는 plan 이다)", file=sys.stderr)
        return 1
    if action == "uninstall" and not dry_run and not plan_arg:
        print("uninstall 에는 --dry-run 출력의 plan_id 를 --plan-id 로 넘겨야 합니다", file=sys.stderr)
        return 1
    sys.stdout.flush()
    saved = os.dup(1)
    try:
        os.dup2(2, 1)  # 자식 프로세스(apt·설치기)는 fd 1 에 바로 쓴다: fd 자체를 표준 오류로 돌린다
        with contextlib.redirect_stdout(sys.stderr):  # 모듈의 print 도 진행 로그다
            mod = importlib.import_module(MODULES[name])
            if action == "status":
                out, code = mod.status(), 0
            elif action == "plan":
                out, code = mod.plan(), 0
            elif action == "apply":
                out = mod.apply(plan_arg)
                code = EXIT[out["status"]]
            else:
                out = mod.uninstall(dry_run, plan_arg)
                code = EXIT[out["status"]]
    except Exception as e:
        out, code = {"module": name, "error": f"{type(e).__name__}: {e}"[:400]}, 1
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)
    if hasattr(sys.stdout, "reconfigure"):  # 계약은 UTF-8 JSON 이다(Windows 콘솔의 기본값은 cp949 등이다)
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return code
