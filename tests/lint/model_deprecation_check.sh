#!/usr/bin/env bash
# Model-deprecation + no-hardcoded-id lint (model routing PRD §7, layer 1 "prevent").
# Two assertions, both fail non-zero:
#
#   (a) NO registry-managed model id (claude-* / deepseek-*) is hardcoded as a VALUE
#       outside gtm_core/models.toml. The registry is the single source of truth, so a
#       provider/version swap must be a one-line config edit — not a literal buried in
#       agent/ or mcp_server/. (Matches quoted string literals only; comments and the
#       backtick-quoted mentions in docstrings are intentionally allowed. Tool/connector
#       names like "deepseek-worker" don't match the model-family pattern.)
#
#   (b) NO model id in gtm_core/models.toml appears on the vendored deprecation denylist
#       (tests/lint/model_deprecation_map.txt). This flags a retiring id (e.g.
#       deepseek-chat, retires 2026-07-24) BEFORE it breaks, so the swap-to-successor is
#       a config edit caught in CI rather than a production 404.
#
# Scope: the registry-managed providers (Anthropic, DeepSeek). Other workers (e.g. the
# Gemini image worker) are not in the registry yet — extending the registry to them is a
# future change; until then they are out of this gate's scope by design.
#
# Slots beside the other gtm-check gates (see .claude/commands/gtm-check.md + ci.yml).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# REGISTRY / MAP / SCAN_ROOT are env-overridable so the gate is unit-testable against
# crafted fixtures (tests/agent/test_model_deprecation_lint.py); production uses defaults.
REGISTRY="${MODEL_REGISTRY:-$ROOT/gtm_core/models.toml}"
MAP="${MODEL_DEPRECATION_MAP:-$ROOT/tests/lint/model_deprecation_map.txt}"
SCAN_ROOT="${MODEL_SCAN_ROOT:-$ROOT}"

fail=0

# --- (a) no hardcoded registry-managed model id outside the registry ----------
# Match a QUOTED model id of a known family (so "deepseek-worker"/"gemini-image-worker"
# tool names don't false-positive); drop full-line comments (the id is documentation).
SCAN_DIRS=(agent gtm_core backend mcp_server cockpit)
PATTERN="[\"'](claude-(sonnet|haiku|opus|[0-9])|deepseek-(chat|reasoner|coder|v[0-9]|[0-9]))[a-z0-9._-]*[\"']"
hardcoded=""
for d in "${SCAN_DIRS[@]}"; do
  [ -d "$SCAN_ROOT/$d" ] || continue
  h="$(grep -rEnI --include='*.py' --exclude-dir=__pycache__ "$PATTERN" "$SCAN_ROOT/$d" 2>/dev/null \
        | grep -vE ':[0-9]+:[[:space:]]*#' || true)"
  [ -n "$h" ] && hardcoded="${hardcoded}${h}"$'\n'
done
if [ -n "$(printf '%s' "$hardcoded" | tr -d '[:space:]')" ]; then
  echo "✗ model-deprecation lint: hardcoded model id outside gtm_core/models.toml"
  echo "   (resolve it via gtm_core.models.resolve_model instead):"
  printf '%s' "$hardcoded" | sed '/^$/d; s/^/    /'
  fail=1
fi

# --- (b) no registry model id is on the deprecation denylist ------------------
if [ ! -f "$REGISTRY" ]; then
  echo "✗ model-deprecation lint: registry not found at $REGISTRY"
  exit 1
fi
# Registry model ids (model = "…") — ids never contain spaces, so word-splitting is safe.
MODELS="$(grep -E '^[[:space:]]*model[[:space:]]*=' "$REGISTRY" | sed -E 's/.*"([^"]+)".*/\1/')"

while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in '' | \#*) continue ;; esac
  mid="$(printf '%s' "$line" | awk '{print $1}')"
  rest="$(printf '%s' "$line" | sed -E 's/^[^[:space:]]+[[:space:]]+//')"
  for m in $MODELS; do
    if [ "$m" = "$mid" ]; then
      echo "✗ model-deprecation lint: registry model '$m' is on the deprecation denylist ($rest)"
      fail=1
    fi
  done
done < "$MAP"

if [ "$fail" -eq 0 ]; then
  n_models="$(printf '%s\n' "$MODELS" | sed '/^$/d' | wc -l | tr -d ' ')"
  echo "✓ model-deprecation lint: no hardcoded model ids; all $n_models registry models off the denylist"
fi
exit "$fail"
