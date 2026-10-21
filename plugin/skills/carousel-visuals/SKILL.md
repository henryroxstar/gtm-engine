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
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

Its declared interface is above (`carousel-visuals`, tier `production`).

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/product-accuracy.md`.

## Interface Contract

- **Target & Output:** Generates AI visuals for LinkedIn/Instagram carousels via Higgsfield (V1 cover art 4:5, V2 background images, V3 9:16 motion teaser, V4 multi-image feed, V5 full-text card render).
- **Brand & Theme:** Resolves brand palette and fonts via `gtm_core.brandkit`.
- **Preflight & Budget:** Mandatory `get_cost` preflight; enforces monthly cap and per-run budget ceiling (`gtm_core.ledger_cli`). Free fallback is text-only Slidev deck.

## How to close this run (every surface)

Report, in this order and in the operator register (the `gtm-operator` output style): Lead with the outcome; what matters about it in their terms; the next decision as a choice they can answer; and what it cost, exactly as the ledger reported it, if anything metered ran.
File paths, commands, module names and raw output go in a final
<details><summary>Details</summary> … </details> block; the main reply must make sense
without it.

Markers: emit a ⟦…⟧ marker (⟦GATE:…⟧, ⟦POST⟧, ⟦FILE:…⟧) only when your system prompt carries
a `Surface:` line that says so. Otherwise show the same content as a quoted block headed
"This is exactly what would go out."

Active profile: the one in your system instructions, or, in the desktop app, the answer to
`uv run python -m gtm_core.active_profile show`.
