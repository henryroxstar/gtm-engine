---
name: gtm-planning
description: >-
  Build or refresh the quarterly GTM plan for the colleague's market. This skill should be
  used when the user says "build my quarterly plan", "refresh my GTM plan", "what's my focus
  this quarter", "plan my quarter", "update the GTM plan", "quarterly planning", "write the
  plan for Q[N]", "what should I prioritise this quarter", or "help me plan my GTM motion".
  Reads PROFILE for market, ICP weighting, and targets. Produces a structured, written plan
  the colleague can share with their manager or regional team.
metadata:
  version: "0.4.0"
  phase: "4"
  capability_tier: core
---

# GTM — Quarterly Planning

> Resolve the **active profile** (the agent provides it; everything loads from `profiles/<active>/`, never `plugin/`).
> Read the company brand from `PROFILE.md` → `brand_name` and use it throughout — never hardcode a company name.

Produce a clear, written quarterly GTM plan for the colleague's market — grounded in the active company's strategy, the colleague's PROFILE, and current pipeline and signals where available. Output is a markdown document saved to the working folder.

## Load context (in this order)

> **Knowledge resolution (product-aware).** Wherever this skill loads a per-product knowledge file —
> `icp-personas.md` or `market-scan-config.md` — resolve its path with
> `python -m gtm_core.resolve_knowledge <file> --profile <active> [--product <slug>]` and read whatever
> path it prints, instead of opening `knowledge/<file>` directly. The helper returns the product-level
> file (`products/<slug>/<file>`) when present and falls back to the profile-level `knowledge/<file>`
> otherwise. Pass `--product` when the run is bound to one product (the lead `default_product` from
> PROFILE.md, or a product the operator named); omit it for profile-wide work — a profile that keeps one
> shared knowledge pack always falls back to the profile level, so nothing changes for it.

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `title`, `brand_name`, `target_markets`, `icp_weighting`, `primary_persona`, `pipeline_stage`, `quarter_targets` (if set), `budget_monthly`, `cadence`.
2. **`profiles/<active>/knowledge/product.md`** — current solution themes, product shapes, pricing tier status.
3. **`profiles/<active>/knowledge/icp-personas.md`** — ICP segments, persona pain hierarchy, buying stages.
4. **`profiles/<active>/knowledge/case-studies.md`** — available proof stories by shape and segment.
5. **Prior quarterly plan** (if one exists in the working folder, named `gtm-plan-Q*` or `gtm-plan-*`) — read it and use it as a baseline for "what changed" framing.
6. **Recent market signals** (if any `market-signals/` files exist) — scan the most recent for H-rated signals relevant to the colleague's target markets.
7. **Pipeline snapshot** — if the colleague shares a prospect CSV, HubSpot export, or any pipeline notes, read them for current deal context.

## Gather inputs

Ask the colleague in one message:
- **Quarter** — which quarter (e.g., Q3 2026) and start/end dates.
- **Revenue or pipeline target** — the number they're working toward, if set.
- **Market focus** — confirm from PROFILE or ask: which cities / countries to prioritise this quarter.
- **Current pipeline state** — roughly how many accounts are active, at what stage? (Rough is fine — "~5 in discovery, 2 in POC" is enough.)
- **Top bets** — 2–3 accounts or segments they're most bullish on. Can leave blank.
- **Changes since last quarter** — any new product capability, new territory, or updated messaging they want to reflect?
- **Constraints** — travel budget, headcount, any market-specific restrictions.

If a prior plan exists and they say "just refresh it", pull changes from the above and update — don't rebuild from scratch.

## Draft the plan

Structure the plan as eight sections. Each section should be a prose paragraph or short labelled list — not a wall of bullets. Keep each section focused and actionable.

---

### Section 1 — Quarter context

One paragraph: the quarter dates, the market(s) in scope, the pipeline target, and the one-sentence strategic priority for the quarter. Example framing: "Q3 2026 for [markets]: focus on closing 2 POC agreements in [segment] while building a 15-account discovery pipeline in [market 2]."

### Section 2 — Market snapshot

2–3 paragraphs: current state of the markets in scope. Pull from the most recent market-signals file if available, otherwise draw from `product.md` competitive positioning and any context the colleague provided. Cover:
- What's moving in the market (regulatory, competitive, agentic AI adoption).
- Where the active company has the strongest signal or proof in these markets.
- Any risks or headwinds to flag.

### Section 3 — ICP and target segments

