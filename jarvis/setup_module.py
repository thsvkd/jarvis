"""setup 모듈의 계약 층(docs/SPEC.md): 하네스·플러그인·스킬·CodeGraph·MCP·줄바꿈 키·Claude 전용 설정·도구의 설치.

엔진은 modules/setup(ai-setup 에서 옮긴 res/stages.py 와 워커 스크립트)이다. 계획은 ai-setup 의
`--stages all`(agentsmd 제외)과 같은 액션 목록이고, 이미 설치한 설정은 배포본이 바뀌었을 때만 다시 적용한다
(ai-setup 에서 `--keybinds`·`--skills` 등을 다시 실행하던 갱신 경로). AGENTS.md 는 프로젝트 폴더 단위라
기기 모듈에 넣지 않는다(`python -m jarvis agentsmd`).
영수증(~/.claude/.claude-code-setup.json)의 키는 ai-setup 과 같아, ai-setup 이 설치한 기기도 이어서 관리한다.
"""
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

from . import module
from .config import STATE_DIR
from .harness_module import user_path

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "setup")
REFRESH_STATE = os.path.join(STATE_DIR, "setup-refresh.json")
_stages = None

# 이미 설치한 설정을 다시 적용하는 워커: (id, 이름, 영수증 판정, 배포본 경로, 엔진 액션의 argv 머리, 워커 스크립트, risk).
# 출력 스타일·OMC HUD 워커는 플러그인이 없으면 마켓플레이스에서 설치하므로 install 이다.
REFRESH = (
    ("newline", "줄바꿈 키", lambda w: w["newline_done"], ("res/keybinds",), "newline", "setup_keybinds", "config"),
    ("skills", "번들 스킬", lambda w: bool(w["receipt_skills"]), ("res/skills",), "skills", "setup_skills", "config"),
    ("output_style", "출력 스타일", lambda w: w["output_style"], ("res/output_style",), "output-style", "setup_output_style", "install"),
    ("mods", "mod", lambda w: w["mods"], ("res/mods",), "mods", "setup_mods", "config"),
    ("omc_hud", "OMC HUD 캐시 표시", lambda w: w["omc_hud"], ("res/omc",), "omc-hud-wrapper", "setup_omc", "install"),
)
STAGE_OF = {"newline": "newline", "skills": "skills"}  # 나머지 머리는 claude_special 단계의 액션이다
# 설치 액션이 성공하면 그 워커의 배포본을 적용한 것이다(다음 계획에 다시 적용이 바로 생기지 않게)
INSTALLS = {"newline": "newline", "install-skills": "skills", "output-style": "output_style", "mods": "mods",
            "omc-hud-wrapper": "omc_hud"}
# jarvis 게이트웨이 자신과 Tailscale 은 지우지 않는다(원격으로 지우면 연결이 끊긴다. Orca 처럼 영수증 기록도 남긴다)
KEEP_ON_UNINSTALL = ("jarvis_unit_owned", "receipt_tools:jarvis", "receipt_tools:tailscale")
# 앞 단계(stage)가 실패하면 뒤 단계는 돌지 않는다(예: CodeGraph 설치가 실패했는데 MCP 에 없는 바이너리를 등록하지 않게)
NEEDS = {"codegraph": ("base",), "harness": ("base",), "plugins": ("harness",), "skills": ("harness",),
         "mcp": ("harness", "codegraph"), "claude_special": ("harness", "plugins")}
SUDO = re.compile(r"\bsudo\b")
PROMPTING_SUDO = re.compile(r"\bsudo(?! -n)\b")  # -n 이 없으면 TTY 가 없어도 비밀번호를 기다릴 수 있다


