#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OMC HUD 에 프롬프트 캐시 TTL 을 띄우도록 statusLine 을 외과적으로 고친다.

scripts/setup_omc.sh(.ps1) 가 OMC 플러그인을 설치한 뒤 호출한다.

  1. statusLine 이 OMC HUD 가 아닌 사용자 설정이면 아무것도 하지 않는다.
  2. OMC HUD 래퍼(~/.claude/hud/omc-hud.mjs)가 없으면 플러그인 캐시의 정본 템플릿에서 복사하고,
     statusLine 이 없으면 OMC HUD 로 만든다.
  3. omc-hud-ttl.mjs 를 복사하고 statusLine.command 앞에 끼운다 — session:NNm 이 cache:NNm 으로 바뀐다.
  4. refreshInterval 이 없으면 넣는다. 입력이 없어도 카운트다운이 흐르게 하기 위해서다.

영수증은 ~/.claude/.claude-code-setup.json 의 "omc_hud" 에 남기고, --uninstall 은 이 설치가
만든 것만 되돌린다. OMC 플러그인 자체와 원래 있던 OMC HUD 는 사용자가 쓰는 도구이므로 남긴다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
from json_files import backup_file, write_json  # noqa: E402

MANIFEST_NAME = ".claude-code-setup.json"
TTL_SCRIPT = "omc-hud-ttl.mjs"
REFRESH_SECONDS = 10
PLUGIN_CACHE = Path("plugins") / "cache" / "omc" / "oh-my-claudecode"
# 우리 접두: node 실행기(node_launcher 가 만드는 세 가지 표기) + 따옴표로 감싼 TTL 스크립트 경로.
# 실행기 표기를 명시해야 앞에 붙은 사용자 텍스트(cd … &&, FOO=1 등)까지 우리 것으로 삼키지 않는다.
PREFIX_RE = re.compile(r'^(?:node|sh "[^"]*find-node\.sh"|"[^"]*") "[^"]*' + re.escape(TTL_SCRIPT) + r'" ')
HUD_FILES = ("omc-hud.mjs", "lib/config-dir.mjs")


def hud_dir_ref(claude_dir: Path) -> str:
    """statusLine 명령에 넣을 hud 디렉터리 표기.

    POSIX 는 기기 사이에서 옮겨도 되도록 $HOME 기반, Windows 는 ~ 가 확장되지 않아 정슬래시 절대 경로.
    파일은 항상 $HOME/.claude 아래에 쓰므로 CLAUDE_CONFIG_DIR 은 쓰지 않는다 — 가리키는 곳이 달라진다.
    """
    if os.name == "nt":
        return (claude_dir / "hud").as_posix()
    return "$HOME/.claude/hud"


def node_launcher(claude_dir: Path, ref: str) -> str:
    """래퍼를 띄울 node 실행기. PATH 에 node 가 없는 환경(nvm 만 쓰는 셸, Windows GUI)에서도
    OMC HUD 가 뜨던 곳이면 우리 래퍼도 떠야 한다. 그래서 OMC 가 이미 쓰는 탐색 수단을 빌린다."""
    if os.name != "nt" and (claude_dir / "hud" / "find-node.sh").is_file():
        return f'sh "{ref}/find-node.sh"'
    try:
        stored = json.loads((claude_dir / ".omc-config.json").read_text(encoding="utf-8")).get("nodeBinary")
    except (OSError, ValueError, AttributeError):
        stored = None
    if os.name == "nt" and isinstance(stored, str) and stored:
        return f'"{Path(stored).as_posix()}"'
    return "node"


def latest_plugin_root(claude_dir: Path) -> Path | None:
    base = claude_dir / PLUGIN_CACHE
    if not base.is_dir():
        return None
    versions = [p for p in base.iterdir() if re.match(r"^\d+(\.\d+)*$", p.name)]
    if not versions:
        return None
    return max(versions, key=lambda p: tuple(int(x) for x in p.name.split(".")))


