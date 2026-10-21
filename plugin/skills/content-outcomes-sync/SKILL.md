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
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

Its declared interface is above (`content-outcomes-sync`, tier `pipeline`).
Pack graph node(s) that invoke it: `outcomes-loop/content-outcomes-loop`.

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/x-tweet-patterns.md`.

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
