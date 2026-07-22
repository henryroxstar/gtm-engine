
# GTM — Call Prep

Build a crisp pre-meeting brief the colleague can read in five minutes. Map the account to the ICP, map each attendee to a persona, select the closest proof story, arm the colleague with objection responses and discovery questions, and close with one clear ask. **Read-only research only; never send anything.**

> Resolve the **active profile** (the agent provides it; everything loads from `profiles/<active>/`, never `plugin/`).

## Load context (in this order)

> **Knowledge resolution (product-aware).** Wherever this skill loads a per-product knowledge file —
> `icp-personas.md` or `market-scan-config.md` — resolve its path with
> `python -m gtm_core.resolve_knowledge <file> --profile <active> [--product <slug>]` and read whatever
> path it prints, instead of opening `knowledge/<file>` directly. The helper returns the product-level
> file (`products/<slug>/<file>`) when present and falls back to the profile-level `knowledge/<file>`
> otherwise. Pass `--product` when the run is bound to one product (the lead `default_product` from
> PROFILE.md, or a product the operator named); omit it for profile-wide work — a profile that keeps one
> shared knowledge pack always falls back to the profile level, so nothing changes for it.

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `brand_name`, `target_markets`, `icp_weighting`, `language`. Respect language throughout.
2. **`profiles/<active>/knowledge/icp-personas.md`** — qualification criteria, scoring rubric, persona cards (Pain · Claim · Gain), buying committee roles, and "why now" triggers.
3. **`profiles/<active>/knowledge/case-studies.md`** — four shapes + case-study selection map. Pick the closest case by shape first, then industry.
4. **`profiles/<active>/knowledge/product.md`** — the product suite (resolve real product names from here; declared in `PROFILE.md` → `products[]`), solution themes, features, discovery questions.
5. **`profiles/<active>/knowledge/company.md`** — company narrative, team, investors, competitive positioning.
6. **Prior prospect file** (if it exists in the account folder `content/<active>/accounts/<account-slug>/`) — a `prospects-*-[company].md` or `outreach-*-[company].md` from a prior run. Pull the score, why-now signal, and persona details already captured. Skip the web research if the file is fresh (≤30 days).

## Gather inputs

Ask the colleague:
- **Company name** (required).
- **Attendees** — names and titles if known; segment them into Enterprise / Startup.
- **Meeting type** — discovery, demo, follow-up, pilot review, QBR, partner call. Default: discovery.
- **Stage** — first touch, mid-cycle, late-stage. Default: first touch.
- **Any context they want to use** — recent news, a specific pain they mentioned, a prior email thread.

Keep the ask brief — one short message, not a form. If they've already given context in their request, use it and don't ask again.

## Research the account (skip if prior file is fresh)

Run a focused web sweep. Do NOT use metered tools (Firecrawl, Vibe Prospecting) for call prep — free paths only (web search + browser).

Gather:
1. **Company signals** — recent job posts mentioning AI agents or LLM/agent frameworks (LangGraph, CrewAI, AutoGen, MCP, A2A), press releases, engineering blog posts, LinkedIn activity.
2. **Agent maturity signals** — are they building, piloting, or running agents in production? Evidence from open sources.
3. **Regulatory exposure** — HQ jurisdiction + industry → infer relevant regulators from `profiles/<active>/knowledge/icp-personas.md` (MAS, HKMA, SEC/FINRA, HIPAA/HSA, EU AI Act, PDPA, etc.).
4. **Attendee LinkedIn** — title, tenure, background, recent posts. Map to the six personas in `profiles/<active>/knowledge/icp-personas.md`. Infer primary pain from their role.
5. **Recent news** — funding rounds (past 18 months), product launches, security incidents, compliance announcements.

Stop after the top signals are clear. This is a 5-minute sweep, not a full prospect run.

## Build the brief

Structure the output as follows. Keep each section tight.

### 1. Account snapshot (4–6 lines)
Company · segment (Enterprise / Startup) · industry · HQ + markets where they operate · employee count + recent funding · ICP score (apply the rubric from `profiles/<active>/knowledge/icp-personas.md`, show the number and the top 3 factors driving it).

### 2. Meeting context
Type · stage · attendees with role and persona mapping.

### 3. Attendee persona cards (one per attendee)
For each: **Title → Persona** → primary pain (from persona card) → what they care about proving in this meeting → how to open with them.

If multiple attendees, identify the economic buyer vs. champion vs. influencer.

### 4. The "why now" signal
The one real, specific, dated signal that makes this meeting timely. Use the prior prospect file if available; otherwise use the best signal from the web sweep. If no strong signal was found, say so — do not invent one.

### 5. Matched proof story
Apply the case-study selection map: shape first, then industry. Name the case study, one-sentence what they did, one-sentence outcome metric. Note the shape (internal multi-agent workforce / cross-org B2B exchange / machine-as-consumer commerce / multi-party verifiable workflow) and why it maps.

If the prospect has a regulatory angle, lead with the compliance-as-wedge framing from `profiles/<active>/knowledge/case-studies.md`.

### 6. Three likely objections + rebuttals

| Objection | Rebuttal |
|---|---|
| (most likely for this persona) | (draw from `profiles/<active>/knowledge/product.md` competitive differentiation + `profiles/<active>/knowledge/case-studies.md` proof) |
| … | … |
| … | … |

Pick objections relevant to the attendees' personas. Common ones: "We're already on [hyperscaler IAM]" / "We don't have agents in production yet" / "We can build this ourselves" / "We're too early-stage for this kind of overhead".

### 7. Discovery questions (5–7)
Pull from the discovery questions in `profiles/<active>/knowledge/product.md`; adapt to the meeting type and stage. For first-touch discovery, prioritise urgency-creating questions. For later-stage, prioritise technical/commercial fit.

### 8. The ask
One specific, low-commitment action to close this meeting with. Match to stage:
- **Discovery** → schedule a scoped technical deep-dive or PoC scoping call.
- **Demo/follow-up** → agree on a PoC success criterion or intro to the technical evaluator.
- **Pilot review** → next milestone agreement or commercial proposal.
- **Partner/BD call** → a named next step with a named person and date.

Never end a meeting without one.

## Output

Save the brief as **`call-prep-[company]-[YYYY-MM-DD].md`** in the account folder
`content/<active>/accounts/<account-slug>/` (see CLAUDE.md "Per-account outputs").

Tell the colleague: "Your brief for [company] is saved as `call-prep-[company]-[date].md`. Here's the quick version:" — then paste the brief inline so they don't have to open the file first.

## Guardrails

- Never use metered tools (Firecrawl, Vibe Prospecting) for call prep — free paths only.
- Apply the **product-accuracy discipline** to any capability claim in the brief — SHIPPED/CONDITIONAL/ROADMAP,
  no roadmap-as-live, verify cited facts: `docs/product-accuracy.md`.
- Never invent a "why now" signal. If the sweep finds nothing strong, say so and suggest the colleague bring their own signal into the meeting opener.
- Never fabricate attendee detail. If title is unknown, map by company stage and infer the most likely person (CEO for Startup first touch; Head of AI Platform for Enterprise first touch).
- Match proof stories by shape, not by vertical keyword alone.
- Keep the brief to one readable page — under 600 words of prose, tables OK.
