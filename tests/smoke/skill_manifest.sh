#!/usr/bin/env bash
# Skill manifest smoke (integrity, pre-SDK): enumerate skills, parse frontmatter, and verify every
# product-bound skill's `requires_capability` is provided by some product in the sample profile.
# The full Agent-SDK plugin-load smoke (instantiate the SDK with plugins=[local], list skills) is
# added alongside the agent app (task 0.12); this is the dependency-free gate that runs in CI today.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SKILLS="$ROOT/plugin/skills"
# Sample profile: the canonical COMPLETE scaffold `_template` (every profile clones it; its
# `_sample` product declares the caps the SA-chain skills need). Deliberately NOT "first real
# tenant": a partial tenant (e.g. a marketing-only profile with no gateway product) can't satisfy
# every product-bound skill's `requires_capability` and must not fail this gate. Spot-check a
# specific tenant with RESOLVE_PROFILE=<name>. Keep in sync with tests/lint/resolve_check.sh.
RESOLVE_PROFILE="${RESOLVE_PROFILE:-_template}"
PROFILE="$RESOLVE_PROFILE"
fail=0

# NB: no hardcoded skill count here anymore. "The skill set changed" is detected by the
# generated inventory docs/SKILLS.md + codegen-sync (tests/skills/test_codegen.py) — adding a
# skill forces a regenerated index or CI fails, so a magic number to bump is no longer needed.
count="$(find "$SKILLS" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"

# Capabilities the sample profile's products provide (union of products/*/PRODUCT.md capabilities: lines)
provided="$(grep -rhoE 'capabilities:[[:space:]]*\[[^]]*\]' "$ROOT/profiles/$PROFILE/products"/*/PRODUCT.md 2>/dev/null \
  | sed -E 's/.*\[//; s/\].*//; s/,/ /g' | tr '\n' ' ')"

for d in "$SKILLS"/*/; do
  s="$(basename "$d")"
  f="${d}SKILL.md"
  if [ ! -f "$f" ]; then echo "✗ $s: no SKILL.md"; fail=1; continue; fi
  grep -qE '^name:' "$f" || { echo "✗ $s: SKILL.md missing 'name:' frontmatter"; fail=1; }
  # read requires_capability ONLY from the frontmatter block (between the first two --- fences), not prose
  fm="$(awk '/^---[[:space:]]*$/{c++; next} c==1' "$f")"
  cap="$(printf '%s' "$fm" | grep -oE 'requires_capability:[[:space:]]*\[[^]]*\]' | sed -E 's/.*\[//; s/\].*//; s/,/ /g' | tr '\n' ' ' || true)"
  if [ -n "$cap" ]; then
    for c in $cap; do
      if ! echo " $provided " | grep -q " $c "; then
        echo "✗ $s: requires_capability '$c' not provided by any product in profile '$PROFILE'"; fail=1
      fi
    done
  fi
done

[ "$fail" -eq 0 ] && echo "✓ skill manifest smoke: all $count skills parse; product-bound caps satisfied by profile '$PROFILE'"
exit "$fail"
