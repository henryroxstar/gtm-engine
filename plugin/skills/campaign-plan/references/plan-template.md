# Outbound program plan — section skeleton

The generic shape for the markdown deliverable. Keep it **exec-summary-first and punchy** — the
summary carries the argument; the sections are the evidence. Fill every figure from live data
(`latest.json`, `costs.jsonl`, the market-signals snapshot); pull framing (wedge, cohorts,
hypotheses, funnel assumptions) from the profile's `outbound-program-defaults.md`. Company-agnostic —
no hardcoded brand or account names in this template.

```markdown
# <Program name> — <one-line positioning>

_<brand_name> · <markets> · <date> · internal exec brief_

## Executive summary
- The bet, in 3–4 sentences: the timing argument (from market signals) + the differentiation wedge +
  what we test first and how we scale only on what converts.
- **North-star: SQLs.** Base case ~<N> SQLs → ~<M> pilots. Every send is human-approved.
- KPI strip: program size · north-star SQLs · category TAM · competitive-gap fact.
- Guardrails in one line: staged, human-gated, rates are hypotheses not forecasts.

## 1. Why now
The market-signal case (competitor move · regulation/enforcement dates · category size · incidents —
flag single-source items). Close with one "so the focus is…" paragraph.

## 2. Pipeline snapshot — what the top-of-funnel shows
- KPI strip from latest.json: total accounts · Tier-A (+Tier-B) · geo split · # at heat +2.
- Density map by cross-org cluster (which ICP concentrates where) → dictates cohort order + geo weight.
- In-market now: name the heat +2 / +3 accounts.
- "Reading → how each insight shapes the program" table (2 cols: what the data says | what we do).
- Week-0 realities, stated honestly (e.g. not-yet-in-CRM; a feed not yet switched on).

## 3. The wedge — use-case cohorts, not industries
Table: cross-org use case & party chain | pain → claim → gain | named accounts already in pipeline.
(One row per cohort from outbound-program-defaults.md; re-segment what's qualified.)

## 4. How the prospecting engine works
Discover → qualify → score → enrich → draft. Claude = brain; two paid feeds; human on every gate;
drafts only. Summarize the ICP gate + rubric + heat axis; link to the prospect skill's
`references/gates-and-scoring.md` + `references/intent-signals-catalog.md` (do not restate mechanics).

## 5. The plan — universe, calendar, hypotheses
- Universe pyramid: Tier-A / Tier-B / Tier-C counts + geo split.
- Cohort calendar: one cohort per period, sequenced by readiness × the §2 density map.
- The learning-agenda hypotheses (see the table shape below).

## 6. The numbers — assumed conversion
Tiered funnel (prospects → sent → opened → replied → positive → **SQLs** → pilots) + a
conservative/base/stretch scenario table. Every rate labeled an assumption. Sanity-check the base
case against the industry packs' bottom-up per-vertical targets.

## 7. Guardrails & decision gates
Deliverability (the binding constraint) · de-dupe vs already-contacted · human send-gate · honesty
(sell the pilot; verify single-source signals) · the gates that decide whether the tail ships.

## 8. Tools & budget
The stack + economics from costs.jsonl: fixed core vs metered; cost per prospect / per qualified
account / **per SQL** vs a labeled industry range.
```

## The hypotheses table (the "how we measure" shape)

Each hypothesis must carry a **measurable pass bar** and a **tripwire** — this is what turns a plan
into a learning agenda. Never a vague "test messaging"; always sample size → pass condition → action.

```markdown
| # | Hypothesis | How we measure (sample → pass bar) | Tripwire (fail → do) |
|---|---|---|---|
| H1 | <what we believe, one line> | <A/B design> · **≥<N> sends/variant**, read at ≥<R> replies → **pass:** <winner> ≥<x>× <baseline> | <what the failing signal is → the action> |
```

- **Sample** — how many accounts/sends per variant, and the reply count at which you read the result
  (small-n hypotheses are flagged "directional").
- **Pass bar** — a concrete multiple or threshold on the measured metric (reply rate, positive-reply
  rate, first-reply latency), not "it works".
- **Tripwire** — the observation that means the hypothesis failed, paired with the concrete next move
  (invert the message, go up-market, drop the filter…).
