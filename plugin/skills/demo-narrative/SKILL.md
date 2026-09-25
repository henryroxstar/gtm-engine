---
name: demo-narrative
description: >-
  Design the demo flow — the last thing first, the moments that earn the meeting, and what is
  real versus staged. Trigger when the user says "plan the demo for [company]", "demo script",
  "demo narrative", or "what should I show [account]".
metadata:
  version: "0.1.0"
  phase: "5"
  capability_tier: core
---
# Demo Narrative

Design the **flow** of a demo: what the audience sees first, the two or three moments that earn the
meeting, what is real versus staged, and where you stop talking. This is the narrative and the
running order — not the recording (`demo-capture`), not the slides (`build-deck`), and not the
product walkthrough the product team already has.

The failure it exists to prevent is the feature tour: a demo that starts at login and works
forward, so the thing the buyer came to see arrives at minute 22 to an audience that left at 14.

---

## Step 1 — Load context

1. **PROFILE** — `profiles/<active>/PROFILE.md`: `name`, `brand_name`, `language`.
2. **Who is in the room** — from `call-prep-*` or `deck-research-*` Layer 1 (`L1-10` buying
   committee) in the account folder `content/<active>/accounts/<account-slug>/`. A demo for an
   architect and a demo for an economic buyer are different demos; if both are in the room, decide
   whose demo it is and say so out loud at the start.
3. **The design or the use case** — `solution-design-*` (§6 How it works is the demo's spine) or
   `solution-discovery-*` (`L1-6` use-case scenarios: their scenarios, not ours).
4. **`profiles/<active>/knowledge/product.md`** — what is shipped versus roadmap.

## Step 2 — Do the last thing first

Open on the **outcome**, then show how it is reached. Not the login, not the architecture, not the
setup — the finished state the buyer is trying to get to.

This inverts the order the product itself imposes, and that is the point: the product's order is
the *builder's* order. The buyer's question is "what do I get", and every minute before the answer
is a minute they are deciding whether to keep listening. If the demo needs eleven configuration
steps first, that is a fact about the demo environment, not about what to show.

Write the first ninety seconds as a script. It is the only part that has to be exact.

## Step 3 — Pick two or three moments, and cut everything else

A **moment** is a single beat where the buyer sees something they did not expect and that only this
product does. Two is usually right; three is the ceiling. Everything else in the flow exists to get
from one moment to the next.

For each moment record: **what they see** · **why it lands for *this* audience** · **the sentence
you say while it happens** (one sentence — while something is happening on screen, the audience is
reading, not listening) · **and what you do NOT say next.** That last field is the discipline: the
most common way a moment is lost is the presenter explaining it for another forty seconds after it
has already landed.

Then cut. If a step does not lead to a moment, it is a feature tour with better intentions.

## Step 4 — Mark what is real and what is staged, honestly and out loud

Per beat, one of: **live product** · **pre-seeded data** · **recorded** · **mock-up**.

Then say the staged ones out loud in the room, once, briefly. "This data is seeded so we don't wait
for a 20-minute sync" costs three seconds and buys the rest of the demo. A staging choice the
audience discovers on their own retroactively discredits the beats that *were* real — including the
ones that mattered.

**Never show a roadmap capability as live.** Tag every capability the flow touches
SHIPPED/CONDITIONAL/ROADMAP (`docs/product-accuracy.md`). If the narrative needs one, label it on
screen and in your sentence — a technical buyer who catches an unlabelled roadmap feature stops
believing the whole demo, and they are the one who checks.

## Step 5 — Plan the interruptions

A demo that runs its full length uninterrupted usually failed. Plan for it:

- **The question you want.** Name it, and name the beat that provokes it. Reaching it is the
  success condition, not finishing the flow.
- **The three likely interruptions** and where each one lands — "that's later", "let me show you",
  or "let's take that offline". Decide now; deciding live costs the thread.
- **The failure plan.** What you do if the environment breaks mid-demo, in one line. Something will.
- **The stop.** Where you stop and hand the room back, with time left. A demo that runs to the
  minute leaves no room for the conversation the demo was for.

## Step 6 — Output

Save as **`demo-narrative-[company]-[YYYY-MM-DD].md`** in the account folder
`content/<active>/accounts/<account-slug>/`. Structure:

1. **Whose demo this is** — the audience, and the one question it answers.
2. **The first ninety seconds** — scripted, exact.
3. **The moments** — Step 3's table.
4. **Running order** — beat by beat, with real/staged marked and a running time.
5. **Interruptions** — the wanted question, the three likely ones, the failure plan, the stop.
6. **Setup checklist** — what must be seeded, logged in, or pre-warmed, and by when.

Only when running under the Telegram cockpit, append the `⟦FILE:…⟧` sentinel with the real absolute path.

Offer the hand-offs: `demo-capture` (record a beat once and reuse it), `build-deck` (the slides
around it), `poc-plan` (when the room's real question is "does it work on *our* data", which is not
a demo question at all).

## Guardrails

- **Outcome first.** A flow that opens on setup is the failure this skill is for.
- **Two or three moments, never a tour.** If everything is a highlight, nothing is.
- **Staged is declared, in the room, once.** Never let the audience discover it.
- **Never present a roadmap capability as live**, on screen or in a sentence.
- **Never demo with a real customer's data** — seeded or fictional only, and never another account's.
- **Read-only.** Designs the narrative; records nothing, provisions nothing, sends nothing.

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
