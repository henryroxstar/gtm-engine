#!/usr/bin/env bash
# De-brand lint (spec §4A "definition of done"): NO hardcoded company tokens in the
# de-branded engine. Company facts live in profiles/<company>/, never in the plugin.
# Exit non-zero on any hit.
#
# TWO MODES:
#   default            — CI gate for the LIVE (private) repo. Scans the skill bodies +
#                        generated SKILL.md (plugin/skills/) AND the Python manifests
#                        (gtm_core/skills/) for the active-tenant token set. This is the
#                        per-commit gate; it stays green on the current repo.
#   DEBRAND_RELEASE=1  — PRE-PUBLISH gate for an open-source cut (also `--release`).
#   (or --release)       Widens BOTH the scan scope (the whole de-branded engine surface:
#                        all of plugin/ incl. README / plugin.json / references, plus
#                        gtm_core, agent, backend, mcp_server, cockpit, schemas, the
#                        runtime-referenced docs that ship, and profiles/_template) AND
#                        the token set (adds prior tenants, the private org name, personal
#                        handles) PLUS a regex pass for real hostnames/URLs. This is the
#                        gate a pre-publish OSS cut must pass; it WILL surface residuals the
#                        default scan intentionally does not (a branded plugin README, a
#                        company default-profile slug, schema `$id` org refs, real
#                        staging/webhook hosts, etc.). Each hit must be scrubbed or explicitly
#                        allowlisted (tests/lint/.debrandignore) before publish.
#
# The token/host denylist is TENANT DATA, not code — it lives in
# tests/linter/safe_share_denylist.txt (single-sourced with content_linter.py). Add your
# company there, or override via DEBRAND_TOKENS / DEBRAND_RELEASE_TOKENS / DEBRAND_HOST_RE
# (|-separated). Substring grep: only collision-free tokens belong (a generic string like
# "IMDA" or "Arize" — substring of "summarize" — would false-positive). Hosts go through the
# separate regex pass below.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

RELEASE=0
[ "${DEBRAND_RELEASE:-0}" = "1" ] && RELEASE=1
[ "${1:-}" = "--release" ] && RELEASE=1

# Tenant denylist is TENANT DATA, single-sourced in tests/linter/safe_share_denylist.txt
# (shared with content_linter.py). The OSS export EXCLUDES that file, so the public cut
# scans with an EMPTY default set (a public user sets DEBRAND_TOKENS to their own company).
# Absent file → empty defaults, by design.
DENYLIST_FILE="${GTM_SAFE_SHARE_DENYLIST_FILE:-$ROOT/tests/linter/safe_share_denylist.txt}"
_deny() { # $1 = prefix (token|rtoken|host) → pipe-joined values, or empty
  [ -f "$DENYLIST_FILE" ] || return 0
  sed -n "s/^$1:[[:space:]]*//p" "$DENYLIST_FILE" | paste -sd'|' - 2>/dev/null || true
}
TOKENS="${DEBRAND_TOKENS:-$(_deny token)}"
# Canonical skill source spans two places: the prompt bodies + generated SKILL.md under
# plugin/skills/, AND the Python manifests under gtm_core/skills/ (a company token in a
# manifest description would otherwise bypass a plugin/-only scan).
#
# packs/ joined the DEFAULT scan on 2026-08-14. A pack graph is engine-layer data — its node
# prompts are brand-agnostic mechanism, tenant facts belong in profiles/. It was previously
# scanned only under RELEASE mode, so a tenant slug in a pack prompt would pass every
# per-commit gate and surface only at pre-publish: the same narrower-than-the-carve scope gap
# that let a tenant slug through in a tests/ fixture (see the RELEASE block below).
SCAN_DIRS=("$ROOT/plugin/skills" "$ROOT/gtm_core/skills" "$ROOT/packs")

