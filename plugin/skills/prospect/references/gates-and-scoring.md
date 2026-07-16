# Prospect — Qualification gates & scoring (generic mechanics)

> **This file is company-agnostic.** It defines the *machinery* of gating and scoring — the shapes,
> the order of operations, the heat axis, the thresholds-as-defaults, and the per-run distribution.
> The **actual criteria** (what each gate tests, the rubric line-items, the segment definitions and
> firmographic floors) are **tenant-specific and live in the active profile**:
> `profiles/<active>/knowledge/icp-personas.md` (or a dedicated scoring file it links, e.g. a
> `buyer-intent-signals.md`). Resolve that path with
> `python -m gtm_core.resolve_knowledge icp-personas.md --profile <active> [--product <slug>]`.
>
> If the profile defines its own gates + rubric, **they win** over anything illustrated here. A
> worked example (an agentic-infrastructure tenant) is kept at the bottom purely to show the shape.

## Order of operations (every tenant)

1. **Gate** the candidate for its **segment** — it must clear all gates or it's dropped.
2. **Score** it on that segment's rubric (fit — *who they are*).
3. **Add heat** (intent — *when*) after the rubric, capped at the rubric ceiling.
4. **Tier** it against the publish / Tier-A thresholds.
5. Select the run mix per the profile's `segment_mix`, then order the Tier-A queue.

Only publish accounts at or above the publish threshold. Flag Tier A with 🔥.

## Gate structure (the pattern — fill criteria from the profile)

Each **segment** the profile defines (e.g. enterprise / startup, or SMB-clinic / health-system —
whatever `icp-personas.md` lists) carries three gates:

- **Gate A — Common ICP qualification:** the "must be roughly right" test (hit ≥N of M positives).
- **Gate B — Universal disqualifiers:** the account must **fail all** of these to survive (any one
  true → drop).
- **Gate C — Firmographic floor:** size / stage / geography minimums, incl. HQ or material
  operations in a PROFILE `target_market`.

The specific bullets under each gate come from the profile. If the profile names only one segment,
run one gate set. **Market-aware:** wherever a gate references geography, read `target_markets` from
`profiles/<active>/PROFILE.md`.

## Scoring rubric (the pattern)

Each segment has a **points rubric** whose line-items come from the profile. Two thresholds govern
it, and the profile may set them explicitly; absent an override, use these **defaults**:

- **Publish threshold — default ≥ 6 points** (below it, drop; never publish).
- **Tier-A threshold — default ≈ 70% of the rubric ceiling**, rounded (e.g. ≥7 of 10, ≥8 of 12).

State the ceiling per segment (the profile's rubric defines how many points are available). Keep the
publish threshold stable across segments so cross-segment conversion analysis stays comparable.

## Heat axis (intent add-on — applied after the rubric, identical for every tenant)

The rubric scores *fit* (who); heat scores *when*. After totalling the rubric, add:

| Signal | Points |
|---|---|
| High topic-intent on **either** feed — Vibe `business_intent_topics` `high_intent` on the profile's topic list, or a RocketReach tracked-topic / `intent`-facet hit | **+2** |
| **Both** feeds fire on the same account (**double-intent**) | **+1 more** |
| 🆕 New-in-role champion / economic buyer (≤6 months in seat — `job_change_signal` or `current_role_months`) | queue priority, not points |

Cap the boosted total at the rubric ceiling; publish and Tier-A thresholds apply to the boosted
total. Record `heat` (0–3) and `intent_feeds` per account. Single-feed **event** hits (funding,
hiring, a new location, a breach) are "why now" signals, **not** heat — heat is topic-intent only,
so it stays a scarce, meaningful boost. If no intent feed is connected for the profile, heat is 0
for every account and Tier-A is decided by fit + 🆕 new-in-role + 🔥 signal recency.

## Tier-A queue ordering (every tenant)

Within Tier-A, order outreach (and the run file's section order) by: **(1) heat, (2) 🆕
new-in-role, (3) 🔥 signal recency** — newest first. Re-rank every run: a stale Tier-A yields to a
fresh one (the new-in-role conversion premium decays inside ~90 days). Fit-without-heat accounts
stay Tier-B: monitoring plus a monthly no-ask value touch, never a meeting-ask sequence — a later
signal promotes them.

## Per-run distribution (every tenant)

Read the default run size and segment mix from PROFILE (`segment_mix`); absent one, default to **10
accounts, 3 enterprise + 7 startup** split across the colleague's `target_markets`. Adapt the split
to whatever segments + markets the profile lists, keeping the profile's ratio.

**Reallocation.** If a segment falls short of qualified candidates: shift to another segment/market
per the profile's priority, and **document the reallocation in the run-file header** so weekly
conversion analysis stays clean. **Tier-A aim:** ≥3 of 10 at Tier A; if fewer, broaden the signal
search next run.

---

## Example — an agentic-infrastructure tenant (illustrative only)

> **This is one profile's rubric, shown to make the pattern concrete. It is NOT the default and does
> NOT apply to your active profile.** Your profile's real gates + rubric live in its
> `knowledge/icp-personas.md`, which is always the source of truth — this appendix is illustration
> only.

**Segments:** Enterprise / Startup. **Publish ≥ 6; Tier-A ≥ 8/12 (enterprise), ≥ 7/10 (startup).**

*Gate A (both segments) — hit ≥4 of 5:* production AI agents (or launching within ~6 months) · uses
≥1 major agent framework or building on MCP/A2A/AP2 · cross-boundary requirements (multi-cloud/org/
team/jurisdiction) · stated need for AI observability/governance/audit/identity, or clear external
pressure · won't accept hyperscaler-locked solutions as the only path.

*Enterprise rubric (12):* 1,000+ employees & $250M+ revenue (1) · regulated / AI-governance
pressure (1) · public signals of agent production deployment (2) · multi-cloud / anti-lock-in (1) ·
named CISO + Head of AI Platform/CoE (1) · recent compliance event (2) · multiple agent frameworks
(1) · engages W3C/DIF/open-standards (1) · cross-org agent/data exchange in the ecosystem (1) · HQ
in a target market (1).

*Startup rubric (10):* building agents as core product/feature (2) · Series A–C, raised in last 18
months (1) · sells to enterprise (1) · CEO/CPO publicly engaged on responsible AI (1) · hit an
enterprise procurement/security barrier on AI governance (2) · on/adopting MCP/A2A/AP2 (1) · HQ in a
target market (1) · CTO/founder with security or infra background (1).

*A different tenant looks completely different* — e.g. a healthcare intake vendor scores "EMR is
eClinicalWorks/athenahealth (+3, and a gate)" instead of "agents in production," on SMB-clinic vs
health-system segments. Same machinery above; different criteria, from that profile.
