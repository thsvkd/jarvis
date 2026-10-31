"""훅 모듈의 계약 층(docs/SPEC.md): 하네스 세션의 안전장치(danger_guard·rm 대체 래퍼)와 ~/.claude/settings.json 병합.

코드는 modules/hooks(ai-setup 에서 옮긴 것)이고, 설치 흐름(ai-setup 의 setup_hooks.sh·setup_hooks.ps1)을 여기서 다시 쓴다.
status·plan 은 읽기만 한다(병합은 메모리에서 미리 돌려 본다. 셀프테스트는 guard.log 를 쓰므로 apply 에서만 돈다).
영수증(~/.claude/.claude-code-setup.json)의 hook_files·권한 기록은 ai-setup 과 같은 키라, ai-setup 이 설치한 기기도 이어서 관리한다.
"""
import copy
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import site
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from . import module

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "hooks")
FRAGMENT = os.path.join(HOOKS, "settings.json")
WINDOWS = sys.platform == "win32"
FILES = ("danger_guard.py", "shims/rm") + (("send_to_trash.py", "shims/RmShim.psm1", "graft_sync_hidden.cjs") if WINDOWS else ())
LEGACY = ("secrets_guard.py",)  # 예전 설치가 심은 훅. 영수증에 있으면 지운다
# 영수증에 hook_files 가 없는 옛 설치를 지울 때의 목록(ai-setup 의 uninstall_hooks 와 같다)
OLD_FILES = ("danger_guard.py", "secrets_guard.py", "send_to_trash.py", "shims/rm", "shims/RmShim.psm1")
# (이름, 이벤트, 판정이 있어야 하는가). 판정 없이 통과해야 하는 경우와 deny·입력 변경이 있어야 하는 경우를 함께 본다
SELF_TESTS = [
    ("rm -rf temp/", {"tool_name": "Bash", "tool_input": {"command": "rm -rf temp/"}}, False),
    ("rm -rf ~", {"tool_name": "Bash", "tool_input": {"command": "rm -rf ~"}}, True),
    ("ls -la", {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}, False),
] + ([
    ("PowerShell Remove-Item", {"tool_name": "PowerShell", "tool_input": {"command": "Remove-Item -Recurse -Force .\\build"}}, True),
    ("Remove-Item C:\\", {"tool_name": "PowerShell", "tool_input": {"command": "Remove-Item -Recurse -Force C:\\"}}, True),
] if WINDOWS else [
    ("rm -rf \"$V/x\"", {"tool_name": "Bash", "tool_input": {"command": "rm -rf \"$V/x\""}}, False),
    ("find -exec rm", {"tool_name": "Bash", "tool_input": {"command": "find . -name x -exec rm -f {} +"}}, False),
])
_merge = None


def merger():
    """modules/hooks/merge_and_diff.py. 설치기 밖에서도 단독 실행하는 스크립트라 경로로 읽는다."""
    global _merge
    if _merge is None:
        spec = importlib.util.spec_from_file_location("jarvis_hooks_merge", os.path.join(HOOKS, "merge_and_diff.py"))
        _merge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_merge)
    return _merge


def _claude():
    return Path.home() / ".claude"


def _receipt_path():
    return _claude() / ".claude-code-setup.json"


def _read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def _receipt():
    data = _read_json(_receipt_path())
    if data is not None and not isinstance(data, dict):
        raise RuntimeError(f"설치 영수증을 읽을 수 없습니다: {_receipt_path()}")
    return data or {}


