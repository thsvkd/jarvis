#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_and_diff.py — 실제 settings.json 조각을 기존 settings.json에 외과적으로 병합

입력 조각(res/settings.json)은 진짜 Claude Code settings.json 형식이며(SSoT), 훅 command에
{{PY}} / {{HOOKS_DIR}} 자리표시자를 담는다. 이 스크립트가:
  1) 자리표시자를 치환하고 (Windows/POSIX 설치 스크립트 양쪽이 같은 이 로직을 씀),
  2) permissions(allow/deny/ask/defaultMode)와 hooks.* 만 기존 settings.json에 병합하며,
  3) 그 외 최상위 키(model, env, statusLine, 다른 훅 등)는 100% 그대로 보존한다.
예상치 못하게 바뀐 최상위 키가 감지되면 비정상 종료(exit 1)한다.

allow/deny/ask 는 합집합(중복 없이 뒤에 추가), defaultMode 는 덮어쓰기, hooks.* 는
(matcher, 훅 스크립트 파일명 집합) 시그니처로 중복 판정해 없는 것만 추가한다 → 재실행 멱등.

병합 시 기존 settings.json 이 있고 변경이 있으면 <path>.bak.<timestamp> 로 백업한다.
설치 시 manifest_path 를 주면 "우리가 실제로 추가한 allow/deny/ask 항목 + 이전 defaultMode" 를
설치 영수증(매니페스트)으로 기록한다. --uninstall 모드는 그 영수증을 읽어, 설치가 건드린
부분만 정밀하게 되돌린다 (훅 등록 해제 + 우리가 추가한 권한 항목만 제거 + defaultMode 원복).

--default-mode 를 주면 조각의 permissions.defaultMode 를 그 값으로 바꿔 병합한다. 온보딩이
사용자에게 고르게 한 값(auto / bypassPermissions)을 전달하는 통로다. 조각(res/settings.json)
자체는 건드리지 않으므로 SSoT 는 그대로 유지된다.

--windows 는 Windows 설치기가 넘긴다. 없으면 조각의 WINDOWS_ONLY_ENV 키를 빼고 병합한다.

usage: merge_and_diff.py [--default-mode <mode>] [--windows] <fragment.json> <settings_path> <dry_run:0|1> <py_invoke> <hooks_dir> [manifest_path] [hook_files_csv]
       merge_and_diff.py --uninstall <settings_path> <dry_run:0|1> <manifest_path>
"""
import copy
import datetime
import json
import os
import re
import shutil
import sys

PY_RE = re.compile(r'[A-Za-z0-9_.\-]+\.py')

# 스칼라 최상위 설정 키(단일 값). allow/deny 처럼 '없을 때만 추가'하고, 기존 값이 있으면
# 사용자 선택을 그대로 보존한다. uninstall 은 '우리가 실제로 추가한' 키만 정밀 제거한다.
SCALAR_KEYS = ("model", "effortLevel", "language")

# Windows 설치기(--windows)에서만 병합하는 env 키. GRAFT_TEST_SYNC_RUN 은 Windows 에만 설치되는
# graft_sync_hidden.cjs 를 가리키므로, POSIX 에 넣으면 graft 가 없는 파일을 보고 동기화를 건너뛴다.
WINDOWS_ONLY_ENV = ("GRAFT_TEST_SYNC_RUN",)

# 온보딩이 --default-mode 로 넘길 수 있는 값. Claude Code 가 실제로 아는 모드만 받는다 —
# 오타가 그대로 settings.json 에 박히면 사용자는 모드가 왜 안 켜지는지 알 길이 없다.
VALID_DEFAULT_MODES = (
    "auto",
    "bypassPermissions",
    "default",
    "acceptEdits",
    "plan",
)


def load_manifest(path):
    """설치 영수증을 읽는다. 없으면 빈 dict.
    영수증은 하네스·도구·Orca 설치 기록도 담으므로, 읽지 못하면 덮어쓰지 않도록 아무것도 쓰기 전에
    오류로 멈춘다. BOM 은 PowerShell 쪽과 같게 허용한다."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        print(f"❌ Cannot parse the install receipt: {path}\n"
              "   Nothing was changed. Fix or move the file, then run again.", file=sys.stderr)
        raise SystemExit(1)
    # 목록·null 같은 JSON 은 키 섹션을 담을 수 없어 잃을 기록이 없다. 빈 영수증으로 본다.
    return data if isinstance(data, dict) else {}