def stages():
    """modules/setup/res/stages.py. ai-setup 처럼 단독 실행도 하는 파일이라 경로로 읽는다."""
    global _stages
    if _stages is None:
        spec = importlib.util.spec_from_file_location("jarvis_setup_stages", os.path.join(ROOT, "res", "stages.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # dataclass(Action) 가 자기 모듈을 sys.modules 에서 찾는다
        spec.loader.exec_module(mod)
        _stages = mod
    return _stages


@contextlib.contextmanager
def _user_path():
    """게이트웨이 서비스 PATH 에는 ~/.local/bin·nvm 이 없다. 엔진의 PATH 보강과 update-ai 의 PATH 보강
    (~/.npm-global/bin·Homebrew 포함)을 함께 쓰고 끝나면 되돌린다."""
    before = os.environ.get("PATH", "")
    try:
        stages()._ensure_user_bin_on_path()
        os.environ["PATH"] = user_path(os.environ["PATH"])
        yield
    finally:
        os.environ["PATH"] = before


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _bundle_digest(paths):
    """배포본 파일들(디렉터리는 그 안의 파일 전부)의 다이제스트. 저장소 체크아웃에만 생기는 것(__pycache__,
    점으로 시작하는 .omc 등)은 뺀다(릴리스 묶음과 저장소가 같은 값을 내도록)."""
    h = hashlib.sha256()
    for rel in paths:
        base = Path(ROOT, rel)
        files = [base] if base.is_file() else sorted(
            p for p in base.rglob("*") if p.is_file() and not any(x == "__pycache__" or x.startswith(".") for x in p.relative_to(base).parts))
        for p in files:
            h.update(p.relative_to(ROOT).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def _selection(world, uninstall=False):
    s = stages()
    receipt_harnesses = [h for h in s.HARNESS_LABELS if h in world["receipt_harnesses"]]
    harnesses = [h for h in s.HARNESS_LABELS if h in ("claude", "codex") or h in receipt_harnesses]
    chosen = {"stages": [st for st in s.STAGE_ORDER if st != "agentsmd"], "harnesses": harnesses, "uninstall": uninstall}
    if world["newline_chord"]:  # 기록된 줄바꿈 키로 다시 적용한다(기본값으로 되돌리지 않는다)
        chosen["newline"] = world["newline_chord"]
    return chosen


def _needs_sudo(action):
    text = " ".join(action.argv)
    return bool(SUDO.search(text)) or "tailscale.com/install.sh" in text  # Tailscale 설치기는 안에서 sudo 를 부른다


def _risk(action):
    if _needs_sudo(action):
        return "sudo"
    if action.stage in ("base", "harness", "codegraph", "tools") or action.argv[:3] == ("claude", "plugin", "install"):
        return "install"
    return "config"


def _noninteractive(action):
    """게이트웨이에는 TTY 가 없다. sudo 는 언제나 -n 으로 불러 비밀번호를 기다리며 멈추지 않게 한다."""
    if not PROMPTING_SUDO.search(" ".join(action.argv)):
        return action
    return stages().Action(action.stage, action.detail, action.writes, tuple(PROMPTING_SUDO.sub("sudo -n", a) for a in action.argv),
                           action.effects)


def sudo_ok():
    """비밀번호 없이 sudo 를 쓸 수 있는가(NOPASSWD 또는 유효한 캐시). 묻지 않는다. Windows 는 None."""
    if os.name == "nt":
        return None
    try:
        return subprocess.run(["sudo", "-n", "true"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _worker_argv(script):
    return [f"scripts/{script}.ps1" if os.name == "nt" else f"scripts/{script}.sh"]


def _snapshot():
    with _user_path():
        world = stages().probe_world(str(Path.home()))
    return world


def _plan(world, uninstall=False, check_sudo=True):
    s = stages()
    chosen = _selection(world, uninstall)
    actions = [_noninteractive(a) for a in s.plan_actions(chosen, world) if a.writes]
    if uninstall:
        actions = [a for a in actions if not any(k in e for e in a.effects for k in KEEP_ON_UNINSTALL)]
    # status 는 sudo 를 부르지 않는다(읽을 때마다 auth 로그에 남는다). 그때 sudo 단계는 unknown 이다
    sudo = (sudo_ok() if check_sudo else None) if any(_needs_sudo(a) for a in actions) else None
    steps = []
    for i, a in enumerate(actions, 1):
        step = {"id": f"{i}-{a.stage}", "summary": a.detail, "risk": "remove" if uninstall else _risk(a), "argv": list(a.argv),
                "stage": a.stage}
        # plan_id 가 실제로 돌 본문까지 묶게 한다: 하네스 설치는 카탈로그 명령, 설정 워커는 그 배포본의 다이제스트
        if a.argv[:1] == ("install-harness",):
            step["cmd"] = s._catalog_command(a.argv[-1], uninstall)
        elif a.argv and a.argv[0] in INSTALLS:
            step["digest"] = _refresh_digest(INSTALLS[a.argv[0]])
        if _needs_sudo(a):
            step["sudo"] = "unknown" if sudo is None else "ok" if sudo else "password"
            if sudo is False:
                step["summary"] += " — 비밀번호 없는 sudo 를 쓸 수 없어 건너뜁니다(터미널에서 직접 실행)"
        steps.append(step)
    if not uninstall:
        done = _read_json(REFRESH_STATE)
        for rid, label, present, _, _, script, risk in REFRESH:  # 설치 기록이 있는 것만(없으면 위의 설치 액션이 새 판을 쓴다)
            digest = _refresh_digest(rid)
            if present(world) and done.get(rid) != digest:
                steps.append({"id": f"refresh-{rid}", "summary": f"{label}: 이 배포본으로 다시 적용(사용자가 고친 파일은 그대로 둠)",
                              "risk": risk, "argv": _worker_argv(script), "digest": digest})
    return module.seal({"module": "setup", "device": socket.gethostname(), "action": "uninstall" if uninstall else "update",
                        "steps": steps, "blocked": []}), actions, chosen


def status():
    world = _snapshot()
    p, _, _ = _plan(world, check_sudo=False)
    receipt = _read_json(Path.home() / ".claude" / ".claude-code-setup.json")
    keys = ("harness_installs", "plugins", "skills", "mcp", "marketplaces", "tool_installs", "keybinds", "output_style", "mods", "omc_hud")
    recorded = {k: (sorted(receipt[k]) if isinstance(receipt.get(k), dict) else bool(receipt.get(k))) for k in keys if receipt.get(k)}
    return {"module": "setup", "installed": bool(recorded), "owned": bool(recorded), "version": None, "latest": None,
            "health": "degraded" if p["steps"] else "ok",
            "details": {"harnesses": [h for h in stages().HARNESS_LABELS if h in world["bins"]], "recorded": recorded,
                        "pending": [st["summary"] for st in p["steps"]],
                        "sudo_steps": sum(1 for st in p["steps"] if "sudo" in st)}}


def plan():
    return _plan(_snapshot())[0]


def _write_refresh(rid, digest):
    done = _read_json(REFRESH_STATE)
    done[rid] = digest
    os.makedirs(os.path.dirname(REFRESH_STATE), exist_ok=True)
    tmp = REFRESH_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(done, f, indent=2)
    os.replace(tmp, REFRESH_STATE)


def _refresh_digest(rid):
    r = next(r for r in REFRESH if r[0] == rid)
    return _bundle_digest(r[3] + tuple(f"scripts/{r[5]}.{ext}" for ext in ("sh", "ps1")))


def _run_steps(steps, actions, chosen):
    """액션을 하나씩 엔진의 execute 로 돌린다. 한 단계(stage) 안에서 실패하면 그 단계의 나머지는 건너뛰고,
    서로 독립인 다른 단계는 계속한다. 비밀번호 없는 sudo 를 쓸 수 없는 단계는 실행하지 않고 건너뛴다(명령을 남긴다)."""
    s, results = stages(), []
    failed_stages, unmet_stages = set(), set()
    with _user_path():
        for step, action in zip(steps, actions + [None] * (len(steps) - len(actions))):
            stage = step.get("stage") or step["id"]  # refresh 는 서로 독립이다
            try:
                # 같은 단계는 실패했을 때만 멈추고(sudo 로 건너뛴 다른 액션과는 독립이다), 뒤 단계는 앞 단계가
                # 실패했거나 건너뛰었으면 기댈 수 없다
                blocked_by = [x for x in NEEDS.get(stage, ()) if x in failed_stages or x in unmet_stages]
                blocked_by += [stage] if stage in failed_stages else []
                if blocked_by:
                    result, message = "skipped", f"앞 단계({', '.join(blocked_by)})가 실패해 건너뜀"
                elif step.get("sudo") == "password":
                    result, message = "skipped", "비밀번호 없는 sudo 가 없어 실행하지 않음. 터미널에서 실행: " + \
                        " ".join(step["argv"]).replace("sudo -n", "sudo")
                elif step["id"].startswith("refresh-"):
                    rid = step["id"][len("refresh-"):]
                    head = next(r[4] for r in REFRESH if r[0] == rid)
                    code = s.execute([s.Action(STAGE_OF.get(head, "claude_special"), step["summary"], True, (head,), ())], chosen)
                    if code == 0:
                        _write_refresh(rid, step["digest"])
                    result, message = ("ok", "") if code == 0 else ("failed", f"종료 코드 {code}")
                else:
                    code = s.execute([action], chosen)
                    if code == 0 and action.argv and action.argv[0] in INSTALLS and "--uninstall" not in action.argv:
                        _write_refresh(INSTALLS[action.argv[0]], _refresh_digest(INSTALLS[action.argv[0]]))
                    result, message = ("ok", "") if code == 0 else ("failed", f"종료 코드 {code}")
            except Exception as e:  # 명령·경로만 들어간다(비밀을 다루지 않는다)
                result, message = "failed", f"{type(e).__name__}: {e}"[:300]
            if result == "failed":
                failed_stages.add(stage)
            elif result == "skipped":
                unmet_stages.add(stage)
            results.append({"id": step["id"], "result": result, "message": message})
    return ("failed" if any(r["result"] == "failed" for r in results) else "done"), results


def apply(plan_id):
    world = _snapshot()
    current, actions, chosen = _plan(world)
    if current["plan_id"] != plan_id:
        return {"status": "stale", "plan_id": current["plan_id"], "steps": []}
    if not current["steps"]:
        return {"status": "current", "steps": []}
    status, results = _run_steps(current["steps"], actions, chosen)
    return {"status": status, "steps": results}


def uninstall(dry_run=False, plan_id=None):
    world = _snapshot()
    current, actions, chosen = _plan(world, uninstall=True)
    if dry_run:
        return {"status": "done", "plan_id": current["plan_id"], "steps": [
            {"id": st["id"], "result": "skipped", "message": st["summary"]} for st in current["steps"]]}
    if plan_id != current["plan_id"]:
        return {"status": "stale", "plan_id": current["plan_id"], "steps": []}
    status, results = _run_steps(current["steps"], actions, chosen)
    return {"status": status, "plan_id": current["plan_id"], "steps": results}
