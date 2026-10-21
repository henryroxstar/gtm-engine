---
name: budget
description: >-
  Report current month tool spend and monthly budget cap in one plain sentence. Trigger when
  the user says "what's my budget", "how much have we spent", "budget", "cost", or "check
  budget".
metadata:
  version: "0.1.0"
  phase: "UX"
  capability_tier: core
---
# budget — tool spend and monthly budget cap

Report tool spend and monthly budget in one plain sentence.

## Step 1 — Check budget status

Run the deterministic budget CLI for the active profile:

```bash
uv run python -m gtm_core.budget_status --profile <active>
```

## Step 2 — Report the one sentence

Repeat the exact rendered sentence to the operator:

<!-- operator -->
> "$X.XX of your $Y.YY for Month. Resets 1 NextMonth."
<!-- /operator -->

Do not invent numbers or elaborate with unnecessary breakdowns unless explicitly asked.

## Step 3 — If asked to change or raise the cap

The engine cannot edit your profile file directly. Say:

"I can't change your cap from here. It's the `monthly_tool_budget_usd` line in your profile file; open it and change the number, and I'll use the new cap on the next run."

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
