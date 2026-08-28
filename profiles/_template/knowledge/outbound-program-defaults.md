---
source: manual
refreshed: 2026-07-18
review: 90d
---
# Outbound-program defaults

Replace this file with your durable outbound-program framing. The `campaign-plan` skill reads it as
**defaults to refine with live data** (the current `content/<active>/prospects/latest.json`, the newest
`market-signals/` snapshot, and `costs.jsonl`) — not as frozen facts. If this file is absent the skill
derives cohorts from your industry packs + ICP instead. Keep it company-specific; it lives in the
profile, not the de-branded plugin.

Typical content:

## The wedge (why you win)
`<one paragraph: the structural reason your product wins a class of workflow — and how it complements,
rather than replaces, the incumbent stack. Keep positioning discipline: lead with your layer, frame
competitors' scope as by-design, never throw an analyzed company under the bus.>`

## Use-case cohorts
A table, one row per target workflow: `use case & party chain | pain → claim → gain | representative
accounts already in your pipeline`. Re-segment what you've qualified; don't invent logos.

## Cohort sequencing
The order to run cohorts (test-first, ramp-on-the-winner), by readiness × where your pipeline
concentrates. The skill re-weights this against the live density map.

## Learning-agenda hypotheses
The default hypothesis set, each with a **measurable pass bar** (sample size → pass condition) and a
**tripwire** (fail signal → action). See `plugin/skills/campaign-plan/references/plan-template.md`.

## Funnel & conversion assumptions
Tiered reply rates (A/B/C), open/positive/SQL/pilot rates, and conservative/base/stretch scenarios.
Every rate is an assumption to test. Sanity-check against your industry packs' bottom-up targets.
**North-star = SQLs.**

## Budget & unit-economics baseline
Your fixed core vs metered spend, all-in program cost, and cost per prospect / per qualified account /
**per SQL** — verified against `content/<active>/costs.jsonl` each run.

## Geo default
Which markets are primary vs validate-first, and any market-specific caps (e.g. a thin startup universe).

## Proof shapes & honesty guardrails
Your reusable proof stories, and the honesty rules to carry into every plan (sell the pilot not the
product if pre-GA; flag single-source signals as leads-to-verify; never quote intent data in outreach).
