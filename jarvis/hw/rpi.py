"""Raspberry Pi 전용: vcgencmd(PMIC 전력·스로틀링·클럭·전압·온도), V3D GPU, 쿨링 팬.
Pi 가 아니면 AVAILABLE 이 False 이고 모든 함수가 빈 값을 돌려준다."""

import glob
import re
import shutil

from ..util import _ENV, read, read_int, run

GPU_QUEUES = ("bin", "render", "tfu", "csd", "cache_clean", "cpu")
THROTTLE_BITS = [
    ("under_voltage", "저전압", 0, 16),
    ("freq_capped", "ARM 클럭 제한", 1, 17),
    ("throttled", "스로틀링", 2, 18),
    ("soft_temp_limit", "온도 소프트 리밋", 3, 19),
]


def _gpu_stats_path():
    for pat in ("/sys/devices/platform/axi/*.v3d/gpu_stats", "/sys/devices/platform/*.v3d/gpu_stats"):
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return None


GPU_PATH = _gpu_stats_path()
VCGENCMD = shutil.which("vcgencmd", path=_ENV["PATH"])
AVAILABLE = bool(GPU_PATH or VCGENCMD)


def static():
    rc, gpu_mem, _ = run(["vcgencmd", "get_mem", "gpu"]) if VCGENCMD else (-1, "", "")
    m = re.search(r"(\d+)M", gpu_mem or "")
    return {"gpu_name": "VideoCore VII (V3D)" if GPU_PATH else None, "gpu_mem_mb": int(m.group(1)) if m else None}


class Gpu:
    """V3D 큐별 누적 runtime(ns) / 타임스탬프(ns) 의 변화량."""

    def __init__(self):
        self.prev = None

    def sample(self):
        gpu = {"usage_pct": None, "queues": {}, "jobs_per_s": None, "available": bool(GPU_PATH)}
        if not GPU_PATH:
            return gpu
        rows, ts = {}, None
        for line in (read(GPU_PATH, "") or "").splitlines()[1:]:
            p = line.split()
            if len(p) == 4 and p[1].isdigit():
                rows[p[0]] = (int(p[3]), int(p[2]))
                ts = int(p[1])
        if self.prev and ts and ts > self.prev[0]:
            dts = ts - self.prev[0]
            jobs = 0
            for q, (rt, jb) in rows.items():
                prt, pjb = self.prev[1].get(q, (rt, jb))
                gpu["queues"][q] = round(max(0.0, min(100.0, (rt - prt) / dts * 100)), 1)
                jobs += max(0, jb - pjb)
            gpu["usage_pct"] = max(gpu["queues"].values()) if gpu["queues"] else 0.0
            gpu["jobs_per_s"] = round(jobs / (dts / 1e9), 1)
        self.prev = (ts, rows) if ts else self.prev
        return gpu


def fan():
    out = None
    for d in glob.glob("/sys/devices/platform/cooling_fan/hwmon/hwmon*"):
        rpm = read_int(d + "/fan1_input")
        pwm = read_int(d + "/pwm1")
        out = {"rpm": rpm, "pwm": pwm, "pct": round(pwm / 255 * 100) if pwm is not None else None}
    return out


def parse_pmic_adc(adc):
    """`vcgencmd pmic_read_adc` → 레일별 V×A. 5V 입력 손실은 빠지므로 '보드 추정 전력'이다."""
    amps, volts = {}, {}
    for line in (adc or "").splitlines():
        m = re.match(r"\s*(\S+)_([AV])\s+\w+\(\d+\)=([\d.]+)", line)
        if m:
            (amps if m.group(2) == "A" else volts)[m.group(1)] = float(m.group(3))
    rails = []
    for name, a in amps.items():
        v = volts.get(name)
        if v is not None:
            rails.append({"name": name, "volts": round(v, 3), "amps": round(a, 4), "watts": round(v * a, 3)})
    rails.sort(key=lambda r: -r["watts"])
    return {
        "total_w": round(sum(r["watts"] for r in rails), 2) if rails else None,
        "input_v": volts.get("EXT5V"),
        "rails": rails,
    }


def parse_throttled(s):
    m = re.search(r"0x([0-9a-fA-F]+)", s or "")
    if not m:
        return None
    raw = int(m.group(1), 16)
    now_flags = [label for key, label, bn, _ in THROTTLE_BITS if raw >> bn & 1]
    past_flags = [label for key, label, _, bp in THROTTLE_BITS if raw >> bp & 1]
    return {"raw": hex(raw), "ok": raw == 0, "now": now_flags, "since_boot": past_flags}


def medium():
    """전력·스로틀링·클럭·코어 전압·PMIC 온도. Pi 가 아니면 모두 빈 값."""
    out = {"power": {"total_w": None, "input_v": None, "rails": []}, "throttle": None, "clocks_mhz": {},
           "core_volts": None, "pmic_temp_c": None}
    if not VCGENCMD:
        return out
    out["power"] = parse_pmic_adc(run(["vcgencmd", "pmic_read_adc"])[1])
    out["throttle"] = parse_throttled(run(["vcgencmd", "get_throttled"])[1])
    for name in ("arm", "core", "v3d", "isp", "hevc"):
        m = re.search(r"=(\d+)", run(["vcgencmd", "measure_clock", name])[1] or "")
        if m:
            out["clocks_mhz"][name] = round(int(m.group(1)) / 1e6)
    m = re.search(r"([\d.]+)V", run(["vcgencmd", "measure_volts", "core"])[1] or "")
    out["core_volts"] = float(m.group(1)) if m else None
    m = re.search(r"([\d.]+)'C", run(["vcgencmd", "measure_temp", "pmic"])[1] or "")
    out["pmic_temp_c"] = float(m.group(1)) if m else None
    return out
