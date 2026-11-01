#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""번들 mod 를 ~/.claude/mods/<name> 에 복사하고 CLAUDE_CODE_PLUGIN_DIRS 로 매 세션 로드되게 한다.

scripts/setup_mods.sh(.ps1) 가 호출한다. 번들:
  bash-diff-filter  OMC HUD 가 렌더마다 프로젝트에 쓰는 .omc/state/hud-stdin-cache.json 을
                    Bash 의 '바뀐 파일' 표시에서 뺀다.
  cache-ttl-status  프롬프트 캐시 잔여 시간(cache:57m)을 프롬프트 아래 상태 줄에 띄운다.
  korean-reply-rewrite  한국어 프롬프트 뒤 메인 루프의 영어 응답 블록을 저장 직전에 한국어로
                    바꿔, 쌓인 영어 기록이 이후 응답을 영어로 끌고 가지 않게 한다.

CLAUDE_CODE_PLUGIN_DIRS 는 ~/.claude/settings.json 의 env 에서 읽힌다(프로젝트 설정은 아님)
(https://code.claude.com/docs/en/plugins/mods/reference#settings-and-environment-variables).
영수증은 ~/.claude/.claude-code-setup.json 의 "mods" 에 남기고, --uninstall 은 우리가 넣은
경로와 복사 당시 내용 그대로인 파일만 치운다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
from json_files import backup_file, write_json  # noqa: E402

MANIFEST_NAME = ".claude-code-setup.json"
ENV_KEY = "CLAUDE_CODE_PLUGIN_DIRS"
MODS = ("bash-diff-filter", "cache-ttl-status", "korean-reply-rewrite")
# 엔진이 mod 를 로드할 때마다 이 폴더에 타입 선언과 tsconfig 를 새로 써 넣는다. 우리 파일이 아니다.
GENERATED = ".claude-plugin/types"
# 엔진은 mod 루트에도 위 tsconfig 를 상속하는 한 줄짜리 tsconfig.json 을 둔다.
GENERATED_TSCONFIG = {"extends": "./.claude-plugin/types/tsconfig.json"}


def load_json_object(path: Path) -> dict:
    """없으면 {}. 있는데 읽을 수 없으면 ValueError — 호출부는 쓰지 않고 멈춘다."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("top level is not an object")
    return data


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(name: str) -> list[str]:
    root = HERE / name
    # 테스트는 claude plugin test 용이라, 생성 타입은 엔진 것이라 사용자 홈에 깔지 않는다.
    # .claude-plugin 밖의 숨김 경로(.omc 상태, 에디터 폴더 등)는 작업 부산물이다.
    rels = (p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    return sorted(r for r in rels
                  if not r.endswith((".test.ts", ".test.tsx")) and not r.startswith(GENERATED + "/")
                  and r != "tsconfig.json"
                  and not any(part.startswith(".") and part != ".claude-plugin" for part in r.split("/")))


def dir_ref(claude_dir: Path, name: str) -> str:
    """env 에 넣을 표기. 절대 경로 — ~ 는 실행 시점의 홈으로 풀려 복사한 곳과 어긋날 수 있다."""
    return str(claude_dir / "mods" / name)


def split_dirs(value: str) -> list[str]:
    return [p for p in value.split(os.pathsep) if p]


def same_dir(a: str, b: str) -> bool:
    norm = lambda p: os.path.normcase(os.path.normpath(os.path.expanduser(p)))  # noqa: E731
    return norm(a) == norm(b)


def is_engine_tsconfig(path: Path) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")) == GENERATED_TSCONFIG
    except (OSError, ValueError):
        return False


def valid_receipt(receipt) -> bool:
    return (isinstance(receipt, dict)
            and isinstance(receipt.get("files", {}), dict)
            and isinstance(receipt.get("env_dirs", []), list)
            and all(isinstance(d, str) for d in receipt.get("env_dirs", [])))


def install(claude_dir: Path, dry_run: bool) -> int:
    settings_path = claude_dir / "settings.json"
    manifest_path = claude_dir / MANIFEST_NAME
    try:
        settings = load_json_object(settings_path)
        manifest = load_json_object(manifest_path)
    except ValueError as e:
        print(f"❌ Cannot parse the settings file; aborting: {e}", file=sys.stderr)
        return 1
    env = settings.get("env", {})
    if not isinstance(env, dict) or not isinstance(env.get(ENV_KEY, ""), str):
        print(f"❌ Unrecognized format for env.{ENV_KEY} in settings.json; aborting.", file=sys.stderr)
        return 1

    receipt = manifest.get("mods", {})
    if not valid_receipt(receipt):
        print("❌ Unrecognized format for the mods receipt in the manifest; aborting.", file=sys.stderr)
        return 1
    owned = dict(receipt.get("files", {}))
    claimed = False
    copies, changes = [], []
    for name in MODS:
        target = claude_dir / "mods" / name
        for rel in source_files(name):
            src, dst, key = HERE / name / rel, target / rel, f"{name}/{rel}"
            if dst.exists() and not dst.is_file():
                print(f"❌ {dst} exists but is not a file; aborting.", file=sys.stderr)
                return 1
            if dst.is_file():
                if sha256(dst) == sha256(src):
                    # 앞선 설치가 중간에 끊겨 영수증에 못 남긴 파일도 우리 것으로 기록한다.
                    if owned.get(key) is None:
                        owned[key] = sha256(dst)
                        claimed = True
                    continue
                # 우리가 복사한 그대로가 아니면 사용자(또는 다른 도구)가 고친 파일이다.
                if owned.get(key) != sha256(dst):
                    print(f"❌ {dst} already exists with different content, so it will not be overwritten. Clean it up yourself and run again.",
                          file=sys.stderr)
                    return 1
            copies.append((src, dst, key))
            changes.append(f"Copy mod file: {dst}")

    dirs = split_dirs(env.get(ENV_KEY, ""))
    added = [dir_ref(claude_dir, name) for name in MODS]
    added = [d for d in added if not any(same_dir(d, x) for x in dirs)]
    for d in added:
        changes.append(f"Add to env.{ENV_KEY}: {d}")

    for c in changes or ["(no changes - already installed)"]:
        print(f"  • {c}")
    if dry_run:
        print(f"🧪 DRY RUN — {settings_path} was not actually written.")
        return 0

    for src, dst, key in copies:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        owned[key] = sha256(dst)
    if added:
        receipt.setdefault("settings_created", not settings_path.is_file())
        # 사용자가 env 블록을 통째로 지운 뒤 재설치하면 이번에 만든 것이다.
        receipt["env_created"] = "env" not in settings or bool(receipt.get("env_created"))
        receipt["env_dirs"] = sorted(set(receipt.get("env_dirs", [])) | set(added))
        settings["env"] = env
        env[ENV_KEY] = os.pathsep.join(dirs + added)
        if settings_path.is_file():
            backup_file(str(settings_path))
        write_json(str(settings_path), settings)
    if copies or added or claimed:
        receipt["files"] = owned
        manifest["mods"] = receipt
        write_json(str(manifest_path), manifest)
        print("✅ Mod installation complete (loaded from the next new session; requires Claude Code v2.1.290 or later)")
    return 0


def uninstall(claude_dir: Path, dry_run: bool) -> int:
    settings_path = claude_dir / "settings.json"
    manifest_path = claude_dir / MANIFEST_NAME
    try:
        settings = load_json_object(settings_path)
        manifest = load_json_object(manifest_path)
    except ValueError as e:
        print(f"❌ Cannot parse the settings file; aborting: {e}", file=sys.stderr)
        return 1
    if "mods" not in manifest:
        print("  • (nothing to revert - already clean)")
        return 0
    receipt = manifest["mods"]
    if not valid_receipt(receipt):
        print("❌ Unrecognized format for the mods receipt in the manifest; aborting.", file=sys.stderr)
        return 1

    changes = []
    env = settings.get("env")
    ours = receipt.get("env_dirs", [])
    settings_changed = False
    if isinstance(env, dict) and isinstance(env.get(ENV_KEY), str):
        dirs = split_dirs(env[ENV_KEY])
        kept = [d for d in dirs if not any(same_dir(d, o) for o in ours)]
        if kept != dirs:
            settings_changed = True
            changes.append(f"Remove from env.{ENV_KEY}: {', '.join(d for d in dirs if d not in kept)}")
            if kept:
                env[ENV_KEY] = os.pathsep.join(kept)
            else:
                del env[ENV_KEY]
                if not env and receipt.get("env_created"):
                    del settings["env"]

    files = []
    for key, digest in receipt.get("files", {}).items():
        path = claude_dir / "mods" / key
        if path.is_file() and sha256(path) == digest:
            files.append(path)
        elif path.is_file():
            changes.append(f"Keep {path}: it changed after copying (it is dropped from env, so it will no longer be loaded)")
    changes += [f"Delete {p}" for p in files]
    generated = [claude_dir / "mods" / name / GENERATED for name in MODS]
    generated = [g for g in generated if g.is_dir()]
    changes += [f"Delete {g} (types the engine generated at load)" for g in generated]
    # 루트 tsconfig.json 은 엔진이 쓰는 모양 그대로일 때만 엔진 것으로 보고 지운다.
    stubs = [claude_dir / "mods" / name / "tsconfig.json" for name in MODS]
    stubs = [t for t in stubs if t.is_file() and is_engine_tsconfig(t)]
    changes += [f"Delete {t} (tsconfig the engine generated at load)" for t in stubs]

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
    for g in generated:
        shutil.rmtree(g)
    for t in stubs:
        t.unlink()
    # 비게 된 디렉터리만 아래에서부터 지운다.
    for name in MODS:
        root = claude_dir / "mods" / name
        if root.is_dir():
            for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                if not any(d.iterdir()):
                    d.rmdir()
            if not any(root.iterdir()):
                root.rmdir()
    mods_dir = claude_dir / "mods"
    if mods_dir.is_dir() and not any(mods_dir.iterdir()):
        mods_dir.rmdir()
    del manifest["mods"]
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
