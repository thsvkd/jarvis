#!/usr/bin/env python3
"""운영 Bento의 마크업을 그대로 쓰고 디자인 랩용 로더만 붙인다."""
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parents[1]
source = (root / "static/index.html").read_text()
# 실험용 배치는 각 시안이 맡는다. 운영 Focus 부트스트랩은 제외한다.
source = source.replace('<script src="/app/focus-layout.js"></script>\n', "")
source = source.replace('<script src="/app/focus.js"></script>\n', "")
source = source.replace('<link rel="stylesheet" href="/app/focus.css">\n', "")
source = source.replace("<title>개요 · Jarvis</title>", "<title>콘텐츠 구성 시안 · Jarvis</title>")
source = source.replace('<link rel="stylesheet" href="/app/bento.css">',
                        '<link rel="stylesheet" href="/app/bento.css">\n<link rel="stylesheet" href="/lab/proposal/proposal.css">\n<link rel="stylesheet" href="/app/focus.css">')
source = source.replace('<script src="/shared/jarvis.js"></script>',
                        '<script src="/shared/jarvis.js"></script>\n<script src="/lab/concepts.js"></script>\n<script src="/lab/session.js"></script>\n<script src="/lab/proposal/bridge.js"></script>')
source = source.replace('<main id="main" class="wrap">',
                        '<main id="main" class="wrap">\n<div class="lab-note"><a href="/lab/">← 시안 갤러리</a><strong id="lab-name"></strong><span id="lab-intent"></span></div>')
source = source.replace('<script src="/app/bento.js"></script>',
                        '<script src="/lab/proposal/proposal.js"></script>\n<script src="/app/focus.js"></script>\n<script src="/app/bento.js"></script>')
target = root / "static/lab/proposal/index.html"
if "--check" in sys.argv:
    if target.read_text() != source:
        raise SystemExit("Lab markup is stale: run python3 tools/build-lab.py")
    print("Lab markup matches the production Bento template")
else:
    target.write_text(source)
    print("Generated", target.relative_to(root))
