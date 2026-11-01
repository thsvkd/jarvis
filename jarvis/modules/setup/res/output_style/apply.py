#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""~/.claude/settings.json 의 outputStyle 을 fluent-korean 으로 지정한다.

scripts/setup_output_style.sh(.ps1) 가 fluent-korean 플러그인을 설치한 뒤 호출한다.
플러그인이 주는 출력 스타일은 `<plugin>:<name>` 으로 불린다
(https://code.claude.com/docs/en/plugins/components#themes-and-output-styles).

outputStyle 키 하나만 고친다. 원래 값은 ~/.claude/.claude-code-setup.json 의 "output_style"
영수증에 남기고, --uninstall 은 값이 아직 우리가 넣은 그대로일 때만 원래 값으로 되돌린다.
플러그인 자체는 사용자가 쓰는 도구이므로 남긴다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
from json_files import backup_file, write_json  # noqa: E402

MANIFEST_NAME = ".claude-code-setup.json"
STYLE = "fluent-korean:fluent-korean"


def load_json_object(path: Path) -> dict:
    """없으면 {}. 있는데 읽을 수 없으면 ValueError — 호출부는 쓰지 않고 멈춘다."""
    if not path.is_file():
        return {}
    # utf-8-sig: Windows PowerShell 5.1 의 Set-Content -Encoding UTF8 은 BOM 을 붙인다.
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("top level is not an object")
    return data


def install(claude_dir: Path, dry_run: bool) -> int:
    settings_path = claude_dir / "settings.json"
    manifest_path = claude_dir / MANIFEST_NAME
    try:
        settings = load_json_object(settings_path)
        manifest = load_json_object(manifest_path)
    except ValueError as e:
        print(f"❌ Cannot parse the settings file; stopping: {e}", file=sys.stderr)
        return 1

    current = settings.get("outputStyle")
    if current == STYLE:
        print(f"  • already set: outputStyle = {STYLE}")
        return 0

    print(f"  • outputStyle: {current!r} → {STYLE!r}" if current is not None
          else f"  • outputStyle = {STYLE}")
    if dry_run:
        print(f"🧪 DRY RUN — nothing was written to {settings_path}.")
        return 0

    # 여기 왔다면 지금 값은 우리 값이 아니다 — 설치 전 값이거나, 설치 뒤 사용자가 고른 값이다.
    # 어느 쪽이든 uninstall 이 되살릴 값은 지금 값이다. settings_created 는 처음 기록을 지킨다.
    receipt = manifest.get("output_style")
    if not isinstance(receipt, dict):
        receipt = {"settings_created": not settings_path.is_file()}
    receipt["previous"] = current
    settings["outputStyle"] = STYLE
    if settings_path.is_file():
        backup_file(str(settings_path))
    write_json(str(settings_path), settings)
    manifest["output_style"] = receipt
    write_json(str(manifest_path), manifest)
    print(f"✅ Output style set: {settings_path} (takes effect from the next session)")
    return 0


def uninstall(claude_dir: Path, dry_run: bool) -> int:
    settings_path = claude_dir / "settings.json"
    manifest_path = claude_dir / MANIFEST_NAME
    try:
        settings = load_json_object(settings_path)
        manifest = load_json_object(manifest_path)
    except ValueError as e:
        print(f"❌ Cannot parse the settings file; stopping: {e}", file=sys.stderr)
        return 1
    receipt = manifest.get("output_style")
    if not isinstance(receipt, dict):
        print("  • (nothing to revert — already cleaned up)")
        return 0

    # 사용자가 나중에 바꾼 값은 사용자 것이다. 우리가 넣은 값 그대로일 때만 되돌린다.
    restore = settings.get("outputStyle") == STYLE
    previous = receipt.get("previous")
    if restore:
        print(f"  • outputStyle: {STYLE!r} → {previous!r}" if previous is not None
              else "  • outputStyle removed")
    elif not settings_path.is_file():
        print("  • settings.json is missing, nothing to revert (manifest cleanup only)")
    else:
        print("  • outputStyle was changed, leaving it as is (manifest cleanup only)")
    if dry_run:
        print(f"🧪 DRY RUN — nothing was written to {settings_path}.")
        return 0

    if restore:
        if previous is None:
            del settings["outputStyle"]
        else:
            settings["outputStyle"] = previous
        if not settings and receipt.get("settings_created"):
            settings_path.unlink()
        else:
            backup_file(str(settings_path))
            write_json(str(settings_path), settings)
    del manifest["output_style"]
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
