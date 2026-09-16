---
name: infographic-handwritten
description: >-
  Render handwritten-style notebook, formula, or whiteboard infographics on paper texture
  using Higgsfield. Trigger when the user says "make a handwritten infographic",
  "whiteboard-style graphic", "notebook sketch of [framework]", or "hand-drawn visual".
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

Its declared interface is above (`infographic-handwritten`, tier `production`).

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/product-accuracy.md`.

## Interface Contract

- **Target & Output:** Renders single-image handwritten-style infographics (notebook page, whiteboard, formula sheet) via Higgsfield.
- **Spec & Platforms:** Pins elements/labels in approved spec at plan gate before paid call. Supports LinkedIn 4:5, X 16:9, Instagram 4:5/9:16.
- **Accuracy Verification:** Mandatory vision accuracy check ensures handwritten text is correct and legible.
- **Budget:** `get_cost` preflight; hard budget cap. Free fallback is text wireframe.
