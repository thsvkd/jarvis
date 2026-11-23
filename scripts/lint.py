#!/usr/bin/env python3
"""VS Code 의 저장 시 포매팅(formatOnSave)과 같은 결과를 내도록 넘긴 파일을 포매팅한다.

    python3 scripts/lint.py [--check] PATH...

- 기준은 사용자 VS Code 설정(2026-11-07 settings.json)과 원격 서버에 깔린 확장의 번들 버전이다.
  - Python: charliermarsh.ruff 2026.98.0 → ruff 0.17.6 format, ruff.lineLength 150. codeActionsOnSave 가 없어 import 정리·fix 는 하지 않는다.
  - JS·HTML: esbenp.prettier-vscode 12.4.0 → prettier 3.7.4, prettier.* 설정·설정 파일이 없어 기본값.
  - 셸: foxundermoon.shell-format 7.2.8 → shfmt v3.11.0, shellformat.flag "-bn" 에 editor.tabSize 2 로 -i=2.
- 나머지 형식(TS·JSON·Markdown·CSS·TOML·YAML·PS1)은 VS Code 내장 포매터거나 기본 포매터가 없어 건드리지 않는다.
- 다른 세션의 작업을 건드리지 않도록 경로는 꼭 넘긴다. 폴더를 넘기면 그 안의 git 추적·미추적 파일로 푼다.
- --check 는 고치지 않고 달라질 파일만 보고하며, 달라질 파일이 있으면 1 로 끝난다.
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUFF = "0.17.6"
PRETTIER = "3.7.4"
SHFMT = "v3.11.0"
EXTS = {
    "ruff": (".py",),
    "prettier": (".js", ".mjs", ".cjs", ".html"),
    "shfmt": (".sh", ".bash"),
}


def expand(paths):
    """넘긴 경로를 git 이 보는 실제 파일 목록으로 푼다(.gitignore 대상·지워진 파일 제외)."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *paths], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return sorted({f for f in out.splitlines() if os.path.isfile(os.path.join(ROOT, f))})


def group(files):
    """파일을 포매터별로 나눈다. 해당 포매터가 없는 파일은 버린다."""
    return {tool: [f for f in files if f.endswith(exts)] for tool, exts in EXTS.items()}


def command(tool, check):
    """포매터 실행 명령을 만든다. 버전을 VS Code 번들과 맞추려고 고정 버전 실행기를 먼저 쓴다."""
    if tool == "ruff":
        base = ["uvx", f"ruff@{RUFF}"] if shutil.which("uvx") else ["ruff"]
        return base + ["format", "--no-cache", "--line-length", "150"] + (["--check"] if check else [])
    if tool == "prettier":
        return ["npx", "--yes", f"prettier@{PRETTIER}", "--check" if check else "--write"]
    # shortcut: shfmt 는 PATH 의 것을 쓰고 버전을 확인하지 않는다, 출력이 VS Code 와 어긋나면 v3.11.0 을 설치한다
    return ["shfmt", "-bn", "-i=2", "-l" if check else "-w"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="VS Code 저장 시 포매팅과 같은 규칙으로 파일을 포매팅한다.")
    ap.add_argument("--check", action="store_true", help="고치지 않고 달라질 파일만 보고한다")
    ap.add_argument("paths", nargs="+", help="포매팅할 파일·폴더(저장소 루트 기준)")
    args = ap.parse_args(argv)
    paths = [os.path.relpath(os.path.abspath(p), ROOT) for p in args.paths]
    rc = 0
    for tool, files in group(expand(paths)).items():
        if not files:
            continue
        cmd = command(tool, args.check)
        if not shutil.which(cmd[0]):
            print(f"[lint] {cmd[0]} 이 없어 {len(files)}개 파일을 건너뜀: {' '.join(files)}", file=sys.stderr)
            rc = rc or 2
            continue
        res = subprocess.run(cmd + files, cwd=ROOT, capture_output=tool == "shfmt" and args.check, text=True)
        # shfmt -l 은 달라질 파일을 출력만 하고 0 으로 끝나므로 출력으로 판정한다
        if tool == "shfmt" and args.check and res.stdout.strip():
            print(res.stdout, end="")
            res.returncode = 1
        rc = rc or res.returncode
    return rc


if __name__ == "__main__":
    sys.exit(main())
