"""훅이 '설치 대상 머신에 실제로 있는 python3' 에서 실행되는지 검증한다.

설치 스크립트는 훅 command 에 절대 경로가 아니라 `python3` 를 그대로 박는다. 그래서 훅은
Claude Code 가 PATH 에서 찾아내는 아무 python3 에서나 돌아야 한다. macOS 는 여전히
/usr/bin/python3 = 3.9.x 를 기본으로 제공한다.

훅이 예외로 죽으면 Claude Code 는 가드 없이 명령을 실행한다(fail-open). 즉 3.10 전용 문법을
하나만 써도 가드 전체가 조용히 무력화된다 — 테스트가 최신 인터프리터로만 돌면 절대 안 잡힌다.
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

HOOKS_DIR = Path(__file__).resolve().parent.parent / "jarvis" / "modules" / "hooks"
HOOKS = sorted(HOOKS_DIR.glob("*.py"))

# 훅이 지원해야 하는 최저 인터프리터. macOS Sonoma~Sequoia 의 /usr/bin/python3 가 3.9.x 다.
MIN_SUPPORTED = (3, 9)


def _has_future_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _pep604_annotations(tree: ast.Module) -> list:
    """`X | None` 처럼 3.10+ 에서만 런타임 평가되는 애노테이션을 모은다."""
    found = []
    for node in ast.walk(tree):
        annotations = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations = [a.annotation for a in node.args.args if a.annotation]
            annotations += [a.annotation for a in node.args.kwonlyargs if a.annotation]
            if node.returns:
                annotations.append(node.returns)
        elif isinstance(node, ast.AnnAssign) and node.annotation:
            annotations = [node.annotation]
        for annotation in annotations:
            for sub in ast.walk(annotation):
                if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                    found.append(ast.dump(annotation))
    return found


@pytest.mark.parametrize("hook", HOOKS, ids=lambda p: p.name)
def test_pep604_annotations_are_deferred(hook):
    """PEP 604 (`X | None`) 를 쓰려면 `from __future__ import annotations` 가 있어야 한다.

    없으면 애노테이션이 정의 시점에 평가되어 Python 3.9 에서 TypeError 로 죽는다.
    """
    tree = ast.parse(hook.read_text(encoding="utf-8"))
    offenders = _pep604_annotations(tree)
    if offenders:
        assert _has_future_annotations(tree), (
            f"{hook.name} uses PEP 604 annotations "
            f"without `from __future__ import annotations` -> it crashes on Python 3.9: {offenders[0]}"
        )


def _legacy_interpreter():
    """3.10 미만의 python3 를 찾는다 (없으면 None)."""
    for candidate in ("/usr/bin/python3", "python3.9", "python3.8"):
        try:
            out = subprocess.run(
                [candidate, "-c", "import sys; print('%d %d' % sys.version_info[:2])"],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode != 0:
            continue
        major, minor = (int(x) for x in out.stdout.split())
        if (major, minor) >= MIN_SUPPORTED and (major, minor) < (3, 10):
            return candidate
    return None


@pytest.mark.parametrize("hook", HOOKS, ids=lambda p: p.name)
def test_hook_runs_on_legacy_interpreter(hook):
    """실제 구버전 인터프리터가 있으면 그것으로 훅을 돌려본다 (macOS 의 /usr/bin/python3)."""
    interpreter = _legacy_interpreter()
    if interpreter is None:
        pytest.skip(f"no 3.9.x python3 found (need >= {MIN_SUPPORTED} and < 3.10)")

    if hook.name in ("merge_and_diff.py", "send_to_trash.py"):
        pytest.skip(f"{hook.name} does not use the stdin hook protocol (argument-based)")

    event = {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}
    proc = subprocess.run(
        [interpreter, "-X", "utf8", str(hook)],
        input=json.dumps(event), capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, f"{hook.name} crashed on {interpreter}:\n{proc.stderr}"
    assert "Traceback" not in proc.stderr, f"{hook.name} raised on {interpreter}:\n{proc.stderr}"


@pytest.mark.parametrize("hook", HOOKS, ids=lambda p: p.name)
def test_hook_compiles_under_current_interpreter(hook):
    subprocess.run([sys.executable, "-m", "py_compile", str(hook)], check=True, capture_output=True)


# --- 회귀: stdin 선행 BOM 을 먹어도 판정을 내야 한다 ---
#
# Windows PowerShell 5.1 의 Process.StandardInput 은 AutoFlush StreamWriter 라 스트림에
# 접근하는 순간 인코딩 preamble(UTF-8 BOM)을 먼저 흘려보낸다. .NET Framework 에는
# StandardInputEncoding 이 없어 호출부에서 끌 수 없다. BOM 이 남으면 json.loads 가 실패하고
# 훅은 '판정할 수 없는 입력'으로 보고 조용히 exit 0 한다 — 가드가 통째로 무력화되는데 아무
# 신호도 남지 않는다. 실제로 설치 셀프테스트가 이 상태로 판정을 한 줄도 못 내고 있었다.

BOM = "\ufeff"


@pytest.mark.parametrize("hook,event,expected", [
    ("danger_guard.py",
     {"tool_name": "PowerShell", "tool_input": {"command": "Remove-Item -Recurse -Force C:\\"}},
     "deny"),
    ("danger_guard.py",
     {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
     "deny"),
])
def test_hooks_tolerate_leading_bom(hook, event, expected):
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(HOOKS_DIR / hook)],
        input=BOM + json.dumps(event),
        text=True, capture_output=True, encoding="utf-8",
    )
    out = (proc.stdout or "").strip()
    assert out, f"{hook}: must not skip the verdict because a BOM is present (stderr={proc.stderr!r})"
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == expected


load_tests = collect(globals())
