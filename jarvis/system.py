"""호스트 자원: CPU·GPU·메모리·온도·팬·전력·디스크·네트워크·프로세스. psutil 로 읽고, 운영체제 전용은 platform/,
기기 전용(Raspberry Pi·Apple Silicon)은 hw/ 에서 채운다."""

import os
import platform as _stdplatform
import re
import socket
import sys
import time
from types import SimpleNamespace

import psutil

from . import platform
from .hw import macmon, rpi
from .util import Rate, is_tailnet_ip

try:
    import pwd
except ImportError:  # Windows
    pwd = None

PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
# psutil 의 swap sin 은 Linux 에서 pswpin × 4 KiB(고정)다. 4 KiB 로 나눠 '페이지/초'(pswpin 과 같은 값)로 되돌린다.
# macOS 는 pageins × 실제 페이지 크기(파일 매핑 포함이라 근삿값).
# Windows 는 num_threads·status 가 프로세스마다 시스템 전체 목록을 다시 떠서(프로세스 500개에 각 1.7초) 뺀다.
# status 는 Windows 에서 사실상 늘 running 이다. uids 는 Windows 에 없다.
PROC_ATTRS = ["name", "cpu_times", "memory_info"] + ([] if sys.platform == "win32" else ["num_threads", "status", "uids"])
SWAP_UNIT = 4096 if sys.platform.startswith("linux") else PAGE


def iface_kind(name):
    if name in ("lo", "lo0"):
        return "loopback"
    if name.startswith(("eth", "en")):
        return "wired"
    if name.startswith(("wlan", "wl")):
        return "wifi"
    if name.startswith("tailscale"):
        return "tailnet"
    if name.startswith(("docker", "br-")):
        return "docker"
    if name.startswith("veth"):
        return "veth"
    return "other"


def _cpu_times():
    """{"cpu": (total, idle+iowait, iowait), "cpu0": …}. /proc/stat 의 앞 8칸 합과 같게 guest 는 뺀다(user 에 포함돼 있다)."""
    def one(t):
        total = sum(t) - getattr(t, "guest", 0) - getattr(t, "guest_nice", 0)
        iow = getattr(t, "iowait", 0)
        return total, t.idle + iow, iow
    out = {"cpu": one(psutil.cpu_times())}
    for i, t in enumerate(psutil.cpu_times(percpu=True)):
        out[f"cpu{i}"] = one(t)
    return out


def _disk_counters():
    try:
        d = psutil.disk_io_counters(perdisk=True) or {}
    except (RuntimeError, OSError):
        return {}
    return {k: (v.read_bytes, v.write_bytes) for k, v in d.items() if platform.disk_io_ok(k)}


_KW = (r"token|secret|pass(?:w(?:or)?d)?|pwd|api[_-]?key|private[_-]?key|access[_-]?key|auth\w*|credential|"
       r"cookie|jwt|pat|nonce|requirepass|key|hmac|sign(?:ing|ature)|session|pin")
# 키 이름은 플래그(-x, --xxx)이거나 key=value 형태일 때만 인정한다(값에 'Secret' 이 들어 있다고 그 값을 키로 오인하지 않게).
_FLAG_RE = re.compile(rf"(\s--?[\w.-]*(?:{_KW})[\w.-]*)(=|\s+)(\"[^\"]*\"|'[^']*'|\S+)", re.I)
_ASSIGN_RE = re.compile(rf"((?:^|[\s,;{{\"'])[\w.-]*(?:{_KW})[\w.-]*[\"']?)(=|:\s*)([\"']?[^\s,;\"'}}]+)", re.I)
_URLCRED_RE = re.compile(r"(\w+://[^/\s:@]+:)[^@\s]+@")
_BEARER_RE = re.compile(r"((?:Bearer|Basic)\s+)\S+", re.I)
_USERPW_RE = re.compile(r"(\s-u\s+[^\s:]+:)\S+")
_SSHPASS_RE = re.compile(r"(sshpass\s+-p\s*)\S+")
# 비밀번호를 -p 로 받는 DB·원격 CLI(psql 의 -p 는 포트라 빼고, redis-cli 는 -a).
_DBPW_RE = re.compile(r"((?:^|/|\s)(?:mysql|mysqldump|mysqladmin|mariadb|mariadb-dump)\b.*?\s-p)(\s*)(\S+)")
_REDISPW_RE = re.compile(r"((?:^|/|\s)redis-cli\b.*?\s-a)(\s+)(\S+)")
_URLPATH_RE = re.compile(r"(https?://[^\s/]+)(/[^\s?#]*)")
_OPAQUE_RE = re.compile(r"[A-Za-z0-9_\-]{32,}")
# 32·64 자리 hex 는 흔한 비밀 길이라 가린다(커밋 SHA 40자리만 통과)
_BENIGN_ID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _mask_url_path(m):
    """웹훅 URL 경로의 토큰 조각(길고 영숫자가 섞인 세그먼트)을 가린다."""
    segs = m.group(2).split("/")
    out = ["***" if len(sg) >= 20 and re.search(r"[A-Za-z]", sg) and re.search(r"\d", sg) else sg for sg in segs]
    return m.group(1) + "/".join(out)


