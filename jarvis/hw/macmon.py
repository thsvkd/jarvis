"""Apple Silicon: macmon(https://github.com/vladkens/macmon, sudo 없이 동작)이 있으면 온도·GPU·전력을 읽는다.
없으면 None 을 돌려주고 화면은 그 칸을 숨긴다. 실동작은 Mac Studio 에서 한 번 확인해야 한다."""

import json
import shutil

from ..util import _ENV, run

MACMON = shutil.which("macmon", path=_ENV["PATH"])


def parse(line):
    """`macmon pipe -s 1` 한 줄(JSON) → {cpu_temp_c, gpu_temp_c, gpu_pct, gpu_mhz, power_w}"""
    try:
        d = json.loads((line or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    temp = d.get("temp") or {}
    gpu = d.get("gpu_usage") or [None, None]
    rnd = lambda v, n=1: round(v, n) if isinstance(v, (int, float)) else None  # noqa: E731
    return {
        "cpu_temp_c": rnd(temp.get("cpu_temp_avg")),
        "gpu_temp_c": rnd(temp.get("gpu_temp_avg")),
        "gpu_mhz": round(gpu[0]) if isinstance(gpu[0], (int, float)) else None,
        "gpu_pct": rnd(gpu[1] * 100) if isinstance(gpu[1], (int, float)) else None,
        "power_w": rnd(d.get("sys_power") or d.get("all_power"), 2),
    }


def sample():
    if not MACMON:
        return None
    rc, out, _ = run([MACMON, "pipe", "-s", "1", "-i", "500"], timeout=5)
    return parse(out) if rc == 0 else None
