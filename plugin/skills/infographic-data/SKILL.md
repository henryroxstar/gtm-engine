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
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`infographic-data`, tier `production`).

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/product-accuracy.md`.

## Interface Contract

- **Target & Output:** Renders single-image data-dense editorial infographics via Higgsfield from research briefs or source documents.
- **Layout & Platforms:** Re-flows layout per platform (LinkedIn 4:5, X 16:9, Instagram 4:5/9:16). Brand palette via `gtm_core.brandkit`.
- **Validation & Gate:** Pins spec at plan gate before paid call; runs mandatory vision accuracy check comparing render to approved spec.
- **Budget:** `get_cost` preflight; hard budget ceiling. Free fallback is text wireframe.
