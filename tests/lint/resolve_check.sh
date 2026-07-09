#!/usr/bin/env bash
# Functional resolve check (integrity): grep-zero is necessary but NOT sufficient. Every skill must still
# FUNCTION — resolve the active profile and read real paths. This verifies:
#   1. no stale working-folder PROFILE refs (skills must read profiles/<active>/PROFILE.md)
#   2. no stale in-plugin company-knowledge refs (company knowledge lives in profiles/<active>/knowledge/)
#   3. every profiles/<active>/... path a skill references resolves against a sample profile
#      (<active> -> $RESOLVE_PROFILE, <product> -> the sample profile's default/first product)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SKILLS="$ROOT/plugin/skills"
# Default profile: first real profile dir (underscore-prefixed ones like _template are
# scaffolding), falling back to _template — private repo → tenant, fresh/public clone →
# template. RESOLVE_PROFILE overrides either way. Keep in sync with tests/smoke/skill_manifest.sh.
if [ -z "${RESOLVE_PROFILE:-}" ]; then
  RESOLVE_PROFILE="$(find "$ROOT/profiles" -mindepth 1 -maxdepth 1 -type d -not -name '_*' -exec basename {} \; 2>/dev/null | sort | head -1)"
  RESOLVE_PROFILE="${RESOLVE_PROFILE:-_template}"
fi
PROFILE="$RESOLVE_PROFILE"
PRODUCT="${RESOLVE_PRODUCT:-$(ls "$ROOT/profiles/$PROFILE/products" 2>/dev/null | head -1)}"
# Scan the generated SKILL.md + prompt bodies under plugin/skills/ AND the canonical
# manifests under gtm_core/skills/ (a path ref authored in a manifest must resolve too).
SCAN=("$SKILLS"); [ -d "$ROOT/gtm_core/skills" ] && SCAN+=("$ROOT/gtm_core/skills")
fail=0

# 1. stale working-folder PROFILE refs (old single-file model)
if hits="$(grep -rInE --exclude-dir=__pycache__ '[a-z][a-z0-9-]*-PROFILE\.md' "${SCAN[@]}" 2>/dev/null)" && [ -n "$hits" ]; then
  echo "✗ stale working-folder PROFILE ref (skills must read profiles/<active>/PROFILE.md):"
  echo "$hits" | sed 's/^/    /'; fail=1
fi

# 2. stale in-plugin company-knowledge refs (knowledge moved to the profile)
if hits="$(grep -rInE --exclude-dir=__pycache__ 'CLAUDE_PLUGIN_ROOT\}/knowledge' "${SCAN[@]}" 2>/dev/null)" && [ -n "$hits" ]; then
  echo "✗ stale in-plugin knowledge ref (company knowledge lives in profiles/<active>/knowledge/):"
  echo "$hits" | sed 's/^/    /'; fail=1
fi

# 3. every profiles/<active>/ path referenced resolves against the sample profile
missing=0
while read -r p; do
  [ -z "$p" ] && continue
  rel="$(printf '%s' "$p" | sed -e "s#<active>#$PROFILE#g" -e "s#<product>#$PRODUCT#g")"
  # skip pure structural placeholders that survive substitution (e.g. <company>, <slug>)
  case "$rel" in *'<'*'>'*) continue ;; esac
  rel="${rel%>}"   # drop a trailing '>' left by a path embedded in a <...> prose placeholder
  if [ ! -e "$ROOT/$rel" ]; then
    echo "✗ unresolved skill ref: $p  ->  $rel"; missing=$((missing+1))
  fi
done < <(grep -rhoIE --exclude-dir=__pycache__ 'profiles/<active>/[A-Za-z0-9_./<>-]+' "${SCAN[@]}" 2>/dev/null | sed -E 's/[`).,:*]+$//' | sort -u)
[ "$missing" -gt 0 ] && fail=1

if [ "$fail" -eq 0 ]; then
  echo "✓ resolve check: all skill refs resolve (profile=$PROFILE, product=$PRODUCT); no stale PROFILE/knowledge refs"
fi
exit "$fail"