def _mask_opaque(m):
    s = m.group(0)
    return s if _BENIGN_ID_RE.fullmatch(s) else "***"  # 컨테이너 ID·커밋 SHA·UUID 는 비밀이 아니라 진단 정보다


def redact(cmd):
    """명령줄에 섞인 토큰·비밀번호 값을 가린다(표시용이라 조금 과하게 가려도 괜찮다)."""
    cmd = " " + cmd[:2048]  # 정규식이 긴 인자에서 다항 시간으로 느려진다. 표시용이라 앞부분이면 충분하다
    for rx, rep in ((_URLCRED_RE, r"\1***@"), (_BEARER_RE, r"\1***"), (_USERPW_RE, r"\1***"),
                    (_SSHPASS_RE, r"\1***"), (_DBPW_RE, r"\1\2***"), (_REDISPW_RE, r"\1\2***")):
        cmd = rx.sub(rep, cmd)
    cmd = _FLAG_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", cmd)
    cmd = _ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", cmd)
    cmd = _URLPATH_RE.sub(_mask_url_path, cmd)
    return _OPAQUE_RE.sub(_mask_opaque, cmd)[1:]


_uid_cache = {}


def _user(uid):
    if uid not in _uid_cache:
        try:
            _uid_cache[uid] = pwd.getpwuid(uid).pw_name
        except (KeyError, AttributeError):  # Windows 에는 pwd 가 없다
            _uid_cache[uid] = str(uid)
    return _uid_cache[uid]


