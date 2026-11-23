#!/usr/bin/env python3
"""Makefile 의 타깃과 '## 설명' 을 목록으로 보여 준다(인자 없이 make 를 부르면 이것이 돈다).

python3 scripts/help.py
"""

import os
import re
import sys

from deps import ROOT


def targets(text):
    """'타깃: ## 설명' 줄을 (타깃, 설명) 목록으로."""
    return re.findall(r"^([\w-]+):[^#\n]*## (.+)$", text, re.M)


def main():
    with open(os.path.join(ROOT, "Makefile"), encoding="utf-8") as f:
        rows = targets(f.read())
    width = max(len(t) for t, _ in rows)
    print("사용법: make <타깃> [변수=값 ...]\n")
    for t, desc in rows:
        print(f"  {t.ljust(width)}  {desc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
