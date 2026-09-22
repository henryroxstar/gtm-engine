# Prospect — Qualification gates & scoring (generic mechanics)

> **This file is company-agnostic.** It defines the *machinery* of gating and scoring — the shapes,
> the order of operations, the heat axis, the thresholds-as-defaults, and the per-run distribution.
> The **actual criteria** (what each gate tests, the rubric line-items, the segment definitions and
> firmographic floors) are **tenant-specific and live in the active profile**:
> `profiles/<active>/knowledge/icp-personas.md` (or a dedicated scoring file it links, e.g. a
> `buyer-intent-signals.md`). Resolve that path with
> `python -m gtm_core.resolve_knowledge icp-personas.md --profile <active> [--product <slug>]`.
>
> A profile may also ship **`knowledge/scorecard.toml`** — the same rubric in declarative form, so
> the machinery below is computed rather than judged. Where it exists it is the one the run scores
> against, and the prose file is what a human maintains and the card cites. See § "Scored vs
> categorised".
>
> If the profile defines its own gates + rubric, **they win** over anything illustrated here. A
> worked example (an agentic-infrastructure tenant) is kept at the bottom purely to show the shape.

## Order of operations (every tenant)

1. **Gate** the candidate for its **segment** — it must clear all gates or it's dropped.
2. **Check sufficiency** — is every input the rubric reads actually *on the row*? A row missing one
   gets a **category naming the unlock** and leaves the ranking. It is never scored low for it.
3. **Score** it on that segment's rubric (fit — *who they are*).
4. **Add heat** (intent — *when*) after the rubric, capped at the rubric ceiling.
5. **Tier** it against the publish / Tier-A thresholds.
6. Select the run mix per the profile's `segment_mix`, then order the Tier-A queue.

Only publish accounts at or above the publish threshold. Flag Tier A with 🔥.

Step 2 is the step that is cheap to skip and expensive to have skipped — § "Scored vs categorised"
is why it is a step of its own and not a zero on the rubric.

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

**A line-item may not award points for our own research coverage.** The litmus: *would this change
if we researched harder, without the company changing?* If yes it is a coverage proxy — dossier
length, whether an evidence URL turned up, how many colleagues have listed the account, how long
the description is — and it cannot be a rubric line-item. On 2026-09-21 an ICP-fit line-item was in
effect "the description is long enough", and that run's mean score tracked how many sales teams had
listed a company rather than anything about the company; every arithmetic check was green. The same
facts are legitimate — required, in fact — as **sufficiency** inputs (next section): thin research
must decide *whether* a row is scorable, and must never decide *how well* it scores. Enforced at
load time for a declarative card, and written up as `docs/RULES.md` §R19.

## Scored vs categorised (the sufficiency gate — every tenant)

A rubric can only read what the row carries. When a required input is absent, the honest answer is
**not a low score** — it is a **category naming what to do next**: classify the ICP, research the
account, probe intent. The two outcomes are exclusive: a row comes back either with a score and a
tier, or with a category and the name of the input it is waiting on. Never a blend.

Why this is a step and not a zero: a zero is a *finding about the account*, and it ranks alongside
real findings. "Nobody has researched this one yet" is a fact about **our effort** — score it as a
weak account and the difference between an unworked account and a rejected one is gone, with no way
to recover it afterwards. That is the same failure § "Gates vs. research thinness" describes for
the *angle*, one level up: there it costs you a personalised opening, here it costs you the account.

- A categorised row carries **`tier: "unscored"`** and **no `score` key at all** — not `score:
  null`, which is a number-shaped hole that downstream readers trip over.
- `unscored` is **not a fit level below the bottom tier.** It means *not answerable yet*. Do not
  rank it, do not count it among the scored, and do not report it as dropped — a dropped row is one
  we judged and declined, which is a completely different sentence to say about an account.
- The category **is** the deliverable: a categorised row is a work queue, and its category names
  which queue. Report the counts per category, not one lump of "unscored".

**Every scored row must name the rubric it was scored against** — `rubric_source` and
`rubric_version`, carried on the row, not recalled in prose. Without them a later reply-rate change
cannot be attributed to a rubric change rather than to noise, and `prospects_import finalize`
refuses a scored row that lacks them.

Where the profile ships `knowledge/scorecard.toml`, all of the above is computed and the engine
refuses to emit a number when an input is missing:

```bash
uv run python -m gtm_core.scorecard score --profile <active> --items <rows.json>
```

