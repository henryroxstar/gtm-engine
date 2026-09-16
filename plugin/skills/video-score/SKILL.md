---
name: video-score
description: >-
  Score short-form video variants on retention and virality to recommend optimal cuts and
  platform destinations. Trigger when the user says "score the renders", "which cut should we
  post", "check virality", "rank the variants", or as the score stage of the creator pack.
metadata:
  version: "0.6.0"
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

Its declared interface is above (`video-score`, tier `pipeline`).
Pack graph node(s) that invoke it: `creator/demo-clips`, `creator/live-action-video`, `creator/presenter-video`, `creator/repurpose-clips`, `creator/restyle-shorts`, `creator/short-form-video`.

See `docs/SKILLS.md` for the full skill roster.
