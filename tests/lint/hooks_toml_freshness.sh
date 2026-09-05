#!/usr/bin/env bash
# hooks.toml / hook-matrix.md freshness gate (Grade A+ Phase 12).
#
# For every profile that has BOTH files, a hook id present in BOTH must not drift.
# Profiles that still use hook-matrix.md alone are not forced to migrate.
#
# Two scoping rules, because the files are not two views of one set (docs/hook-craft.md):
# hook-matrix.md holds 1:1 OUTREACH openers (persona x why-now signal for a named
# account); hooks.toml holds the 1:many FEED hook bank. They legitimately diverge.
#
#   1. A hook-matrix.md with no `id` column is a persona x signal grid, not a hook
#      table. It is skipped entirely -- comparing it yields persona labels, not ids.
#   2. Where both files ARE id-keyed, the check is one-directional: every id in
#      hook-matrix.md must still exist in hooks.toml (a missing one means the
#      migration went stale). hooks.toml may carry MORE -- feed-only hooks that never
#      had an outreach row are expected, not drift.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

ERRORS=0
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

for matrix in profiles/*/knowledge/hook-matrix.md; do
    [ -e "$matrix" ] || continue
    profile_dir="$(dirname "$matrix")"
    toml="$profile_dir/hooks.toml"
    profile="$(basename "$(dirname "$profile_dir")")"

    if [ ! -f "$toml" ]; then
        continue
    fi

    # Rule 1: a matrix with no `id` column is a persona x signal grid, not a hook
    # table. Comparing it would yield persona labels ("**CISO**"), never hook ids.
    if ! grep -qiE '^\|\s*id\s*\|' "$matrix"; then
        continue
    fi

    matrix_ids="$TMPDIR/${profile}-matrix.ids"
    toml_ids="$TMPDIR/${profile}-toml.ids"
    missing="$TMPDIR/${profile}-missing.ids"

    # Hook ids from hook-matrix.md table rows. Drops delimiter rows and the literal
    # "id" header token, which repeats once per table in a multi-table matrix.
    (grep -E '^\|[^-].*\|' "$matrix" | awk -F'|' '{print $2}' | sed 's/ //g' \
        | grep -vxiE 'id' | grep -v '^$' | sort -u) > "$matrix_ids" || true

    # Hook ids from hooks.toml id = "..." lines.
    (grep -E '^id\s*=' "$toml" | sed -E 's/.*"([^"]+)".*/\1/' | sort -u) > "$toml_ids" || true

    # Rule 2: matrix ids must be a SUBSET of hooks.toml. Extra feed-only hooks are fine.
    comm -23 "$matrix_ids" "$toml_ids" > "$missing" || true

    if [ -s "$missing" ]; then
        echo "hook-matrix.md ids missing from hooks.toml in profile: $profile" >&2
        echo "  missing: $(tr '\n' ' ' < "$missing")" >&2
        ERRORS=$((ERRORS + 1))
    fi
done

if [ "$ERRORS" -gt 0 ]; then
    echo "Found $ERRORS mismatch(es). Re-run migration or update hooks.toml." >&2
    exit 1
fi

echo "hooks.toml / hook-matrix.md freshness OK"
