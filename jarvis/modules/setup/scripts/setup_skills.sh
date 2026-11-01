#!/usr/bin/env bash
# Bundled Claude Code skills worker (macOS/Linux).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/setup_common.sh
source "$SCRIPT_DIR/lib/setup_common.sh"
if ! PY="$(resolve_python_command)"; then
    echo "Python 3 is required." >&2
    print_python_install_hint
    exit 1
fi
case " $* " in *" --uninstall "*) echo "🧹 Removing bundled skills" ;; *) echo "🔧 Bundled skills" ;; esac
"$PY" -X utf8 "$REPO_ROOT/res/skills/apply.py" --home "$HOME" "$@"
