#!/usr/bin/env bash
# Usage-index gate (knowledge-lifecycle PRD, Phase 2): the committed skill↔knowledge dependency
# map docs/knowledge-usage.md is generated from plugin/skills/ — fail if it drifted from a fresh
# render, so the map (which skills read which knowledge topic; which topics no skill reads) can
# never go stale after a skill is added or a knowledge reference changes.
#
# Fix with: uv run python -m gtm_core.knowledge_usage generate
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
uv run python -m gtm_core.knowledge_usage check
