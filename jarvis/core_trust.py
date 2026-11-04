"""core 전환: 게이트웨이 설정의 최상위 `core` 한 키만 고치는 로컬 CLI(docs/SPEC.md 의 "core 전환").

    python -m jarvis core-trust plan  [--add <IP> | --remove <IP>] --json
    python -m jarvis core-trust apply (--add <IP> | --remove <IP>) --plan-id <sha256> --json

core 는 셸 권한급 신뢰라서 바꾸는 권한은 그 기기의 셸 사용자에게만 둔다: HTTP 경로·화면 버튼·module.MODULES 에는 없다.
proxy_key 값은 어디에도 내지 않는다. 출력하는 해시도 그 줄을 고정 문자열로 바꾼 바이트로 계산한다(키 추측 오라클 방지).
"""

import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import sys
import tempfile

from . import module
from .util import TAILNET_V4, run

USAGE = ("사용법: python -m jarvis core-trust plan [--add <IP> | --remove <IP>] --json\n"
         "        python -m jarvis core-trust apply (--add <IP> | --remove <IP>) --plan-id <sha256> --json")
_KEY = re.compile(rb"""^\s*["']?proxy_key["']?\s*=""")
_CORE = re.compile(rb"""^\s*["']?core["']?\s*=""")
_TABLE = re.compile(rb"^\s*\[")


def config_path():
    return os.environ.get("JARVIS_CONFIG") or os.path.expanduser("~/.config/jarvis/config.toml")


def valid(x):
    """IPv4 tailnet 정규형인가. 서버는 IPv4 로만 bind 하고 _trusted 는 문자열이 정확히 같아야 통과시킨다."""
    try:
        ip = ipaddress.ip_address(x)
    except ValueError:
        return False
    # str(ip) == x 는 방어용이다: Python 3.11+ 의 ip_address 는 비정규 표기를 이미 ValueError 로 거절한다
    return ip.version == 4 and ip in TAILNET_V4 and str(ip) == x


def _eol(line):
    return line[len(line.rstrip(b"\r\n")):]


def _parses(chunk):
    try:
        import tomllib
        tomllib.loads(chunk.decode("utf-8"))
        return True
    except (ImportError, ValueError):
        return False


def _masked(data):
    """proxy_key 값을 고정 문자열로 바꾼 바이트. 키의 존재·위치만 반영하고 값은 반영하지 않는다.
    여러 줄 문자열 값이면 그 줄부터 따로 파싱되는 곳까지 가린다(못 찾으면 파일 끝까지)."""
    lines, out, i = data.splitlines(keepends=True), [], 0
    while i < len(lines):
        j = i
        if _KEY.match(lines[i]):
            while j + 1 < len(lines) and not _parses(b"".join(lines[i:j + 1])):
                j += 1
            out.append("proxy_key=<숨김>".encode() + _eol(lines[j]))
        else:
            out.append(lines[i])
        i = j + 1
    return b"".join(out)


def _items(v):
    """config.env_list 와 같은 해석(배열 또는 쉼표 문자열). 그 밖의 형태면 None."""
    if v is None:
        return []
    if not isinstance(v, (str, list)):
        return None
    items = v.split(",") if isinstance(v, str) else v
    return [str(x).strip() for x in items if str(x).strip()]


def _host(ip):
    """tailscale status --json 의 Self·Peer 에서 그 IP 를 가진 노드의 호스트명. 못 찾거나 조회가 실패하면 None."""
    from . import config
    rc, out, _ = run([config.TAILSCALE_BIN, "status", "--json"], timeout=8)
    try:
        d = json.loads(out) if rc == 0 else {}
        for n in [d.get("Self") or {}, *(d.get("Peer") or {}).values()]:
            if ip in (n.get("TailscaleIPs") or []):
                # HostName 은 노드가 스스로 정하고 겹칠 수 있다. MagicDNS 이름(DNSName)은 tailnet 에서 유일하다
                return (n.get("DNSName") or "").rstrip(".") or n.get("HostName") or None
    except (ValueError, AttributeError):
        pass
    return None


