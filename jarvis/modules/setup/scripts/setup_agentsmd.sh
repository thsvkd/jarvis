#!/usr/bin/env bash
#
# Install the project instruction files into the current working directory,
# which is assumed to be the target project root (macOS/Linux).
#
#   Which files get written depends on the selected harnesses. Codex, Grok and
#   OpenCode read the root AGENTS.md directly, so AGENTS.md is always written.
#   Claude Code does not read AGENTS.md -- it reads CLAUDE.md -- so a one-line
#   CLAUDE.md importing @AGENTS.md is written as well when claude is selected.
#   A symlink would also work but needs Administrator rights on Windows, so the
#   import is used on every platform for parity.
#
#   An existing file is never overwritten; it is skipped. These files are meant
#   to be customised per project, so there is no surgical merge as there is for
#   settings.json -- the policy is simply "install when absent".
#
#   --force overwrites, and is only used when the user explicitly chose to
#   overwrite during onboarding. The original is kept as .bak.<timestamp>.
#
#   This is the worker that the scripts/setup.sh dispatcher calls with
#   --agentsmd. It can also be run directly from a project root.
#
# Usage:
#   bash setup_agentsmd.sh                        # install (skip existing files)
#   bash setup_agentsmd.sh --harness claude,codex # only those harnesses
#   bash setup_agentsmd.sh --force                # back up, then overwrite
#   bash setup_agentsmd.sh --dry-run              # preview, touch nothing
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(pwd)"

DRY_RUN=0
FORCE=0
# Every supported harness by default: this installer exists to set up whichever
# agent the user runs, and each artefact is additive and skipped when present.
HARNESS_ARG="all"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --force) FORCE=1; shift ;;
        --harness)
            if [[ -z "${2:-}" ]]; then
                echo "--harness requires a value (claude|codex|grok|opencode|agy|gemini|copilot|all)" >&2
                exit 1
            fi
            HARNESS_ARG="$2"; shift 2 ;;
        --harness=*) HARNESS_ARG="${1#*=}"; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# Expand the comma-separated selection into a space-padded list, rejecting names
# we do not know rather than silently installing something the user did not ask
# for. bash 3.2 (the macOS default) has no associative arrays, so membership is
# a substring test against a padded string.
SELECTED=""
normalize_harnesses() {
    local raw item seen
    raw="$(printf '%s' "$1" | tr 'A-Z' 'a-z' | tr ',' ' ')"
    for item in $raw; do
        case "$item" in
            all) item="claude codex grok opencode" ;;
            claude|claude-code) item="claude" ;;
            codex|grok|opencode|agy|gemini|copilot) ;;
            *) echo "Unknown harness: $item (claude|codex|grok|opencode|agy|gemini|copilot|all)" >&2; return 1 ;;
        esac
        for seen in $item; do
            case " $SELECTED " in
                *" $seen "*) ;;
                *) SELECTED="$SELECTED $seen" ;;
            esac
        done
    done
    SELECTED="${SELECTED# }"
    if [[ -z "$SELECTED" ]]; then
        echo "Pick at least one harness (claude|codex|grok|opencode|agy|gemini|copilot|all)" >&2
        return 1
    fi
    return 0
}

has_harness() {
    case " $SELECTED " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

# Install one file, honouring skip/force/backup/dry-run identically for every
# file so a second artefact cannot acquire weaker guarantees than AGENTS.md.
install_file() {
    local source="$1" target="$2"

    if [[ ! -f "$source" ]]; then
        echo "❌ Source file not found: $source" >&2
        return 1
    fi

    if [[ -f "$target" && "$FORCE" -eq 0 ]]; then
        echo "  ℹ️ $target already exists — skipped without overwriting."
        return 0
    fi

    if [[ "$DRY_RUN" -eq 1 ]]; then
        if [[ -f "$target" ]]; then
            echo "  🧪 DRY RUN — would back up the existing $target and overwrite it (nothing is written)."
        else
            echo "  🧪 DRY RUN — $target would be installed (nothing is written)."
        fi
        return 0
    fi

    # Overwriting destroys user content, so the original is always kept.
    if [[ -f "$target" ]]; then
        local backup="$target.bak.$(date +%Y%m%d%H%M%S)"
        cp "$target" "$backup"
        echo "  💾 Backed up existing file: $backup"
    fi
    cp "$source" "$target"
    echo "  ✅ Installed: $target"
    return 0
}

normalize_harnesses "$HARNESS_ARG"

echo "🔧 Installing project instructions... (harnesses: $(printf '%s' "$SELECTED" | tr ' ' ','))"

# AGENTS.md is the shared source for every harness, including Claude Code, which
# reaches it through the CLAUDE.md import.
install_file "$REPO_ROOT/res/AGENTS.md" "$PROJECT_DIR/AGENTS.md"

if has_harness claude; then
    CLAUDE_TARGET="$PROJECT_DIR/CLAUDE.md"
    HAD_CLAUDE_MD=0
    [[ -f "$CLAUDE_TARGET" ]] && HAD_CLAUDE_MD=1

    install_file "$REPO_ROOT/res/CLAUDE.md" "$CLAUDE_TARGET"

    # Skipping an existing CLAUDE.md is the right default -- it is user content.
    # But if that file does not import AGENTS.md, Claude Code never reads the
    # instructions we just installed, and staying silent would report success
    # for an install that did nothing for the harness the user selected.
    if [[ "$HAD_CLAUDE_MD" -eq 1 && "$FORCE" -eq 0 ]] && ! grep -q '@AGENTS\.md' "$CLAUDE_TARGET"; then
        echo "  ⚠️ The existing $CLAUDE_TARGET has no '@AGENTS.md' — Claude Code will not read the AGENTS.md just installed." >&2
        echo "     To keep its existing content, add a single '@AGENTS.md' line at the top of that file yourself." >&2
    fi
fi