class SystemCollector:
    def __init__(self):
        self.cpu_prev = None
        self.gpu = rpi.Gpu()
        self.rate = Rate()
        self.proc_prev = {}
        self.proc_prev_t = None
        self.rss_by_pid = {}
        self.mac = None  # 마지막 macmon 표본(10초마다)
        self.tailnet_ifaces = set()  # tailnet 주소를 가진 인터페이스(macOS 의 utunN 은 이름으로 알 수 없다)
        self.static = self._static()

    # ---------- 정적 정보 ----------
    def _static(self):
        u = os.uname() if hasattr(os, "uname") else _stdplatform.uname()
        try:
            f = psutil.cpu_freq()
        except (NotImplementedError, OSError, FileNotFoundError):
            f = None
        hw = rpi.static()
        return {
            "hostname": socket.gethostname(),
            "model": platform.host_model(),
            "os": platform.os_name(),
            "kernel": u.release,
            "arch": u.machine,
            "cores": os.cpu_count(),
            "cpu_max_mhz": round(f.max) if f and f.max else None,
            "cpu_min_mhz": round(f.min) if f and f.min else None,
            "gpu_name": hw["gpu_name"] or ("Apple GPU" if macmon.MACMON else None),
            "gpu_mem_mb": hw["gpu_mem_mb"],
            "page_size": PAGE,
        }

    # ---------- 2초 주기 ----------
    def fast(self):
        now = time.time()
        out = {"t": now}

        # CPU
        cur = _cpu_times()
        cpu = {"usage_pct": None, "per_core_pct": [], "iowait_pct": None}
        if self.cpu_prev:
            for key in sorted(cur, key=lambda k: (k != "cpu", int(k[3:] or -1))):
                if key not in self.cpu_prev:
                    continue
                dt = cur[key][0] - self.cpu_prev[key][0]
                di = cur[key][1] - self.cpu_prev[key][1]
                pct = round(max(0.0, min(100.0, (1 - di / dt) * 100)), 1) if dt > 0 else 0.0
                if key == "cpu":
                    cpu["usage_pct"] = pct
                    dio = cur[key][2] - self.cpu_prev[key][2]
                    cpu["iowait_pct"] = round(max(0.0, dio / dt * 100), 1) if dt > 0 else 0.0
                else:
                    cpu["per_core_pct"].append(pct)
        self.cpu_prev = cur
        freqs = platform.cpu_freqs()
        cpu["freq_mhz"] = round(sum(freqs) / len(freqs)) if freqs else None
        cpu["per_core_freq_mhz"] = freqs
        cpu["governor"] = platform.cpu_governor()
        try:
            cpu["load"] = [round(x, 2) for x in psutil.getloadavg()]  # Windows 는 psutil 이 성능 카운터로 흉내 낸다
        except (OSError, RuntimeError):  # 성능 카운터가 꺼진 Windows
            cpu["load"] = None
        cpu["procs_running"], cpu["threads_total"] = platform.run_queue()
        out["cpu"] = cpu

        gpu = self.gpu.sample()
        if not gpu["available"] and self.mac and self.mac.get("gpu_pct") is not None:
            gpu.update(usage_pct=self.mac["gpu_pct"], available=True)
        out["gpu"] = gpu

        # 메모리: 사용량은 total - available(캐시는 빼고 센다)
        vm = psutil.virtual_memory()
        try:
            sw = psutil.swap_memory()
        except RuntimeError:  # 성능 카운터가 꺼진 Windows: 스왑을 모른다(0 으로 둔다)
            sw = SimpleNamespace(total=0, used=0, free=0, percent=0.0, sin=None, sout=None)
        total, avail = vm.total, vm.available
        out["memory"] = {
            "total": total,
            "available": avail,
            "used": total - avail,
            "free": vm.free,
            "cached": getattr(vm, "cached", 0),
            "buffers": getattr(vm, "buffers", 0),
            "shared": getattr(vm, "shared", 0),
            "usage_pct": round((total - avail) / total * 100, 1) if total else None,
            "swap_total": sw.total,
            "swap_used": sw.total - sw.free,
            "swap_pct": round((sw.total - sw.free) / sw.total * 100, 1) if sw.total else 0.0,
        }
        # 스왑 인 속도(페이지/초): 스왑이 차 있어도 다시 읽어 들이지 않으면 메모리 압박이 아니다
        swin = self.rate.per_s("pswpin", sw.sin // SWAP_UNIT, now) if sw.sin is not None else None
        out["memory"]["swap_in_ps"] = round(swin, 1) if swin is not None else None

        # 온도·팬
        temp = platform.cpu_temp_c()
        if temp is None and self.mac:
            temp = self.mac.get("cpu_temp_c")
        fan = rpi.fan()
        if fan is None:
            try:
                f = next(iter((psutil.sensors_fans() or {}).values()), None)
            except (AttributeError, OSError):
                f = None
            fan = {"rpm": f[0].current, "pwm": None, "pct": None} if f else None
        out["thermal"] = {"cpu_temp_c": temp, "fan": fan}

        # 네트워크
        nets = {}
        agg = {"rx_bps": 0.0, "tx_bps": 0.0, "tailnet_rx_bps": 0.0, "tailnet_tx_bps": 0.0, "docker_rx_bps": 0.0,
               "docker_tx_bps": 0.0}
        counters = {k: (v.bytes_recv, v.bytes_sent) for k, v in psutil.net_io_counters(pernic=True).items()}
        disks = _disk_counters()
        for name, (rx, tx) in counters.items():
            kind = "tailnet" if name in self.tailnet_ifaces else iface_kind(name)
            rrx = self.rate.per_s(("rx", name), rx, now)
            rtx = self.rate.per_s(("tx", name), tx, now)
            if kind in ("wired", "wifi"):
                agg["rx_bps"] += rrx or 0
                agg["tx_bps"] += rtx or 0
            elif kind == "tailnet":
                agg["tailnet_rx_bps"] += rrx or 0
                agg["tailnet_tx_bps"] += rtx or 0
            elif kind == "docker":
                agg["docker_rx_bps"] += rrx or 0
                agg["docker_tx_bps"] += rtx or 0
            if kind in ("veth", "loopback"):
                continue
            nets[name] = {"name": name, "kind": kind, "rx_bps": rrx, "tx_bps": rtx, "rx_total": rx, "tx_total": tx}
        out["net"] = {"interfaces": nets, **{k: round(v, 1) for k, v in agg.items()}}
        self.rate.prune({("rx", n) for n in counters} | {("tx", n) for n in counters} |
                        {("dr", d) for d in disks} | {("dw", d) for d in disks} | {"pswpin"})

        # 디스크 I/O
        dio = {"read_bps": 0.0, "write_bps": 0.0, "devices": {}}
        for dev, (r, w) in disks.items():
            rr = self.rate.per_s(("dr", dev), r, now)
            rw = self.rate.per_s(("dw", dev), w, now)
            dio["devices"][dev] = {"read_bps": rr, "write_bps": rw}
            dio["read_bps"] += rr or 0
            dio["write_bps"] += rw or 0
        out["disk_io"] = dio

        out["uptime_s"] = round(now - psutil.boot_time(), 2)
        return out

    # ---------- 10초 주기 ----------
    def medium(self):
        out = rpi.medium()  # power·throttle·clocks_mhz·core_volts·pmic_temp_c (Pi 가 아니면 빈 값)
        self.mac = macmon.sample()
        if self.mac and out["power"]["total_w"] is None:
            out["power"]["total_w"] = self.mac.get("power_w")
        if self.mac and self.mac.get("gpu_mhz"):
            out["clocks_mhz"].setdefault("gpu", self.mac["gpu_mhz"])

        # 디스크 사용량. psutil 은 all=False 면 tmpfs 를 빼므로 전부 받아서 거른다(/tmp tmpfs 는 넣는다).
        disks, seen = [], set()
        for p in psutil.disk_partitions(all=True):
            if p.device in seen or not platform.disk_mount_ok(p.mountpoint, p.fstype, p.opts):
                continue
            seen.add(p.device)
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            if u.total <= 0:
                continue
            disks.append({
                "mount": p.mountpoint, "device": p.device, "fs": p.fstype, "total": u.total, "used": u.used,
                "free": u.free, "usage_pct": round(u.used / (u.used + u.free) * 100, 1) if u.used + u.free else None,
            })
        out["disks"] = disks

        # 인터페이스 주소·링크·Wi-Fi 신호
        addrs = platform.iface_addrs()
        for name, w in platform.wifi_signal().items():
            addrs.setdefault(name, {})["wifi"] = w
        out["iface_addrs"] = addrs
        self.tailnet_ifaces = {n for n, a in addrs.items() if any(is_tailnet_ip(ip) for ip in a.get("ipv4") or [])}
        out["routes_default"] = platform.default_routes()
        return out

    # ---------- 프로세스 (10초 주기) ----------
    def processes(self):
        now = time.time()
        cur, meta = {}, {}
        total_threads = 0
        for p in psutil.process_iter(PROC_ATTRS):
            i = p.info
            if i["cpu_times"] is None and i["memory_info"] is None:
                continue  # 권한이 없어 아무것도 못 읽은 프로세스(macOS 의 남의 프로세스)
            threads = (i["num_threads"] or 0) if "num_threads" in i else None  # Windows 는 읽지 않는다
            total_threads += threads or 0
            if i["cpu_times"] is not None:
                cur[p.pid] = i["cpu_times"].user + i["cpu_times"].system
            uid = i["uids"].effective if i.get("uids") else None
            meta[p.pid] = (i["name"] or "?", i["memory_info"].rss if i["memory_info"] else 0, threads, uid,
                           i.get("status"))
        dt = (now - self.proc_prev_t) if self.proc_prev_t else None
        rows = []
        for pid, (comm, rss, threads, uid, state) in meta.items():
            cpu = None
            if dt and pid in cur and pid in self.proc_prev:
                cpu = max(0.0, (cur[pid] - self.proc_prev[pid]) / dt * 100)
            rows.append((pid, comm, cpu, rss, threads, uid, state))
        self.proc_prev, self.proc_prev_t = cur, now
        self.rss_by_pid = {p[0]: p[3] for p in rows}

        def enrich(p):
            pid, comm, cpu, rss, threads, uid, state = p
            try:
                cmd = redact(" ".join(psutil.Process(pid).cmdline()).strip())
            except (psutil.Error, OSError):
                cmd = ""
            kind, name = platform.owner_of_pid(pid)
            return {
                "pid": pid, "name": comm, "cpu_pct": round(cpu, 1) if cpu is not None else None,
                "rss": rss, "threads": threads, "user": _user(uid) if uid is not None else None, "state": state,
                "cmd": cmd[:200] or f"[{comm}]", "owner_kind": kind, "owner": name,
            }

        top_cpu = sorted((r for r in rows if r[2] is not None), key=lambda r: -r[2])[:12]
        top_mem = sorted(rows, key=lambda r: -r[3])[:12]
        return {
            "count": len(rows),
            "threads": total_threads if "num_threads" in PROC_ATTRS else None,
            "top_cpu": [enrich(p) for p in top_cpu],
            "top_mem": [enrich(p) for p in top_mem],
        }
