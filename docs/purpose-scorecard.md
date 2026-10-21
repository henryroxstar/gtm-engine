# Purpose scorecard — the judgment layer that checks a deliverable *works*

Companion to [`prose-craft.md`](prose-craft.md). The two docs are the **two layers** of content
quality, and they answer different questions:

| Layer | Question | Tool | Nature |
|---|---|---|---|
| **1 — tells** | Does it *read* like a machine wrote it? | `tests/linter/content_linter.py` (`prose-craft.md`) | Deterministic regex — CI-gateable |
| **2 — purpose** | Does it *achieve what it was for*? | this scorecard — model fills it, shows it, before presenting | Judgment — no regex sees "misses the point" |

Layer 1 is necessary and not sufficient. A reply can pass every tell check and still fail its job —
a clean, specific, well-voiced comment that warms no one and sells nothing. That failure is invisible
to a regex, so it needs a **visible, self-scored gate**: the author writes the scorecard *before*
presenting the draft, and any ❌ (or a weak score on a load-bearing goal) is a **revise trigger**, not
a footnote.

## When a purpose scorecard earns its place

Not every skill needs one — a soft self-score bolted onto mechanical output just trains box-ticking
(the rubber-stamp failure mode). Add a scorecard only where **all three** hold:

1. The output has a real purpose beyond correctness (persuasion, positioning, judgment).
2. Failing that purpose is **not deterministically catchable** — no linter/test sees it.
3. Silent failure is a live risk — "technically clean, actually useless."

Where a goal *can* be enforced in code, prefer that — code can't rubber-stamp itself. (The
community-signal metrics and the voice-of-customer speaker tags are computed deterministically for
exactly this reason; they don't need a self-score for those properties.)

## §2 — The social-selling deliverable rubric (outreach family)

Shared by the relationship/outreach skills — **`linkedin-reply`, `draft-outreach`, `email-sequence`**
— because they share one goal set. Grade every draft against all six, each with a one-line basis, and
show the table before presenting.

| # | Goal | Passes when… | Common failure |
|---|---|---|---|
| 1 | **Build a relationship** | Opens signal-first on what they actually did/shipped; credits their move before any gap; peer register, addressed to them | Generic-observation opener ("this stuck with me"); no credit; vendor tone |
| 2 | **Further the conversation** | Ends on a specific, answerable, non-rhetorical question tied to their world | A rhetorical question, or a flat closing statement that invites no reply |
| 3 | **Warm the lead** | Leaves a thread to pull — a reframe, an open state to reveal — that makes a next touch natural | All give, no hook: sharp insight that gives them no reason to engage back |
| 4 | **Align with best practice** | Specific (could not be pasted under any post), value-first not pitchy, veiled bridge where apt, inviting close | Meets the *generic* bar but skips the veiled bridge / signal-first / inviting close |
| 5 | **Indirectly sell** | A ≤1-sentence bridge (veiled by default for experts; named for peers) makes them feel the seam the product fills, anchored to a SHIPPED capability | No bridge at all — the single most common miss on a bullseye post |
| 6 | **Get them excited** *(lever only)* | Carries a reframe or non-obvious insight they'll want to agree with or argue — the *lever* for a reaction | Correct but inert — nothing to react to. **Grade the lever, not the outcome** (see below) |

### Honesty rule for #6

Excitement is an emotional reaction; you cannot *test* it pre-send. Grade only whether the draft
**carries an excitement lever** (a reframe/insight worth reacting to) — never claim it *will* excite.
The real verification of #6 is the recipient's actual reply. Mark it ⚠️ *lever present, outcome
unverified* rather than ✅ when there's no live signal yet.

### Discipline

- **Fill it honestly.** A scorecard of six ✅s written without looking is worse than no scorecard —
  it manufactures false confidence (the exact rubber-stamp trap this repo keeps flagging). If a goal
  is weak, say so.
- **A ❌ or a weak load-bearing goal is a revise trigger**, not a note to the reader. Fix the draft,
  then re-score.
- **Show the table** with the draft, so the reviewer sees the self-assessment and can challenge it.

## What this is NOT

Not a hard gate (it never blocks — it forces a *visible judgment*). Not a second linter — it is the
judgment layer a linter can't be. Not portable beyond its goal set: analytical/internal deliverables
have a *different* purpose (evidence integrity, no fabrication, source attribution) and get their own
rubric when one is warranted — never this one.
