---
name: infographic-data
description: >-
  Render postable data-dense editorial infographics with charts and callout stats using
  Higgsfield based on approved specs. Trigger when the user says "make a data infographic",
  "visualize this report", "turn these stats into an infographic", or "render the data
  infographic".
metadata:
  version: "0.3.0"
  phase: "3B"
  capability_tier: production
---
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

Its declared interface is above (`infographic-data`, tier `production`).

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/product-accuracy.md`.

## Interface Contract

- **Target & Output:** Renders single-image data-dense editorial infographics via Higgsfield from research briefs or source documents.
- **Layout & Platforms:** Re-flows layout per platform (LinkedIn 4:5, X 16:9, Instagram 4:5/9:16). Brand palette via `gtm_core.brandkit`.
- **Validation & Gate:** Pins spec at plan gate before paid call; runs mandatory vision accuracy check comparing render to approved spec.
- **Budget:** `get_cost` preflight; hard budget ceiling. Free fallback is text wireframe.

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
