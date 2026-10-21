---
name: video-script
description: >-
  Script stage of the video pipeline (routed via video-router): turns an approved video brief
  into a shot-by-shot script or scenes.json with cold opens, beats, captions, and visual
  prompts. Trigger when the user says "write the video script", "turn the plan into a
  short-form script", "write the hook and beats", or as the script stage of the video pack.
metadata:
  version: "0.22.0"
  phase: "6"
  capability_tier: pipeline
---
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

Its declared interface is above (`video-script`, tier `pipeline`).
Pack graph node(s) that invoke it: `video/cross-modal-campaign`, `video/live-action-video`, `video/presenter-video`, `video/short-form-video`.

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/direct-response-patterns.md`, `docs/virality-engineering.md`.

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
