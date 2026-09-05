---
name: demo-capture
description: >-
  Turn LOCAL footage the operator already has — a phone recording of themselves, a camera
  file, a webinar export, or a product screen recording — into finished short-form clips via
  Reap. This is the repo's local-file ingest lane: the file is PUT through
  `gtm_core.reap_upload` (host-pinned, content-root-confined, signature-freshness checked) and
  clipped by `create_clips`, so nothing has to be uploaded to YouTube or Vimeo first. Use it
  when the operator says 'I recorded myself', 'here's my phone video', 'clip this file', 'I
  have footage on my laptop', 'turn this recording into shorts', 'make a demo clip', or 'clip
  this screen recording'. Real footage carries NO EU AI Act Article 50 disclosure duty because
  nothing is synthesised, and it clears the likeness bar by construction — which is why this
  is the preferred lane for anything showing the operator's face. Reads the clip/caption plan
  from the operator gate; never publishes.
metadata:
  version: "0.2.0"
  phase: "C"
  capability_tier: production
---
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`demo-capture`, tier `production`).
Pack graph node(s) that invoke it: `creator/demo-clips`.

See `docs/SKILLS.md` for the full skill roster.
