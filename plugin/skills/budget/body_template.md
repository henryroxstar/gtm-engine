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
