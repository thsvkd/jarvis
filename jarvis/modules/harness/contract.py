"""harness 모듈의 계약 층(docs/SPEC.md): 설치된 AI 하네스·도구의 갱신(update-ai).

엔진은 modules/setup/res/update/update_ai.py(ai-setup 에서 옮긴 것)다. status·plan 은 update-ai --check 와 같은
읽기만 하고(설치본·최신 버전, codex 관리형 데몬), apply 는 바뀔 대상만 `update-ai --only` 로 올린다.
설치되지 않은 것은 설치하지 않는다(설치는 setup 모듈). 플러그인·마켓플레이스 갱신은 claude 대상과 함께 돈다.
"""
import contextlib
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import sys
from pathlib import Path

from ... import module

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "setup")
UPDATE_AI = os.path.join(ROOT, "res", "update", "update_ai.py")
TIMEOUT = 3000  # 하네스 여러 개를 차례로 받는다(update-ai 는 단계마다 900초). 게이트웨이의 apply 제한(3600초)보다 먼저 끝낸다
_tools = None


def tools():
    """modules/setup/res/update/update_ai.py. 단독 실행도 하는 파일이라 경로로 읽는다."""
    global _tools
    if _tools is None:
        spec = importlib.util.spec_from_file_location("jarvis_update_ai", UPDATE_AI)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _tools = mod
    return _tools


def _version_key(v):
    return [int(x) for x in re.findall(r"\d+", v)]


def user_path(path):
    """update-ai.sh 와 같은 PATH 보강. cron·서비스에는 로그인 셸의 PATH 가 없다.

    사용자 bin, nvm 의 node(높은 버전 먼저), Homebrew 를 앞에 붙인다. Windows 는 그대로 둔다(Update-Ai.ps1 과 같다).
    """
    if os.name == "nt":
        return path
    home = Path.home()
    extra = [str(home / d) for d in (".local/bin", ".grok/bin", ".opencode/bin", ".npm-global/bin") if (home / d).is_dir()]
    nvm = home / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        versions = sorted((p for p in nvm.glob("v*") if (p / "bin").is_dir()), key=lambda p: _version_key(p.name), reverse=True)
        extra += [str(p / "bin") for p in versions]
    extra += [d for d in ("/opt/homebrew/bin", "/usr/local/bin") if os.path.isdir(d)]
    return os.pathsep.join(extra + [path]) if extra else path


@contextlib.contextmanager
def _env_path():
    before = os.environ.get("PATH", "")
    os.environ["PATH"] = user_path(before)
    try:
        yield
    finally:
        os.environ["PATH"] = before


def _rows():
    """update-ai --check 의 표를 구조로. [{id, method, version, latest, state}]"""
    u = tools()
    rows, table = [], None
    scratch = tempfile.mkdtemp(prefix="jarvis-harness-")
    try:
        catalog = json.loads(Path(u.CATALOG).read_text(encoding="utf-8"))
        with _env_path(), contextlib.redirect_stdout(sys.stderr):
            for entry in catalog:
                found = u.locations(entry["detect"]["bin"])
                if not found:
                    continue
                path = found[0]
                method, value = u.method_for(entry, path)
                before = _version(u, path, scratch)
                latest = u.latest_of(entry, path, method, value)
                # 설치본 판을 모르면 갱신 여부도 모른다(계획하지 않는다)
                state = "unknown" if not latest or not before else ("current" if u.is_current(before, latest) else "outdated")
                rows.append({"id": entry["id"], "method": method, "version": before, "latest": latest or None, "state": state})
                if entry["id"] == "codex":
                    if table is None:
                        table = u.process_table()
                    # 데몬은 설치된 CLI 에 맞춘다(update-ai 가 --from-cli 로 고정한다). 최신 릴리스와 비교하면 CLI 만
                    # 뒤처져도 데몬 재시작이 계획에 끼어든다
                    daemon_rows = u.codex_check_rows(path, before or latest, table)
                    managed = any(name == "codex-daemon" for name, *_ in daemon_rows)
                    unmanaged = u.codex_unmanaged_servers(entry, table)
                    if unmanaged:
                        # 고아로 남은 관리되지 않는 app-server 는 codex 갱신을 막는다. 승인된 재시작이 그것을 멈추고 관리형 데몬으로
                        # 바꾼다(update-ai --allow-interrupt). 그 서버가 제어 소켓을 잡고 있으면 데몬 행은 그 서버의 판이라 뺀다
                        daemon_rows = [r for r in daemon_rows if r[0] != "codex-server"]
                        rows.append({"id": "codex-server", "method": "codex daemon", "version": None, "latest": before or latest,
                                     "state": "outdated", "unmanaged": unmanaged})
                    for name, st, have, want in daemon_rows:
                        # update-ai 처럼 데몬이 CLI 보다 새로우면 그대로 둔다(내리지 않는다). 관리형 설치가 없으면
                        # 실행 중 서버는 다른 것이 띄운 것이라 update-ai 가 맞출 수 없다: 보고만 한다
                        current = st == "up to date" or (have and want and u.is_current(have, want))
                        rows.append({"id": name, "method": "codex daemon", "version": have, "latest": want,
                                     "state": "current" if current else "outdated" if managed else "unknown"})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return rows


