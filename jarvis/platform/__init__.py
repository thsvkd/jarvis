"""운영체제별 구현을 고른다. 모든 모듈이 같은 이름의 함수를 내놓는다(common.py 가 기본값)."""

import sys

if sys.platform == "darwin":
    from .darwin import *  # noqa: F401,F403
elif sys.platform.startswith("linux"):
    from .linux import *  # noqa: F401,F403
else:
    from .common import *  # noqa: F401,F403
