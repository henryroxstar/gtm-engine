<!--
Standalone Solution Overview template (de-branded, reusable).

HOW TO USE
- Copy this file per account; fill in every [bracketed placeholder]; delete the _italic guidance_ lines.
- This is the same structure the `solution-design` skill produces automatically — use this only when
  drafting by hand. The skill is the faster path (it grounds the design in the profile's product refs).
- THREE FILES. Part 1 below is the customer overview — the only file the customer reads. Part 2 is the
  technical appendix (its own `-appendix.md`). Part 3 is the internal notes (`-internal.md`, never sent).
  Split them before sending; never send one scroll.
- Before drafting, answer two questions: who reads this (and who presents it), and what risk is the
  reader trying to avoid? §2 leads with that risk.
- Customer overview ≤ ~2,000 words. One visual per section. No "X, not Y" slogans, no working-note
  phrases ("stated plainly", "this design"), every technical term defined where first used.
- MODE: sections marked "(Mode A only)" assume an off-the-shelf product you map onto. For a bespoke
  custom build (Mode B), follow the "Bespoke (Mode B) delta" note at the very bottom — and never
  import a product's component vocabulary.
- Render with `references/html-companion.md`: create the .html once, then re-run
  `gtm_core.design_render` after every edit to the .md. Component markup lives there.
-->

<!-- ═══════════════ PART 1 — CUSTOMER OVERVIEW: solution-design-[account]-[YYYY-MM-DD].md ═══════════════ -->

# [Account] × [Vendor] — Solution Overview

_[The product] · [Pilot design / proposal] · Draft for discussion_

<!-- appendix: solution-design-[account]-[YYYY-MM-DD]-appendix.md -->

## Executive summary

_≤150 words after the headline. The headline names the problem solved; the subline says how and
names the governing framework, if any._

<div class="outcome"><span class="o-after">[The problem the design solves, in one line.]</span><span class="o-sub">[How — who does what — and the framework it follows.]</span></div>

- **The problem.** [The risk the reader wants to avoid, and why today's way does not remove it.]
- **The solution.** [Who does what, in one or two sentences.]
- **The constraint.** [The one limit that shaped the design.]
- **Who it's for.** [The buying / approving roles.]

---

## 1. Requirements

_What the design must do, grouped by driver (the regulation it meets · the framework it aligns with ·
ease of adoption). R1…Rn in group order. Cite section + page of the primary document for every
framework-derived requirement — read the document; never describe its legal status from memory._

<div class="req-groups">
<section><h4><span>[Meet the regulation]</span>[What it asks]</h4><ul>
<li><em>R1</em><b>[Requirement]</b><span>[One sentence.]</span></li></ul></section>
<section><h4><span>[Align with the framework]</span>[What it asks]</h4><ul>
<li><em>R2</em><b>[Requirement]</b><span>[One sentence.]</span><small class="cite">[Framework: component, p. N]</small></li></ul></section>
<section><h4><span>[Easy to adopt]</span>[What it asks]</h4><ul>
<li><em>R3</em><b>[Requirement]</b><span>[One sentence.]</span></li></ul></section>
</div>

<details class="gloss"><summary>Key terms</summary>
<ul class="defs">
<li><b>[Customer system / acronym]</b><span>[One line.]</span></li>
<li><b>[Framework]</b><span>[What it is — and how the document describes its own status.]</span></li>
<li><b>[Product term]</b><span>[One line.]</span></li>
</ul>
</details>

## 2. The problem

_Lead with the risk from the intake question, not with volume or effort. Check every "no X" against
how things work today (an email trail IS a record — say what is actually missing)._

[One or two short paragraphs: the obligation, and why sharing / acting on it today carries the risk.]

[Current-state flow — `.cstate`, or diagram 1 + "How to read it:" line.]

The design has to solve:

- **[Gap]** — [one sentence]
- **[Gap]** — [one sentence]

## 3. The solution

_Who does what, and who stays accountable._

[What is being built, in plain terms — one short paragraph.]

<div class="who">
<section data-own="4"><h4><span>[The customer]</span>[Decides, and stays accountable]</h4><ul><li>[…]</li></ul></section>
<section data-own="3"><h4><span>[The integrator / partner]</span>[Builds and runs …]</h4><ul><li>[…]</li></ul></section>
<section data-own="1"><h4><span>[The vendor]</span>[Provides …]</h4><ul><li>[…]</li></ul></section>
</div>

## 4. How [the product] works  _(Mode A only)_

_The product primer in plain language, then its per-request steps as the check pipeline, each tagged
with the framework component it maps to._

[What the product is, in one or two sentences.]

[Check pipeline — `.gate-pipe`.]

## 5. Architecture

[Suite key — `.suite-key`.]

![[What the diagram shows]](diagrams/[target-state].svg)

*How to read it:* [one line], then [a line per component]. [Where the record stops, if a path is not covered.]

**[The one "why not …?" a reader will ask]** [Two-sentence answer.]

## 6. How it works — end to end

_The representative request, step by step: a `.lanes` swimlane when more than two parties act,
otherwise diagram 3 + a `.steps` walkthrough._

[Swimlane.]

**What is recorded, and what is not.** [Plainly: what the record covers, and where it stops.]

## 7. Common questions

_Grouped by reader type; at most two per type; each answer ≤ ~40 words, from the requirements or the
design only. Anything inferred goes to the internal notes._

<div class="faq">
<p class="faq-h">[Regulator]</p>
<details><summary>[Question?]</summary><p>[Answer.]</p></details>
<p class="faq-h">[IT &amp; security]</p>
<details><summary>[Question?]</summary><p>[Answer.]</p></details>
</div>

## 8. How each requirement is met

_One row per requirement; columns grouped under technology and parties; each cell names what that
part does (2–4 words). Mark a part only if the requirement fails without it; amber only where a real
gap lies, with the gap stated in the row._

[Coverage table — `table.cov`.]

## 9. Further reading

- [[Product docs]]([url])
- [[Primary document of each cited regulation or framework]]([url])

<!-- ═══════════════ PART 2 — TECHNICAL APPENDIX: solution-design-[account]-[YYYY-MM-DD]-appendix.md ═══════════════ -->

# [Account] × [Vendor] — Technical appendix

_Technical detail — for the customer's architects; **omit from the exec / customer copy.**_

## A1. Assumptions

- [every place the design assumes something not yet confirmed]

## A2. Open questions & dependencies

- **Customer to provide:** [...]
- **Decisions to make:** [...]
- **Vendor to confirm internally:** [...]
- **Beta constraint:** [...]

<div class="board"><section><h4>V1 <span class="when">[Pilot · now]</span></h4><ul><li>[item]</li></ul></section><section class="is-next"><h4>V2 <span class="when">Next</span></h4><ul><li>[deferred item]</li></ul></section><section class="is-out"><h4>Not building <span class="when">V1</span></h4><ul><li>[explicit exclusion]</li></ul></section></div>

## A3. Component inventory  _(Mode A — the bridge to the setup runbook; keep this heading + types)_

| Component | Type | What it does |
|---|---|---|
| [name] | [the product's own configurable object type] | [configuration] |

## A4. Identity, policy & data flow  _(Mode A)_

_Lead with the identity spine (per agent, every leg); close with the capability-coverage matrix._

| Capability | Status | Notes |
|---|---|---|
| [capability the design claims] | <span class="tag ok">Enforced</span> | [note] |

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

- **Decision:** [what we chose] · **Date:** [YYYY-MM-DD] · **Alternatives:** [what we did not choose] ·
  **What it cost us:** [the thing we gave up]

## A8. Constraints

_Fixed and not ours to choose — separate from A1's assumptions. One line each, with who owns it._

- …

## A9. Quality requirements

| Attribute | Target | Owner |
|---|---|---|
| Availability | …% over … | … |
| Latency | … ms at the …th percentile | … |
| Throughput | … sustained, … burst | … |
| Recovery | RPO … · RTO … | … |
| Residency | … | … |

_A figure or "not yet agreed" (and then an A2 open question). Never "fast" or "highly available"._

## A10. Deployment topology

_Where each component runs, in whose tenancy, and every boundary crossed. Diagram + a line per hop._

## A11. Glossary

| Term | What it means here |
|---|---|
| … | … |

<!-- ═══════════════ PART 3 — INTERNAL NOTES: solution-design-[account]-[YYYY-MM-DD]-internal.md ═══════════════ -->

# [Account] — Internal notes _(omit from customer copy)_

- **What we heard:** [the discovery recap]
- **Talking points:** [one-line benefit points for the presenters]
- **Inferred, not yet confirmed:** [any FAQ answer or claim that came from inference]
- **Deal context:** [persona codes, ICP score — never in the customer-facing files]

---

<!--
BESPOKE (Mode B) DELTA — for a custom build with no off-the-shelf product to map onto:
- DELETE §4 (How [the product] works) — there is no product to teach. §3 becomes "How this build works".
- §5 / §6 use your bespoke architecture layers (Interface · Agents/Logic · Intelligence/Data sources ·
  Integration/Egress · Data/State) with a real data source/API named per layer — NEVER import a
  product's component vocabulary.
- ADD "What ships first — V1 / V2 / not building" to the customer overview (before Common questions):
  for a bespoke build the scope cut is what the customer is buying. Move the shipping board there.
- Appendix: REPLACE A3 with the feature/feasibility table (Capability | Buildable now? | Data/API source |
  Hard parts | Confidence | V1/V2); REPLACE A4 with "Tech choices" (stack + rationale); DELETE A5 & A6.
-->