def _analyze(op=None, ip=None):
    """(계획, 쓰기에 쓸 문맥). 부작용은 없다."""
    path = config_path()
    blocked = []

    def block(reason):
        blocked.append({"step": "core", "reason": reason})

    data = parsed = st = before = after = host = None
    lines, idx, top = [], None, 0
    if os.environ.get("JARVIS_CORE") is not None:
        block("이 프로세스에 JARVIS_CORE 가 있습니다(파일보다 환경 변수가 이깁니다)")
    if os.path.islink(path):
        block("설정 파일이 심볼릭 링크입니다(교체하면 일반 파일이 됩니다)")
    elif not os.path.exists(path):
        block("설정 파일이 없습니다")
    else:
        try:
            with open(path, "rb") as f:
                data = f.read()
            st = os.stat(path)
        except OSError:
            block("설정 파일을 읽지 못했습니다")
    if data is not None:
        try:
            import tomllib
            parsed = tomllib.loads(data.decode("utf-8"))
        except ImportError:
            block("설정을 읽으려면 Python 3.11 이상이 필요합니다")
        except UnicodeDecodeError:
            block("설정 파일이 UTF-8 이 아닙니다")
        except ValueError as e:  # TOML 오류(BOM 포함). 메시지에 값 조각이 섞일 수 있어 위치만 낸다
            loc = re.search(r"\(at [^)]*\)", str(e))
            block("설정 TOML 오류" + (f" {loc.group(0)}" if loc else ""))
    if parsed is not None:
        lines = data.splitlines(keepends=True)
        top = next((i for i, l in enumerate(lines) if _TABLE.match(l)), len(lines))
        found = [i for i in range(top) if _CORE.match(lines[i])]
        if len(found) > 1:
            block("첫 테이블 앞에 core 줄이 둘 이상입니다")
        elif found:
            idx = found[0]
            if b"#" in lines[idx]:  # IP 에는 # 이 없으므로 주석이다. 보존하지 않고 막는다
                block("core 줄에 꼬리 주석이 있습니다(지운 뒤 다시 계획하세요)")
            else:
                try:
                    alone = tomllib.loads(lines[idx].decode("utf-8"))
                except ValueError:
                    block("core 가 여러 줄 배열입니다")
                else:
                    if alone.get("core") != parsed.get("core"):
                        block("tomllib 이 읽은 최상위 core 가 그 줄과 맞지 않습니다")
        elif "core" in parsed:
            block("tomllib 이 읽은 최상위 core 가 그 줄과 맞지 않습니다")
        before = _items(parsed.get("core"))
        if before is None:
            block("core 가 배열이나 문자열이 아닙니다")
    if before is not None:
        rest = [x for x in before if x != ip]
        after = [ip] + rest if op == "add" else rest if op == "remove" else list(before)
        if not any(valid(x) for x in after):
            block("결과 목록에 유효한 IPv4 tailnet 항목이 없습니다(아무도 믿지 않는 상태가 됩니다)")
    if op == "add" and not valid(ip):
        block("--add 는 IPv4 100.64.0.0/10 정규형만 받습니다")
    elif op:
        host = _host(ip)
        if op == "add" and not host:
            block("tailnet 에서 그 IP 의 호스트명을 찾지 못했습니다")
    if data is not None and not os.access(os.path.dirname(os.path.abspath(path)), os.W_OK):
        block("설정 파일이 있는 디렉터리에 쓰기 권한이 없습니다")
    if st and hasattr(os, "geteuid") and os.geteuid() != st.st_uid:  # sudo 로 바꾸면 게이트웨이가 못 읽는 파일이 된다
        block("실행 사용자가 설정 파일 소유자가 아닙니다(sudo 없이 소유자로 실행하세요)")
    if parsed is not None:
        key = bool(parsed.get("proxy_key"))
    else:
        key = any(_KEY.match(l) for l in (data or b"").splitlines())
    changes = after is not None and after != before
    plan = module.seal({
        "module": "core-trust", "device": socket.gethostname(), "path": path,
        "sha256": hashlib.sha256(_masked(data)).hexdigest() if data is not None else None,
        "mode": f"{stat.S_IMODE(st.st_mode):04o}" if st else None,
        "proxy_key": "set" if key else "unset", "before": before, "after": after,
        "target": {"ip": ip, "host": host} if op else None,
        "steps": [{"id": "core", "summary": f"core 를 {before} 에서 {after} 로 바꾼다", "risk": "config", "argv": []}]
        if changes else [],
        "blocked": blocked})
    return plan, {"path": path, "data": data, "lines": lines, "idx": idx, "top": top, "parsed": parsed, "st": st}


