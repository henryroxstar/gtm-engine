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
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`video-clip`, tier `production`).
Pack graph node(s) that invoke it: `creator/live-action-video`, `creator/repurpose-clips`.

See `docs/SKILLS.md` for the full skill roster.

## Interface Contract

- **Target & Output:** Repurposes long-form video into 1–20 short-form clips via Reap Video Studio `create_clips`. Saves clips under `content/<active>/video/clips/` with a manifest of Reap project/clip IDs.
- **Source Types:** Accepts local video files (`gtm_core.reap_upload` host-pinned PUT) or public source URLs from the plan gate.
- **Spend & Cap Rules:** Confirms via Reap two-step flow; tracks unit spend via `gtm_core.ledger_cli month-units --tool reap`.
- **Caption Discipline:** Uses brand kit `captions.preset` (or neutral Reap preset). Enforces layout: centre 60% of frame, ~48-62px at 1080w, 28-36 chars/line, max 2 lines.