def _version(u, path, scratch):
    """update-ai 의 version_of 와 같지만 먼저 doctor 처럼 홈·설정·캐시를 임시 폴더로 돌려 부른다
    (gemini·opencode 는 --version 만 불러도 홈에 상태 파일을 쓴다). 그 홈에서 판을 내지 못하는 것(래퍼를 거치는
    codex, 설정이 있어야 도는 grok)은 update-ai 와 같은 실제 홈으로 다시 읽는다. 판정이 update-ai 와 갈리면
    계획한 갱신이 끝나도 실패로 남기 때문이다."""
    from ..doctor.contract import tools as doctor
    out = u.run_quiet([path, "--version"], timeout=30, env=doctor().probe_env(scratch))
    match = out and out.returncode == 0 and u.VERSION_RE.search((out.stdout or "") + (out.stderr or ""))
    return match.group(0).rstrip(".") if match else u.version_of(path)


def status():
    rows = _rows()
    outdated = [r for r in rows if r["state"] == "outdated"]
    return {"module": "harness", "installed": bool(rows), "owned": False, "version": None, "latest": None,
            "health": "degraded" if outdated else "ok", "details": {"tools": rows}}


def _plan(rows):
    steps = []
    unmanaged = next((r["unmanaged"] for r in rows if r.get("unmanaged")), None)
    for r in rows:
        if r["state"] != "outdated":
            continue
        if r["id"] == "codex" and unmanaged:
            continue  # 혼자서는 관리되지 않는 app-server 에 막힌다. 아래 데몬 단계가 codex 도 함께 올린다
        if unmanaged and r["id"] in ("codex-daemon", "codex-server"):
            if not any(s["id"] == "codex-daemon" for s in steps):
                steps.append({"id": "codex-daemon", "risk": "config", "target": r["latest"],
                              "summary": f"관리되지 않는 codex app-server(PID {' '.join(map(str, unmanaged))})를 멈추고 관리형 데몬으로 교체, codex 도 갱신 (재시작)",
                              "argv": ["update-ai", "--only", "codex", "--allow-interrupt"]})
            continue
        if r["id"] in ("codex-daemon", "codex-server"):
            # 관리형 데몬 교체는 돌고 있는 Codex 작업을 끊을 수 있다(update-ai 의 --allow-interrupt). 승인이 필요하다
            if any(s["id"] == "codex-daemon" for s in steps):
                continue
            steps.append({"id": "codex-daemon", "summary": f"codex 관리형 데몬 {r['version']} → {r['latest']} (재시작)",
                          "risk": "config", "argv": ["update-ai", "--only", "codex", "--allow-interrupt"], "target": r["latest"]})
            continue
        steps.append({"id": r["id"], "summary": f"{r['id']} {r['version'] or '?'} → {r['latest']} ({r['method']})",
                      "risk": "update", "argv": ["update-ai", "--only", r["id"]], "target": r["latest"]})
    head = {"module": "harness", "device": socket.gethostname(), "action": "update", "blocked": []}
    # 하네스 하나만 올리는 승인: 단계마다 그 단계만 담은 계획의 plan_id. apply 는 전체 또는 이 값을 받는다
    for st in steps:
        st["plan_id"] = module.plan_id({**head, "steps": [st]})
    return module.seal({**head, "steps": steps})


def plan():
    return _plan(_rows())


# update-ai 결과 표의 실패 줄: "  x <id>  <판>  (<이유>)". 이유는 화면에 그대로 보인다(종료 코드만으로는 왜 실패했는지 모른다)
FAILED = re.compile(r"^\s*x\s+(\S+)\s+\S+\s+\((.+)\)\s*$", re.M)
HELD = "failed: deferred (in use)"


