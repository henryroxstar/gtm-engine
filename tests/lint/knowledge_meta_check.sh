#!/usr/bin/env bash
# Knowledge-metadata gate (knowledge-lifecycle PRD, Phase 1): every managed knowledge topic file
# (across ALL profiles) must carry valid freshness/provenance frontmatter — source, refreshed
# (YYYY-MM-DD), review cadence. Fails if any managed topic is missing frontmatter or has an
# invalid field, so the corpus can never silently lose its lifecycle metadata.
#
# Fix with: uv run python -m gtm_core.knowledge_meta seed --all
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
uv run python -m gtm_core.knowledge_meta check --all