def load_json_object(path: Path) -> dict:
    """없으면 {}. 있는데 읽을 수 없으면 ValueError — 호출부는 쓰지 않고 멈춘다."""
    if not path.is_file():
        return {}
    # utf-8-sig: Windows PowerShell 5.1 의 Set-Content -Encoding UTF8 은 BOM 을 붙인다.
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("top level is not an object")
    return data


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(claude_dir: Path, dry_run: bool) -> int:
    settings_path = claude_dir / "settings.json"
    manifest_path = claude_dir / MANIFEST_NAME
    hud = claude_dir / "hud"
    ref = hud_dir_ref(claude_dir)
    try:
        settings = load_json_object(settings_path)
        manifest = load_json_object(manifest_path)
    except ValueError as e:
        print(f"❌ Cannot parse the settings file; aborting: {e}", file=sys.stderr)
        return 1

    status = settings.get("statusLine")
    if status is not None and not (isinstance(status, dict) and isinstance(status.get("command"), str)
                                   and status["command"]):
        print("❌ Unrecognized statusLine format; aborting. Check it yourself.", file=sys.stderr)
        return 1
    if status is not None and "omc-hud" not in status["command"]:
        print(f"⚠️ statusLine is not the OMC HUD ({status['command']}). It is a user setting, so it is left untouched.")
        return 0

    # 우리 래퍼가 들어 있는데 접두 표기로 벗겨 낼 수 없으면(앞에 사용자 텍스트가 붙은 경우)
    # 다시 감싸면 이중 래핑이 된다. 사용자가 손본 명령이므로 아무것도 쓰기 전에 멈춘다.
    if status is not None and TTL_SCRIPT in status["command"] and not PREFIX_RE.match(status["command"]):
        print(f"❌ The statusLine contains the cache TTL wrapper in an unrecognized form; aborting: {status['command']}",
              file=sys.stderr)
        return 1

    before = json.dumps(settings, sort_keys=True)
    receipt = dict(manifest.get("omc_hud") or {})
    changes = []

    if not all((hud / name).is_file() for name in HUD_FILES):
        root = latest_plugin_root(claude_dir)
        sources = [root / "scripts" / "lib" / "hud-wrapper-template.txt",
                   root / "scripts" / "lib" / "config-dir.mjs"] if root else []
        if sources and all(p.is_file() for p in sources):
            changes.append(f"Copy OMC HUD wrapper: {sources[0]} → {hud / 'omc-hud.mjs'}")
            if not dry_run:
                (hud / "lib").mkdir(parents=True, exist_ok=True)
                # 이미 있는 파일은 사용자(또는 OMC) 것이라 덮어쓰지 않는다. 복사한 것만 해시와 함께
                # 기록해, 나중에 OMC 가 다시 써서 주인이 바뀐 파일은 uninstall 이 지우지 않게 한다.
                copied = receipt.get("hud_copied")
                copied = dict(copied) if isinstance(copied, dict) else {}
                for src, name in zip(sources, HUD_FILES):
                    if not (hud / name).exists():
                        shutil.copyfile(src, hud / name)
                        copied[name] = sha256(hud / name)
                receipt["hud_copied"] = copied
        elif dry_run:
            changes.append("Copy OMC HUD wrapper (decided after the plugin is installed)")
        else:
            print("❌ Could not find the HUD template in the OMC plugin cache. Check the plugin installation.",
                  file=sys.stderr)
            return 1

    launcher = node_launcher(claude_dir, ref)
    if status is None:
        status = {"type": "command", "command": f'{launcher} "{ref}/omc-hud.mjs"'}
        settings["statusLine"] = status
        receipt["statusline_added"] = status["command"]
        receipt.setdefault("settings_created", not settings_path.is_file())
        changes.append("Add statusLine: OMC HUD")

    changes.append(f"Copy cache TTL wrapper: {hud / TTL_SCRIPT}")
    # 이전 버전 접두가 있으면 벗겨 내고 현재 표기로 다시 붙인다 — 재설치가 곧 갱신이 되게.
    inner = status["command"]
    while PREFIX_RE.match(inner):  # 이전 버그가 남긴 이중 래핑도 한 겹으로 정리한다
        inner = PREFIX_RE.sub("", inner, count=1)
    wrapped = f'{launcher} "{ref}/{TTL_SCRIPT}" ' + inner
    if status["command"] != wrapped:
        changes.append("Prepend cache TTL wrapper to statusLine.command (session → cache)"
                       if inner == status["command"] else "Update the cache TTL wrapper notation in statusLine.command")
        status["command"] = wrapped
    if "refreshInterval" not in status:
        status["refreshInterval"] = REFRESH_SECONDS
        receipt["refresh_interval_added"] = True
        changes.append(f"statusLine.refreshInterval = {REFRESH_SECONDS}")

    for c in changes:
        print(f"  • {c}")
    if dry_run:
        print(f"🧪 DRY RUN — {settings_path} was not actually written.")
        return 0

    hud.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / TTL_SCRIPT, hud / TTL_SCRIPT)
    # 내용이 같으면 다시 쓰지 않는다 — 재실행마다 .bak 이 쌓이지 않게.
    if json.dumps(settings, sort_keys=True) != before:
        backup_file(str(settings_path))
        write_json(str(settings_path), settings)
    manifest["omc_hud"] = receipt
    write_json(str(manifest_path), manifest)
    print(f"✅ OMC HUD cache TTL setup complete: {settings_path}")
    return 0


