---
name: capabilities
description: >-
  Explain what the engine can do and what tools are currently connected in plain English.
  Trigger when the user says "what can you help me with", "what's connected", "what can you
  do", "help me", or "show capabilities".
metadata:
  version: "0.1.0"
  phase: "UX"
  capability_tier: core
---
# capabilities — what the engine can do and what is connected

Explain what the engine can do and which tools are connected in the founder's words.

## Step 1 — Check technical connectivity

Run the capabilities probe:

```bash
uv run python -m gtm_core.capabilities
```

## Step 2 — Cross-check with session tools

Desktop-side OAuth connectors are invisible to the environment variables checked by the process.
Inspect the tools available in your current session context:
- If a connector tool (e.g. Google, Vibe, Slack, HubSpot) is visible in your active tool list, count it as connected.
- Report the union honestly — never claim a tool is disconnected if you can see its active MCP tools.

## Step 3 — Present what is connected in plain English

State clearly:
1. **Connected tools**: What each tool gives the founder.
2. **Not connected**: Plain instructions on where to connect it (e.g. `Settings → Connectors → Add custom connector`). Never show raw environment variable names.

## Step 4 — Suggest the most useful next steps

Offer the most helpful commands the founder can say right now:
- *"Run my weekly market scan"* — scan market news and competitor signals
- *"Prospect for accounts"* — find and qualify high-fit target buyers
- *"Plan this week's content"* — propose cross-platform posts
- *"Where do I stand?"* — see current pipeline status and whose move it is
- *"What's my budget?"* — check spend against your monthly cap
- *"Learn from my new material"* — update company facts from documents or decks

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
