# Prospect — Qualification gates & scoring rubrics

> Generic scoring mechanics. Each account clears all three gates **for its segment** before scoring. **Only publish accounts scoring ≥6.** Flag Tier A with 🔥.
> **Market-aware:** wherever this rubric references a specific geography, read the colleague's `target_markets` from `profiles/<active>/PROFILE.md` instead. Full ICP logic lives in `profiles/<active>/knowledge/icp-personas.md`.

## Gates — Enterprise

**Gate E-A — Common ICP qualification.** Hit ≥4 of 5:
- Production AI agents, or launching within ~6 months
- Uses ≥1 major agent framework (LangGraph, CrewAI, AutoGen, LlamaIndex) or building on MCP / A2A / AP2
- Cross-boundary requirements (multi-cloud, multi-org, multi-team, multi-jurisdiction)
- Stated need for AI observability/governance/audit/identity — OR clear external pressure
- Will not accept hyperscaler-locked solutions as the only path

**Gate E-B — Universal disqualifiers.** Account must fail **all** of:
- No agents in production and no plan to deploy within 6 months
- Fully committed to one hyperscaler, no portability mandate, no external pressure to change
- No identifiable champion (no Head of AI Platform / CISO / CTO present and engaged)
- On-prem-only mandate the deployment model can't meet
- Data-residency rules that prevent any acceptable deployment topology

**Gate E-C — Enterprise firmographic floor.**
- 1,000+ employees (sweet spot 5,000+)
- US$250M+ revenue (sweet spot US$1B+)
- HQ or material operations in a **target market** (PROFILE)

## Gates — Startup

**Gate S-A — Common ICP qualification.** Same 4-of-5 as enterprise.

**Gate S-B — Startup-specific disqualifiers.** Account must fail **all** of:
- Pre-product / pre-revenue with no clear customer pull
- Pure consumer AI (no enterprise customers, no near-term B2B plan)
- Solo founder still deciding tech stack — no real buying signal
- Built entirely on one hyperscaler with no portability mandate and no enterprise customers asking
- No identifiable founder-buyer (CEO / CPO / CTO not present and engaged)

**Gate S-C — Startup firmographic floor.**
- Seed through Series C
- 10–500 employees (sweet spot 30–200)
- Raised within the last 18 months **OR** visible enterprise traction (named large-enterprise logo, design-partner deal, recent enterprise hire)
- One of: (a) AI-native agentic product, (b) vertical AI SaaS with agents as core feature, (c) agent framework / dev-tools company, (d) agentic commerce / marketplace
- HQ or primary office in a **target market** (PROFILE)
- Sells B2B (or has a clear B2B revenue path)

## Scoring — Enterprise rubric (12 points; Tier A ≥ 8; publish ≥ 6)

| # | Criterion | Pts |
|---|---|---|
| 1 | 1,000+ employees, $250M+ revenue | 1 |
| 2 | Regulated industry or under AI-governance pressure | 1 |
| 3 | Public signals of agent / agentic-workflow production deployment (job posts, talks, blogs) | 2 |
| 4 | Multi-cloud strategy or explicit anti-lock-in posture | 1 |
| 5 | Named CISO + named Head of AI Platform / AI CoE | 1 |
| 6 | Recent compliance event: AI regulatory mention, audit finding, security incident | 2 |
| 7 | Uses multiple agent frameworks (LangGraph + CrewAI + AutoGen or similar) | 1 |
| 8 | Engages with W3C / DIF / open standards or cloud-neutrality communities | 1 |
| 9 | Partner / supplier ecosystem involves cross-org agent or data exchange | 1 |
| 10 | HQ in a target market (or material operations there) | 1 |
| | **Total** | **12** |

## Scoring — Startup rubric (10 points; Tier A ≥ 7; publish ≥ 6)

| # | Criterion | Pts |
|---|---|---|
| 1 | Building agents as core product or core feature | 2 |
| 2 | Series A–C; raised in last 18 months | 1 |
| 3 | Sells to enterprise customers | 1 |
| 4 | CEO and/or CPO publicly engaged on responsible AI / trust / governance | 1 |
| 5 | Hit an enterprise procurement / security-review barrier on AI governance | 2 |
| 6 | Already on or planning to adopt MCP / A2A / AP2 | 1 |
| 7 | HQ in a target market | 1 |
| 8 | CTO or founder with security or infra background | 1 |
| | **Total** | **10** |

## Heat axis (intent add-on — applied after the rubric)

The rubric scores *fit* (who); heat scores *when*. After totalling the rubric, add:

| Signal | Points |
|---|---|
| High topic-intent on **either** feed — Vibe `business_intent_topics` `high_intent` hit on the profile's topic list, or a RocketReach tracked-topic / `intent`-facet hit | **+2** |
| **Both** feeds fire on the same account (**double-intent**) | **+1 more** |
| 🆕 New-in-role champion / economic buyer (≤6 months in seat — `job_change_signal` or `current_role_months`) | queue priority, not points |

Cap the boosted total at the rubric ceiling (12 enterprise / 10 startup); publish and Tier-A
thresholds are unchanged and apply to the boosted total. Record `heat` (0–3) and `intent_feeds`
per account. Single-feed *event* hits (funding, hiring, breach) are "why now" signals, **not**
heat — heat is topic-intent only, so it stays a scarce, meaningful boost.

## Tier-A queue ordering

Within Tier-A, order outreach (and the run file's section order) by: **(1) heat, (2) 🆕
new-in-role, (3) 🔥 signal recency** — newest first. Re-rank every run: a stale Tier-A yields to
a fresh one (the new-in-role conversion premium decays inside ~90 days). Fit-without-heat
accounts stay Tier-B: monitoring plus a monthly no-ask value touch, never a meeting-ask
sequence — a later signal promotes them.

## Per-run distribution (default 10 accounts)

Default mix = **3 enterprise + 7 startup**, split across the colleague's `target_markets` (the original routine used 5 US + 5 SG: 1 US-ent + 2 SG-ent + 4 US-startup + 3 SG-startup). Adapt the split to whatever markets PROFILE lists, keeping the 3:7 enterprise:startup ratio unless PROFILE's `segment_mix` overrides it.

**Reallocation.** If a segment falls short of qualified candidates: enterprise shortfall → try the other market's enterprise quota, then backfill with startups in the same market; startup shortfall → mirror. Document any reallocation in the file header so weekly conversion analysis stays clean.

**Tier-A aim:** ≥3 of 10 at Tier A. If fewer, broaden the signal search next run.
