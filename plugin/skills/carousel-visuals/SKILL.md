---
name: carousel-visuals
description: >-
  Generate AI visuals, cinematic cover art, background cards, and motion teaser videos for
  carousels using Higgsfield. Trigger when the user says "add visuals to my carousel",
  "generate cover art for the carousel", "make an image carousel", "create a motion teaser",
  or "make it visual".
metadata:
  version: "0.7.0"
  phase: "3B"
  capability_tier: production
---
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`carousel-visuals`, tier `production`).

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/product-accuracy.md`.

## Interface Contract

- **Target & Output:** Generates AI visuals for LinkedIn/Instagram carousels via Higgsfield (V1 cover art 4:5, V2 background images, V3 9:16 motion teaser, V4 multi-image feed, V5 full-text card render).
- **Brand & Theme:** Resolves brand palette and fonts via `gtm_core.brandkit`.
- **Preflight & Budget:** Mandatory `get_cost` preflight; enforces monthly cap and per-run budget ceiling (`gtm_core.ledger_cli`). Free fallback is text-only Slidev deck.
