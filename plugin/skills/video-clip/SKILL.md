---
name: video-clip
description: >-
  Repurpose the operator's own long-form video into 1-20 short-form clips using Reap Video
  Studio's create_clips — the highest-volume, lowest-marginal-cost lane in the creator pack (a
  single job returns many clips instead of one render call per asset). Takes EITHER a local
  file the operator already has (preferred — uploaded via gtm_core.reap_upload's host-pinned,
  content-root-confined PUT, so a phone recording never has to be posted to YouTube first) OR
  a public source URL, whichever the operator supplied at the plan gate; warns before spending
  if a URL does not look like the tenant's own channel. create_clips has a two-step confirm
  flow (a plannedSettings response with no project id is a confirmation request, never a
  completed job) and NO cost/balance tool anywhere in Reap's surface, so the ledger is the
  only meter: a cost row is written the instant a job is confirmed submitted, self-tracked by
  clip count rather than a verified spend figure, checked against a unit-count cap (python -m
  gtm_core.ledger_cli month-units --tool reap --unit media_credits) instead of a dollar cap.
  Caption styling prefers the brand kit's declared captions.preset and falls back to a neutral
  Reap preset (stated as such, never implied to be brand-matched); returned captions are
  judged against Reap's published spec — centre 60% of frame, ~48-62px at 1080 width, 28-36
  chars/line, max 2 lines. Saves every clip under content/<active>/video/clips/ with a
  manifest recording each clip's Reap clip/project id for the scoring stage. This skill should
  be used when the user says 'repurpose this video', 'clip my long-form video', 'make shorts
  from this video', or as the clip stage of the creator pack's repurpose-clips variant.
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
