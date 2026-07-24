---
name: account-plan
description: >-
  Build a strategic account plan for one target company — ICP score, buying committee map,
  entry point strategy, matched proof stories, and a 5-step action plan with owners and dates.
  This skill should be used when the user says "build an account plan for [company]",
  "strategic plan for [account]", "account plan for [company]", "plan my approach to
  [company]", "how do I land [company]", or "help me develop [account]". Reads PROFILE for
  markets and ICP weighting. Saves the plan to the account folder
  (`content/<active>/accounts/<account-slug>/`). For a lighter pre-call brief, use call-prep
  instead.
metadata:
  version: "0.3.0"
  phase: "3"
  capability_tier: core
---

# GTM — Account Plan

> Resolve the **active profile** (the agent provides it; everything loads from `profiles/<active>/`, never `plugin/`). Read the company brand from `PROFILE.md` (`brand_name`) — never hardcode a company name.

Produce a structured account plan for one named company: full ICP qualification, buying committee map, entry point recommendation, matched case studies, competitive positioning, and a sequenced 5-step action plan. Heavier than call-prep — use this when committing time to a specific account over a multi-week or multi-month horizon.

## Load context (in this order)

> **Knowledge resolution (product-aware).** Wherever this skill loads a per-product knowledge file —
> `icp-personas.md` or `market-scan-config.md` — resolve its path with
> `python -m gtm_core.resolve_knowledge <file> --profile <active> [--product <slug>]` and read whatever
> path it prints, instead of opening `knowledge/<file>` directly. The helper returns the product-level
> file (`products/<slug>/<file>`) when present and falls back to the profile-level `knowledge/<file>`
> otherwise. Pass `--product` when the run is bound to one product (the lead `default_product` from
> PROFILE.md, or a product the operator named); omit it for profile-wide work — a profile that keeps one
> shared knowledge pack always falls back to the profile level, so nothing changes for it.

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `title`, `email_signature`, `brand_name`, `target_markets`, `icp_weighting`, `language`.
2. **`profiles/<active>/knowledge/icp-personas.md`** — segments, qualification criteria, scoring rubrics (Enterprise 0–12, Startup 0–10), persona cards, buying committee roles, "why now" triggers, universal disqualifiers.
3. **`profiles/<active>/knowledge/case-studies.md`** — four shapes + case-study selection map. Cross-cutting patterns (compliance as wedge; buyer ≠ builder; full stack vs. entry-point install).
4. **`profiles/<active>/knowledge/product.md`** — product suite, competitive differentiation table (vs. hyperscaler), deployment options, discovery questions.
5. **`profiles/<active>/knowledge/company.md`** — company narrative, open-source credentials, team, investors.
6. **Prior prospect/outreach files** — look for `prospects-*-[company].md` or `outreach-*-[company].md` in the account folder `content/<active>/accounts/<account-slug>/`. If found and ≤30 days old, pull signals and scores from there.

## Gather inputs

In one short message ask:
- **Company name** (required).
- **Segment** — Enterprise or Startup (or let it be inferred from headcount/funding).
- **What they already know** — any prior interaction, context from a call or email, or a specific pain the account mentioned. Blank = start cold.
- **Timeline horizon** — how many weeks/months to plan for. Default: 8 weeks.
- **Priority contacts** — names/titles if known. Blank = research.

If the colleague has already provided this in their request, use it.

## Research (if no fresh prior file)

Run a targeted web sweep — free paths only (web search + browser), no metered tools.

Gather:
1. Company overview — industry, HQ, employee count, recent funding (with date).
2. Agent maturity signals — job posts naming agent frameworks (LangGraph, CrewAI, AutoGen, MCP, A2A), engineering blog posts, conference talks, or product announcements mentioning AI agents.
3. Regulatory exposure — jurisdiction + industry → relevant regulators (pull from icp-personas.md).
4. Buying committee candidates — LinkedIn search for Head of AI Platform / CISO / CTO / CEO/Founder. Note tenure, background, and any recent public posts on AI governance/compliance.
5. Competitive signals — are they using a hyperscaler agent service? Any lock-in signals (AWS Bedrock, Azure AI Foundry, GCP Vertex)?
6. Recent news — press, funding, incident, compliance filing (last 18 months).

## Build the plan

### Section 1 — Account Qualification

**Segment:** {Enterprise | Startup}
**ICP Score:** apply the full rubric from `icp-personas.md`. Show the score (e.g. 8/12) and list each factor with a tick (✓ met) or cross (✗ not met). Flag universal disqualifiers if any apply.

**Qualification summary:** 2–3 sentences. Is this a Tier A, B, or C account? Tier A = strong fit, pursue actively. Tier B = fit with a gap — note the gap. Tier C = marginal — suggest parking.

### Section 2 — Why Now

The top 1–2 "why now" triggers from the research (use the trigger lists in `icp-personas.md`). Name each trigger specifically (the job post date, the regulatory deadline, the incident, the funding round). If no strong trigger is found, note it — and suggest what to monitor.

