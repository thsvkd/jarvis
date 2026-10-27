"""서비스 관리자: Linux 는 systemd, macOS 는 launchd, Windows 는 아직 없음. 둘 다 같은 모양의 유닛 목록을 내놓는다."""

import sys

if sys.platform == "darwin":
    from .launchd import UnitCollector  # noqa: F401
elif sys.platform == "win32":
    class UnitCollector:  # ponytail: Windows 서비스(SCM) 목록은 아직 안 본다. 필요해지면 psutil.win_service_iter 로
        def collect(self, rss_by_pid):
            return []
else:
    from .systemd import UnitCollector  # noqa: F401