# The runtime-referenced docs that SHIP in the public cut (skills/linter read them at load,
# CONTRIBUTING points at RULES, plugin/README at DEPLOY). Everything else under docs/ is
# private-only and deliberately NOT scanned/shipped. Kept in sync with the export allowlist
# (scripts/oss-export.sh).
SHIPPING_DOCS=(
  "$ROOT/docs/prose-craft.md"
  "$ROOT/docs/hook-craft.md"
  "$ROOT/docs/x-tweet-patterns.md"
  "$ROOT/docs/audience-psychology-method.md"
  "$ROOT/docs/virality-engineering.md"
  "$ROOT/docs/facebook-optimization.md"
  "$ROOT/docs/linkedin-optimization.md"
  "$ROOT/docs/instagram-optimization.md"
  "$ROOT/docs/x-optimization.md"
  "$ROOT/docs/email-optimization.md"
  "$ROOT/docs/email-deliverability.md"
  "$ROOT/docs/email-compliance.md"
  "$ROOT/docs/cold-email-craft-evidence.md"
  "$ROOT/docs/RULES.md"
  "$ROOT/docs/DEPLOY.md"
  "$ROOT/docs/SKILLS.md"
  "$ROOT/docs/gtm-data-infra.md"
  "$ROOT/docs/onboarding-surfaces.md"
  "$ROOT/docs/product-accuracy.md"
  "$ROOT/docs/sales-questions-by-deal-phase.md"
  "$ROOT/docs/purpose-scorecard.md"
  "$ROOT/docs/onboarding/SALES-FAQ.md"
  # grep -I below treats a binary file as a non-match, so this entry never actually catches
  # a leak in the PDF's own bytes — real coverage is the source it's rendered from,
  # END-USER-ONBOARDING.md (scanned via SCAN_DIRS below). Listed anyway so the file's
  # presence in the shipped set is documented here, not just in oss-export.sh.
  "$ROOT/docs/onboarding/END-USER-ONBOARDING.pdf"
)

# Real hostnames/URLs that must never ship (regex, case-insensitive). The repo convention
# is to use <domain>/example.com placeholders; these catch leaked literals. Sourced from the
# same denylist file (host: lines); empty when the file is absent (public cut).
HOST_RE="${DEBRAND_HOST_RE:-$(_deny host)}"

if [ "$RELEASE" -eq 1 ]; then
  # Pre-publish: scan the entire to-be-published engine surface, and add the release-only
  # tokens (prior tenants, org name, personal handles — the `rtoken:` lines) to the set.
  _rel="$(_deny rtoken)"
  TOKENS="${DEBRAND_RELEASE_TOKENS:-${TOKENS}${_rel:+|$_rel}}"
  # Scope = everything the carve ships (scripts/oss-export.sh §2), nothing less: its
  # ALLOW_DIRS, its ALLOW_FILES, and the two CI workflows it copies. tests/, packs/,
  # telegram/ and .semgrep/ ship in the carve but were unscanned here until 2026-08-12,
  # when a tenant slug in a brand-new test fixture passed this lint and would have
  # failed only the carve's own post-carve token sweep — the same narrower-than-the-carve
  # scope gap this RELEASE mode exists to prevent. The root ALLOW_FILES and
  # .github/workflows/{ci,static-analysis}.yml were the residual half of that same gap,
  # closed 2026-08-28: the private GitHub App name sat in a comment block in BOTH carved
  # workflows for four days, invisible to this lint and caught only by the export's own
  # post-carve sweep. Anything oss-export.sh copies belongs in this list.
  # tests/linter/safe_share_denylist.txt names every tenant BY DESIGN (it is the token
  # source) and is excluded from the carve by the export's rsync — filtered via
  # .debrandignore rather than skipped here, so a stray copy elsewhere still flags.
  SCAN_DIRS=(
    "$ROOT/plugin"
    "$ROOT/gtm_core"
    "$ROOT/agent"
    "$ROOT/backend"
    "$ROOT/mcp_server"
    "$ROOT/cockpit"
    "$ROOT/telegram"
    "$ROOT/schemas"
    "$ROOT/tests"
    "$ROOT/packs"
    "$ROOT/.semgrep"
    "$ROOT/profiles/_template"
    "$ROOT/END-USER-ONBOARDING.md"
    "$ROOT/scripts/bootstrap.sh"
    "$ROOT/.github/workflows/ci.yml"
    "$ROOT/.github/workflows/static-analysis.yml"
    "$ROOT/Dockerfile"
    "$ROOT/pyproject.toml"
    "$ROOT/docker-compose.override.yml.example"
    "$ROOT/.env.example"
    "$ROOT/.gitleaks.toml"
    "$ROOT/.semgrepignore"
    "$ROOT/.pre-commit-config.yaml"
    "$ROOT/.gitignore"
    "${SHIPPING_DOCS[@]}"
  )
  echo "▶ de-brand lint: RELEASE mode (pre-publish gate) — widened scope + tokens ($TOKENS) + hosts (/$HOST_RE/)"
