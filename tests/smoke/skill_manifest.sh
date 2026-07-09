#!/usr/bin/env bash
# Skill manifest smoke (integrity, pre-SDK): enumerate skills, parse frontmatter, and verify every
# product-bound skill's `requires_capability` is provided by some product in the sample profile.
# The full Agent-SDK plugin-load smoke (instantiate the SDK with plugins=[local], list skills) is
# added alongside the agent app (task 0.12); this is the dependency-free gate that runs in CI today.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SKILLS="$ROOT/plugin/skills"
# Default profile: first real profile dir (underscore-prefixed ones like _template are
# scaffolding), falling back to _template — so the private repo resolves its tenant and a
# fresh/public clone (where only _template exists) resolves the template. RESOLVE_PROFILE
# overrides either way.
if [ -z "${RESOLVE_PROFILE:-}" ]; then
  RESOLVE_PROFILE="$(find "$ROOT/profiles" -mindepth 1 -maxdepth 1 -type d -not -name '_*' -exec basename {} \; 2>/dev/null | sort | head -1)"
  RESOLVE_PROFILE="${RESOLVE_PROFILE:-_template}"
fi
PROFILE="$RESOLVE_PROFILE"
EXPECT="${EXPECT_SKILLS:-31}"
fail=0

count="$(find "$SKILLS" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
if [ "$count" -eq "$EXPECT" ]; then echo "✓ skills: $count (expected $EXPECT)"; else echo "✗ skills: $count (expected $EXPECT)"; fail=1; fi

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
