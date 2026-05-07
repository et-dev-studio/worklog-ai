#!/usr/bin/env bash
# Pre-commit hook: run pytest in BitNet venv, block commit on failure.
# Install: ln -sf "$(pwd)/scripts/pre-commit.sh" .git/hooks/pre-commit
set -euo pipefail

VENV="${WORKLOG_VENV:-$HOME/BitNet/BitEnv}"
ACTIVATE="$VENV/bin/activate"

if [[ ! -f "$ACTIVATE" ]]; then
    echo "pre-commit: venv not found at $ACTIVATE" >&2
    echo "Set WORKLOG_VENV to override or skip with: git commit --no-verify" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$ACTIVATE"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "pre-commit: running pytest -q ..."
if ! pytest -q; then
    echo "pre-commit: tests failed; commit aborted." >&2
    exit 1
fi

echo "pre-commit: all tests passed."
