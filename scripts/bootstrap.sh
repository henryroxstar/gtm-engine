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

# --- 2b. prove the interpreter is real and gtm_core actually imports --------
# The Windows twin (scripts/bootstrap.ps1) needs this because `uv run python` there can
# still be shadowed by the App Execution Alias stub; a modern-python shim on this side
# blocks a bare `python` the same way. Either failure mode means every gtm_core.* CLI —
# the budget guard, the ledger writers, the integrity gates — fails silently, one at a
# time, and a pipeline run degrades into a hand-written result that looks finished. Fail
# loudly HERE instead, before any of that.
echo "==> interpreter probe ..."
content_root="$(uv run python -m gtm_core.paths || true)"
if [ -z "${content_root}" ]; then
  echo "" >&2
  echo "The self-check could not start Python. On Windows this is usually the Store placeholder; see END-USER-ONBOARDING.md, 'If the check fails'. Do not run the engine until this ends with Ready." >&2
  exit 1
fi
echo "==> content root: ${content_root}"

# --- 2c. ensure .claude/skills link is active for Claude Code ----------------
if [ ! -d "${REPO_ROOT}/.claude/skills" ]; then
  mkdir -p "${REPO_ROOT}/.claude"
  rm -rf "${REPO_ROOT}/.claude/skills"
  ln -sfn ../plugin/skills "${REPO_ROOT}/.claude/skills" 2>/dev/null || \
    cp -R "${REPO_ROOT}/plugin/skills" "${REPO_ROOT}/.claude/skills"
fi

# --- 2d. system media tools probe (video pipeline) --------------------------
if command -v ffmpeg >/dev/null 2>&1; then
  echo "==> ffmpeg: $(ffmpeg -version 2>&1 | head -n 1)"
else
  echo "==> [Notice] ffmpeg is not installed on PATH."
  echo "    Video rendering, local captions, and clip finishing require ffmpeg."
  if [[ "${OSTYPE:-}" == "darwin"* ]]; then
    echo "    To install on macOS: brew install ffmpeg"
  elif command -v apt-get >/dev/null 2>&1; then
    echo "    To install on Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y ffmpeg"
  fi
fi

# --- 3. environment self-check ----------------------------------------------
echo ""
CHECK_CMD=(uv run python -m gtm_core.check_env)
if command -v doppler >/dev/null 2>&1; then
  echo "==> Doppler detected — running self-check via \`doppler run\` ..."
  CHECK_CMD=(doppler run -- uv run python -m gtm_core.check_env)
fi

"${CHECK_CMD[@]}" || {
  echo ""
  echo "==> Not ready yet. This copy runs on the self-hosted server, which needs an API key: copy .env.example to .env and set ANTHROPIC_API_KEY, or sign in with Doppler."
  exit 0   # not a hard failure — deps are installed; the user just needs a key
}

echo ""
echo "==> Ready. Open the folder in the Claude app and say \"set me up\"."
