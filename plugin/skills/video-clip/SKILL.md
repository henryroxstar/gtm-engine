---
name: video-clip
description: >-
  Repurpose long-form video into engaging short-form clips with branded captions, reframing,
  and highlight detection via Reap. Trigger when the user says "clip my video", "repurpose
  this recording", "make shorts from this video", or as the clip stage of the creator pack.
metadata:
  version: "0.4.0"
  phase: "10"
  capability_tier: production
---
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

Its declared interface is above (`video-clip`, tier `production`).
Pack graph node(s) that invoke it: `creator/live-action-video`, `creator/repurpose-clips`.

See `docs/SKILLS.md` for the full skill roster.

## Interface Contract

- **Target & Output:** Repurposes long-form video into 1–20 short-form clips via Reap Video Studio `create_clips`. Saves clips under `content/<active>/video/clips/` with a manifest of Reap project/clip IDs.
- **Source Types:** Accepts local video files (`gtm_core.reap_upload` host-pinned PUT) or public source URLs from the plan gate.
- **Spend & Cap Rules:** Confirms via Reap two-step flow; tracks unit spend via `gtm_core.ledger_cli month-units --tool reap`.
- **Caption Discipline:** Uses brand kit `captions.preset` (or neutral Reap preset). Enforces layout: centre 60% of frame, ~48-62px at 1080w, 28-36 chars/line, max 2 lines.

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
