---
name: solution-scope-check
description: >-
  The customer-facing **Scope Check** — a short, on-brand 2-page Word (.docx) worksheet the
  buyer marks up to **confirm or reshape the scope** — that runs at either of two moments:
  **pre-design**, sourced from a `solution-discovery` brief (page 1 = what we heard + the
  direction we're leaning; page 2 = the questions to answer so the design can be made right —
  validates scope *before* investing in the design), or **post-design**, sourced from a
  `solution-design` (page 1 = the solution simplified; page 2 = the residual assumptions the
  design rests on — confirm *before* build). The question bank is the same generic,
  decision-tagged set either way; only page 1's source differs. **Draft by default.** Trigger
  when the user says "scope check for [company]", "solution scope check", "scope validation
  for [company]", "validate the scope for [company]", "scope check before the design", "make a
  scope-check doc", "questions to validate the scope for [company]", or "simplify the solution
  design plus validation questions". Consumes a `solution-design` or `solution-discovery`
  dossier (and `account-dossier`) when present, and draws its questions from a generic
  question-type bank (`references/question-types.md`). Reads PROFILE for brand, byline, output
  folder, and language. Read-only; builds the .docx with python-docx (direct formatting, no
  style overrides). Produces files in content/<active>/accounts/<account-slug>/.
metadata:
  version: "0.1.0"
  phase: "5"
  capability_tier: core
---

# Solution Scope Check

The **customer-facing scope worksheet**: a **2-page Word (.docx)** that restates what's being proposed
in plain terms and then asks the handful of questions that will **confirm or reshape the scope**. It is
a worksheet the buyer marks up and sends back — every answer either confirms an assumption or changes a
specific design decision.

**It runs at either of two moments** — the questions are the same generic bank; only *page 1's source*
differs:

- **Pre-design (discovery-sourced).** Run *after* `solution-discovery`, *before* the detailed design.
  Page 1 = **what we heard + the direction we're leaning** (provisional — no full architecture yet);
  page 2 = the questions to answer *so the design can be made right*, from discovery's requirements
  question bank. Validates scope **before** you invest in the design; the design then consumes the
  answers as confirmed inputs.
- **Post-design (design-sourced).** Run *after* `solution-design`, *before* build. Page 1 = **the
  solution, simplified**; page 2 = the residual assumptions and open questions **the design rests on**,
  to confirm before build.

**Draft by default.** It's issued while things are still moving — mark it a **draft** on both pages,
and frame every answer as something that sharpens or reshapes what comes next, not a commitment.

**Two pages, read-only, a companion.** Page 1 = plain-language restatement; page 2 = grouped
scope-validation questions, each tagged with the decision it unlocks and the assumption it tests. It
documents questions; it never provisions, sends, or contacts anyone, and it never replaces the design.

**Where it sits in the SA chain:**
`solution-discovery → [scope-check · pre-design] → solution-design → [scope-check · post-design] → build-deck / gateway-runbook`.
It is not a fixed step — it is the doc you send the customer **whenever you need scope confirmed**:
before the design (to validate inputs) or after (to validate the design).

**Product-agnostic.** Works for a product-led (Mode A) or a bespoke (Mode B) engagement. The domain
vocabulary comes entirely from the discovery brief or design it consumes; the question *types* are
generic (`references/question-types.md`).

---

## Step 1 — Intake & resolve the mode

> Resolve the active profile from `profiles/<active>/`. This is a customer-facing doc — use the
> profile's brand, byline, output folder, and language. Override the doc's accent with the profile's
> brand colour if one is documented in `profiles/<active>/knowledge/brand/` (else neutral default).

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `brand_name`, `email_signature` /
   byline, `output_folder`, `language`. Respect language.
2. **Resolve the mode** from what exists in the account folder `content/<active>/accounts/<account-slug>/`:
   - A fresh **`solution-design-[company]-*.md`** present → default **post-design** (richer: it has a
     concrete solution to simplify + its own residual assumptions).
   - Only a fresh **`solution-discovery-[company]-*.md`** present → **pre-design**.
   - **Both** present → default **post-design**; the operator may force **pre-design** (e.g. to
     validate scope *before* committing effort to the design) — honour that.
   - **Neither** → ask for a design, a discovery brief, or a short plain-language direction first.
     Do **not** fabricate a solution or a discovery.
