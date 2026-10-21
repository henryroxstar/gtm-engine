#!/usr/bin/env bash
# Codegen-sync gate (Phase A): the Python skill manifest is canonical;
# plugin/skills/<name>/SKILL.md is generated from it. Fail if any committed
# SKILL.md drifted from its manifest. Run FIRST in /gtm-check so downstream gates
# (debrand/resolve/manifest) inspect guaranteed-fresh SKILL.md.
#
# Fix with: uv run python -m gtm_core.skills.codegen generate-all
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
uv run python -m gtm_core.skills.codegen check
