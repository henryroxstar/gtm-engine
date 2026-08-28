#!/usr/bin/env bash
# gtm-engine — one-command local bootstrap.
#
# Gets a fresh checkout runnable with zero manual steps:
#   1. ensures `uv` is installed (installs it if missing)
#   2. `uv sync` — uv provisions the right Python (3.11+) and all deps
#   3. prints the environment self-check (which capability tier is unlocked)
#
# Safe to re-run (idempotent). Used by the `setup` skill on first run, or run it
# yourself:  bash scripts/bootstrap.sh
#
# We deliberately do NOT vendor Python or uv binaries in the repo — they are
# platform/arch-specific and would bloat and rot. uv is a single static binary
# that then manages the correct Python for your machine.

set -euo pipefail

# Resolve repo root from this script's location (works from any CWD).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

echo "==> gtm-engine bootstrap (repo: ${REPO_ROOT})"

# --- 1. ensure uv -----------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  echo "==> uv not found — installing (astral.sh) ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installs to ~/.local/bin (or ~/.cargo/bin); make it visible this session.
  export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "!! uv install did not land on PATH. Open a new shell, or add" >&2
  echo "   ~/.local/bin to PATH, then re-run: bash scripts/bootstrap.sh" >&2
  exit 1
fi
echo "==> uv: $(uv --version)"

# --- 2. install deps (uv provisions Python 3.11+ automatically) -------------
echo "==> uv sync ..."
uv sync

# --- 3. environment self-check ----------------------------------------------
echo ""
uv run python -m gtm_core.check_env || {
  echo ""
  echo "==> Bootstrap finished, but TIER 0 is not set yet."
  echo "    Copy .env.example to .env and set ANTHROPIC_API_KEY, then re-run this."
  exit 0   # not a hard failure — deps are installed; the user just needs a key
}

echo ""
echo "==> Ready. Open the folder in Claude Code and say \"set me up\"."