def _write(ctx, after):
    """같은 폴더 임시 파일에 쓰고 다시 파싱해 검증한 뒤에만 원자적으로 바꾼다. 실패하면 사유(원본은 그대로), 성공하면 None."""
    import tomllib
    path, lines = ctx["path"], list(ctx["lines"])
    eol = b"\r\n" if b"\r\n" in ctx["data"] else b"\n"
    line = b"core = " + json.dumps(after).encode()  # 문자열 JSON 배열은 TOML 한 줄 배열이다
    if ctx["idx"] is not None:
        lines[ctx["idx"]] = line + _eol(lines[ctx["idx"]])
    else:  # 첫 테이블 바로 앞(없으면 끝). 파일 끝의 [infra_units] 같은 테이블 안으로 들어가지 않게
        if ctx["top"] == len(lines) and lines and not _eol(lines[-1]):
            lines[-1] += eol
        lines.insert(ctx["top"], line + eol)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".config-", suffix=".tmp")
    err = None
    try:
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(b"".join(lines))
            if os.name != "nt":  # Windows 는 상속된 ACL 이 권한을 정한다
                os.chmod(tmp, stat.S_IMODE(ctx["st"].st_mode))
            with open(tmp, "rb") as f:
                got = tomllib.load(f)
            others = lambda t: {k: v for k, v in t.items() if k != "core"}  # noqa: E731
            if others(got) != others(ctx["parsed"]) or got.get("core") != after:
                err = "다시 읽은 설정이 계획과 다릅니다"
            else:
                with open(path, "rb") as f:
                    if f.read() != ctx["data"]:
                        err = "교체 직전에 설정 파일이 바뀌었습니다"
            if not err:
                os.replace(tmp, path)
        except (OSError, ValueError) as e:
            err = f"설정 파일을 쓰지 못했습니다({type(e).__name__})"
    finally:
        if os.path.lexists(tmp):
            try:
                os.unlink(tmp)
            except OSError:  # Windows 에서 백신 등이 잡고 있으면 지우지 못한다. proxy_key 가 든 사본이므로 알린다
                err = (err or "임시 파일을 지우지 못했습니다") + f"(임시 파일이 남았습니다: {tmp})"
    return err


def plan(op=None, ip=None):
    return _analyze(op, ip)[0]


def apply(op, ip, plan_id):
    """승인한 plan_id 와 지금 다시 세운 계획이 같을 때만 core 한 줄을 바꾼다. 백업은 만들지 않는다(proxy_key 사본 방지)."""
    current, ctx = _analyze(op, ip)
    out = {"before": current["before"], "after": current["after"]}
    if current["plan_id"] != plan_id:
        return {"status": "stale", **out, "steps": []}
    if current["blocked"]:
        return {"status": "failed", **out,
                "steps": [{"id": b["step"], "result": "skipped", "message": b["reason"]} for b in current["blocked"]]}
    if not current["steps"]:
        return {"status": "current", **out, "steps": []}
    err = _write(ctx, current["after"])
    return {"status": "failed" if err else "done", **out,
            "steps": [{"id": "core", "result": "failed" if err else "done", "message": err or "기록했다(반영은 다음 설정 확인 뒤)"}]}


def cli(argv):
    action, rest = (argv[0], argv[1:]) if argv else (None, [])
    opts, it, ok = {}, iter(rest), action in ("plan", "apply")
    for a in it:
        if a in ("--add", "--remove", "--plan-id") and a not in opts:
            opts[a] = next(it, None)
            ok = ok and opts[a] is not None
        elif a != "--json":
            ok = False
    ops = [k for k in ("--add", "--remove") if k in opts]
    if len(ops) > 1 or (action == "apply") != ("--plan-id" in opts) or (action == "apply" and not ops):
        ok = False
    if not ok:
        print(USAGE, file=sys.stderr)
        return 1
    op, ip = (ops[0][2:], opts[ops[0]]) if ops else (None, None)
    try:
        if action == "plan":
            out, code = plan(op, ip), 0
        else:
            out = apply(op, ip, opts["--plan-id"])
            code = module.EXIT[out["status"]]
    except Exception as e:  # 메시지에 설정 값이 섞일 수 있어 종류만 낸다
        out, code = {"module": "core-trust", "error": type(e).__name__}, 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return code
