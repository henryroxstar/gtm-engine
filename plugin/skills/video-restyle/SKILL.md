---
name: video-restyle
description: >-
  Restyle the operator's own real footage (4-120s) into an on-brand short using Higgsfield's
  shorts_studio_create and the brand kit's restyle_preset_id (a look trained once by
  identity-kit from 1-20 brand reference files, then reused). Interactive-first by
  construction: the upload is media_upload_widget, browser-side by provider design, so a
  headless run stops at the gate and asks rather than faking an upload. Preflights the exact
  spend via get_cost + duration_seconds before generating, and states the 720p-only output
  constraint in its report rather than hiding it. This skill should be used when the user says
  'restyle this video', 'apply the brand look to my footage', 'make this on-brand', or as the
  restyle stage of the creator pack's restyle-shorts variant.
metadata:
  version: "0.1.0"
  phase: "11"
  capability_tier: production
---
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`video-restyle`, tier `production`).
Pack graph node(s) that invoke it: `creator/restyle-shorts`.

See `docs/SKILLS.md` for the full skill roster.