def _update(ids, allow_interrupt, timeout=TIMEOUT):
    """update_ai.py --only ids. (종료 코드, {실패한 대상: update-ai 가 적은 이유}). 출력은 진행 로그라 표준 오류로 옮긴다.
    시간이 넘으면 프로세스 그룹째 끊는다(남은 update-ai 가 잠금을 쥐고 다음 실행을 막지 않게)."""
    argv = [sys.executable, "-X", "utf8", UPDATE_AI, "--only", ",".join(ids)] + (["--allow-interrupt"] if allow_interrupt else [])
    env = dict(os.environ, PATH=user_path(os.environ.get("PATH", "")))
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=os.name != "nt")
        try:
            code = proc.wait(timeout=max(1, timeout))
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            code = 124
        out.seek(0)
        text = out.read().decode("utf-8", "replace")
    sys.stderr.write(text)
    return code, dict(FAILED.findall(text))


def _explain(reason, have):
    """update-ai 의 실패 이유. 받았는데 판이 그대로면(latest is …) PATH 에서 먼저 잡히는 다른 사본·래퍼를 의심하게 한다."""
    if reason.startswith("failed: latest is"):
        return f"갱신은 끝났지만 PATH 의 실행 파일이 아직 {have or '?'} 입니다. 먼저 잡히는 다른 사본이나 래퍼를 확인하세요 · update-ai: {reason}"
    return f"update-ai: {reason}"


def apply(plan_id):
    current = plan()
    if current["plan_id"] == plan_id:
        chosen = current["steps"]
    else:
        chosen = [s for s in current["steps"] if s["plan_id"] == plan_id]
        if not chosen:
            return {"status": "stale", "plan_id": current["plan_id"], "steps": []}
    if not chosen:
        return {"status": "current", "steps": []}
    codes, why = [], {}
    deadline = time.monotonic() + TIMEOUT  # 두 번 부르더라도 합이 게이트웨이의 apply 제한 안에 들게 한다
    # 승인한 데몬 재시작만 --allow-interrupt 로 따로 돌린다. 이 플래그는 update-ai 실행 전체에 걸리므로
    # 함께 넘기면 세션이 돌 때 보류해야 할 npm -g·winget 대상까지 바꾼다
    if any(s["id"] == "codex-daemon" for s in chosen):
        code, failed = _update(["codex"], True, deadline - time.monotonic())
        codes.append(code)
        why.update(failed)
    rest = [s["id"] for s in chosen if s["id"] != "codex-daemon" and not (s["id"] == "codex" and codes)]
    if rest:
        code, failed = _update(rest, False, deadline - time.monotonic())
        codes.append(code)
        why.update(failed)
    after = {r["id"]: r for r in _rows()}  # 결과는 다시 읽은 버전으로 판정한다(update-ai 의 표에 기대지 않는다)
    results = []
    for s in chosen:
        rows = [after[k] for k in (("codex-daemon", "codex-server") if s["id"] == "codex-daemon" else (s["id"],)) if k in after]
        ok = bool(rows) and all(r["state"] == "current" for r in rows)
        reason = why.get("codex" if s["id"] == "codex-daemon" else s["id"])
        held = not ok and reason == HELD
        have = rows[0].get("version") if rows else None
        results.append({"id": s["id"], "result": "ok" if ok else "deferred" if held else "failed",
                        "message": f"지금 {have or '?'}" + ("" if ok else " (세션이 돌고 있어 보류)" if held
                                                           else f" ({_explain(reason, have)})" if reason
                                                           else f" (update-ai 종료 코드 {', '.join(map(str, codes))})")})
    kinds = {r["result"] for r in results}
    return {"status": "failed" if "failed" in kinds else "deferred" if "deferred" in kinds else "done", "steps": results}


def uninstall(dry_run=False, plan_id=None):
    """하네스 제거는 setup 모듈의 uninstall 이 영수증에 기록한 것만 한다. 이 모듈은 지울 것이 없다."""
    current = module.seal({"module": "harness", "device": socket.gethostname(), "action": "uninstall", "steps": [], "blocked": []})
    if not dry_run and plan_id != current["plan_id"]:
        return {"status": "stale", "plan_id": current["plan_id"], "steps": []}
    return {"status": "done", "plan_id": current["plan_id"], "steps": []}
