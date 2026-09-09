---
name: content-research
description: >-
  Research a planned content item into verifiable, citable material for the active company.
  For a given ContentItem from the week's plan, gathers 3–6 verifiable facts (each with a
  source), strong quotables, credible counterpoints, and an explicit "claims to avoid / needs
  a caveat" list. DeepSeek (the worker MCP) does bulk extraction; Claude verifies every claim
  against the source and drops anything unsupported. Free web by default via the Firecrawl
  MCP. This skill should be used when the user says "research this item", "research the
  content", "get facts for the post", "content research", or after a content plan is approved.
metadata:
  version: "0.1.0"
  phase: "1"
  capability_tier: core
---

# Content Research

Turn one approved `ContentItem` into a tight research pack the studio can draft from — facts that
are **verifiable and sourced**, not vibes. DeepSeek extracts in bulk; Claude verifies.

> Resolve the **active profile** (the agent provides it; company facts load from
> `profiles/<active>/`). The only writable state is `content/<active>/`.

## Step 0 — Pick the item + budget check

- **Ad-hoc mode (no plan item).** If the user asks to research a **raw topic** with no approved
  `ContentItem` (an off-pipeline request), skip the plan read: research the topic directly and return
  the fact pack plus **2–3 angle options** (each a one-line take + who it's for) instead of updating a
  plan item. Everything else below (sourcing, verify, cite-or-drop) applies unchanged. Pair with the
  `brainstorming` skill when the ask is ideation rather than facts.
- Otherwise (pipeline mode): read the approved plan `content/<active>/plans/<YYYY-WW>-plan.json`. Take
  the `ContentItem` the user named (or the highest-priority unresearched one).
- Read its `StoryCluster` from the matching `content/<active>/radar/*-clusters.json` (via
  `story_id`) for the source URLs and angle seeds.
- Budget guard before any metered call:
  ```bash
  python -m gtm_core.ledger_cli month-total --profile <active>
  ```
  If at/over `monthly_tool_budget_usd` (from PROFILE), stop and tell the user.

## Step 1 — Gather sources (free web first)

Default to free web. Use the **Firecrawl** MCP to scrape the cluster's source URLs and a few fresh
corroborating sources. **If Firecrawl is not configured** (no `FIRECRAWL_API_KEY`, so the tool isn't
loaded), fall back to the built-in keyless **`WebFetch`** (and `WebSearch` to find corroborating
sources) — same job, no key required, just less structured. Pull the active company's own grounding
from `profiles/<active>/knowledge/` (e.g. `company.md`, `product.md`, `case-studies.md`) so claims
stay on-narrative and accurate.

## Step 2 — Extract (worker MCP) → verify (Claude)

Send the gathered source text to the **worker** MCP `summarize` tool to extract candidate facts,
quotes, and counterpoints in bulk. Then YOU (Claude) verify EACH item against its source:

- Keep a fact only if it is directly supported by a cited source — record the source URL.
- Drop or flag anything the worker asserts that the source does not support (the worker may
  hallucinate — assume nothing).

## Step 3 — Write the research pack

Write `content/<active>/research/<item-id>.md` with:

- **Verifiable facts (3–6)** — each with its source link.
- **Quotables** — verbatim, attributed lines usable in the post.
- **Counterpoints** — credible opposing views to acknowledge (makes the post defensible).
- **Claims to avoid / needs a caveat** — anything unverified, overstated, or legally sensitive.

Then update the item in `content/<active>/plans/<YYYY-WW>-plan.json`: set
`research_ref` to the research file path and `status` to `researched`.

## Step 4 — Ledger + report

```bash
python -m gtm_core.ledger_cli append-cost --profile <active> \
  --json '{"tool":"firecrawl","skill":"content-research","cost_usd":<usd>,"item_id":"<item-id>"}'
python -m gtm_core.ledger_cli append-history --profile <active> \
  --json '{"event":"item_researched","skill":"content-research","item_id":"<item-id>","research_ref":"<path>"}'
```

Report: the item, the count of verified facts, and the "claims to avoid" list. Note month-to-date
spend vs budget.

## Guardrails

- Free web (Firecrawl) by default — only use metered tools within budget and surface the cost.
- DeepSeek (worker MCP) extracts; Claude verifies every claim before it lands in the pack.
- Never invent facts or stats. If it isn't in a cited source or `profiles/<active>/knowledge/`,
  it goes in "claims to avoid", not the post.
- Only write under `content/<active>/`. `profiles/<active>/` and `plugin/` are read-only.
