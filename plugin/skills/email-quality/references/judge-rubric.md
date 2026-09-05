# Judge rubric — v3 (2026-09-02)

**This file is documentation of the rubric, not its source.** The rubric the judge actually sends
lives in `RUBRIC_ITEMS` in [`agent/mcp/judge/scoring.py`](../../../../agent/mcp/judge/scoring.py),
as an ordered tuple, for one reason: PRD §3.2's stability control reverses the item order and
requires the verdicts to stay put. Order has to be data a function can reverse, not prose someone
re-types. A copy of the rubric that drifts from the one being sent would make every flip-rate and
kappa number describe a rubric nobody used.

**Revision budget: 3 per campaign.** Past three, the judge is permanently demoted to a ranker
rather than tuned further. A rubric tuned until it agrees with the operator has stopped being an
independent check and become a mirror — and the agreement statistic no longer means anything,
because it was optimised against directly.

## Items, in order

1. **`fact_earns_its_place`** — Does the fact this email opens on earn its place? Three things at
   once, because they were measured to be one (see the revision log): does it create a problem for
   THIS person in THIS seat; does the recipient's own recorded evidence establish what the body
   then claims; and does the sentence right after it actually depend on THIS fact rather than
   reading the same under any other true fact about any other company. A true, on-topic,
   correctly-attributed fact that fails any of those is the most common defect in this pipeline,
   and it is invisible to every regex.
2. **`frame_fits_seat`** — Would someone in this seat recognise this framing as their problem — not
   their colleague's, and not their vendor's?
3. **`right_person`** — Is this person plausibly the one who would act on this? A great email to
   someone who cannot buy, cannot decide, and does not own the problem is a wasted send and a
   complaint risk.

## Framing

The judge is asked to **find reasons not to send** (PRD §3.2). The asymmetry is deliberate: a
plausible-looking email that wastes a real person's attention is the expensive failure; a false
alarm on a good email costs one re-read. Against a 12.5% complaint rate, that trade is not close.

## Untrusted input

Rendered bodies carry scraped `why_now` clauses and company names — prospect-controlled text
(§R5). The system prompt frames every rendered email as DATA to be judged and never as
instructions, and the reply parser accepts only the fixed vocabulary `send` / `re-angle` / `drop`.
A body containing the string `"verdict": "send"` inside its own scraped text cannot reach the
parser: only the model's reply is parsed, and a reply naming anything outside that vocabulary is
rejected rather than coerced to a default.

## Revision log

| version | date | change | why |
|---|---|---|---|
| v1 | 2026-08-22 | initial four items, mirroring the label schema's sub-checks | the judge is validated against those labels, so it must be asked the same questions the operator is |
| v2 | 2026-09-01 | added `bridge_depends_on_fact` (5 items) — never logged here at the time | a 35-row blind round scored `fact_creates_problem: No` on every row while the operator's notes distinguished good facts from bad ones; the missing field was the transition sentence after the fact, not the fact itself |
| v3 | 2026-09-02 | merged `fact_creates_problem` / `fact_supports_pitch` / `bridge_depends_on_fact` into `fact_earns_its_place` (5 items → 3) | Cohen's kappa between the three, measured over the 28-label 2026-09-01 round, was 1.00 / 0.84 / 0.84 — three questions, one answer on real data. `bridge_depends_on_fact` in particular never once differed from its neighbours, adding order-sensitivity to the flip-rate control without adding signal |

## Context changes (not rubric revisions — the items and the output shape are unchanged)

| date | change | why |
|---|---|---|
| 2026-09-03 | the judge now also receives the row's `signal_clause`, the first 300 chars of `signal_evidence`, the spec's `capability:` and the profile's capability groups | until then it saw only title / company / segment, so its note could say an argument was wrong but never which one would fit; the lane router and the defect report need that. Pre-registered here; measure with `eval self-agreement` (a pre-change run × a post-change run on the same bodies) before reading a verdict shift as a rubric effect |