It prints `scored N · categorised C · rubric <source>@<version>` plus the tier spread, and returns
each row's outcome with the provenance attached. A profile without a card runs this same order of
operations by hand — the sufficiency step is not optional because it is manual.

## Heat axis (intent add-on — applied after the rubric, identical for every tenant)

The rubric scores *fit* (who); heat scores *when*. Topic-intent feeds return a **numeric 0–100
score** — use the number, not a boolean. Vibe/Bombora carries the score inline on each row
(`business_business_intent_topics`); RocketReach/Intentsify exposes scores only via its weekly
snapshot file (see `discovery-and-budget.md`), not the API facet; Apollo/LeadSift exposes a
tracked-topic **filter hit** on company search (no score, same shape as RocketReach's facet) and
needs tracked topics configured in Apollo's own web UI first — same prerequisite as RocketReach's
Intentsify topics. After totalling the rubric, add:

| Signal | Points |
|---|---|
| Topic-intent **score ≥75** on **any** feed — a Vibe row score ≥75, an Intentsify snapshot score ≥75, or an Apollo tracked-topic filter hit (no score, but a match is itself the surge signal — same qualifying logic as RocketReach's `intent` facet). | **+2** |
| **Two or more** feeds ≥75 on the same account (**double-intent** — the name predates a third feed; it still applies whenever ≥2 of Vibe/RocketReach/Apollo converge, not only exactly two) | **+1 more** |
| 🆕 New-in-role champion / economic buyer (≤6 months in seat — `job_change_signal` or `current_role_months`) | queue priority, not points |

Cap the boosted total at the rubric ceiling; publish and Tier-A thresholds apply to the boosted
total. Record `heat` (0–3) and `intent_feeds` per account. A score **60–74 is elevated but not
heat** — note it in the account line, don't award the +2 (keeps the boost scarce and meaningful).
Single-feed **event** hits (funding, hiring, a new location, a breach) are "why now" signals,
**not** heat — heat is topic-intent only. If no intent feed is connected for the profile, heat is 0
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
>
> **Every number below is a frozen snapshot and is deliberately not kept in sync with any live
> profile.** It has already drifted once — the profile it was copied from has since added a third
> segment and split its rubric into required + bonus subtotals, neither of which appears here.
> Carrying a threshold out of this appendix into a run is the failure this box exists to prevent.

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
medipath/carevault (+3, and a gate)" instead of "agents in production," on SMB-clinic vs
health-system segments. Same machinery above; different criteria, from that profile.

## Gates vs. research thinness (the routing principle)

> **Gates disqualify on properties of the ACCOUNT. Research thinness downgrades the ANGLE, never
> the account.**

Gate A/B/C failures are findings about the company — competitor, wrong motion, below the
firmographic floor, outside the market. Those drop. "The sweep found nothing dated this pass" is a
fact about *our research effort*, and dropping on it converts an unworked account into a rejected
one with no way to tell the two apart afterwards.

A candidate that clears the gates but yields no usable signal therefore **routes** rather than
drops: it becomes a `generic`-lane row, enrollable on a body that makes no account-specific claim.
The lane vocabulary and the admissible-verdict table live in SKILL.md Step 8; the router is
`gtm_core.lanes`.

Two consequences worth stating here:

- **Tier is not the send gate.** *Both* tiers get emailed — Tier-A earns a 1:1 pack, Tier-B goes in
  the merge sequence. Reporting a Tier-A count as though it were the count of sendable accounts
  understates a run by the whole Tier-B population. The publish and Tier-A numbers themselves are
  the **active profile's**, they differ per segment, and they are not repeated here: read them from
  `knowledge/icp-personas.md` § "Gates, scoring rubric & thresholds" at the start of every run.
- **Thinness has two answers, at two different levels.** No usable *signal* downgrades the
  **angle**: the account still scores, and routes to the `generic` lane as above. A missing rubric
  **input** is the stronger case — the account cannot be scored at all, and becomes `unscored` with
  a category (§ "Scored vs categorised"). Both protect the distinction this section exists for;
  they differ in whether a number was ever earned.
- **Tier and lane are orthogonal.** A Tier-A account can be `generic` (high fit, no clause) and a
  Tier-B account can be `personalised` (modest fit, excellent signal). Do not collapse them into
  one axis, and do not add a "Tier C" for the generic population — it would make tier mean two
  things and break cross-segment conversion analysis.
