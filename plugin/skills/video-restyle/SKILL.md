---
name: video-restyle
description: >-
  Apply trained brand styles, palettes, and visual presets to real footage using Higgsfield
  Shorts Studio. Trigger when the user says "restyle this video", "apply brand look to
  footage", "restyle shorts", or as the restyle stage of the creator pack.
metadata:
  version: "0.1.0"
  phase: "11"
  capability_tier: production
---
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`video-restyle`, tier `production`).
Pack graph node(s) that invoke it: `creator/restyle-shorts`.

See `docs/SKILLS.md` for the full skill roster.

## Interface Contract

- **Target & Output:** Restyles real footage (4–120s) into an on-brand short using Higgsfield `shorts_studio_create`.
- **Look Preset:** Applies brand kit's `restyle_preset_id` trained from brand reference files. Output is 720p.
- **Upload Flow:** Interactive-first; requires browser-side `media_upload_widget`. Headless sessions pause and prompt operator.
- **Preflight & Spend:** Preflights cost via `get_cost` + duration; enforces monthly cap.
