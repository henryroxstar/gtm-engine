---
name: demo-capture
description: >-
  Transform local product screen recordings or phone footage into polished short-form demo
  clips via Reap. Trigger when the user says "clip this recording", "turn footage into
  shorts", "make a demo clip", "I recorded myself", or "repurpose local video".
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

## Interface Contract

- **Target & Output:** Ingests local footage (screen recording, camera file, webinar) and produces finished short-form clips via Reap `create_clips`.
- **Egress & Security:** Uploads local file via host-pinned `gtm_core.reap_upload`. Real footage carries no EU AI Act Article 50 disclosure duty.
- **Spend & Cap:** Pre-checks budget with `gtm_core.ledger_cli month-total`. Never publishes.
