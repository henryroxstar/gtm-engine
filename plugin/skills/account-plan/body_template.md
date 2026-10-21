
# GTM — Account Plan

> Resolve the **active profile** (the agent provides it; everything loads from `profiles/<active>/`, never `plugin/`). Read the company brand from `PROFILE.md` (`brand_name`) — never hardcode a company name.

Produce a **CRO-grade strategic account plan** for one named company — the artifact a seasoned enterprise AE running MEDDPICC and Miller Heiman Strategic Selling would actually work from, not a research brief. It states the deal's real stage, scores qualification against a evidence-based framework, maps every buying influence (not just a wishlist of titles), names the commercial model or flags that one doesn't exist yet, and sequences a mutual action plan toward a **commercial event** — not just a meeting. Heavier than call-prep — use this when committing time to a specific account over a multi-week or multi-month horizon.

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
4. **`profiles/<active>/knowledge/product.md`** — product suite, competitive differentiation table (vs. hyperscaler), deployment options, discovery questions, **and commercial/pricing model if one is documented**.
5. **`profiles/<active>/knowledge/company.md`** — company narrative, open-source credentials, team, investors, certifications held (never overclaim a cert not listed here — e.g. don't say SOC 2 if only ISO 27001 is held).
6. **`docs/sales-questions-by-deal-phase.md`** — the evidence-based method behind discovery sequencing, multi-threading, and the indecision/de-risking play used throughout this plan.
7. **`deck-research-*[company]*.md` / `account-dossier-*[company]*` in the account folder** — the reusable Layer 1. Read it before the research sweep; the field map and the three reuse rules are in the next section.
8. **Prior prospect/outreach files** — look for `prospects-*-[company].md` or `outreach-*-[company].md` in the account folder `content/<active>/accounts/<account-slug>/`. If found, note whether any touch was actually **sent** (not just drafted) — a drafted-but-unsent pack means the account is still genuinely cold; don't treat it as a prior touch.

## Gather inputs

In one short message ask:
- **Company name** (required).
- **Segment** — Enterprise or Startup (or let it be inferred from headcount/funding).
- **What they already know** — any prior interaction, context from a call or email, or a specific pain the account mentioned. Blank = start cold.
- **Timeline horizon** — how many weeks/months to plan for. Default: 8 weeks.
- **Priority contacts** — names/titles if known. Blank = research.

If the colleague has already provided this in their request, use it.

## Reuse before you research — `deck-research` Layer 1 and the account dossier

**Read Layer 1 before you research anything.** `deck-research` writes eleven persona-agnostic,
sourced fields (`L1-1`…`L1-11`) to `deck-research-[company]-[YYYY-MM-DD].md` in the same account
folder, and `account-dossier` writes the walked-through version of the same account. Both are built
to be reused — Layer 1's whole reason for existing is that it is *persona-agnostic*, so re-running
the sweep here does not produce better facts, it produces a **second set** of facts that can
disagree with the ones already in front of the customer.

| You need | Layer 1 field |
|---|---|
| company overview, size, segment, ICP score | `L1-1` firmographics_icp |
| agent maturity — do they run agents in production | `L1-2` agentic_maturity |
| stack, frameworks, cloud, IAM | `L1-3` tech_stack |
| regulatory exposure and certifications | `L1-4` regulatory_posture |
| the live threats specific to their deployment | `L1-5` threat_hypotheses |
| concrete agent scenarios from their own portfolio | `L1-6` use_case_scenarios |
| incumbent / build-vs-buy / lock-in | `L1-7` incumbent_competitive |
| the matched proof story | `L1-8` proof_story |
| the dated why-now trigger | `L1-9` why_now |
| named buying committee → persona → primary pain | `L1-10` buying_committee |
| the numbered sources behind every external claim | `L1-11` sources |

Three rules when you reuse it:

- **Freshness is per-file, not per-account.** Fresh (≤30 days) → use it and sweep only for what it
  marks `null`, plus anything dated after it was written. Stale → still read it, still carry its
  `L1-11` numbering, and re-verify only the **time-sensitive** claims rather than starting over.
- **Carry the sourcing, do not launder it.** An `L1-11` footnote travels with the fact. A claim
  that arrives here without one is not promoted to sourced by being restated in a different
  document — that is how an unverified signal becomes a number in a QBR.
- **Never upgrade a flagged claim.** A field tagged unverified stays unverified here. If `L1-2` is
  empty the account was flagged as possibly not deck-ready, and that is a finding for this
  document, not a gap to quietly fill with a fresh guess.

For this plan specifically: `L1-10` is the seed of §7's buying-influence table, not a substitute
for it. Layer 1 names people and their pain; §7 adds response mode, rating and coverage, which are
**our** read of the relationship and cannot come out of a research file. A role that arrives from
`L1-10` with no contact is Coverage = Researched and Rating = 0, always.

---

## Research (only for what Layer 1 does not already answer)

Run a targeted web sweep — free paths only (web search + browser), no metered tools.

Gather:
1. Company overview — industry, HQ, employee count, recent funding (with date).
2. Agent maturity signals — job posts naming agent frameworks (LangGraph, CrewAI, AutoGen, MCP, A2A), engineering blog posts, conference talks, or product announcements mentioning AI agents.
3. Regulatory exposure — jurisdiction + industry → relevant regulators (pull from icp-personas.md).
4. Buying committee candidates — LinkedIn search for Head of AI Platform / CISO / CTO / CEO/Founder. Note tenure, background, and any recent public posts on AI governance/compliance.
5. Competitive signals — are they using a hyperscaler agent service? Any lock-in signals (AWS Bedrock, Azure AI Foundry, GCP Vertex)?
6. Recent news — press, funding, incident, compliance filing (last 18 months).
7. **Organizational structure** — is this a single entity, or does it span multiple business units, subsidiaries, or geographic hubs that could hold different authority? (Drives §4.)
8. **Industry/competitive landscape** — what are named peers/competitors in the account's industry and market doing in this space right now? (Drives §5.)

## Build the plan

Number every section as below — a CRO scanning the doc should find the same section in the same place across every account plan.

### Section 1 — Deal Snapshot

The executive summary. Write this **last**, after every other section is built, but place it **first** in the document.

| Field | Value |
|---|---|
| Account | {company} |
| Opportunity | {one-line description of what's being sold} |
| **Forecast stage** | See the stage ladder below — name the real stage, don't round up |
| **Forecast category** | Omitted / Pipeline / Best Case / Commit / Closed — a Stage-0 or Stage-1 account belongs in **Omitted**, never Best Case or Commit |
| ICP score | {score}/12 or /10 — Tier {A/B/C} |
| **ACV hypothesis** | State it, and mark it **validated** (customer-confirmed) or **unvalidated** (our hypothesis only) — see §9. If no pricing model exists for the product at all, say so plainly; that is an internal blocker, not a detail to gloss over |
| Realistic first commercial event | Name what actually happens first — a paid pilot, a design-partner agreement, a full contract — don't default to "signed contract" if the product/account shape makes a smaller first step more realistic |
| Earliest realistic close | A real date, reasoned from the account's likely procurement/paper process (§11), not a hopeful round number |
| Next milestone | The next concrete, dated action |
| Confidence | State confidence on *fit* and on *timing* separately — they are often very different for a strong-fit, early-stage account |
| Biggest single risk | One sentence, the actual thing most likely to kill this deal |

**Stage ladder (adapt names to the org's own CRM if one is named in PROFILE.md; otherwise use this default):**
`Stage 0 Target/Unqualified` → `Stage 1 Qualifying` (MEDDPICC in progress, no confirmed champion) → `Stage 2 Validating` (champion + economic buyer confirmed, pain validated by the customer) → `Stage 3 Proposing` (pilot/proposal in front of them) → `Stage 4 Negotiating` (paper process underway) → `Closed-Won` / `Closed-Lost`.

**Write one honest paragraph** underneath the table: what this account actually is right now (a target vs. a qualified opportunity), and how it should be resourced accordingly. Naming a target as pipeline, or a research target's champion as a real champion, is the single most common way this document misleads a CRO — don't do it.

### Section 2 — Qualification & ICP Fit

**Segment:** {Enterprise | Startup}
**ICP Score:** apply the full rubric from `icp-personas.md`. Show the score and list each factor with a tick (✓ met) or cross (✗ not met/unconfirmed — distinguish the two). Flag universal disqualifiers if any apply, even partially.

**Qualification summary:** 2–3 sentences. Tier A = strong fit, pursue actively. Tier B = fit with a gap — name the gap. Tier C = marginal — suggest parking. Score strictly; do not inflate to make an account look attractive — an inflated score is what lets a bad deal survive into forecast.

### Section 3 — Why Now (Trigger Events)

The top 1–3 "why now" triggers from the research (use the trigger lists in `icp-personas.md`). Name each trigger specifically (the job post date, the regulatory deadline, the incident, the funding round) with a source. If no strong trigger is found, say so and name what to monitor. **State any honest counter-signal too** (e.g. a cited framework is non-binding with no deadline) — a trigger that collapses under the prospect's own scrutiny is worse than no trigger.

### Section 4 — Account Landscape (multi-entity / multi-geo accounts only)

**Include this section whenever the account spans more than one business unit, subsidiary, or geographic hub with potentially different authority — skip it for a single-entity account.**

Map what is led centrally/globally vs. what is led locally/by the business unit, and who sits where. State the **strategic implication** explicitly: where should the plan enter, and where does sign-off authority likely sit? A mismatch between the two (build happens locally, budget sits centrally) is a structural reason deals stall — name it here if it applies, and turn it into a specific multi-threading action in §14.

### Section 5 — Industry & Competitive Landscape

What is happening in the account's specific industry and market that a buyer would assume we already know. Pull from `market-scan`/`content-radar` outputs if a recent one exists for this industry; otherwise run a targeted sweep. Table format: development → detail → why it matters to this deal. Close with a one-paragraph **read** — what frame does this landscape support (e.g. "the market has converged on X, and the account is a named participant" beats a generic "you have a problem" pitch).

### Section 6 — MEDDPICC Scorecard

Score each element **0–3** (0 = nothing, 1 = hypothesis/inferred, 2 = partially validated, 3 = validated directly by the customer). Total out of 24. **A low total on a Stage-0/1 account is expected and correct** — the value of this table is naming exactly what's missing and who closes each gap by when, not producing a high number.

| Element | Score | Where we actually are | Gap-closing action | Owner / by |
|---|---|---|---|---|
| **M**etrics | | | | |
| **E**conomic Buyer | | | | |
| **D**ecision Criteria | | | | |
| **D**ecision Process | | | | |
| **P**aper Process | | | | |
| **I**dentify Pain | | | | |
| **C**hampion | | | | |
| **C**ompetition | | | | |

### Section 7 — Buying Influences (Miller Heiman)

For every real role in the deal — not a wishlist, only roles that plausibly exist for this account:

| Buying influence | Role (Economic / User / Technical Buyer, or Coach) | Response mode | Rating (−5 to +5) | Their win-result (the *personal* win, not just the business result) | Degree of influence | Coverage |
|---|---|---|---|---|---|---|

- **Response mode** — Growth, Trouble, Even Keel, or Overconfident. You can sell into Growth and Trouble; you generally cannot sell into Even Keel or Overconfident until something changes their situation. Rate honestly per person, not per account.
- **Rating** — our current standing with them, from hostile to advocate. **0 for anyone we have not contacted, always** — do not infer a positive rating from public sentiment alone.
- **Win-result** — what personally wins for *them*, not the outcome we want. This is what Strategic Selling calls the difference between the business result and the win-result; missing it is why technically-correct value props don't move people.
- **Coverage** — Known (we have a real point of contact) / Researched (named, but no contact) / Unknown (role not yet identified, or not yet filled at the account).

Flag **single-threaded risk** explicitly if fewer than two buying influences are at Coverage = Known — deals with only one engaged contact are the ones that stall (`docs/sales-questions-by-deal-phase.md`, Phase 3). Note wherever **control-function influences (security, procurement, TPRM/legal) are in Even Keel or Overconfident mode while business-side influences are in Growth** — that asymmetry is common in regulated enterprise and should shape sequencing in §14: move fast on the receptive side, pre-empt the control functions before they discover the deal unprompted.

**The influence map is a diagram, and the table is its source.** Once the rows above are settled,
draw them — who reports to whom, who we have reached, where the single thread runs — as Mermaid,
then render:

```
uv run python -m gtm_core.diagrams render --input <influence-map.mmd> --out <influence-map.svg> \
    --format svg --profile <active> \
    --title "Buying influences — who we have reached" \
    --desc "<one sentence: the coverage gap the map makes visible>"
```

Draw the map **from the table, never from memory of the account**: a role that is not a row does
not belong on it, and a rating the table records as 0 is not drawn as a warm contact because the
picture looked lopsided. Single-threading is a shape — one line from us into the account — and it
is the thing a reader sees in a diagram and argues with in a table. Brand tokens resolve through
`gtm_core.brandkit`; `--title`/`--desc` are the SVG's own accessibility layer and are not optional.
The MEDDPICC scorecard in §6 is a **table**, not a diagram: it is eight named fields with evidence
per field, and a picture of it loses the evidence column, which is the only part that is load-bearing.

### Section 8 — Champion Development Plan

**Do not call a target a champion.** A champion has (a) power or access to power, (b) a personal win tied to our success, and (c) will sell for us when we are not in the room. Someone we have merely identified and not yet spoken to is a **target**, full stop — correct the language even if an earlier note called them a champion.

Use this progression and name, for the account's actual best-candidate champion, what evidence at each stage would prove they've moved up it:

| Stage | Test | Evidence it is real |
|---|---|---|
| Contact | They reply | Any substantive reply |
| Coach | They give us information we couldn't get ourselves | e.g. how budget actually works, who really owns the decision |
| Champion | They spend their own credibility on us | Unprompted introduction to another buying influence |
| Tested champion | They defend us when we're not there | Carries our framing into an internal review, reports back |

**Champion enablement** — name the one artifact that makes *them* look prepared to their own internal audience (not a generic pitch deck) that we owe them once they engage.

**Fallback champion** — if the primary target doesn't engage, who is the next-best candidate and why (often a role not yet filled, or a technical evaluator who will personally own the resulting build).

### Section 9 — Value Hypothesis & Commercial Model

**Value hypothesis.** Table: value lever → hypothesis (ours, unvalidated until stated otherwise) → how to validate it in discovery. Explicitly label every number as a hypothesis to test, not a claim to assert — inventing a quantified ROI model before discovery is a credibility risk with a sophisticated buyer, and worse with one that has published its own technical thinking on the problem.

**Commercial model.** State plainly:
- The unit of value / pricing model for the product in this deal, **if one is documented** in `product.md` or PROFILE.md.
- **If no pricing model exists for this product yet, say so as a named internal blocker with an owner and a date** — this is exactly the kind of gap a CRO stops on, and burying it inside prose instead of flagging it is a failure of this document. Don't invent a number to fill the gap.
- Whether a design-partner / pilot construct exists (paid vs. free, and what the conversion trigger is) — if not, name that as a decision to make before the plan's proposal step.
- Any deployment-topology or maturity constraints (e.g. Beta-stage software, a deployment option not yet productised) that the commercial model must account for.
- **Size-of-prize** — an order-of-magnitude estimate if there's a reasonable basis for one (peer spend, deal shape, expansion potential), explicitly marked directional/unvalidated. This is a resourcing argument, never a forecast number.

### Section 10 — Entry Strategy & Proof Stories

**Primary entry point** — the persona and angle most likely to open the account: who, why them specifically (their role, their win-result from §7), the why-now hook, the why-{brand_name} hook, channel, and register (pull the formality-register rule from `voice.md` if the account's geography/seniority calls for it). **Secondary entry point** as backup if the primary doesn't respond within 2 weeks — name a real fallback, not "try again."

**Proof stories** — apply the case-study selection map from `case-studies.md`: primary case study (name + shape + one-sentence why it maps), secondary if the buying committee spans two shapes, and a compliance-as-wedge angle if regulatory pressure is a top trigger — framed as *alignment*, never as an obligation the account doesn't actually have. **Confirm reference-naming permission before naming a customer logo to a new account**, especially in a small/regulated market where an unpermitted logo drop does more damage than an anonymized reference.

### Section 11 — Decision Process & Paper Process

The part that actually sets the close date — usually the least-researched part of a plan and the most consequential.

**Decision process** — who approved the last comparable purchase, is there a standing review board/committee, who has selection authority vs. who sets the standard. List as open questions if genuinely unknown; do not guess and present the guess as fact.

**Paper process** — table: gate (vendor onboarding, security/InfoSec review, data protection/privacy, deployment topology, compliance/risk acceptance for any non-GA or novel element, legal/MSA) → expected requirement → our actual exposure (be specific and honest — e.g. name the certification we actually hold vs. one that might be asked for, per `company.md`). **State the realistic timeline consequence** of the paper process explicitly in §1 and §14 — a plan that ignores procurement reality is not a plan a CRO can rely on.

### Section 12 — Competitive Strategy

Table: alternative → their position → our honest counter → the **criterion we want written into their evaluation** (a specific, factual requirement that plays to a genuine strength — introduced as a discovery question, never asserted as a claim, and never phrased so obviously vendor-authored that a sophisticated buyer would recognize and discount it). Cover, at minimum: the incumbent platform/tool (if any — and default to complementary framing, never suggest the account chose wrong), **build in-house** (name honestly if this is the real competitor, which is common when the account has the technical depth to build it themselves), any adjacent point-solution vendor, and do-nothing/status-quo (usually the most likely outcome — the only honest lever against it is the real cost of waiting, never a manufactured deadline).

### Section 13 — Risk Register & Red Flags

**Red flags** — list any Miller Heiman red flags currently present (no contact with any buying influence, Economic Buyer unvalidated, no real champion, a needed role not yet filled at the account, a veto-holding function unidentified, single-threaded access, a control function in a non-receptive response mode). Naming these plainly, even when the list is long for an early-stage account, is the point — a CRO trusts a plan that shows its risk, not one that hides it.

**Risk register** — table: risk → likelihood → impact → mitigation. Cover at minimum whatever showed up in §9 (commercial model), §11 (paper process), and §12 (competition), plus any product-maturity or timing risk specific to this account.

### Section 14 — Mutual Action Plan

Sequence actions toward the **commercial event named in §1** — not just "book a meeting." Adapt the count and pacing to the timeline horizon from inputs; a real plan usually needs more than 5 steps once it reaches past first discovery into proposal and paper process.

| # | Action | Owner | Target date | Success criterion | Exit gate if missed |
|---|---|---|---|---|---|

Every date is **absolute** (a real calendar date), never relative ("in 2 weeks"). Include an explicit **watch/re-evaluate date** — if key steps stall, state what happens (e.g. revisit Tier classification, drop to monitoring) rather than leaving the plan open-ended.

If the account stalls with warm sentiment but no forward motion, treat it as **indecision, not competitive loss** — most stalled deals die there, not to a named rival (`docs/sales-questions-by-deal-phase.md`, Phase 6). The de-risking response is a scoped pilot, an opt-out clause, or a phased rollout as an explicit plan step — never just a re-pitch of the same value.

### Section 15 — Resource Requirements

What this pursuit needs from the seller's own organization to move — the asks a CRO actually needs to see and approve. Table: ask → why it's needed → needed by (date). Cover at minimum whatever internal dependency showed up in §9 (commercial model decision) and §11 (security/compliance materials), plus SA/technical time, executive-sponsor air cover if the account's seniority calls for peer-level engagement, and reference/logo permission if named in §10.

### Section 16 — Land & Expand Thesis

**Land** — the smallest real first win (usually the pilot named in §1/§14), stated concretely.
**Expand** — the sequenced next steps after land, in order, each tied to something specific about this account (an internal expansion path they've already stated publicly, an adjacent team, a second product/use case).
**Why this account is (or isn't) unusually good land-and-expand ground** — one paragraph naming the specific structural reason (e.g. a stated internal rollout mechanism, an ecosystem of counterparties already in the flow) that justifies accepting a small or low-margin first engagement, if one exists. If the account has no such structural multiplier, say that plainly instead of inventing one.

### Section 17 — Open Questions

List up to 4 things that would materially change the plan if answered differently. Sequence them per `docs/sales-questions-by-deal-phase.md` (Phase 2/4): situational fact-finding first, then at least one question that surfaces the cost of the status quo — write that specific question out verbatim, ready to ask on the first call.

### Section 18 — Honesty Notes

Everything that must be said plainly on every touch with this account, gathered in one place so it's never accidentally dropped from an outreach draft: product maturity caveats (Beta/GA status), any known default-behavior caveat that could embarrass us if asserted otherwise, certifications actually held vs. commonly assumed/asked-for ones, capabilities explicitly not available yet, current relationship status with the account (don't imply a relationship that doesn't exist), and any source-reliability caveat from the research itself (e.g. a primary source was unreachable and a claim rests on secondary corroboration only — say so and name what to re-verify before quoting it to the customer).

## Output

Save the plan as **`account-plan-[company]-[YYYY-MM-DD].md`** in the account folder
`content/<active>/accounts/<account-slug>/` (see CLAUDE.md "Per-account outputs"). If a prior-dated
account plan exists for the same account, this new plan **supersedes** it — say so in a one-line note
under the title, and tell the colleague to remove the superseded file rather than leaving two
competing plans in the folder.

**Also render a `.docx` version** — a CRO-readable working document, not a branded customer artifact
(no logo/banner treatment needed; this never leaves the building). Compose a JSON spec of the plan's
sections and render it with `scripts/render_account_plan_pydocx.py <spec>.json <out>.docx` (this
skill's own committed renderer — python-docx, always available, no external install). Validate with
the docx skill's `validate.py`, then save both files side by side in the account folder.

Tell the colleague: "Account plan for [company] saved as `account-plan-[company]-[date].md` (+ `.docx`). Here are the highlights:" — then summarize Section 1 (deal snapshot: stage + forecast category + biggest risk), Section 6 (MEDDPICC total + the single biggest gap), Section 7 (champion status — real or target), and Section 14 (first two actions) inline.

## Guardrails

- Free paths only for research — no metered tools.
- Never fabricate buying committee contacts. If a name is unknown, use the title and note "Unknown — identify via LinkedIn or warm intro."
- Score strictly against the ICP rubric and MEDDPICC — do not inflate either to make an account look more advanced than it is. An inflated MEDDPICC total is how a bad deal survives into a CRO's forecast.
- **Never present a Stage-0/1 account as pipeline, Best Case, or Commit.** Forecast category in §1 must match the real stage, not the seller's hope.
- **Never call a target a champion.** Use the progression in §8; a person we have not yet spoken to has not earned the title regardless of how well their public role fits.
- Flag disqualifiers, red flags, and commercial-model gaps clearly — do not bury them in prose where a skimming reader would miss them.
- The action plan must have real dates (absolute, not "in 2 weeks"). If the colleague didn't give a horizon, default to starting from today.
- Frame discovery questions, multi-threading, and the indecision/de-risking play per the evidence-based
  method in `docs/sales-questions-by-deal-phase.md` — question *content* still comes from
  `profiles/<active>/knowledge/product.md` and `icp-personas.md`.
- Never assert a certification, deployment option, or capability the account's product knowledge doesn't confirm the company actually holds/ships — check `company.md`/`product.md` before writing any claim into §9–12, not just from memory of a prior plan.
- If a primary source used for a claim was unreachable during research, say so in §18 rather than presenting a secondary-sourced claim with unqualified confidence.
