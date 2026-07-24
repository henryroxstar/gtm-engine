<!--
Standalone Solution Overview template (de-branded, reusable).

HOW TO USE
- Copy this file per account; fill in every [bracketed placeholder]; delete the _italic guidance_ lines.
- This is the same structure the `solution-design` skill produces automatically — use this only when
  drafting by hand. The skill is the faster path (it grounds the design in the profile's product refs).
- CUSTOMER COPY = Executive summary + Tier 1. Delete Tier 2 (and ALWAYS the internal appendix) before
  sending to a customer. Tier 2 is for the customer's own architects / your internal record.
- MODE: sections marked "(Mode A only)" assume an off-the-shelf product you map onto. For a bespoke
  custom build (Mode B), delete them and follow the "Bespoke (Mode B) delta" note at the very bottom —
  and never import a product's component vocabulary.
- Render the diagrams + polished styling with `references/html-companion.md` (produces the .html).
-->

# [Use case] — Solution Overview

_[Company] · [the product] · [YYYY-MM-DD]_

---

## Executive summary

_½ page, no jargon — five lines a busy exec reads in a minute. Open with the outcome band (below): the
before→after payoff as a designed element. It degrades to a readable line in a plain viewer._

<div class="outcome"><span class="o-before">[before, e.g. "trust me"]</span><span class="o-arrow">→</span><span class="o-after">[after, e.g. provable]</span><span class="o-note">[one-line qualifier]</span></div>

- **What it is:** [the value, in one sentence]
- **The problem:** [one line]
- **The solution:** [one line]
- **The outcome:** [before → after, e.g. "10–14 days → under 30 seconds"]
- **Who it's for:** [the buying / using stakeholders]

---

# Tier 1 — Customer overview

## 1. What we heard

_The discovery recap — 3–5 lines — plus a glossary of the customer's OWN terms (a second reader knows
neither your product's terms nor theirs)._

[Use case agreed on the call, the pains it addresses, the why-now driver, what this covers vs defers.]

**Systems & agents in scope**

- **[Customer system / agent]** — [one line: what it is / does]
- **[Their acronym]** — [plain meaning]

## 2. The problem & why now

_Quantify only where the numbers are real; keep it to what the customer already feels._

[The pain today, step by step, and what it costs (time / error / risk).] [The market or regulatory
"why now" driver.]

## 3. The solution

_Plain language first, then stakeholder value, then the pieces._

[What's being built, in plain terms — one short paragraph.]

**Who it's for & what they get**

- **[Stakeholder, e.g. compliance/legal]** → [concrete benefit, e.g. a provable audit chain]
- **[Stakeholder, e.g. platform/engineering]** → [concrete benefit, e.g. drop-in, no rewrite]
- **[The business]** → [concrete benefit, e.g. ship with governance built in]

**The pieces**

- **[Component / service]** — [one line]
- **[Component / service]** — [one line]

## 4. How [the product] works  _(Mode A only)_

_The product primer + key terms. This is a first-class section — customers still need teaching on the
product. Delete entirely for a bespoke build._

[What the product is, in plain language — e.g. "a secure [category] that sits in front of your AI
agents; every request passes through it; it drops in with no agent rewrite."]

[What it does, on every request — rendered as the control strip (numbering is meaningful: each request
passes through these in order). Degrades to a numbered list in a plain viewer.]

<ol class="controls"><li><b>[Step 1]</b><span>[what it does]</span></li><li><b>[Step 2]</b><span>[what it does]</span></li><li><b>[Step 3]</b><span>[what it does]</span></li><li><b>[Step 4]</b><span>[what it does]</span></li><li><b>[Step 5]</b><span>[what it does]</span></li></ol>

**Key terms**

| Term | Plain meaning |
|---|---|
| **[Term]** | [one line] |
| **[Term]** | [one line] |

## 5. Architecture: current → target

_(Mode A: open by saying how the design realises the product's identity spine — identity is the core,
not a feature listed later.) Every diagram ships with a plain-language walkthrough._

**Current state**

```mermaid
[current-state / today's-workflow diagram]
```

_How to read this:_ [one line], then [a per-node line]. What's broken today: [gap the design closes].

**Target state**

```mermaid
[target-state architecture diagram]
```

_How to read this:_ [one line], then [per-node lines]. [Which capability closes which gap from current
state.] Closest proven shape: [case-study shape it mirrors].

## 6. How it works — end to end

_The representative request as numbered steps (caller → [the product] → target → response)._

```mermaid
[request-sequence diagram]
```

1. [step — what happens]
2. [step — what happens]
3. [step — what happens]

## 7. What ships first — V1 / V2 / not building

_A first draft carries no changelog — just the current cut to align on; **a revision (v2+) carries a
Version log at the back** (see the last section). No rigid roadmap. Render the cut as the two phase
cards (V2 recessed); keep "not building" as ordinary bullets below._

<div class="phases"><section class="phase"><h4>V1 <span class="when">[POC · weeks]</span></h4><ul><li>[high/medium-confidence item, confirmed source]</li><li>[item]</li></ul></section><section class="phase phase-next"><h4>V2 <span class="when">Next</span></h4><ul><li>[deferred item]</li><li>[item]</li></ul></section></div>

- **Not building (V1):** [explicit list — mandatory]

## 8. Talking points & FAQ

_Keep it light — a few benefit lines + the top objections. The full pitch is the deck's job._

- [one-line benefit / talking point]
- [one-line benefit / talking point]

<details><summary>[Common question?]</summary><p>[Short, honest answer.]</p></details>
<details><summary>[Common question?]</summary><p>[Short, honest answer.]</p></details>

## 9. Further reading

- [[Product docs]]([url])
- [[Standard / protocol the design cites]]([url])
- [[Regulatory driver]]([url])

> **Assumptions & still confirming** — [3–5 load-bearing assumptions and top open questions]. These may
> change the target architecture above; full detail in the appendix.

---

# Tier 2 — Technical appendix

_Technical detail — for the customer's architects; **omit from the exec / customer copy.**_

## A1. Assumptions

- [every place the design assumes something not yet confirmed]

## A2. Open questions & dependencies

- **Customer to provide:** [...]
- **Decisions to make:** [...]
- **Vendor to confirm internally:** [...]
- **Beta constraint:** [...]

## A3. Component inventory  _(Mode A — the bridge to the setup runbook; keep this heading + types)_

| Component | Type | What it does |
|---|---|---|
| [name] | Surface / Proxy / Credential / Policy / Connection / Secret | [configuration] |

## A4. Identity, policy & data flow  _(Mode A)_

_Lead with the identity spine (per agent, every leg); close with the capability-coverage matrix._

| Capability | Enforced / Simulated / Design-target | Notes |
|---|---|---|
| [capability the design claims] | [tag — align to the reference demo] | [note] |

## A5. Standards alignment  _(Mode A — only the 2–3 frameworks this account uses)_

| Requirement | Product control | Framework control ID | Evidence artifact | Owner |
|---|---|---|---|---|
| [requirement] | [control] | [ID] | [artifact] | [owner] |

## A6. Shared responsibility  _(Mode A)_

| [The product] does | Customer does | Out of scope → [the safety product] |
|---|---|---|
| [...] | [...] | [...] |

_Honest scoping: state the company's real certifications accurately; the product produces audit-ready
**evidence** for the customer's compliance — it is not itself the certification._

## A7. Trade-offs & alternatives considered

- **[Material choice, e.g. managed vs self-hosted]:** [pros] / [cons] → **recommendation:** [x]
- **[Material choice, e.g. build vs buy]:** [pros] / [cons] → **recommendation:** [x]

## A8. Internal appendix _(omit from customer copy)_

- [persona codes, ICP score, deal context — never in the customer-facing body]

## Version log  _(revisions only — omit on a first draft)_

_Every substantive change since the prior version, so a detailed reviewer can trace each edit to its
section. Below the table, add a short "precise wording (vN → vN+1)" note for the most material corrections._

| v | Date | Section(s) | Change | Why |
|---|---|---|---|---|
| [v2] | [YYYY-MM-DD] | [§ refs] | [what changed] | [why — grounded reason / reviewer point] |

---

<!--
BESPOKE (Mode B) DELTA — for a custom build with no off-the-shelf product to map onto:
- DELETE §4 (How [the product] works) — there is no product to teach.
- §3 becomes "How this build works" (a one-paragraph plain-language summary of the proposed system).
- §5 / §6 use your bespoke architecture layers (Interface · Agents/Logic · Intelligence/Data sources ·
  Integration/Egress · Data/State) with a real data source/API named per layer — NEVER import a
  product's component vocabulary (no Surfaces/Proxies/Policies, etc.).
- Tier 2: REPLACE A3 with the feature/feasibility table (Capability | Buildable now? | Data/API source |
  Hard parts | Confidence | V1/V2); REPLACE A4 with "Tech choices" (stack + rationale); DELETE A5 & A6.
-->