### Section 3 — Buying Committee Map

For each likely role in the deal, map:
- **Name** (if known) or **Title** (if not)
- **Persona** (from the six cards in icp-personas.md)
- **Committee role:** Economic buyer · Champion · Technical evaluator · Co-signer · Influencer
- **Primary pain** (one line from their persona card)
- **Priority:** High / Medium / Low — rank by influence on the deal
- **Status:** Known / Researched / Unknown

Flag single-threaded risk if only one contact is identified — deals with only one engaged contact are the ones that stall (`docs/sales-questions-by-deal-phase.md`, Phase 3). Prioritise adding a second thread as an explicit action in Section 7.

### Section 4 — Entry Point Strategy

Recommend **one primary entry point** — the persona and angle most likely to open the account:
- Which persona to reach first and why (maps to their role as champion or economic buyer).
- The "why now" hook to lead with.
- The "why {brand_name}" hook most relevant to their pain (pull from persona card value props).
- Outreach channel: LinkedIn DM (use draft-outreach skill), email, or warm intro through a known connection or partner.

Then a **secondary entry point** as backup if the primary doesn't respond in 2 weeks.

### Section 5 — Proof Story Selection

Apply the case-study selection map. For this account:
- **Primary case study:** name + shape + one-sentence why it maps.
- **Secondary case study** (if the buying committee spans two shapes): name + shape.
- **Compliance-as-wedge angle** (if regulatory pressure is a top trigger): cite the specific regulator + the relevant compliance case framing from case-studies.md.

### Section 6 — Competitive Positioning

Based on research, what's the likely alternative they'll compare against?
- Hyperscaler (AWS Bedrock AgentCore / Azure AI Foundry / GCP Vertex) → use the portable-vs-locked-in table from product.md.
- Build in-house → use the build-vs-buy framing from icp-personas.md (Startup value prop #3).
- Identity vendor (Okta/Auth0/Ping) → note the gap (they handle human identity, not agent identity at creation + runtime policy enforcement).
- Nothing / "we'll deal with it later" → urgency framing: regulatory timeline or production incident as the trigger.

### Section 7 — 5-Step Action Plan

Sequence the next 5 actions with owners, target dates, and success criteria. Adapt to the timeline horizon from inputs.

| Step | Action | Owner | Target date | Success criterion |
|---|---|---|---|---|
| 1 | {e.g. "Draft + send LinkedIn DM to [Champion] using draft-outreach skill"} | Colleague | {date} | {e.g. "Reply received"} |
| 2 | {e.g. "Discovery call booked"} | Colleague | {date} | {e.g. "30-min call scheduled"} |
| 3 | {e.g. "Send tailored one-pager after call (build-deck A2)"} | Colleague | {date} | {e.g. "One-pager sent + acknowledged"} |
| 4 | {e.g. "Intro to technical evaluator; PoC scoping call"} | Colleague + Champion | {date} | {e.g. "PoC scope document agreed"} |
| 5 | {e.g. "PoC kicked off"} | Colleague + {brand_name} SA | {date} | {e.g. "PoC environment live; first success criterion tested"} |

Include a **watch / re-evaluate** date — if no response after Step 2, revisit Tier classification.

If the account stalls with warm sentiment but no forward motion, treat it as **indecision, not a
competitive loss** — most stalled deals die there, not to a named rival (`docs/sales-questions-by-deal-phase.md`,
Phase 6). Add a de-risking step to the plan (a scoped pilot, an opt-out clause, a phased rollout) rather
than just re-pitching value.

### Section 8 — Open Questions

List up to 3 things that would materially change the plan if answered differently (e.g. "Does their CISO have an active AI risk mandate?", "Are they already evaluating Okta's ITDR product?"). These become the priority discovery questions for the first call — sequence them per `docs/sales-questions-by-deal-phase.md` (Phase 2/4): situational fact-finding first, then at least one question that surfaces the cost of the status quo.

## Output

Save the plan as **`account-plan-[company]-[YYYY-MM-DD].md`** in the account folder
`content/<active>/accounts/<account-slug>/` (see CLAUDE.md "Per-account outputs").

Tell the colleague: "Account plan for [company] saved as `account-plan-[company]-[date].md`. Here are the highlights:" — then summarize Section 1 (score + tier), Section 4 (entry point), and Section 7 (first two actions) inline.

## Guardrails

- Free paths only for research — no metered tools.
- Never fabricate buying committee contacts. If a name is unknown, use the title and note "Unknown — identify via LinkedIn or warm intro."
- Score strictly against the rubric — do not inflate scores to make an account look attractive.
- Flag disqualifiers clearly; don't bury them.
- The action plan must have real dates (absolute, not "in 2 weeks"). If the colleague didn't give a horizon, default to starting from today.
- Frame discovery questions, multi-threading, and the indecision/de-risking play per the evidence-based
  method in `docs/sales-questions-by-deal-phase.md` — question *content* still comes from
  `profiles/<active>/knowledge/product.md` and `icp-personas.md`.
