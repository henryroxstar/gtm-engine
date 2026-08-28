# Judge rubric — v1 (2026-08-22)

**This file is documentation of the rubric, not its source.** The rubric the judge actually sends
lives in `RUBRIC_ITEMS` in [`agent/mcp/judge/server.py`](../../../../agent/mcp/judge/server.py),
as an ordered tuple, for one reason: PRD §3.2's stability control reverses the item order and
requires the verdicts to stay put. Order has to be data a function can reverse, not prose someone
re-types. A copy of the rubric that drifts from the one being sent would make every flip-rate and
kappa number describe a rubric nobody used.

**Revision budget: 3 per campaign.** Past three, the judge is permanently demoted to a ranker
rather than tuned further. A rubric tuned until it agrees with the operator has stopped being an
independent check and become a mirror — and the agreement statistic no longer means anything,
because it was optimised against directly.

## Items, in order

1. **`fact_creates_problem`** — Does the fact this email opens on actually create a problem for THIS person, in THIS seat? A true, on-topic, correctly-attributed fact that creates no problem for the reader is the most common defect in this pipeline, and it is invisible to every regex.
2. **`fact_supports_pitch`** — Does the recipient's own recorded evidence establish what the body then claims? If the body claims plurality ('multiple frameworks') and the evidence attests one thing, the premise is unsupported however true the fact is.
3. **`frame_fits_seat`** — Would someone in this seat recognise this framing as their problem — not their colleague's, and not their vendor's?
4. **`right_person`** — Is this person plausibly the one who would act on this? A great email to someone who cannot buy, cannot decide, and does not own the problem is a wasted send and a complaint risk.

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
