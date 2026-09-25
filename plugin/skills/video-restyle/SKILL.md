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
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

Its declared interface is above (`video-restyle`, tier `production`).
Pack graph node(s) that invoke it: `creator/restyle-shorts`.

See `docs/SKILLS.md` for the full skill roster.

## Interface Contract

- **Target & Output:** Restyles real footage (4–120s) into an on-brand short using Higgsfield `shorts_studio_create`.
- **Look Preset:** Applies brand kit's `restyle_preset_id` trained from brand reference files. Output is 720p.
- **Upload Flow:** Interactive-first; requires browser-side `media_upload_widget`. Headless sessions pause and prompt operator.
- **Preflight & Spend:** Preflights cost via `get_cost` + duration; enforces monthly cap.

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
