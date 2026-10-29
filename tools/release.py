#!/usr/bin/env python3
"""jarvis 릴리스: jarvis/__init__.py 의 __version__ 으로 태그 v<버전> 을 달고, 소스 묶음과 manifest.json 을 GitHub 릴리스에 올린다.
기기는 core 의 'jarvis 업데이트' 버튼(또는 python -m jarvis update)으로 이 릴리스를 받는다(jarvis/self_update.py).

    python3 tools/release.py          # main 의 커밋된 HEAD 를 릴리스한다(gh 로그인 필요)
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from jarvis import __version__ as V  # noqa: E402
from jarvis.self_update import REPO, VER_RE  # noqa: E402


def sh(*cmd, out=False):
    r = subprocess.run(cmd, cwd=ROOT, check=True, text=True, capture_output=out)
    return r.stdout.strip() if out else None


def main():
    tag = "v" + V
    if not VER_RE.match(V):
        sys.exit(f"__version__ 형식이 다릅니다: {V}")
    if sh("git", "symbolic-ref", "--short", "HEAD", out=True) != "main":
        sys.exit("main 브랜치에서만 릴리스합니다")
    if sh("git", "status", "--porcelain", "--untracked-files=no", out=True):
        sys.exit("커밋 안 된 수정이 있습니다")
    if subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/tags/" + tag], cwd=ROOT, capture_output=True).returncode == 0:
        sys.exit(f"{tag} 태그가 이미 있습니다. jarvis/__init__.py 의 __version__ 을 올리세요")
    with tempfile.TemporaryDirectory() as d:
        name = f"jarvis-{V}.tar.gz"
        tgz = os.path.join(d, name)
        sh("git", "archive", "--format=tar.gz", f"--prefix=jarvis-{V}/", "-o", tgz, "HEAD")
        with open(tgz, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        manifest = os.path.join(d, "manifest.json")
        with open(manifest, "w") as f:
            json.dump({"version": V, "url": f"https://github.com/{REPO}/releases/download/{tag}/{name}", "sha256": digest}, f)
        # 태그는 gh 가 릴리스와 함께 원격에 만든다: 중간에 실패해도 지울 로컬 태그가 남지 않는다
        sh("git", "push", "origin", "main")
        sh("gh", "release", "create", tag, tgz, manifest, "--repo", REPO, "--target", sh("git", "rev-parse", "HEAD", out=True),
           "--title", tag, "--generate-notes", "--latest")
        sh("git", "fetch", "-q", "origin", "tag", tag)
    print(f"{tag} 릴리스를 올렸습니다 (sha256 {digest[:12]}…)")


if __name__ == "__main__":
    main()