3. **Consume the primary source** for the resolved mode (verbatim in meaning — do not invent):
   - **Post-design** → the design's **Executive summary** (value / problem / solution / outcome /
     who-it's-for), the **V1 / V2 / not-building** cut, and the **assumptions box + open-questions
     appendix** (the raw material for page 2).
   - **Pre-design** → the discovery brief's **Current state user journey** (the problem + jobs-to-be-done
     — the "what we heard"), the **direction/approach** under consideration and **frameworks to cite**,
     and the **Requirements question bank** (its must-ask items — already tagged with the design decision
     each unlocks; the raw material for page 2).
4. **Consume the account dossier (optional).** If a fresh `account-dossier-[company]-*` exists, pull the
   **buyer's name + role** for the recipient line. Never restate the whole dossier.

**Confirm in one short message** (skip what the source already answers):

- **Recipient** — buyer name + role (from the dossier, or asked).
- **Mode + source** — pre-design (from the discovery brief) or post-design (from the design file), and
  which file.
- **The open decisions to validate** — the top 4–8 that actually matter (from the design's
  assumptions/open-questions, or discovery's must-ask bank); the rest can be carried as stated
  assumptions.

---

After Step 1, emit the gate marker on its own line:

⟦GATE:plan⟧

Do not proceed until the operator confirms the recipient, the mode + source, and the decisions to validate.

---

## Step 2 — Page 1: the solution / direction, simplified

Page 1 is a plain-language restatement a busy buyer reads in a minute. Its **shape depends on the mode**;
everything on it must trace to the source (design or discovery brief) — never a new proposal.

### Post-design (from the solution design)

Restate the design's Executive summary in plain, executive language:

1. **Header** — a `DRAFT · SCOPE CHECK` kicker · a plain title (e.g. "The solution, simplified — and
   what we need to confirm") · the recipient line (name, role, company · product/brand · **draft,** date).
2. **The one-line outcome** — the design's before→after payoff in one sentence, then the value in one line.
3. **What we're proposing (in plain terms)** — 4–6 short bullets: the core move, each key requirement in
   one line, the underpinning (audit/evidence), any deployment note. Demote anything the design marks
   tertiary to a single clause. No jargon, no enforcement-tag nuance.
4. **The N things you asked for → how each is delivered** — a two-column table mapping the buyer's stated
   requirements to the mechanism, plus one row for the underpinning. N = however many the design names.
5. **What V1 covers / later / not now** — three compact lines from the design's V1 / V2 / not-building cut.

### Pre-design (from the discovery brief)

Present the **direction**, clearly provisional — you do **not** have a detailed architecture yet, and
presenting one would be the over-investment you're trying to avoid:

1. **Header** — a `DRAFT · SCOPE CHECK` kicker · a plain title (e.g. "What we're solving — and what we
   need to confirm before we design it") · the recipient line (**draft,** date).
2. **The one-line goal** — the problem/outcome in one sentence (from the discovery journey).
3. **What we heard** — the problem + jobs-to-be-done, in plain language (from the current-state journey).
4. **The direction we're leaning** — the approach/pattern under consideration as **2–4 bullets, framed
   as a hypothesis to confirm, not a committed design**. If more than one approach is live, name them as
   options. Do **not** invent a detailed architecture, a component inventory, or firm V1/V2 scope — those
   are the *output* of the design this worksheet precedes.
5. **What we'd need to lock to design it right** — one line pointing to the page-2 questions.

## Step 3 — Page 2: the scope-validation questions

From `references/question-types.md`, **select the 4–8 question types that match the open decisions**, and
instantiate a **question group** per decision (not one per available type). Source the open decisions by mode:

- **Post-design** → from the design's **assumptions box + open-questions appendix**.
- **Pre-design** → from discovery's **Requirements question bank** (its must-ask items — each is already
  tagged with the design decision it unlocks; map each to the matching generic type).

Each group carries:

- **A group title** phrased as the question it answers.
- **`→ unlocks: [the decision]`** — the specific decision this resolves. Mandatory — a question with no
  decision behind it is cut.
- **1–3 concrete questions** — the bank's templates, filled with the real systems/actors/terms.
- **`Our assumption today: [x]`** — the assumption currently being rested on, so a blank answer becomes an
  *explicit* assumption, not a silent guess. (Omit only where no assumption has been made yet.)

Close with a one-line invitation ("Answer what you can — anything left blank we'll carry as an explicit
assumption in the next revision, not a silent guess.") and a footer (brand · subject · prepared-by byline ·
"companion to solution-design-[company]-[date]" **or** "companion to solution-discovery-[company]-[date]",
whichever this was built from). **Order groups by leverage** — the decision that most reshapes what comes
next first; definition-of-done last.

---

After assembling page 1 + the question groups, emit the gate marker on its own line:

⟦GATE:plan⟧

Do not render the .docx until the operator confirms the simplified restatement and the question set.

---

## Step 4 — Render the .docx

Build the 2-page Word doc with **python-docx**, following `references/scope-check-template.md` (the
de-branded structure + a builder skeleton with brand tokens). Non-negotiables (see Guardrails):

- **Direct run formatting + Arial** on every run — never override the `Normal`/`Heading` styles (style
  overrides render serif/tight in Quick Look/Word even when LibreOffice looks fine).
- **Brand accent** from the profile applied to the kicker, section rules, the table header, question
  numbers, and the `→ unlocks` tags — one accent, navy/near-black ink.
- **Two-page discipline** — compact spacing; if it spills to a third page, tighten the assumption notes or
  drop the lowest-leverage group. Never past two pages.

Save as **`solution-scope-check-[company]-[YYYY-MM-DD].docx`** in the account folder
`content/<active>/accounts/<account-slug>/` (see CLAUDE.md "Per-account outputs").

**Verify before done** (verification-before-completion): convert to PDF, confirm it is **exactly 2 pages**,
and generate a Quick Look thumbnail (`qlmanage -t`) to confirm the font renders as **Arial, not a serif
fallback**. Report the page count.

After all prose, append the `⟦FILE:…⟧` sentinel so the cockpit delivers the file:

```
⟦FILE:/absolute/path/to/content/<active>/accounts/<account-slug>/solution-scope-check-[company]-[YYYY-MM-DD].docx⟧
```

Offer the natural next step by mode: **pre-design** → feed the answers into `solution-design`;
**post-design** → fold the answers into the design's next revision.

## Guardrails

- **Pick the mode by what exists; respect an override.** Design present → post-design (default);
  discovery-only → pre-design; both → post-design unless the operator forces pre-design; neither → get a
  source first, never fabricate.
- **Pre-design page 1 presents a provisional *direction*, never a fabricated architecture.** No component
  inventory, no firm V1/V2, no invented detail — that's the design's job, downstream. Post-design page 1
  faithfully *simplifies* the design.
- **Draft by default.** Mark both pages a draft; frame answers as reshaping what comes next. Drop the
  draft marker only on explicit operator instruction.
- **Companion, not replacement.** Page 1 restates a source; it never invents a solution or a discovery.
- **Every question earns its place.** Each group carries a `→ unlocks: [decision]` tag; draw from
  `references/question-types.md`, don't free-associate.
- **State the assumption.** Each group names the current assumption, so a blank answer is an explicit
  assumption in the next step, not a silent guess.
- **De-branded / generic.** The question *types* are domain-agnostic; the vocabulary comes only from the
  consumed source. Never hardcode a product's or company's terms into this skill.
- **Two pages, hard cap.** Compact, scannable. Tighten or drop the lowest-leverage group before spilling.
- **Direct formatting, Arial.** Never override docx `Normal`/`Heading` styles (serif-fallback pitfall);
  format every run directly on `ascii/hAnsi/cs/eastAsia`. Verify with `qlmanage`.
- **One accent.** Brand accent for labels/rules/numbers only; navy/near-black ink; no gradient text, no
  colour side-stripes.
- **Read-only.** No provisioning, no sends.
