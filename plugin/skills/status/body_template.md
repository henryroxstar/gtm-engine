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
