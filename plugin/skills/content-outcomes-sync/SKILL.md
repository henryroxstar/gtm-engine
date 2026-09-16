---
name: content-outcomes-sync
description: >-
  Sync published post performance from Buffer MCP and social channels into content outcome
  ledgers to calibrate predictor_band accuracy. Trigger when the user says "sync content
  outcomes", "how did our posts perform", "analyze content metrics", or "calibrate virality
  scores".
metadata:
  version: "0.9.0"
  phase: "8"
  capability_tier: pipeline
---
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`content-outcomes-sync`, tier `pipeline`).
Pack graph node(s) that invoke it: `outcomes-loop/content-outcomes-loop`.

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/x-tweet-patterns.md`.