def uninstall(claude_dir: Path, dry_run: bool) -> int:
    settings_path = claude_dir / "settings.json"
    manifest_path = claude_dir / MANIFEST_NAME
    hud = claude_dir / "hud"
    try:
        settings = load_json_object(settings_path)
        manifest = load_json_object(manifest_path)
    except ValueError as e:
        print(f"❌ Cannot parse the settings file; aborting: {e}", file=sys.stderr)
        return 1
    receipt = manifest.get("omc_hud") or {}

    changes = []
    status = settings.get("statusLine")
    statusline_removed = False
    if isinstance(status, dict) and isinstance(status.get("command"), str):
        if PREFIX_RE.match(status["command"]):
            status["command"] = PREFIX_RE.sub("", status["command"], count=1)
            changes.append("Remove the cache TTL wrapper from statusLine.command")
        # 사용자가 나중에 바꾼 값은 사용자 것이다. 우리가 넣은 값 그대로일 때만 지운다.
        if receipt.get("refresh_interval_added") and status.get("refreshInterval") == REFRESH_SECONDS:
            del status["refreshInterval"]
            changes.append("Remove statusLine.refreshInterval")
        if receipt.get("statusline_added") and status == {"type": "command",
                                                          "command": receipt["statusline_added"]}:
            del settings["statusLine"]
            statusline_removed = True
            changes.append("Remove the statusLine that install added")
    settings_changed = bool(changes)
    if isinstance(status, dict) and TTL_SCRIPT in str(status.get("command")):
        # 벗겨 내지 못한 래퍼가 남아 있다. 여기서 스크립트나 영수증을 지우면 HUD 가 빈 줄이 된다.
        print(f"❌ Could not strip the cache TTL wrapper from statusLine because its form is unrecognized: {status['command']}\n"
              "   Clean it up yourself and run again. Nothing was changed.", file=sys.stderr)
        return 1

    # 복사한 HUD 파일은 그것을 가리키던 statusLine 도 함께 치울 때, 그리고 복사한 파일이 모두
    # 복사 당시 내용 그대로일 때만 한꺼번에 지운다. 하나만 지우면 남은 omc-hud.mjs 가 lib 없이
    # 깨지고, OMC 가 다시 쓴 파일은 이미 OMC 것이다.
    copied = receipt.get("hud_copied") if isinstance(receipt.get("hud_copied"), dict) else {}
    copied = {n: d for n, d in copied.items() if n in HUD_FILES and (hud / n).is_file()}
    files = [hud / TTL_SCRIPT]
    if statusline_removed and copied and all(sha256(hud / n) == d for n, d in copied.items()):
        files += [hud / n for n in copied]
    files = [p for p in files if p.is_file()]
    changes += [f"Delete {p}" for p in files]

    for c in changes or ["(nothing to revert - already clean)"]:
        print(f"  • {c}")
    if dry_run:
        print(f"🧪 DRY RUN — {settings_path} was not actually written.")
        return 0

    if settings_changed:
        if not settings and receipt.get("settings_created"):
            settings_path.unlink()
        else:
            backup_file(str(settings_path))
            write_json(str(settings_path), settings)
    for p in files:
        p.unlink()
    for d in (hud / "lib", hud):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    if "omc_hud" in manifest:
        del manifest["omc_hud"]
        if manifest:
            write_json(str(manifest_path), manifest)
        else:
            manifest_path.unlink()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--home", default=None)
    args = parser.parse_args(argv)
    home = Path(args.home or os.environ.get("HOME") or os.environ.get("USERPROFILE") or ".")
    claude_dir = home / ".claude"
    if args.uninstall:
        return uninstall(claude_dir, args.dry_run)
    return install(claude_dir, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
