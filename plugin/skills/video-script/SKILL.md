---
name: video-script
description: >-
  Turn an approved short-form video concept into a shot-by-shot script with cold opens, beats,
  captions, and visual prompts. Trigger when the user says "write the video script", "script
  this reel", "turn the plan into a short-form script", "write the hook and beats", or "script
  a 90-second video".
metadata:
  version: "0.22.0"
  phase: "6"
  capability_tier: pipeline
---
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`video-script`, tier `pipeline`).
Pack graph node(s) that invoke it: `creator/cross-modal-campaign`, `creator/live-action-video`, `creator/presenter-video`, `creator/short-form-video`.

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/direct-response-patterns.md`, `docs/virality-engineering.md`.
