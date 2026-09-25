---
name: creator-brief
description: >-
  Plan a short-form video concept into an approved brief.json, creative brief, and beat sheet
  before script generation. Trigger when the user says "plan a video about [topic]", "write a
  creator brief", "creative brief for [concept]", or as the brief stage of the creator pack.
metadata:
  version: "0.5.0"
  phase: "6"
  capability_tier: pipeline
---
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

Its declared interface is above (`creator-brief`, tier `pipeline`).
Pack graph node(s) that invoke it: `creator/live-action-video`, `creator/presenter-video`, `creator/short-form-video`.

See `docs/SKILLS.md` for the full skill roster.

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
