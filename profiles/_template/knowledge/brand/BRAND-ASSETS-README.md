---
refreshed: 2026-09-01
review: 180d
---
# Brand Asset Library (TEMPLATE)

Inventory of the brand ASSETS this profile holds — logo files, fonts, product screenshots,
infographics, diagrams — and guidance on where each one belongs.

## This file declares no colours and no typefaces

Those live in exactly one place:

    profiles/<this-profile>/knowledge/BRAND.toml

Read them with `uv run python -m gtm_core.brandkit --profile <active> [--product <slug>]`.

Do not put a palette, an accent token table, or a font name in this file. An asset README that
also declares colour becomes a second source of truth, and a repo with two of those reliably
grows a third. If you are looking at a hex here, it is a bug.

## Directory layout

| Path | Holds |
|---|---|
| `logos/` | Official logo files. Wire them into `BRAND.toml [assets]` by lockup and background. |
| `fonts/` | Real font FILES (`.woff2` / `.ttf`). A face name alone cannot rasterise. |
| `product-screenshots/` | Real product UI. Pair each with a feature name and a one-line outcome. |
| `infographics/` | Diagrams and infographics extracted from decks or design sources. |

## Logos — the one rule that matters

**Never generate, redraw, trace or approximate a logo.** Not as a placeholder, not for a mockup,
not because the real files were not to hand. A profile with no logo files simply has none: deliver
the design with an intentional logo-safe area and say the official mark must be added before
publication. Governance rules (clear space, minimum size, placement) belong in `BRAND.toml [logo]`.

## Inventory

_List each asset with its source and its intended use. Delete this line once the table exists._

| File | Source | Shows / use |
|---|---|---|