def _digest(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None


def session_path():
    """하네스 세션이 보는 PATH 에 가깝게: 게이트웨이(systemd·launchd·예약 작업)의 PATH 에는 사용자 bin 이 빠져 있다.
    trash-cli 를 pip --user 로 깔면 ~/.local/bin(macOS 는 사용자 site 의 bin)에 들어간다."""
    extra = [str(Path.home() / ".local" / "bin"), os.path.join(site.getuserbase(), "Scripts" if WINDOWS else "bin")]
    return os.pathsep.join([os.environ.get("PATH", "")] + extra)


def _which(name):
    return shutil.which(name, path=session_path())


def _quiet(argv, timeout):
    """출력은 버리고 성공 여부만. 없거나 시간이 넘으면 실패다."""
    try:
        return subprocess.run(argv, capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def python_invoke():
    """훅을 실행할 명령(조각의 {{PY}}). ai-setup 의 resolve_python_command·Resolve-PythonCommand 와 같은 순서다."""
    if not WINDOWS:
        for name in ("python3", "python"):
            if _which(name):
                return name
        return None
    for argv in (["python"], ["python3"], ["py", "-3"], ["py"]):
        if _which(argv[0]) and _quiet(argv + ["-c", "print(1)"], 20):
            return " ".join(argv)
    return None


def _has_trash(py):
    """셔임은 trash-put → macOS 의 trash 순서로, Windows 는 send2trash 로 휴지통에 옮긴다."""
    if WINDOWS:
        return bool(py) and _quiet(py.split() + ["-c", "import send2trash"], 20)
    return bool(_which("trash-put") or (sys.platform == "darwin" and _which("trash")))


def _trash_steps(py):
    """휴지통 도구가 없을 때의 단계들. 실패해도 훅 설치는 계속한다(없으면 rm 이 경고 뒤 실제로 지울 뿐이다)."""
    if not py or _has_trash(py):
        return []
    if WINDOWS:
        return [{"id": "trash", "summary": "휴지통 도구 send2trash 설치(pip)", "risk": "install",
                 "argv": py.split() + ["-m", "pip", "install", "--quiet", "--disable-pip-version-check", "send2trash"]}]
    argv = [py, "-m", "pip", "install", "--user", "trash-cli"]
    try:
        if "break-system-packages" in subprocess.run([py, "-m", "pip", "install", "--help"], capture_output=True,
                                                      text=True, timeout=60).stdout:
            argv.insert(4, "--break-system-packages")
    except (OSError, subprocess.TimeoutExpired):
        pass
    steps = [{"id": "trash", "summary": "휴지통 도구 trash-cli 설치(pip --user)", "risk": "install", "argv": argv}]
    if _which("apt-get"):
        # Debian 12·Raspberry Pi OS 는 시스템 python 의 pip 를 막는다(PEP 668). 그때만 돈다
        steps.append({"id": "trash_apt", "summary": "pip 로 깔리지 않으면 배포판 패키지 trash-cli 설치", "risk": "sudo",
                      "argv": ["sudo", "-n", "apt-get", "install", "-y", "trash-cli"]})
    return steps


def _fragment(mode):
    """조각(자리표시자는 그대로). 사용자가 지금 쓰는 권한 모드를 바꾸지 않는다: 병합기가 아는 값이면 그 값으로,
    모르는 값(새 Claude Code 모드 등)이면 조각에서 defaultMode 를 빼서 손대지 않는다."""
    with open(FRAGMENT, encoding="utf-8") as f:
        fragment = json.load(f)
    fragment.pop("_comment", None)
    perms = fragment.setdefault("permissions", {})
    if mode in merger().VALID_DEFAULT_MODES:
        perms["defaultMode"] = mode
    elif mode:
        perms.pop("defaultMode", None)
    return fragment


def _mode(before, receipt):
    return (before.get("permissions") or {}).get("defaultMode") or receipt.get("default_mode_after")


def _settings_plan(py, receipt):
    """(바뀔 항목들, 예상 밖으로 바뀌는 최상위 키, defaultMode). 실제 파일은 쓰지 않는다."""
    m = merger()
    before = _read_json(_claude() / "settings.json") or {}
    mode = _mode(before, receipt)
    fragment = m.substitute(_fragment(mode), {"{{PY}}": py, "{{HOOKS_DIR}}": str(_claude() / "hooks").replace("\\", "/")})
    if not WINDOWS:
        for key in m.WINDOWS_ONLY_ENV:
            fragment.get("env", {}).pop(key, None)
    after = copy.deepcopy(before)
    changes, _ = m.merge(after, fragment, m.load_manifest(str(_receipt_path())))
    _, violated = m.top_level_diff(before, after)
    # 승인할 사람이 먼저 봐야 할 것(권한 모드·훅 등록·권한 목록)을 앞에 둔다
    order = ("permissions.defaultMode", "hooks.", "permissions.")
    changes.sort(key=lambda c: next((i for i, p in enumerate(order) if c.startswith(p)), len(order)))
    return changes, violated, mode


def _broken_registrations():
    """settings.json 에 등록된 danger_guard 명령 가운데 인터프리터나 스크립트가 없는 것. 같은 이름의 훅은 병합기가
    다시 넣지 않으므로(matcher·파일 이름으로 같다고 본다) 이런 등록은 고쳐지지 않은 채 가드가 꺼진다."""
    broken = []
    for entries in ((_read_json(_claude() / "settings.json") or {}).get("hooks") or {}).values():
        for entry in entries if isinstance(entries, list) else []:
            for h in (entry.get("hooks") or []) if isinstance(entry, dict) else []:
                cmd = h.get("command") if isinstance(h, dict) else None
                if not isinstance(cmd, str) or "danger_guard.py" not in cmd:
                    continue
                try:
                    words = shlex.split(cmd, posix=not WINDOWS)
                except ValueError:
                    words = []
                script = next((w.strip('"') for w in words if w.strip('"').endswith("danger_guard.py")), None)
                if not words or not _which(words[0].strip('"')) or not script or not os.path.isfile(os.path.expanduser(script)):
                    broken.append(cmd)
    return broken


def _files(receipt):
    """[(이름, 상태: ok/missing/changed/foreign)]. foreign 은 영수증에 없는 같은 이름 파일(덮어쓰지 않는다)."""
    owned = set(receipt.get("hook_files") or [])
    rows = []
    for name in FILES:
        have = _digest(_claude() / "hooks" / name)
        if have is None:
            rows.append((name, "missing"))
        elif have == _digest(os.path.join(HOOKS, name)):
            rows.append((name, "ok"))
        else:
            rows.append((name, "changed" if name in owned else "foreign"))
    return rows


def _bundle(paths):
    digests = [_digest(p) for p in paths]
    return None if None in digests else hashlib.sha256("".join(digests).encode()).hexdigest()[:12]


def status():
    receipt = _receipt()
    py = python_invoke()
    files = _files(receipt)
    trash = _has_trash(py)
    changes, violated, mode = _settings_plan(py, receipt) if py else ([], [], None)
    broken = _broken_registrations()
    installed = (_claude() / "hooks" / "danger_guard.py").exists()
    healthy = all(s == "ok" for _, s in files) and not changes and not violated and not broken and trash and py
    return {"module": "hooks", "installed": installed, "owned": "danger_guard.py" in (receipt.get("hook_files") or []),
            "version": _bundle([_claude() / "hooks" / n for n in FILES]),
            "latest": _bundle([os.path.join(HOOKS, n) for n in FILES]),
            "health": "ok" if healthy else ("degraded" if installed else "down"),
            "details": {"files": [{"name": n, "state": s} for n, s in files], "settings_changes": changes,
                        "unexpected_keys": violated, "broken_registrations": broken, "trash": trash, "python": py,
                        "default_mode": mode}}


def plan():
    receipt = _receipt()
    py = python_invoke()
    steps, blocked = [], []
    if not py:
        blocked.append({"step": "settings", "reason": "훅을 실행할 python 을 PATH 에서 찾지 못했습니다"})
    for cmd in _broken_registrations():
        blocked.append({"step": "settings", "reason": f"등록된 훅 명령의 python 이나 스크립트가 없습니다(직접 고쳐야 합니다): {cmd}"})
    steps += _trash_steps(py)
    files = _files(receipt)
    owned = set(receipt.get("hook_files") or [])
    write = [n for n, s in files if s in ("missing", "changed")]
    legacy = [n for n in LEGACY if n in owned and (_claude() / "hooks" / n).exists()]
    for n, s in files:
        if s == "foreign":
            blocked.append({"step": "files", "reason": f"~/.claude/hooks/{n} 가 설치 기록에 없는 다른 파일입니다(덮어쓰지 않습니다)"})
    if write or legacy:
        steps.append({"id": "files", "summary": "훅 파일 쓰기: " + ", ".join(write + [f"{n} 지움" for n in legacy]),
                      "risk": "config", "argv": [],
                      "digests": {n: _digest(os.path.join(HOOKS, n)) for n in write},
                      "from": {n: _digest(_claude() / "hooks" / n) for n in write}})
    if py:
        changes, violated, mode = _settings_plan(py, receipt)
        if violated:
            blocked.append({"step": "settings", "reason": f"병합이 예상 밖의 최상위 키를 바꿉니다: {violated}"})
        if changes or write or not receipt.get("hook_files"):
            # 파일만 바뀌어도 병합을 다시 돌려 영수증의 hook_files 를 맞춘다(설정 내용은 그대로다)
            shown = "; ".join(changes[:8]) + (f" 외 {len(changes) - 8}건" if len(changes) > 8 else "")
            steps.append({"id": "settings", "risk": "config", "argv": [], "default_mode": mode, "python": py,
                          "changes": changes,
                          "summary": "~/.claude/settings.json 병합(권한·훅 등록, 비어 있는 기본값(model 등)만 채움, 나머지 키 보존, "
                                     "바뀌면 백업): " + (shown or "바뀔 항목 없음, 영수증만 맞춤")})
    if steps:
        steps.append({"id": "selftest", "summary": "설치한 danger_guard 에 정해진 입력을 넣어 판정을 확인", "risk": "read", "argv": []})
    return module.seal({"module": "hooks", "device": socket.gethostname(),
                        "action": "update" if receipt.get("hook_files") else "install", "steps": steps, "blocked": blocked})


def _run(argv, timeout):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 1, f"{os.path.basename(argv[0])} 가 {timeout}초 안에 끝나지 않았습니다"
    except OSError as e:
        return 1, f"{os.path.basename(argv[0])} 를 실행하지 못했습니다: {e.strerror}"
    return r.returncode, (r.stdout + r.stderr).strip()


def _install_trash(step):
    """실패는 건너뜀이다: 휴지통 도구가 없어도 가드는 설치해야 한다."""
    if _has_trash(python_invoke()):
        return "skipped", "이미 있습니다"
    code, out = _run(step["argv"], 600 if step["id"] == "trash" else 900)
    if code == 0 and _has_trash(python_invoke()):
        return "ok", "설치했습니다"
    why = (out.splitlines() or ["설치하지 못했습니다"])[-1][:200]
    return "skipped", f"설치하지 못했습니다({why}). rm 은 경고를 남기고 실제로 지웁니다"


def _write_files(step, receipt):
    hooks = _claude() / "hooks"
    for name, digest in step["digests"].items():
        src, dest = os.path.join(HOOKS, name), hooks / name
        if _digest(src) != digest:
            return "failed", f"배포본 {name} 가 계획과 다릅니다"
        if _digest(dest) != step["from"][name]:  # 계획을 세운 뒤(휴지통 도구를 까는 동안) 누가 바꿨다
            return "failed", f"~/.claude/hooks/{name} 가 계획을 세운 뒤 바뀌었습니다(덮어쓰지 않습니다)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".jarvis-tmp")
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, dest)
    for name in LEGACY:
        if name in (receipt.get("hook_files") or []):
            try:
                (hooks / name).unlink()
            except FileNotFoundError:
                pass
    return "ok", ", ".join(step["digests"]) or "지울 것만 지웠습니다"


def _merge_settings(step):
    claude = _claude()
    # 영수증에는 지금 배포본과 같은 파일만 적는다(설치 기록에 없는 사용자 파일을 우리 것으로 만들지 않는다)
    ours = [n for n in FILES if _digest(claude / "hooks" / n) == _digest(os.path.join(HOOKS, n))]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(_fragment(step.get("default_mode")), f)
        fragment = f.name
    try:
        argv = [sys.executable, "-X", "utf8", os.path.join(HOOKS, "merge_and_diff.py")] + (["--windows"] if WINDOWS else [])
        argv += [fragment, str(claude / "settings.json"), "0", step["python"], str(claude / "hooks"),
                 str(_receipt_path()), ",".join(ours)]
        code, out = _run(argv, 120)
    finally:
        os.unlink(fragment)
    return ("ok", "병합했습니다") if code == 0 else ("failed", (out.splitlines() or ["병합에 실패했습니다"])[-1][:300])


def selftest():
    """(실패 수, 이유). ai-setup 의 run_hook_self_test 와 같은 판정: 빈 출력은 통과해야 하는 경우만 성공이다."""
    hook = _claude() / "hooks" / "danger_guard.py"
    py = (python_invoke() or sys.executable).split()  # 세션이 훅을 부르는 그 python 으로 돌린다(구버전 호환까지 본다)
    failures = []
    for label, event, decides in SELF_TESTS:
        r = subprocess.run(py + ["-X", "utf8", str(hook)], input=json.dumps(event), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
        out = r.stdout.strip()
        if r.returncode != 0:
            failures.append(f"{label}: 종료 코드 {r.returncode}")
        elif bool(out) != decides:
            failures.append(f"{label}: " + ("판정이 없습니다" if decides else "판정이 없어야 합니다"))
        elif out:
            h = json.loads(out).get("hookSpecificOutput", {})
            if not (h.get("permissionDecision") or "updatedInput" in h):
                failures.append(f"{label}: 판정을 읽지 못했습니다")
    return len(failures), "; ".join(failures)


def apply(plan_id):
    current = plan()
    if current["plan_id"] != plan_id:
        return {"status": "stale", "plan_id": current["plan_id"], "steps": []}
    if current["blocked"]:  # 막힌 단계가 있으면 앞 단계의 부수 효과(설치·쓰기)도 남기지 않는다
        return {"status": "failed", "steps": [{"id": b["step"], "result": "failed", "message": b["reason"]}
                                              for b in current["blocked"]]}
    if not current["steps"]:
        return {"status": "current", "steps": []}
    receipt = _receipt()
    results, status = [], "done"
    for step in current["steps"]:
        try:
            if step["id"] in ("trash", "trash_apt"):
                result, message = _install_trash(step)
            elif step["id"] == "files":
                result, message = _write_files(step, receipt)
            elif step["id"] == "settings":
                result, message = _merge_settings(step)
            else:
                n, why = selftest()
                result, message = ("ok", f"{len(SELF_TESTS)}건 통과") if not n else ("failed", why)
        except Exception as e:  # 파일 경로·조각 값만 들어간다(비밀을 다루지 않는다)
            result, message = "failed", f"{type(e).__name__}: {e}"[:300]
        results.append({"id": step["id"], "result": result, "message": message})
        if result == "failed":
            status = "failed"
            break
    return {"status": status, "steps": results}


def _uninstall_plan(receipt):
    hooks = _claude() / "hooks"
    names = receipt.get("hook_files") or OLD_FILES  # 영수증이 없는 옛 설치도 ai-setup 처럼 정해진 목록을 걷어낸다
    remove = [n for n in names if (hooks / n).is_file()]
    steps = []
    settings = _claude() / "settings.json"
    if receipt.get("hook_files") or receipt.get("added_allow") or receipt.get("default_mode_changed") \
            or (settings.is_file() and "danger_guard.py" in settings.read_text(encoding="utf-8", errors="replace")):
        steps.append({"id": "settings", "summary": "settings.json 에서 설치가 넣은 훅 등록·권한 항목만 걷어내고 defaultMode 원복",
                      "risk": "config", "argv": []})
    if remove:
        steps.append({"id": "files", "summary": "훅 파일 지움: " + ", ".join(remove), "risk": "remove", "argv": [],
                      "digests": {n: _digest(hooks / n) for n in remove}})
    return module.seal({"module": "hooks", "device": socket.gethostname(), "action": "uninstall", "steps": steps, "blocked": []})


# 훅 기록을 지운 뒤에도 영수증에 남길 다른 단계의 기록(ai-setup 의 uninstall_hooks 와 같은 목록)
KEEP = ("keybinds", "omc_hud", "output_style", "mods", "tool_installs", "tool_attempts", "harness_installs", "plugins",
        "mcp", "jarvis_unit", "agentsmd", "skills", "marketplaces")


def uninstall(dry_run=False, plan_id=None):
    receipt = _receipt()
    current = _uninstall_plan(receipt)
    if dry_run:
        return {"status": "done", "plan_id": current["plan_id"], "steps": [
            {"id": s["id"], "result": "skipped", "message": s["summary"]} for s in current["steps"]]}
    if plan_id != current["plan_id"]:
        return {"status": "stale", "plan_id": current["plan_id"], "steps": []}
    claude, results = _claude(), []
    for step in current["steps"]:
        if step["id"] == "settings":
            code, out = _run([sys.executable, "-X", "utf8", os.path.join(HOOKS, "merge_and_diff.py"), "--uninstall",
                              str(claude / "settings.json"), "0", str(_receipt_path())], 120)
            if code:
                return {"status": "failed", "plan_id": current["plan_id"],
                        "steps": results + [{"id": "settings", "result": "failed", "message": out.splitlines()[-1][:300] if out else ""}]}
            results.append({"id": "settings", "result": "ok", "message": "되돌렸습니다"})
        else:
            for name in step["digests"]:
                (claude / "hooks" / name).unlink(missing_ok=True)
            (claude / "hooks" / "guard.log").unlink(missing_ok=True)
            try:
                (claude / "hooks" / "shims").rmdir()
            except OSError:
                pass
            results.append({"id": "files", "result": "ok", "message": ", ".join(step["digests"])})
    kept = {k: receipt[k] for k in KEEP if receipt.get(k)}
    path = _receipt_path()
    if kept:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    elif path.exists():
        path.unlink()
    return {"status": "done", "plan_id": current["plan_id"], "steps": results}
