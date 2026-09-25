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
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

Its declared interface is above (`demo-capture`, tier `production`).
Pack graph node(s) that invoke it: `creator/demo-clips`.

See `docs/SKILLS.md` for the full skill roster.

## Interface Contract

- **Target & Output:** Ingests local footage (screen recording, camera file, webinar) and produces finished short-form clips via Reap `create_clips`.
- **Egress & Security:** Uploads local file via host-pinned `gtm_core.reap_upload`. Real footage carries no EU AI Act Article 50 disclosure duty.
- **Spend & Cap:** Pre-checks budget with `gtm_core.ledger_cli month-total`. Never publishes.

## How to close this run (every surface)

Report, in this order and in the operator register (the `gtm-operator` output style): Lead with the outcome; what matters about it in their terms; the next decision as a choice they can answer; and what it cost, exactly as the ledger reported it, if anything metered ran.
File paths, commands, module names and raw output go in a final
<details><summary>Details</summary> … </details> block; the main reply must make sense
without it.

Markers: emit a ⟦…⟧ marker (⟦GATE:…⟧, ⟦POST⟧, ⟦FILE:…⟧) only when your system prompt carries
a `Surface:` line that says so. Otherwise show the same content as a quoted block headed
"This is exactly what would go out."

Active profile: the one in your system instructions, or, in the desktop app, the answer to
`uv run python -m gtm_core.active_profile show`.
