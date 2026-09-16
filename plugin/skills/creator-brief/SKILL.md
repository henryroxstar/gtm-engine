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
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`creator-brief`, tier `pipeline`).
Pack graph node(s) that invoke it: `creator/live-action-video`, `creator/presenter-video`, `creator/short-form-video`.

See `docs/SKILLS.md` for the full skill roster.
