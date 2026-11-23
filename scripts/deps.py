#!/usr/bin/env python3
"""저장소의 .venv 에 Python 3.13 과 psutil 을 받는다(uv 필요). 다른 스크립트는 venv_python() 으로 이 venv 를 쓴다.

python3 scripts/deps.py
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(ROOT, ".venv")


def venv_python():
    """.venv 가 있으면 그 Python, 없으면 지금 실행 중인 Python(시스템 Python 에는 psutil 이 없을 수 있다)."""
    p = os.path.join(VENV, "Scripts", "python.exe") if os.name == "nt" else os.path.join(VENV, "bin", "python")
    return p if os.path.isfile(p) else sys.executable


def main():
    if not shutil.which("uv"):
        print("[deps] uv 가 없습니다: https://docs.astral.sh/uv/ 에서 설치한 뒤 다시 실행하세요", file=sys.stderr)
        return 2
    rc = subprocess.run(["uv", "venv", "--allow-existing", "--python", "3.13", VENV], cwd=ROOT).returncode
    return rc or subprocess.run(["uv", "pip", "install", "--python", venv_python(), "psutil"], cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
