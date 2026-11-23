#!/usr/bin/env python3
"""hswap list --json 출력을 테스트 픽스처로 가린다(표준 라이브러리만, jarvis 를 import 하지 않는다).

기기 안에서 `python3 -I` 로 돌려 원본이 기기 밖으로 나가지 않게 한다.
  hswap list codex --json --cached | python3 -I hswap_fixture.py hswap --version 0.4.1 --device s2 --argv "hswap list codex --json --cached"
  hswap claude list --json | python3 -I hswap_fixture.py claude --version 0.4.1 --device macmini --argv "hswap claude list --json"
  python3 -I hswap_fixture.py hswap --check < 저장한_픽스처.json    # 검사만 하고 입력을 그대로 낸다

허용 목록에 없는 키는 모두 버리고, 이메일·조직·라벨은 가짜 값으로 바꾼다. 출력(또는 원본)에 토큰 꼴이 있으면 표준 출력 없이 종료 코드 3,
빈 입력이나 깨진 JSON 이면 2 다. 표준 오류에는 규칙 이름과 개수만 낸다(값은 내지 않는다).
허용 목록은 jarvis 가 읽는 키 기준이다. jarvis 가 hswap 출력의 새 키를 읽기 시작하면 이 목록도 같이 늘린다.
"""
import argparse
import json
import re
import sys
import time

# jarvis/usage/hswap.py 의 NO_CREDIT_USAGE·INACTIVE_CONSOLE·PATH_RE 와 같아야 한다(tests/test_hswap_fixtures.py 가 맞춰 본다)
NO_CREDIT_USAGE = "Grok reported no included-credit usage"
INACTIVE_CONSOLE = "measured only while it is the active OpenCode Console account"
PATH_RE = re.compile(r"(?:[A-Za-z]:\\|~[/\\]|/)(?:[^\s/\\\"']+[/\\])+[^\s/\\\"']*")
EMAIL_RE = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

CLAUDE_TOP = ("schemaVersion", "keychainUnavailable", "accounts")
CLAUDE_ROW = ("number", "active", "disabled", "usageStatus", "usageError", "usageRetryAt", "usage", "usageFetchedAt", "fetchedAt",
              "lastGood", "lastGoodUsage", "lastGoodFetchedAt", "loginExpiresAt", "plan", "rateLimitTier", "authDeadStrikes")
HSWAP_ROW = ("provider", "active", "authMode")
HSWAP_USAGE = ("state", "measuredAt", "plan", "balance")
HSWAP_WINDOW = ("name", "usedPercent", "windowSeconds", "resetsAt")

# 원본과 출력 모두에 거는 규칙
BOTH = {
    "sk-ant": re.compile(r"sk-ant-"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}"),
    "bearer": re.compile(r"bearer\s", re.I),
}
KEY_RE = re.compile(r"token|secret|password|cookie|api_?key", re.I)
# 출력에만 거는 규칙. 긴 문자열은 원본의 경로 때문에 오탐이 난다
FAKE_UUID = re.compile(r"00000000-0000-4000-8000-[0-9]{12}")
OUT_ONLY = {
    "long-token": re.compile(r"[A-Za-z0-9_\-+/=]{41,}"),
    "uuid": re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"),
    "long-hex": re.compile(r"[0-9a-fA-F]{33,}"),
}


