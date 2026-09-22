# Value Case

Build the **quantified** business case for one deal: what the status quo costs today, what the
proposed change is modelled to do to that number, and — the part that makes it survive contact with
a CFO — the assumption every figure rests on, named and separated from the arithmetic.

This runs **after** discovery, not instead of it. `account-plan` §9 states a value *hypothesis*;
this is where that hypothesis gets a baseline, a model and an owner. A value case built before the
customer has given you their own numbers is a brochure with decimal places.

---

## Step 1 — Load context

1. **PROFILE** — `profiles/<active>/PROFILE.md`: `name`, `brand_name`, `email_signature`, `language`.
2. **`profiles/<active>/knowledge/case-studies.md`** — the outcomes we can evidence, and what shape
   of account produced them.
3. **Upstream artifacts in the account folder** `content/<active>/accounts/<account-slug>/`:
   `account-plan-*` §9 (the hypothesis), `solution-discovery-*` (what they told us the workflow
   costs today), `deck-research-*` Layer 1 (`L1-9` why-now, `L1-6` use-case scenarios), and
   `solution-design-*` if one exists — its A9 quality requirements are frequently the value case in
   a different vocabulary.
4. **`docs/sales-questions-by-deal-phase.md` Phase 4** — implication and need-payoff questions.
   The strongest evidence in the file lives here: in major sales, the questions that separate
   winners are the ones that get the **buyer** to state the value. A number the customer said is
   worth more than a better number we computed.

## Step 2 — Establish the baseline before modelling anything

**The baseline is the deliverable's foundation and the part most often skipped.** Without it there
is no delta, only a projection — and a projection with no baseline is the thing a CFO recognises
instantly and stops reading.

For each cost line, record four things:

| | |
|---|---|
| **The quantity** | hours, incidents, headcount, tickets, minutes per run, failed handoffs |
| **Where it came from** | *they said it* · *we observed it* · *we assumed it* — one of exactly these three |
| **Who said it** | a name and a role. "The team estimated" is not a source |
| **The date** | a baseline is true on a day |

Anything with source = *we assumed it* is not a baseline. It is an assumption, and it belongs in
the assumption register in Step 4 where the customer can argue with it.

**When the customer has given no numbers, say so and stop modelling.** Produce the baseline
questions instead — that is a better deliverable than a model built on our own guesses, and it is
the reason to have the next call.

## Step 3 — Model the delta, conservatively and in one direction

State the change as **baseline → modelled**, per line, with the mechanism in between. Three rules:

- **Name the mechanism, not the benefit.** "Saves 20%" is not a mechanism. "The approval step that
  takes 3 days is removed because the policy is evaluated at request time" is.
- **Model one scenario conservatively and one at the customer's own assumptions** — never a single
  number. A range whose low end still clears the cost is a far stronger argument than a point
  estimate that gets negotiated down on the spot.
- **Never model a benefit the design does not deliver.** Every line traces to something in the
  solution design or the discovery brief. A value line with no mechanism in the design is the
  fastest way to lose a technical champion, because they are the one who knows.

Include the **cost of inaction** as its own line: what the baseline becomes over the same horizon
if nothing changes. That is Phase 4's implication question, in a spreadsheet.

## Step 4 — The assumption register is a section, not a footnote

Every number that is not a customer-supplied baseline is an assumption. List them, each with: the
assumption · the value used · **how sensitive the case is to it** · who could confirm it.

Then run the one test that makes this honest: **which single assumption, if wrong, breaks the
case?** Name it in the summary. A value case that cannot say which assumption is load-bearing has
not been checked, and the customer's finance team will find it in four minutes.

## Step 5 — Output

Save as **`value-case-[company]-[YYYY-MM-DD].md`** in the account folder
`content/<active>/accounts/<account-slug>/`. Structure:

1. **The one-line case** — baseline, modelled change, horizon, and the load-bearing assumption.
2. **Baseline** — the table from Step 2, with sources and dates.
3. **Modelled delta** — per line: baseline → modelled, mechanism, conservative and customer-assumption
   scenarios.
4. **Cost of inaction** — the same horizon, unchanged.
5. **Assumption register** — Step 4, with the load-bearing one marked.
6. **What we still need** — the numbers only they can give us, phrased as questions to ask.
7. **Evidence** — case studies cited by shape first, then industry, each with what actually
   transfers and what does not.

Then append the `⟦FILE:…⟧` sentinel with the real absolute path.

Offer the hand-offs: `commercial-proposal` (this priced), `build-deck` (the value chapter),
`poc-plan` (prove the load-bearing assumption before anyone signs).

## Guardrails

- **No baseline, no model.** Produce the questions instead; say plainly that is what you did.
- **Every number carries its source and its date.** *They said it* / *we observed it* / *we assumed
  it* is a closed list — a fourth category is how an assumption becomes a fact.
- **Never present a modelled figure as an achieved one**, and never restate a case study's outcome
  as this account's forecast. A different company's result is evidence, not a projection.
- **Product-accuracy discipline** — tag any capability the case depends on
  SHIPPED/CONDITIONAL/ROADMAP; a value line resting on a roadmap capability is a roadmap line and
  must say so. See `docs/product-accuracy.md`.
- **Read-only.** Drafts a document; sends nothing, prices nothing, commits to nothing.
