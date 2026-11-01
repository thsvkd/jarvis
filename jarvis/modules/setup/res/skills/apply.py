"""Install bundled SKILL.md files without taking ownership of user content."""
import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_path(path, home):
    # Reject ancestor symlinks and Windows junctions as well as file links, but only below home.
    # Links above it (macOS /var -> /private/var, a symlinked /home) are not the user's to preserve.
    inside = home.resolve() / path.relative_to(home)
    if os.path.normcase(str(inside.resolve())) != os.path.normcase(str(inside)):
        raise ValueError(f"Preserved linked path: {path}")


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_receipt(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("files"), dict):
        raise ValueError("Invalid skills receipt; preserved without changes")
    for relative, checksum in data["files"].items():
        if not re.fullmatch(r"[a-z0-9-]+/SKILL\.md", relative) or not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("Invalid skills receipt entry; preserved without changes")
    return data["files"]


def apply(home, dry_run=False, uninstall=False):
    base = home / ".claude"
    skills = base / "skills"
    receipt = base / ".claude-code-setup-skills.json"
    check_path(skills, home)
    check_path(receipt, home)
    files = read_receipt(receipt)
    before = dict(files)
    sources = {f"{p.parent.name}/SKILL.md": p for p in Path(__file__).parent.glob("*/SKILL.md")}
    targets = list(files) if uninstall else list(sources)
    failed = False
    changes = {}
    try:
        for relative in sorted(targets):
            target = skills / relative
            check_path(target, home)
            current = target.read_bytes() if target.exists() else None
            owned = relative in files
            if owned and current is not None and digest(current) != files[relative]:
                print(f"Preserved edited skill: {target}")
                failed = True
                continue
            if uninstall:
                print(f"{'Would remove' if dry_run else 'Remove'} managed skill: {target}")
                if not dry_run:
                    changes[target] = current
                    target.unlink(missing_ok=True)
                    try:
                        target.parent.rmdir()
                    except OSError:
                        pass  # User-created supplementary files stay in place.
                files.pop(relative)
                continue
            content = sources[relative].read_bytes()
            if current is not None and not owned:
                print(f"Preserved existing user skill: {target}")
                failed = failed or current != content
                continue
            print(f"{'Would install' if dry_run else 'Install'} skill: {target}")
            if not dry_run and current != content:
                changes[target] = current
                atomic_write(target, content)
            files[relative] = digest(content)
        if not dry_run and files != before:
            if files:
                content = json.dumps({"version": 1, "files": files}, indent=2, sort_keys=True) + "\n"
                atomic_write(receipt, content.encode("utf-8"))
            else:
                receipt.unlink(missing_ok=True)
    except (OSError, ValueError):
        # File and receipt must agree even when the receipt is locked/unwritable.
        for target, original in reversed(list(changes.items())):
            try:
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_write(target, original)
            except OSError as error:
                print(f"Rollback failed for {target}: {error}")
        raise
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    try:
        return apply(args.home, args.dry_run, args.uninstall)
    except (OSError, ValueError) as error:
        print(f"Skills setup failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