def fold_receipt(previous: dict, current: dict) -> dict:
    """이전 설치 영수증 위에 이번 병합 결과를 누적한다.

    설치는 멱등이라 두 번째 실행의 merge() 는 "추가한 것이 없다"는 빈 영수증을 낸다.
    그걸 그대로 기록하면 첫 설치가 무엇을 바꿨는지에 대한 기억이 사라져, 이후 --uninstall
    이 권한/defaultMode/스칼라 키를 되돌리지 못한다. 그래서 덮어쓰지 않고 합친다.
    """
    current = dict(current)
    retired = current.pop("retired", None) or {}
    if not previous:
        return current

    folded = dict(current)
    for key in ("added_allow", "added_deny", "added_ask", "added_auto_allow"):
        # 이번에 걷어낸 항목은 더 이상 우리 것이 아니다. 남겨 두면 사용자가 나중에 같은 문자열을
        # 직접 넣었을 때 uninstall 이 그것을 지운다.
        gone = retired.get(key[len("added_"):]) or []
        merged = [x for x in previous.get(key) or [] if x not in gone]
        for item in current.get(key) or []:
            if item not in merged:
                merged.append(item)
        folded[key] = merged

    scalars = dict(previous.get("added_scalars") or {})
    scalars.update(current.get("added_scalars") or {})
    folded["added_scalars"] = scalars

    env = dict(previous.get("added_env") or {})
    env.update(current.get("added_env") or {})
    folded["added_env"] = env

    # 구버전 규칙 제거는 '첫 설치'에서만 일어난다 — 두 번째 실행은 이미 지워진 상태라 제거할
    # 것이 없어 빈 dict 를 낸다. 덮어쓰면 uninstall 이 무엇을 되돌려야 하는지 잊어버린다.
    # 영수증이 우리가 넣었다고 말하는 규칙은 예전 설치기가 지웠더라도 uninstall 이 되살리지 않는다.
    legacy = {k: [r for r in v if r not in (previous.get("added_" + k) or [])]
              for k, v in (previous.get("removed_legacy") or {}).items()}
    for key, rules in (current.get("removed_legacy") or {}).items():
        merged = legacy.setdefault(key, [])
        for rule in rules:
            if rule not in merged:
                merged.append(rule)
    folded["removed_legacy"] = legacy

    # defaultMode 는 '설치 전 원래 값'이 유일하게 중요하다. 한 번이라도 우리가 바꿨다면
    # 그 최초 값을 계속 들고 간다.
    if previous.get("default_mode_changed"):
        folded["default_mode_before"] = previous.get("default_mode_before")
        folded["default_mode_after"] = current.get("default_mode_after") or previous.get("default_mode_after")
        folded["default_mode_changed"] = True

    return folded


def backup_file(path):
    """덮어쓰기 전 타임스탬프 백업을 만든다. 생성한 백업 경로(없으면 None)를 반환."""
    if not os.path.exists(path):
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    backup = f"{path}.bak.{ts}"
    shutil.copy2(path, backup)
    return backup


