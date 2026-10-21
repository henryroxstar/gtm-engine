---
name: poc-plan
description: >-
  Turn a proposed solution into a time-boxed proof of concept with pass/fail criteria, a named
  verifier per criterion, exit criteria, and a technical-win memo. Trigger when the user says
  "plan a POC for [company]", "pilot plan", "proof of concept scope", or "what would a trial
  look like for [account]".
metadata:
  version: "0.1.0"
  phase: "5"
  capability_tier: core
---
# POC Plan

Turn a proposed solution into a **time-boxed** proof of concept somebody can pass or fail: criteria
written before the work starts, a named human verifying each one, an exit in both directions, and a
technical-win memo at the end that says what was proven and what was not.

The failure this skill exists to prevent is the POC that never ends — the one with no written
criteria, so every result is arguable, the scope grows to meet each new objection, and the deal
stalls inside an engineering project nobody scoped. A POC without a defined failure is not a test.

---

## Step 1 — Load context

1. **PROFILE** — `profiles/<active>/PROFILE.md`: `name`, `brand_name`, `email_signature`, `language`.
2. **The solution design** — `solution-design-*.md` in the account folder
   `content/<active>/accounts/<account-slug>/`. The POC proves a **claim the design makes**; if there
   is no design, get one or work from the discovery brief and say which you used.
3. **`solution-discovery-*`** — the open technical risks. These are the POC's candidate criteria:
   the things that, if unanswered, block the design.
4. **`value-case-*`** if one exists — its **load-bearing assumption** is usually the single most
   valuable thing a POC can test, and testing it is worth more than demonstrating three features.
5. **`profiles/<active>/knowledge/product.md`** — what is shipped versus roadmap. A criterion that
   depends on a roadmap capability is not a POC criterion, it is a commitment.

## Step 2 — Decide what is actually being proven

Write it as one sentence: *"If `<criterion>` holds, we proceed to `<commercial step>`."* Then check
it against the two questions that kill most POCs:

- **Is anyone in doubt about this?** A POC that demonstrates what nobody disputes buys nothing. If
  the real doubt is commercial or political, a POC will not resolve it and should not be run.
- **Who changes their mind if it passes?** Name the person. A POC whose success convinces nobody
  with authority is a science project with a customer logo on it.

## Step 3 — Criteria: pass/fail, with a named verifier each

Three to five. Not ten. Each one gets four fields, and the last two are what make it a test:

| Field | Rule |
|---|---|
| **Criterion** | a statement that can be false. "Integrates with our IdP" cannot; "an agent issued at 09:00 can call `<API>` and the call is denied after its credential is revoked" can |
| **Measured how** | the observation that settles it — a log line, a response code, a wall-clock number, a screen |
| **Threshold** | the number, with its unit and percentile. "Fast enough" is not a threshold |
| **Verifier** | **a named person on the customer's side.** Not "the team", not us |

The verifier field is the one that is always argued about and is never optional. A criterion we
verify ourselves is a demo. A criterion their engineer verifies is a technical win, and it is their
name on it that makes the internal conversation happen after we leave the room.

## Step 4 — Scope, time-box, and the exit in both directions

- **Time-box** — start date, end date, and the number of working days of *their* effort it assumes.
  Their effort is the cost they actually feel; ours is the one we quote.
- **In scope / out of scope** — write the out-of-scope list first and make it longer than feels
  comfortable. Everything not on the in-scope list is out, explicitly.
- **What each side provides** — access, environments, data, people, by name and by date. A POC
  blocked for three weeks on an environment nobody was assigned is the most common way one dies.
- **Exit criteria, both directions.** *Passes* → the named commercial step, agreed now, in writing.
  *Fails* → what happens then, also agreed now. A POC with no defined failure cannot fail, so it
  cannot end; it only expands.
- **Change control** — one line: a new criterion mid-POC extends the time-box or replaces an
  existing criterion. It never silently joins the list.

## Step 5 — The technical-win memo

Drafted as part of the plan, filled in at the end — because a memo designed at the end is a memo
written to whatever happened.

It records, per criterion: **result** (pass / fail / not run) · **who verified it** · **the evidence**
· **what it does not prove**. That last field is the honest one and it is what earns the next
conversation: a POC that proves one thing cleanly and says so is worth more than one that claims
to have proven four.

## Step 6 — Output

Save as **`poc-plan-[company]-[YYYY-MM-DD].md`** in the account folder
`content/<active>/accounts/<account-slug>/`. Structure:

1. **What is being proven** — the sentence from Step 2, and who changes their mind.
2. **Criteria** — the table from Step 3.
3. **Scope** — in, out, and each side's obligations with dates.
4. **Time-box** — dates, their effort, ours.
5. **Exit criteria** — pass path and fail path, both named.
6. **Risks to the POC itself** — access, data, people, environment.
7. **Technical-win memo** — the skeleton from Step 5, unfilled.

Only when running under the Telegram cockpit, append the `⟦FILE:…⟧` sentinel with the real absolute path.

## Guardrails

- **A criterion that cannot fail is not a criterion.** Rewrite it or drop it.
- **Every criterion has a named verifier on the customer's side**, or it is a demo — say so.
- **Never write a criterion against a roadmap capability.** Tag every capability the plan depends on
  SHIPPED/CONDITIONAL/ROADMAP (`docs/product-accuracy.md`); a POC is where a roadmap claim becomes a
  broken promise with a date on it.
- **The fail path is agreed before the POC starts**, in the document. Not after.
- **Read-only.** Plans a POC; provisions nothing, contacts nobody, commits no resource.

## How to close this run (every surface)

Report, in this order and in the operator register (the `gtm-operator` output style): Lead with the outcome; what matters about it in their terms; the next decision as a choice they can answer; and what it cost, exactly as the ledger reported it, if anything metered ran.
File paths, commands, module names and raw output go in a final
<details><summary>Details</summary> … </details> block; the main reply must make sense
without it.

Markers: emit a ⟦…⟧ marker (⟦GATE:…⟧, ⟦POST⟧, ⟦FILE:…⟧) only when your system prompt carries
a `Surface:` line that says so. Otherwise show the same content as a quoted block headed
"This is exactly what would go out."

Active profile: the one in your system instructions, or, in the desktop app, the answer to
`uv run python -m gtm_core.active_profile show`.