fi

# Allowlist: tests/lint/.debrandignore holds fixed-string patterns (one per line, '#' comments
# ignored) for KNOWN-LEGIT matches. Keep entries specific (a full phrase or path) so a real
# leak is never masked. Applied to every hit stream below.
ALLOWLIST_FILE="$ROOT/tests/lint/.debrandignore"
_filter_allowed() {
  if [ -f "$ALLOWLIST_FILE" ]; then
    local pats
    pats="$(grep -vE '^[[:space:]]*(#|$)' "$ALLOWLIST_FILE" 2>/dev/null || true)"
    if [ -n "$pats" ]; then grep -vF -f <(printf '%s\n' "$pats") || true; else cat; fi
  else
    cat
  fi
}

fail=0

# ── substring token pass ──────────────────────────────────────────────────────
# Distinctive tokens match as substrings (aggressive on purpose). A token of ≤3 chars
# is a collision magnet — the three-letter tenant slug is a substring of the fictional
# DevTrial fixture — so short tokens match whole-word only, mirroring the \b anchoring
# the export sweep already applies (scripts/oss-export.sh's TOKENS comment: "Any token
# this short needs the same treatment"). / and - count as word boundaries, so the slug
# still flags in a path segment or a hyphenated name.
for tok in $(echo "$TOKENS" | tr '|' ' '); do
  word_flag=""
  [ "${#tok}" -le 3 ] && word_flag="-w"
  for path in "${SCAN_DIRS[@]}"; do
    [ -e "$path" ] || continue
    if hits="$(grep -rinI $word_flag --exclude-dir=__pycache__ "$tok" "$path" 2>/dev/null | _filter_allowed)" && [ -n "$hits" ]; then
      echo "✗ de-brand lint: company token '$tok' found in ${path#"$ROOT"/} (must be ZERO):"
      echo "$hits" | sed 's/^/    /'
      fail=1
    fi
  done
done

# ── hostname/URL regex pass (RELEASE only) ────────────────────────────────────
# Guard on non-empty HOST_RE: an empty pattern makes grep -E match every line, which
# would flag the whole tree in a public cut (denylist file absent → HOST_RE empty).
if [ "$RELEASE" -eq 1 ] && [ -n "$HOST_RE" ]; then
  for path in "${SCAN_DIRS[@]}"; do
    [ -e "$path" ] || continue
    if hits="$(grep -rEinI --exclude-dir=__pycache__ "$HOST_RE" "$path" 2>/dev/null | _filter_allowed)" && [ -n "$hits" ]; then
      echo "✗ de-brand lint: hostname/URL leak matching /$HOST_RE/ in ${path#"$ROOT"/} (use <domain>/example.com):"
      echo "$hits" | sed 's/^/    /'
      fail=1
    fi
  done
fi

if [ "$fail" -eq 0 ]; then
  if [ "$RELEASE" -eq 1 ]; then
    echo "✓ de-brand lint (RELEASE): no company tokens/hosts in the publishable engine (checked: $TOKENS + /$HOST_RE/)"
  else
    echo "✓ de-brand lint: no company tokens in plugin/skills/, gtm_core/skills/ or packs/ (checked: $TOKENS)"
  fi
fi
exit "$fail"