def write_json(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def substitute(obj, mapping):
    """조각 안 모든 문자열 값에서 자리표시자를 치환한다 (파싱된 객체 기준이라 JSON 이스케이프는 자동)."""
    if isinstance(obj, str):
        for key, value in mapping.items():
            obj = obj.replace(key, value)
        return obj
    if isinstance(obj, list):
        return [substitute(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: substitute(v, mapping) for k, v in obj.items()}
    return obj


def script_names(entry):
    names = []
    for h in entry.get("hooks", []):
        names.extend(PY_RE.findall(h.get("command", "")))
    return tuple(sorted(names))


def hook_sig(entry):
    return (entry.get("matcher"), script_names(entry))


def merge_list(perms, key, incoming, changes):
    """incoming 을 perms[key] 에 합집합으로 추가하고, 실제로 새로 추가된 항목 목록을 반환."""
    if not incoming:
        return []
    before = list(perms.get(key, []))
    merged = list(before)
    for item in incoming:
        if item not in merged:
            merged.append(item)
    perms[key] = merged
    added = [x for x in merged if x not in before]
    if added:
        changes.append(f"permissions.{key}: +{added}")
    return added


# 예전 버전의 res/settings.json 이 배포했던 permissions 규칙. Claude Code 는 permissions 의 deny 를
# PreToolUse 훅보다 우선 적용하므로, 이게 남아 있으면 reset --hard 전에 스냅샷을 남기고 실행하는
# danger_guard 의 정책이 무력화된다. 영수증이 '우리가 넣었다'고 말하는 것은 retire_list 가
# 걷어낸다. 영수증에 없는 것은 사용자가 직접 쓴 같은 문자열일 수 있어 지우지 않고 알리기만 한다
# (영수증이 없던 2026-07-04 이전 설치본이 여기에 해당한다).
LEGACY_PERMISSION_RULES = {
    # 예전 조각은 git push 규칙도 deny/ask 로 배포했다. 훅이 push 를 판정하지 않는 지금 그 규칙은
    # 훅과 충돌하지 않으므로 경고하지 않는다(영수증에 있으면 retire_list 가 걷어낸다).
    "deny": [
        "Bash(git reset --hard *)",
        "PowerShell(git reset --hard *)",
    ],
}


def retire_list(perms: dict, key: str, frag_perms: dict, previous: dict, changes: list) -> list:
    """예전 설치가 더했고 지금 조각에서 빠진 규칙을 걷어낸다. 영수증에 있는 것만 지운다.

    반환: 영수증의 '우리가 더한 것'에서 뺄 규칙 전부. 이미 설정에 없는 것도 포함한다 — 남겨 두면
    사용자가 나중에 같은 문자열을 직접 넣었을 때 다음 설치가 그것을 우리 것으로 보고 지운다."""
    keep = set(frag_perms.get(key) or [])
    current = perms.get(key) or []
    stale = [r for r in previous.get("added_" + key) or [] if r not in keep]
    gone = [r for r in stale if r in current]
    if gone:
        perms[key] = [x for x in current if x not in gone]
        if not perms[key]:
            del perms[key]
        changes.append(f"permissions.{key}: -{gone} (no longer shipped)")
    return stale


def warn_legacy_rules(perms: dict, frag_perms: dict) -> None:
    """영수증에 없는 구버전 규칙이 남아 있으면 알린다. changes 에 넣지 않는다 — 넣으면 바뀐 것이
    없는데도 실행할 때마다 백업을 만든다."""
    for key, legacy_rules in LEGACY_PERMISSION_RULES.items():
        incoming = set(frag_perms.get(key) or [])
        left = [r for r in legacy_rules if r in (perms.get(key) or []) and r not in incoming]
        if left:
            print(f"  ⚠️ permissions.{key} still has {left}, which overrides the guard hook's git policy. "
                  "The install receipt does not show that this setup added it, so it was left alone; "
                  "remove it yourself if you did not write it.")


def restore_legacy_rules(perms: dict, receipt: dict) -> list:
    """uninstall: 설치가 제거했던 구버전 규칙을 되돌려 놓는다. '설치가 바꾼 것만 원복한다'는
    영수증 철학을 그대로 따른 것 — 이 설치가 없었다면 그 규칙들은 그대로 남아 있었을 것이다."""
    changes = []
    removed_map = receipt.get("removed_legacy") or {}
    for key, rules in removed_map.items():
        if not rules:
            continue
        current = list(perms.get(key, []))
        restored = [r for r in rules if r not in current]
        if not restored:
            continue
        perms[key] = current + restored
        changes.append(f"permissions.{key}: +{restored} (restoring a legacy rule that install had removed)")
    return changes


def merge_scalars(existing: dict, fragment: dict, changes: list) -> dict:
    """model/effortLevel/language 를 '없을 때만' 추가한다(기존 값 보존). 실제로 추가한 {키: 값} 을 반환."""
    added = {}
    for key in SCALAR_KEYS:
        if key not in fragment or key in existing:
            continue  # 조각에 없거나, 사용자가 이미 값을 갖고 있으면 건드리지 않는다.
        existing[key] = fragment[key]
        added[key] = fragment[key]
        changes.append(f"{key}: (none) -> {fragment[key]!r}")
    return added


def merge_env(existing: dict, fragment: dict, changes: list) -> dict:
    """env 하위의 '우리가 관리하는 키'(예: CLAUDE_CODE_SCROLL_SPEED)를 '없을 때만' 추가한다.
    scalar 와 동일한 철학을 env 한 단계 아래에 적용한다 — env 객체 자체와 사용자의 기존 env
    키/값은 절대 건드리지 않고, 조각에 있는 키 중 대상 env 에 없는 것만 채운다.
    실제로 추가한 {키: 값} 을 반환한다 (uninstall 정밀 복원의 근거)."""
    frag_env = fragment.get("env") or {}
    if not frag_env:
        return {}
    existing_env = existing.get("env")
    if not isinstance(existing_env, dict):
        existing_env = {}
    added = {}
    for key, value in frag_env.items():
        if key in existing_env:
            continue  # 사용자가 이미 값을 갖고 있으면 보존한다.
        existing_env[key] = value
        added[key] = value
        changes.append(f"env.{key}: (none) -> {value!r}")
    if added:
        existing["env"] = existing_env
    return added


def retire_auto_allow(existing: dict, fragment: dict, previous: dict, changes: list) -> list:
    """예전 설치가 autoMode.allow 에 더했고 지금 조각에서 빠진 항목을 걷어낸다.

    예전 버전은 'danger_guard 가 가역성을 확인한 push 는 허용'이라는 예외를 넣었다. 훅이 push 를
    판정하지 않게 된 지금 그 예외는 거짓 전제로 Git Destructive 규칙을 약하게 만든다. 우리가 함께
    넣은 "$defaults" 는 사용자 규칙이 남아 있으면 남긴다 — 빠지면 그 목록이 기본 allow 목록을 대체한다."""
    keep = ((fragment.get("autoMode") or {}).get("allow")) or []
    added = previous.get("added_auto_allow") or []
    auto = existing.get("autoMode")
    if not isinstance(auto, dict) or "allow" not in auto:
        # 지울 것이 없다. 영수증에서는 우리 것을 잊는다(autoMode 자체가 형식에 어긋나면 손대지 않는다).
        return [x for x in added if x not in keep] if auto is None or isinstance(auto, dict) else []
    if not isinstance(auto["allow"], list):
        return []
    # 이미 설정에 없는 항목도 영수증에서는 뺀다(retire_list 와 같은 이유).
    stale = [x for x in added if x not in keep and x != "$defaults"]
    gone = [x for x in stale if x in auto["allow"]]
    rest = [x for x in auto["allow"] if x not in gone]
    if rest == ["$defaults"] and "$defaults" in added and "$defaults" not in keep:
        gone.append("$defaults")
        stale.append("$defaults")
        rest = []
    if gone:
        auto["allow"] = rest
        if not rest:
            del auto["allow"]
        if not auto:
            del existing["autoMode"]
        changes.append(f"autoMode.allow: -{gone} (no longer shipped)")
    return stale


def revert_auto_allow(existing: dict, receipt: dict) -> list:
    """uninstall: 설치가 autoMode.allow 에 더한 항목만 걷어낸다. 비면 키를 지운다."""
    added = receipt.get("added_auto_allow") or []
    auto = existing.get("autoMode")
    if not added or not isinstance(auto, dict) or not isinstance(auto.get("allow"), list):
        return []
    keep = [x for x in auto["allow"] if x not in added]
    # 사용자 규칙이 남으면 "$defaults" 도 남긴다 — 빠지면 그 목록이 기본 allow 목록을 통째로 대체한다.
    if keep and "$defaults" in auto["allow"]:
        keep = [x for x in auto["allow"] if x not in added or x == "$defaults"]
    removed = [x for x in auto["allow"] if x not in keep]
    if not removed:
        return []
    auto["allow"] = keep
    if not auto["allow"]:
        del auto["allow"]
    if not auto:
        del existing["autoMode"]
    return [f"autoMode.allow: -{removed}"]


# 우리가 한 번이라도 배포했던 훅 스크립트 이름. 조각에서 빠진 것은 '정책이 바뀌어 그만둔
# 것'이므로 설치 중에 기존 등록을 걷어낸다. 그러지 않으면 새로 설치한 사람에게는 없고
# 업그레이드한 사람에게만 남는 유령 훅이 생긴다 (구버전 permissions 규칙과 같은 성격의
# 마이그레이션이다). uninstall 이 되살리지는 않는다 — 우리가 심은 것을 우리가 거두는 것이라,
# 되살리면 설치가 지운 파일을 가리키는 깨진 훅이 복원된다.
SHIPPED_HOOK_SCRIPTS = ("danger_guard.py", "secrets_guard.py", "send_to_trash.py")


def prune_retired_hooks(existing: dict, fragment: dict, changes: list) -> list:
    """조각에 더 이상 없는 '우리 훅' 등록을 제거한다. 반환: 제거된 스크립트 이름들."""
    shipped_now = set()
    for entries in (fragment.get("hooks") or {}).values():
        for entry in entries:
            shipped_now.update(script_names(entry))
    retired = [n for n in SHIPPED_HOOK_SCRIPTS if n not in shipped_now]
    if not retired:
        return []
    before = len(changes)
    removed = remove_hooks(existing, retired)
    for line in removed:
        changes.append(line + " (hook no longer shipped - removed)")
    return retired if len(changes) > before else []


def merge(existing: dict, fragment: dict, previous: dict = None):
    """(changes, receipt) 를 반환. receipt 는 uninstall 이 정밀 복원에 쓰는 '설치 영수증'.
    previous 는 이전 설치 영수증이다 — 조각에서 빠진 항목 중 우리가 더했던 것만 걷어내는 근거."""
    previous = previous or {}
    changes = []
    receipt = {
        "added_allow": [],
        "added_deny": [],
        "added_ask": [],
        "added_scalars": {},
        "added_env": {},
        "added_auto_allow": [],
        "removed_legacy": {},
        "retired_hooks": [],
        "retired": {},
        "default_mode_before": None,
        "default_mode_after": None,
        "default_mode_changed": False,
    }

    receipt["added_scalars"] = merge_scalars(existing, fragment, changes)
    receipt["added_env"] = merge_env(existing, fragment, changes)
    receipt["retired"]["auto_allow"] = retire_auto_allow(existing, fragment, previous, changes)

    frag_perms = fragment.get("permissions", {})
    if frag_perms:
        perms = existing.setdefault("permissions", {})
        for key in ("allow", "deny", "ask"):
            receipt["retired"][key] = retire_list(perms, key, frag_perms, previous, changes)
            receipt["added_" + key] = merge_list(perms, key, frag_perms.get(key, []), changes)
        warn_legacy_rules(perms, frag_perms)
        if "defaultMode" in frag_perms:
            old_mode = perms.get("defaultMode")
            new_mode = frag_perms["defaultMode"]
            receipt["default_mode_before"] = old_mode
            receipt["default_mode_after"] = new_mode
            if old_mode != new_mode:
                changes.append(f"permissions.defaultMode: {old_mode!r} -> {new_mode!r}")
                perms["defaultMode"] = new_mode
                receipt["default_mode_changed"] = True
        if "mode" in perms:
            changes.append(f"Remove permissions.mode (invalid legacy key): {perms['mode']!r}")
            del perms["mode"]

    frag_hooks = fragment.get("hooks", {})
    if frag_hooks:
        hooks = existing.setdefault("hooks", {})
        for event, entries in frag_hooks.items():
            existing_list = hooks.setdefault(event, [])
            existing_sigs = {hook_sig(e) for e in existing_list}
            for entry in entries:
                sig = hook_sig(entry)
                if sig not in existing_sigs:
                    existing_list.append(entry)
                    existing_sigs.add(sig)
                    names = ", ".join(sig[1]) or "(?)"
                    changes.append(f"hooks.{event}: + matcher={entry.get('matcher')!r} ({names})")

    # 조각을 넣은 '뒤'에 돌려야 한다 — 조각이 다시 등록한 훅까지 지워버리지 않도록.
    receipt["retired_hooks"] = prune_retired_hooks(existing, fragment, changes)

    return changes, receipt


def revert_permissions(existing: dict, receipt: dict) -> list:
    """설치 영수증에 기록된, '우리가 추가한' allow/deny 항목만 제거하고 defaultMode 를 원복한다.
    사용자가 원래 갖고 있던 항목은 영수증에 없으므로 건드리지 않는다."""
    changes = []
    perms = existing.get("permissions")
    if not perms:
        return changes

    # 설치가 제거했던 구버전 규칙을 먼저 복원한다 (아래 added_* 제거와 대상이 겹치지 않는다 —
    # 구버전 규칙은 이번 조각에 없으므로 added_deny 에 들어갈 수 없다).
    changes += restore_legacy_rules(perms, receipt)

    for key in ("allow", "deny", "ask"):
        added = receipt.get("added_" + key) or []
        if not added or key not in perms:
            continue
        removed = [x for x in perms[key] if x in added]
        if removed:
            perms[key] = [x for x in perms[key] if x not in added]
            changes.append(f"permissions.{key}: -{removed}")
        if not perms[key]:
            del perms[key]

    if receipt.get("default_mode_changed"):
        after = receipt.get("default_mode_after")
        before = receipt.get("default_mode_before")
        # 설치 이후 사용자가 defaultMode 를 또 바꿨다면 건드리지 않는다 (여전히 우리 값일 때만 원복).
        if perms.get("defaultMode") == after:
            if before is None:
                if "defaultMode" in perms:
                    del perms["defaultMode"]
                    changes.append("Remove permissions.defaultMode (no value before install)")
            else:
                perms["defaultMode"] = before
                changes.append(f"permissions.defaultMode: {after!r} -> {before!r}")

    if not perms:
        del existing["permissions"]
    return changes


def revert_scalars(existing: dict, receipt: dict) -> list:
    """설치가 '없어서 새로 추가한' model/effortLevel/language 만 제거한다.
    사용자가 원래 갖고 있던 값(영수증에 없음)이나 설치 후 사용자가 다시 바꾼 값은 건드리지 않는다."""
    changes = []
    added = receipt.get("added_scalars") or {}
    for key, value in added.items():
        # 설치 이후 사용자가 값을 또 바꿨다면 건드리지 않는다 (여전히 우리 값일 때만 제거).
        if key in existing and existing[key] == value:
            del existing[key]
            changes.append(f"Remove {key} ({value!r})")
    return changes


def revert_env(existing: dict, receipt: dict) -> list:
    """설치가 '없어서 새로 추가한' env 키만 제거한다. 사용자가 원래 갖고 있던 env 키/값이나
    설치 후 사용자가 다시 바꾼 값은 건드리지 않는다. env 가 비면 env 객체 자체를 제거한다."""
    changes = []
    added = receipt.get("added_env") or {}
    env = existing.get("env")
    if not added or not isinstance(env, dict):
        return changes
    for key, value in added.items():
        # 설치 이후 사용자가 값을 또 바꿨다면 건드리지 않는다 (여전히 우리 값일 때만 제거).
        if key in env and env[key] == value:
            del env[key]
            changes.append(f"Remove env.{key} ({value!r})")
    if not env:
        del existing["env"]
    return changes


def remove_hooks(existing: dict, targets: list) -> list:
    """PreToolUse 등 hooks.* 에서, 지정한 스크립트 파일명을 참조하는 훅을 제거한다.
    같은 matcher 항목 안의 사용자 훅은 남기고, 항목은 안쪽 hooks 가 비었을 때만 지운다."""
    changes = []
    hooks = existing.get("hooks", {})
    target_set = set(targets)
    for event in list(hooks.keys()):
        kept = []
        for entry in hooks[event]:
            hit = set(script_names(entry)) & target_set
            if hit:
                changes.append(f"hooks.{event}: - matcher={entry.get('matcher')!r} ({', '.join(sorted(hit))})")
                entry["hooks"] = [h for h in entry.get("hooks", [])
                                  if not set(PY_RE.findall(h.get("command", ""))) & target_set]
            if not hit or entry["hooks"]:
                kept.append(entry)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if "hooks" in existing and not existing["hooks"]:
        del existing["hooks"]
    return changes


def top_level_diff(before: dict, after: dict):
    preserved, violated = [], []
    for k in before:
        # permissions/hooks/env 는 우리가 의도적으로 병합(merge_env 는 env 하위 관리 키만 추가)하므로
        # '예상치 못한 변경' 위반 검사에서 제외한다. 사용자의 기존 env 키/값 보존은 merge_env 가 보장한다.
        if k in ("permissions", "hooks", "env"):
            continue
        if k == "autoMode" and isinstance(before[k], dict) and isinstance(after.get(k, {}), dict):
            # retire_auto_allow 는 allow 만 바꾸고, 비면 autoMode 까지 지운다. 그 밖의 하위 키는 그대로여야 한다.
            same = {x: v for x, v in before[k].items() if x != "allow"} == \
                {x: v for x, v in after.get(k, {}).items() if x != "allow"}
            (preserved if same else violated).append(k)
            continue
        (preserved if after.get(k) == before.get(k) else violated).append(k)
    return preserved, violated


def main_uninstall(argv) -> int:
    if len(argv) != 3:
        print("usage: merge_and_diff.py --uninstall <settings_path> <dry_run> <manifest_path>", file=sys.stderr)
        return 1
    settings_path, dry_run_s, manifest_path = argv
    dry_run = dry_run_s == "1"

    if not os.path.exists(settings_path):
        print("(settings.json does not exist, so there is nothing to revert.)")
        return 0

    # 설치 영수증(매니페스트). 없으면(구버전 설치) 훅 등록 해제만 하고 권한은 건드리지 못한다.
    receipt = load_manifest(manifest_path)
    hook_files = [f for f in receipt.get("hook_files") or [] if f and "/" not in f and "\\" not in f]
    if not hook_files:
        hook_files = ["danger_guard.py", "secrets_guard.py"]

    with open(settings_path, "r", encoding="utf-8") as f:
        after = json.load(f)

    changes = remove_hooks(after, hook_files)
    changes += revert_permissions(after, receipt)
    changes += revert_scalars(after, receipt)
    changes += revert_env(after, receipt)
    changes += revert_auto_allow(after, receipt)

    print("── Revert plan ────────────────────────")
    if changes:
        for c in changes:
            print(f"  • {c}")
    else:
        print("  (nothing to revert - already clean)")
    print("──────────────────────────────────────")

    if dry_run:
        print(f"🧪 DRY RUN — {settings_path} was not actually written.")
    elif changes:
        backup = backup_file(settings_path)
        if backup:
            print(f"  💾 Backup: {backup}")
        # 설치가 settings.json 을 처음 만든 경우, 되돌린 뒤 빈 {} 만 남으면 그 파일도 제거한다.
        if not after and not receipt.get("settings_existed_before", True):
            os.remove(settings_path)
            print(f"✅ Removed settings.json, which did not exist before install: {settings_path}")
        else:
            write_json(settings_path, after)
            print(f"✅ Reverted the parts install touched: {settings_path}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--uninstall":
        return main_uninstall(argv[1:])

    # 위치 인자 사이 어디에 와도 되도록 --default-mode / --windows 를 먼저 걷어낸다.
    default_mode = ""
    windows = False
    positional = []
    i = 0
    while i < len(argv):
        if argv[i] == "--default-mode" and i + 1 < len(argv):
            default_mode = argv[i + 1]
            i += 2
        elif argv[i] == "--windows":
            windows = True
            i += 1
        else:
            positional.append(argv[i])
            i += 1
    argv = positional

    if default_mode and default_mode not in VALID_DEFAULT_MODES:
        print(f"Unknown defaultMode: {default_mode!r} (valid: {', '.join(VALID_DEFAULT_MODES)})",
              file=sys.stderr)
        return 1

    if len(argv) < 5:
        print("usage: merge_and_diff.py [--default-mode <mode>] [--windows] <fragment.json> <settings_path> <dry_run> <py_invoke> <hooks_dir> [manifest_path] [hook_files_csv]",
              file=sys.stderr)
        return 1

    fragment_path, settings_path, dry_run_s, py_invoke, hooks_dir = argv[:5]
    manifest_path = argv[5] if len(argv) > 5 else ""
    hook_files_csv = argv[6] if len(argv) > 6 else ""
    dry_run = dry_run_s == "1"

    # 훅 command 경로 구분자를 정슬래시로 통일한다. Windows Python 도 정슬래시 경로를
    # 정상 처리하고, Claude Code 자체도 settings.json 에 정슬래시 Windows 경로를 쓴다
    # (예: statusLine 의 "C:/Users/.../omc-hud.mjs"). 혼합 슬래시를 피해 일관성을 얻는다.
    hooks_dir = hooks_dir.replace("\\", "/")

    with open(fragment_path, "r", encoding="utf-8") as f:
        fragment = json.load(f)
    fragment.pop("_comment", None)
    fragment = substitute(fragment, {"{{PY}}": py_invoke, "{{HOOKS_DIR}}": hooks_dir})
    if not windows:
        for key in WINDOWS_ONLY_ENV:
            fragment.get("env", {}).pop(key, None)

    # 온보딩이 고른 권한 모드로 조각의 기본값을 갈아끼운다 (조각 파일 자체는 그대로).
    if default_mode:
        fragment.setdefault("permissions", {})["defaultMode"] = default_mode

    settings_existed = os.path.exists(settings_path)
    if settings_existed:
        with open(settings_path, "r", encoding="utf-8") as f:
            before = json.load(f)
    else:
        before = {}

    after = copy.deepcopy(before)
    previous = load_manifest(manifest_path)
    changes, receipt = merge(after, fragment, previous)
    preserved, violated = top_level_diff(before, after)

    print("── Planned changes ────────────────────")
    if changes:
        for c in changes:
            print(f"  • {c}")
    else:
        print("  (no changes - already up to date)")
    if before:
        print(f"── Preserved existing top-level keys: {preserved or '(none)'}")
        if violated:
            print(f"  ⚠️ Found keys changed unexpectedly: {violated}")
    print("──────────────────────────────────────")

    if dry_run:
        print(f"🧪 DRY RUN — {settings_path} was not actually written.")
    else:
        if changes and settings_existed:
            backup = backup_file(settings_path)
            if backup:
                print(f"  💾 Backup: {backup}")
        write_json(settings_path, after)
        print(f"✅ Merge complete: {settings_path}")

        # 설치 영수증(매니페스트) 기록 — uninstall 이 '건드린 것만' 정밀 복원하는 근거.
        # 재실행(멱등)해도 첫 설치의 기억을 잃지 않도록 이전 영수증 위에 누적한다.
        if manifest_path:
            manifest = {
                "version": 1,
                "hooks_dir": hooks_dir,
                "settings_path": settings_path,
                # settings.json 이 원래 있었는지는 '첫 설치' 시점의 사실이어야 한다.
                "settings_existed_before": previous.get("settings_existed_before", settings_existed)
                if previous else settings_existed,
                # 하위 경로(shims/rm)도 그대로 담는다 — uninstall 이 지울 파일 목록이기도
                # 하기 때문이다. settings.json 의 훅 등록을 지울 때만 바깥에서 걸러낸다
                # (등록 명령에는 파일 이름만 나타난다).
                "hook_files": sorted(
                    set(previous.get("hook_files", []) if previous else [])
                    | {f.strip() for f in hook_files_csv.split(",") if f.strip()}
                ),
            }
            manifest.update(fold_receipt(previous, receipt))
            # 훅이 모르는 섹션(keybinds 등)은 덮지 않는다.
            for key, value in previous.items():
                if key not in manifest:
                    manifest[key] = value
            write_json(manifest_path, manifest)

    return 1 if violated else 0


if __name__ == "__main__":
    raise SystemExit(main())
