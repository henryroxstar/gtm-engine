---
name: status
description: >-
  Answer "where do I stand?" for the current prospecting list in plain words: whose move it
  is, what is held back and why, what is already in the sending tool. Trigger when the user
  says "where do I stand", "status", "what's waiting on me", or "how is my list doing".
metadata:
  version: "0.1.0"
  phase: "UX"
  capability_tier: core
---
# status — where the current prospecting list stands

Answer "where do I stand?" for the current prospecting list in plain words: whose move it is, what is held back and why, and what is already in the sending tool.

## The procedure (execute in order)

**Step 1 — Check freshness and run the status CLI.**
First, check that the campaign status page is fresh:

```bash
uv run python -m gtm_core.email_campaign_dashboard --profile <active> --check-fresh
```

If the freshness check fails (non-zero exit code), refresh the status page:

```bash
uv run python -m gtm_core.email_campaign_dashboard --profile <active>
```

Then run the status summary CLI:

```bash
uv run python -m gtm_core.prospect_status_cli --profile <active>
```

**Step 2 — Present the lede to the operator.**
If the output says "Nothing to show yet — run your prospecting first":
State clearly:
*"You haven't run prospecting yet. Say 'Run my prospecting' to start."*

Otherwise:
Extract **only the lines above "For the record"** (the lede). These lines explain:
- How many contacts can go out today (and why if none).
- What is waiting on the operator ("Yours").
- What is being handled by the engine.

Present these lines directly to the operator inside an operator block:
<!-- operator -->
[Paste the lede lines here]
<!-- /operator -->

**Step 3 — Keep detailed tables collapsed.**
Never show the full accounts or contacts breakdown tables unless the operator explicitly asks for them.

<details>
<summary>Technical details: status reports and storage</summary>
Status state is read from evals/lanes-state.jsonl and the ledger under the active profile directory.
</details>

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
