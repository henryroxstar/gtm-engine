# ICP & Personas

Replace this file with your Ideal Customer Profile and key buyer personas. Typical content:
- ICP definition (company size, industry, signals, tech stack)
- Primary persona (title, goals, pain points, objections)
- Secondary personas (economic buyer, champion, influencer)
- Negative ICP (who to skip and why)

## Scoring & gates  *(the `prospect` skill reads this — fill it in)*

> The generic scoring **machinery** (order of ops, heat axis, Tier-A ordering, per-run distribution,
> default thresholds) lives in `plugin/skills/prospect/references/gates-and-scoring.md`. The
> **criteria below are yours** and override any example shown there. Replace every `<…>`. If your
> rubric grows large, move it to its own `knowledge/buyer-intent-signals.md` and leave a link here.

**Segments:** `<e.g. Enterprise / Startup, or SMB / Mid-market — or one segment>`. **Publish ≥ 6;
Tier-A ≥ `<~70% of the ceiling>`.**

**Gate A — Common ICP qualification (hit ≥`<N>` of `<M>`):** `<the "must be roughly right" positives
every genuine fit shows>`.

**Gate B — Universal disqualifiers (an account must fail ALL to survive):** `<hard nos — wrong
stage/setting, no champion, a tech or deployment constraint you can't meet>`.

**Gate C — Firmographic floor:** `<size / stage / revenue minimums>` · HQ or material ops in a
PROFILE `target_market`.

**Rubric (out of `<ceiling>`):** `<criterion (pts)>` · `<criterion (pts)>` · … — weight the one or
two signals that most predict a buy the highest.

**Heat (topic-intent, added after the rubric):** intent topics live in `market-scan-config.md`; +2
either feed / +1 both, capped at the ceiling. No intent feed connected → heat = 0 and Tier-A is
fit + 🆕 new-in-role + 🔥 signal recency.