Which ICP segments and buyer personas to prioritise this quarter, in ranked order. Base the ranking on `icp_weighting` in PROFILE and the colleague's current pipeline context. For each segment (up to 3):
- Segment name and size estimate (if known).
- Primary buyer persona (from `icp-personas.md`).
- Why this segment is prioritised this quarter (timing, signal strength, proof available).
- Product shape most relevant to them (from `product.md` — pick the best fit).

### Section 4 — Target accounts and pipeline plan

Structure as three tiers:
- **Tier 1 — Close this quarter** (1–3 accounts): POC-stage or advanced discovery; specific ask and timeline.
- **Tier 2 — Advance this quarter** (3–5 accounts): Discovery stage; goal is POC agreement or clear next step.
- **Tier 3 — Build pipeline** (5–10 accounts): Net-new targets to qualify; sourced from the prospecting motion.

If the colleague provided account names, slot them into tiers. If not, describe the profile of accounts to target in each tier, drawing from PROFILE `icp_weighting` and `target_markets`.

For each Tier 1 and Tier 2 account (or archetype if names unknown): note the relevant product shape, the primary buyer persona, the best-fit case study, and the single next action.

### Section 5 — Proof stories to lead with

Select 2–3 case studies from `profiles/<active>/knowledge/case-studies.md` that best match the target segments and markets this quarter. For each:
- Case study name and shape.
- Why it fits the target segments this quarter.
- The reusable hook (from case-studies.md "Reusable messaging hooks").

### Section 6 — Motion plan (weekly cadence)

A week-by-week plan for the quarter. Present as a table or labelled list:

| Week | Prospecting | Outreach | Market scan | Events | Deck / call prep |
|---|---|---|---|---|---|

Populate from the colleague's PROFILE `cadence` setting. If they've scheduled the Monday runs, note that. Highlight any event-heavy weeks or travel windows that would shift the rhythm.

If a monthly budget is set in PROFILE, note the approximate allocation across Vibe Prospecting, Firecrawl, and Higgsfield for the quarter — and flag if targets imply more spend than the budget allows.

### Section 7 — Risks and mitigations

3–5 named risks relevant to this market and quarter. For each:
- Risk name (one line).
- Likelihood (H/M/L) and impact (H/M/L).
- Mitigation action.

Draw from market snapshot, competitive positioning, and pipeline tier gaps. Common risks to assess: thin Tier 1 pipeline, no local proof story, regulatory delay in a target market, single-threaded deals.

### Section 8 — Open questions and decisions needed

List 3–5 things the colleague or their team needs to decide or resolve for the plan to execute cleanly. Examples: pricing sign-off, new case study approval, travel budget confirmation, whether a specific account should go Tier 1.

Label each with **owner** (colleague / regional lead / product / marketing) and **needed by** date.

---

## Review and save

Present the plan as a clean markdown document. Ask the colleague:
- "Anything to adjust before I save?"
- "Do you want me to pull this into a Word doc or deck format for sharing with your manager?"

If they say yes to Word doc, invoke the `docx` skill.

Save the final plan as **`gtm-plan-Q[N]-[YYYY]-[market-slug].md`** in the working folder (e.g., `gtm-plan-Q3-2026-SG.md`). Then append a `⟦FILE:…⟧` sentinel at the very end of your response so the cockpit delivers it automatically:

```
⟦FILE:/absolute/path/to/gtm-plan-Q[N]-[YYYY]-[market-slug].md⟧
```

Use the real resolved absolute path of the file you just saved.

## Quarterly refresh (update an existing plan)

When the colleague says "refresh my plan" or "update the Q[N] plan":
1. Read the existing plan file.
2. Ask: "What's changed? New accounts, new signals, or has the target shifted?"
3. Update only the changed sections. Add a `## Updated [date]` note at the top.
4. Save with the same filename (overwrite).

Do not rebuild from scratch — treat the prior plan as the working draft.

## Guardrails

- Never invent pipeline numbers, market data, or customer names not provided by the colleague or grounded in the knowledge pack.
- Keep Section 3 ICP rankings consistent with PROFILE `icp_weighting` — don't silently deprioritise segments the colleague said were primary.
- Section 6 cadence must be consistent with the colleague's actual scheduled tasks (from PROFILE cadence field). Don't plan a weekly cadence if they've said they only run monthly.
- Free paths only — no metered tool calls during planning. Market scan and prospecting happen in their own skills.
- Quarterly plan is an internal working document. Never suggest posting it publicly or sharing it outside the colleague's team without explicit instruction.
