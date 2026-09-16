---
name: video-finish
description: >-
  Stitch rendered video beats, burn timed captions, mix audio beds, and apply transitions to
  produce ready-to-publish assets. Trigger when the user says "finish the video", "burn
  captions", "stitch video clips", "mix video audio", or as the finish stage of the creator
  pack.
metadata:
  version: "0.7.0"
  phase: "A"
  capability_tier: pipeline
---
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`video-finish`, tier `pipeline`).
Pack graph node(s) that invoke it: `creator/presenter-video`, `creator/short-form-video`.

See `docs/SKILLS.md` for the full skill roster.