def _keys(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _keys(x)
    elif isinstance(v, list):
        for x in v:
            yield from _keys(x)


def violations(text, output=True):
    """text(JSON 문자열)에서 걸린 규칙 → 개수. output 이면 출력 전용 규칙(긴 문자열·UUID·이메일 도메인)도 건다."""
    found = {name: len(rx.findall(text)) for name, rx in BOTH.items()}
    try:
        found["key-name"] = sum(1 for k in _keys(json.loads(text)) if isinstance(k, str) and KEY_RE.search(k))
    except ValueError:
        pass
    if output:
        plain = FAKE_UUID.sub("", text)
        found.update({name: len(rx.findall(plain)) for name, rx in OUT_ONLY.items()})
        found["email"] = sum(1 for m in EMAIL_RE.findall(text) if not m.lower().endswith("@example.com"))
    return {k: n for k, n in found.items() if n}


class _Fake:
    """같은 값은 같은 가짜 값이 된다."""

    def __init__(self):
        self.emails, self.orgs, self.uuids, self.labels = {}, {}, {}, {}

    def email(self, value):
        return self.emails.setdefault(value.lower(), f"user{len(self.emails) + 1}@example.com")

    def label(self, value):
        if EMAIL_RE.fullmatch(value):
            return self.email(value)
        return self.labels.setdefault(value, f"account {len(self.labels) + 1}")

    def org(self, name, email):
        if email and name == f"{email}'s Organization":
            return f"{self.email(email)}'s Organization"
        return self.orgs.setdefault(name, f"Org {len(self.orgs) + 1}")

    def uuid(self, value):
        return f"00000000-0000-4000-8000-{self.uuids.setdefault(value, len(self.uuids) + 1):012d}"


def _paths(v):
    """남긴 값 속의 홈·절대 경로를 <path> 로."""
    if isinstance(v, str):
        return PATH_RE.sub("<path>", v)
    if isinstance(v, dict):
        return {k: _paths(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_paths(x) for x in v]
    return v


def _pick(row, keys):
    return {k: _paths(row[k]) for k in keys if k in row}


def _dicts(v):
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _claude(body, fake):
    out = {k: _paths(body[k]) for k in CLAUDE_TOP if k in body and k != "accounts"}
    rows = []
    for r in _dicts(body.get("accounts")):
        row = _pick(r, CLAUDE_ROW)
        email = r.get("email") if isinstance(r.get("email"), str) else None
        if email:
            row["email"] = fake.email(email)
        if isinstance(r.get("organizationName"), str):
            row["organizationName"] = fake.org(r["organizationName"], email)
        if isinstance(r.get("organizationUuid"), str):
            row["organizationUuid"] = fake.uuid(r["organizationUuid"])
        if "alias" in r:
            row["alias"] = None
        rows.append(row)
    out["accounts"] = rows
    return out


def _usage(u):
    keep = _pick(u, HSWAP_USAGE)
    if "detail" in u:
        keep["detail"] = u["detail"] if u["detail"] in (NO_CREDIT_USAGE, INACTIVE_CONSOLE) else ""
    keep["windows"] = [_pick(w, HSWAP_WINDOW) for w in _dicts(u.get("windows"))]
    return keep


def _hswap(body, fake):
    out = {k: body[k] for k in ("schemaVersion",) if k in body}
    harnesses = []
    for h in _dicts(body.get("harnesses")):
        rows = []
        for r in _dicts(h.get("accounts")):
            row = _pick(r, HSWAP_ROW)
            if isinstance(r.get("label"), str):
                row["label"] = fake.label(r["label"])
            if isinstance(r.get("usage"), dict):
                row["usage"] = _usage(r["usage"])
            rows.append(row)
        harnesses.append({"harness": h.get("harness") if isinstance(h.get("harness"), str) else None, "accounts": rows})
    out["harnesses"] = harnesses
    return out


def run(kind, text, meta=None, check=False, now=None):
    """(종료 코드, 표준 출력, 표준 오류). 걸린 것이 있으면 출력은 비고 종료 코드 3, 입력이 비었거나 JSON 이 아니면 2."""
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return 2, "", "입력이 비었거나 JSON 객체가 아닙니다\n"
    if check:
        found = violations(text)
        return (3, "", "".join(f"규칙 {k}: {n}건\n" for k, n in found.items())) if found else (0, text, "")
    found = violations(text, output=False)
    if found:
        return 3, "", "".join(f"원본에서 규칙 {k}: {n}건\n" for k, n in found.items())
    fake = _Fake()
    header = {"hswap": (meta or {}).get("version", ""), "collected_at": int(now or time.time()),
              "argv": (meta or {}).get("argv", ""), "device": (meta or {}).get("device", "")}
    out = {"_fixture": header, **(_claude(body, fake) if kind == "claude" else _hswap(body, fake))}
    result = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    found = violations(result)
    if found:
        return 3, "", "".join(f"출력에서 규칙 {k}: {n}건\n" for k, n in found.items())
    return 0, result, ""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("kind", choices=("claude", "hswap"))
    p.add_argument("--check", action="store_true", help="검사만 하고 입력을 그대로 낸다")
    p.add_argument("--version", default="")
    p.add_argument("--argv", default="")
    p.add_argument("--device", default="")
    a = p.parse_args(argv)
    code, out, err = run(a.kind, sys.stdin.read(), {"version": a.version, "argv": a.argv, "device": a.device}, a.check)
    sys.stderr.write(err)
    sys.stdout.write(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
