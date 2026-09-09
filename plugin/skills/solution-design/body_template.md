
# Solution Design

Convert requirements into an architecture the customer's engineers can react to. The skill runs in
one of two modes — **Mode A (product-led)** or **Mode B (bespoke)** — sharing the same backbone:
intake → feasibility → architecture → diagrams → design doc. Human-in-the-loop gates checkpoint each
phase transition.

**The output is a customer-facing Solution Overview, not an internal SAD.** Structure every design in
three tiers, in this order: a **½-page Executive summary** (outcome-led, no jargon) → a **Customer
overview** (Tier 1 — the default read: problem → solution → how the product works → architecture →
how-it-works → what ships first) → a clearly-marked **Technical appendix** (Tier 2 — the SA rigor:
assumptions, component inventory, standards, shared-responsibility, trade-offs — that the
customer/exec copy drops). Lead with the outcome and the problem; keep the depth, but move it below
the fold. A reader gets the whole story from the Exec summary + Tier 1 and only descends into the
appendix if they're the architect who has to build it. This keeps the customer read short and
scannable without losing any SA rigor.

- **Mode A — product-led.** The active profile has a product providing `solution-architecture` and
  the full Mode-A reference contract. The skill maps requirements onto **that product's** reference
  patterns and components, loaded from `profiles/<active>/products/<product>/references/`. Nothing
  product-specific lives in this skill.
- **Mode B — bespoke.** No off-the-shelf product satisfies the use case (or operator passes
  `--bespoke`). The skill **synthesises a custom architecture** using the generic scaffold in
  `references/bespoke-scaffold.md`. Grounding shifts to **real, buildable tech and data sources that
  actually exist**.

**Mode selection rule:** Mode A if a product with `solution-architecture` resolves **and** all
Mode-A reference files are present. Any missing file → fail soft to Mode B and state the reason.
Operator can force either mode with `--mode-a` or `--bespoke`.

**Write it customer-ready by default.** Assume a competent technical reader who was **not** in the
first call. Open with the ½-page Executive summary, then a "what we heard" recap; avoid internal
shorthand (ICP scores, persona codes) in the customer-facing body — put deal context in the marked
internal appendix.

**Be a trusted, objective advisor.** Name the downside of your own recommendation and never
overclaim. A design that names its boundaries is more persuasive than one that oversells.

**Lead with agent identity (Mode A).** Every design centres the product's **identity spine** — its
verifiable-identity / scoped-credential / policy / tamper-evident-audit chain. The spine's legs and
the product's flagship, real differentiators are tenant facts: load them from the product's
reference pack (`profiles/<active>/products/<product>/references/gateway-reference.md`, the
deck/design-claims section). The spine is the core of the design, not an add-on bolted onto a
connectivity story. Map the solution to that spine first; the proxy/connectivity wiring follows
from it. Lead with the flagship, real differentiators the reference pack names — verified on the
product's reference demo (per its capability-coverage matrix).

**Claim only what's enforced.** Tag every capability the design promises as **Enforced**,
**Simulated**, or **Design-target**, and pull the tags from the demo's capability-coverage matrix so
a green claim can never outrun the current build's ground truth. The reference pack's
deck/design-claims section lists which capabilities are **Enforced** on the current build and which
are **Design-target** — present design-targets as roadmap, never as live. The product produces
audit-ready *evidence*; never present it as itself certified against the frameworks it evidences.

**Read-only.** This skill designs and documents. It never provisions, sends, or contacts anyone.

**Where it sits in the SA chain:** `solution-discovery` → **`solution-design`** → `gateway-runbook`
(Mode A) / `build-deck`. Mode B hands off to `build-deck` for the customer deck.

---

## Step 1 — Intake

> **Resolve the mode first.** Check whether the active profile's `products[]` contains a product with
> `solution-architecture` in its capabilities **and** whether all Mode-A reference files resolve. If
> yes → **Mode A**. If no → **Mode B** (state the missing file or capability that caused the soft
> fail). Operator can override with `--mode-a` or `--bespoke`.

### Mode A — product-led

> Resolve the active profile from `profiles/<active>/`. Resolve the product by the
> `solution-architecture` capability from `PROFILE.md → products[]`; load its specifics from
> `profiles/<active>/products/<product>/`. Use the product's real name throughout — never a
> hardcoded one.

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `brand_name`, `target_markets`, `language`,
   `output_folder`. Respect language.
2. **`profiles/<active>/products/<product>/references/reference-architectures.md`** — the base patterns
   (the "recipes"), the use-case shape → pattern routing, and how to compose patterns.
3. **`profiles/<active>/products/<product>/references/diagram-library.md`** — Mermaid templates for context,
   target architecture, request sequence (the product's processing flow), federation, and deployment topology.
4. **`profiles/<active>/products/<product>/references/standards-crosswalk.md`** — the framework selector,
   per-framework control hooks, the crosswalk-table format, and the **honesty rules**. Then read the matching
   pack(s) in **`profiles/<active>/knowledge/guidance/`** (`nist-`, `enisa-`, `singapore-`, `owasp-`,
   `csa-maestro-`, `airq-gateway-alignment.md`) for the frameworks this account uses.
5. **`profiles/<active>/products/<product>/references/primer-glossary.md`** — the reusable product primer,
   the key-terms glossary, the "what we heard" recap, and the why-now thread (with sourced market stats +
   branded infographic paths).
6. **Knowledge pack** — `profiles/<active>/knowledge/product.md` (the product's processing flow, features,
   themes, deployment options, competitive framing), `profiles/<active>/knowledge/company.md` (positioning),
   `profiles/<active>/knowledge/case-studies.md` (the solution shapes — match the closest as proof).
7. **Consume the discovery dossier (strongly preferred).** Look for a fresh `solution-discovery-[company]-*.md`.
   If present, pull: the technical stack profile, the integration-surface map, the deployment hypothesis, the
   **answered** requirements, the `## Current state user journey` section, **and the framework(s) flagged for this
   account**. If absent, flag that the design rests on assumptions and recommend running `solution-discovery`
   first or capturing requirements inline.

**Confirm in one short message** (skip what the dossier already answers):

- **Use case** (required) — the workflow being enabled.
- **Requirements** — pulled from the dossier; confirm the must-haves and note anything still open.
- **Deployment model** — self-hosted / managed / open-ecosystem (default: managed — the fast path).
- **Targets & protocols** — the services agents will reach and over which protocols (REST→MCP, MCP,
  A2A, AP2).
- **Audience for the doc** — engineers (sequence + component detail), security/risk (policy + audit +
  trust), or execs (context + value). Drives which diagrams lead.

### Mode B — bespoke

> Load `references/bespoke-scaffold.md` for the generic architecture layers, feasibility rubric,
> V1/V2 discipline, and honesty rule. No product references are loaded.

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `language`, `output_folder`,
   `tools_metered`, `monthly_tool_budget_usd`. Respect language.
2. **Consume the discovery dossier if present.** Look for a fresh `solution-discovery-[company]-*.md`.
   If present, pull: the `## Current state user journey` section (jobs-to-be-done, feature list, V1/V2 intuition)
   **and** the `## Workflow Profile` section. If absent, gather inline in sub-step 3.
3. **Gather inline (if no dossier or current-state user journey is missing).** Ask in one short message:
   - **Jobs to be done** — what manual workflow is being automated? Describe it step by step.
   - **Feature list** — the capabilities the customer imagines (all of them, unconstrained).
   - **Stack & integrations** — what tools/platforms/APIs are in play today?
   - **Data sources** — where does the key data live? Is there a public or partner API for it?
   - **Constraints** — budget, timeline, existing infrastructure, regulatory exposure.
4. **Load `references/bespoke-scaffold.md`** — the architecture layers, feasibility rubric, V1/V2
   discipline, and honesty rule that guide Steps 2 and 3.

After gathering, restate concisely: "Here's what I heard: [functional goals]. [Tech constraints].
[Data sources confirmed/missing]. Confirm or correct before I assess feasibility."

---

After Step 1, emit the gate marker on its own line:

⟦GATE:plan⟧

Do not proceed to Step 2 until the operator confirms, edits, or rejects the requirements summary above.

---

## Step 2 — Feasibility pass

Assess each proposed capability before committing to scope. Produce a **feature/feasibility table**,
an explicit **V1/V2 cut**, and a **"What we are NOT building (V1)"** list.

### Mode A — product-led

For each capability in the confirmed requirements:
- Does the product's `reference-architectures.md` contain a pattern that covers this? (state the pattern slug)
- Are there beta constraints noted in the product reference?
- Confidence the product covers this as scoped (H/M/L).

Produce: the pattern selection with any constraints noted. Carry any L-confidence items forward as open
questions rather than scoping them into V1.

### Mode B — bespoke

Apply the **feasibility rubric** from `references/bespoke-scaffold.md`. One row per proposed capability:

| Capability | Buildable now? | Data / API source | Hard parts / unknowns | Confidence | V1 or V2 |
|---|---|---|---|---|---|
| _(list each from the feature wishlist)_ | Yes/No/Partial | _(API name, official/unofficial)_ | _(rate limits, auth, missing data)_ | H/M/L | V1/V2 |

**Apply the honesty rule from `references/bespoke-scaffold.md`:** any L-confidence capability or one
with no confirmed data source → force to V2 or flag as a risk. Never scope optimistically.

**Depth for data-source verification:** free/offline research by default (web search, public docs).
Pass `--deep` to trigger a targeted metered web check on a specific data source (within PROFILE
budget; only if `tools_metered` allows). `--deep` is for confirming one uncertain API, not a full
research pass.

Summarise as:

- **V1 scope** — capabilities with H or M confidence and a confirmed data source.
- **V2 scope** — everything else.
- **What we are NOT building (V1)** — explicit list (mandatory and non-optional).

---

After Step 2, emit the gate marker on its own line:

⟦GATE:plan⟧

Do not proceed to Step 3 until the operator confirms, edits, or rejects the V1/V2 scope cut above.

---

## Step 3 — Select the architecture

### Mode A — product-led

Using `profiles/<active>/products/<product>/references/reference-architectures.md`, map the use case to one
base pattern, or **compose** several (most real designs are Pattern 1 + one or two others):

1. **Expose an internal service/MCP server** to agents (single gateway).
2. **Cross-org federation** (two gateways, verifiable identity, no shared secrets).
3. **Per-tool authorization** on an MCP server (allow/deny per tool).
4. **Verify an external agent's identity** (trust registry).
5. **Outbound credential delegation** (agent acts on a user's behalf).
6. **Monetise / paywall** agent access.

State the chosen pattern(s) and *why* — tie each to a confirmed requirement. Name the closest
case-study **shape** as proof (internal multi-agent workforce / cross-org B2B exchange /
machine-as-consumer commerce / multi-party verifiable workflow).

**First, map the solution to the identity spine.** Before the pattern, state — in one or two lines
each — how this solution realises each leg of the product's identity spine, since this is the
differentiated core. The legs, the issuance mechanics, and the who-issues-what split come from the
product's reference pack (`profiles/<active>/products/<product>/references/gateway-reference.md`) —
walk every leg, and name the agents in scope (≥2 for least privilege).

**Then map to the product's feature list and say which the chosen pattern EXERCISES vs leaves on the
table.** Lead with the flagship, real differentiators from the reference pack's deck/design-claims
section — every design exercises these. Capabilities that section tags **Design-target** on the
current build are roadmap — if the use case needs them, name them as roadmap, not as exercised
today. List the features the pattern leaves untouched honestly rather than implying full
coverage.

### Mode B — bespoke

Using the **generic architecture layers** from `references/bespoke-scaffold.md` as the starting scaffold:

1. Assign each V1 capability to the layer(s) it lives in (Interface · Agents/Logic · Intelligence/Data
   sources · Integration/Egress · Data/State). Adapt or rename layers to match the domain.
2. For each layer box, annotate the **grounding data source or API** — the real system it reads from or
   writes to. No box without a named source.
3. Show the data flow: Interface → Agents/Logic → Intelligence/Data sources → Data/State.
4. Name any agent-shaped loops identified in the workflow profile (repetitive, rules-based, bounded
   domain, data available).
5. State the tech stack choices that make this buildable now (agent framework, interface, key APIs).

Do not import gateway patterns, dimensions, or component vocabulary. All architecture comes from the
bespoke scaffold and the functional inputs.

---

## Step 4 — Produce the diagrams

### Mode A — product-led

Generate Mermaid from the templates in `profiles/<active>/products/<product>/references/diagram-library.md`,
tailored to the account. Always produce diagrams 1–3; add 4–5 when the use case warrants (a policy
gate the customer cares about; a self-hosted/federated deployment). Lead with whichever fits the audience:

1. **Context / current-state** — their existing stack today, and the gap (no agent identity, no
   runtime policy, no cross-boundary trust). Shows what's broken.
2. **Target-state architecture** — the product dropped in as the intercepting proxy, showing the
   product's processing flow and the components wired to their real targets.
3. **Request sequence** — a representative request walked through the processing steps end to end
   (caller → product → target → response), so engineers see exactly what happens per call.
4. **Policy decision** — the allow/deny fork the design turns on (e.g. Analytics-agent **read, scoped**
   vs Author-agent **denied** on the same tool). The single diagram that makes the governance concrete.
5. **Deployment topology** (optional) — where the product runs (managed vs self-hosted), networking,
   and federation hops if the federation pattern applies.

The product's **per-request control steps** (caller-context → identity → policy → injection → proxy)
are **not** a Mermaid diagram — render them as the **control strip** HTML component (Step 5), which is
more scannable than a linear flowchart and avoids duplicating the request-sequence diagram.

Keep each diagram readable — one idea per diagram, not everything on one canvas. **Every diagram ships
with a walkthrough:** a one-line "how to read this," then a per-node/per-component line in plain
language. The big target-state diagram especially must not stand alone. In the doc, diagrams 1–2
anchor Tier 1 §5 (Architecture: current → target), diagram 3 anchors §6 (How it works — end to
end), and diagram 4 (policy decision) sits in §5 or §6 next to the control strip. Apply the **Mermaid gotchas**
checklist in `profiles/<active>/products/<product>/references/diagram-library.md` before saving (no `;`
in sequence text, quote all flowchart labels). The doc can later go to `build-deck` for polished slides.

### Mode B — bespoke

Produce two Mermaid diagrams:

1. **Context / current-state** — the prospect's manual workflow today, step by step. Highlight where
   time is lost and where the human acts as the connector between systems.
2. **Target-state layer diagram** — the proposed architecture using the bespoke layers, with real
   component names and grounding data sources annotated for each layer box.

Skip gateway-specific diagram types (request sequence through a multi-step processing flow, federation
topology). Every diagram ships with a walkthrough. One idea per diagram.

---

## Step 5 — Assemble the design doc

**No metadata header.** Start with the `# Title` and go straight into §1.

### Mode A — product-led

**Define the customer's own terms, not just the active company's.** A second reader won't know the
*account's* product names and acronyms either. In §1, briefly explain each customer system/agent/acronym
the design references (e.g. "ARC = their cloud platform unifying X", "T1 = their conversational agent that
does Y"). One line each. This is as important as the product glossary.

Structure the output in three tiers — **Executive summary → Customer overview (Tier 1) → Technical
appendix (Tier 2)**. Lead with outcome and problem; the honesty items (assumptions, open questions)
get a short summary box in Tier 1 and their full detail in the appendix, so the overview isn't
front-loaded with caveats.

**Executive summary (½ page, top of the doc).** A handful of short lines a busy exec reads in a
minute: the value in one sentence · the problem in a line · the proposed solution in a line · the
headline outcome (a before→after where one side is real, e.g. "days → seconds") · who it's for. No
diagrams, no jargon. **Open the Exec summary with the outcome band** — render the headline before→after
as the `<div class="outcome">` block (see `references/html-companion.md` Notes) so the one-line payoff
reads as a designed element, then the short bullets follow.

#### Tier 1 — Customer overview (the default read)

1. **What we heard** — the discovery recap (use case, pains, why-now), **plus a short "systems & agents
   in scope" glossary defining the customer's own terms/products/agents** in one line each (as
   important as the product glossary).
2. **The problem & why now** — the pain today, quantified where the numbers are real (market context,
   the regulatory / why-now driver from the primer's why-now thread), and what it costs. Keep it to
   what the customer already feels — enough to answer "why now," not a stats dump.
3. **The solution** — a plain-language description of what's being built, **who it's for & what they
   get** (name the stakeholders in plain terms and the concrete benefit each gets — counsel → provable
   audit chain; platform → drop-in, no rewrite; business → governance built in), and a short summary of
   the pieces involved.
4. **How [the product] works** — the product-education section, **surfaced from the product's
   `primer-glossary.md`** (the "[product] in 60 seconds" primer + the key-terms glossary): what the
   product is and what it does on every request, in plain language. A **first-class section, not a
   footnote** — customers still need teaching on the product. (Mode A only — the product is the thing
   being taught.) Render the product's **per-request control steps** as the `<ol class="controls">`
   **control strip** (see `references/html-companion.md` Notes) — the one place numbered `01/02…`
   markers belong, because every request really passes through them in order.
5. **Architecture: current → target** — **open by stating how the design realises the identity spine**
   (every leg, per the product's reference pack) — identity is the core of the design, not a feature
   listed later — then current state (diagram 1 + walkthrough) and target state (diagram 2 +
   walkthrough), and the case-study shape it mirrors. Weave in **which capability closes which gap**
   from current state, tying benefits to the design rather than a feature list. Place the **policy-decision
   diagram** (Step 4 #4) here or in §6 to make the allow/deny fork concrete. For a concrete design, the
   HTML companion may include a real product screenshot as a `<figure class="shot">` — reference the PNG
   **relatively** and **copy it into the account folder** next to the `.html` (from
   `profiles/<active>/knowledge/brand/product-screenshots/`, see that folder's `INDEX.md`; don't
   base64-embed); it illustrates the *proposed solution*, not the customer's systems.
6. **How it works — end to end** — the representative request walked through as **numbered steps**
   (diagram 3 sequence + walkthrough): caller → product → target → response, so a reader sees exactly
   what happens per call.
7. **What ships first — V1 / V2 / not building** — the V1/V2 cut from Step 2, rendered as the
   **phase cards** (`<div class="phases">` — two side-by-side comparison cards, V2 recessed; see
   `references/html-companion.md` Notes), with the explicit **"not building (V1)"** list kept as
   ordinary bullets below the cards. Frame it as a **draft for discussion** — no
   version-history/changelog, no rigid roadmap, just the current cut to align on.
8. **Talking points & FAQ** — 3–6 one-line benefit points and a short FAQ answering the objections this
   design invites. **Keep it light** — the full persuasion story is `build-deck`'s job, not the doc's.
9. **Further reading** — a few categorized reference links (product docs, the standards/protocols the
   design cites, the regulatory driver). Optional, but useful for a technical reader.

- **Assumptions & open questions (summary box)** — a short 3–5 bullet box closing Tier 1: the
  load-bearing assumptions and the top open questions, with a line that answers **may change the target
  architecture**. Full lists live in the appendix, so the overview leads with the solution, not caveats.

#### Tier 2 — Technical appendix

Open the appendix with a one-line banner: *"Technical detail — for the customer's architects; omit
from the exec/customer copy."* Then:

A1. **Assumptions** — bulleted; every place the design assumes something not yet confirmed.
A2. **Open questions & dependencies** — bulleted, grouped (**Customer to provide / Decisions to make /
    Vendor to confirm internally / Beta constraint**). Carry forward unresolved discovery items.
A3. **Component inventory** — table of what gets configured: 『the product's own configurable object
    types — entry points, proxies, credentials, policies, connections, secrets, whatever the
    product's reference pack names them』, from the product references. **The bridge to
    `gateway-runbook` — keep this heading and these component types (whatever the reference names
    them) verbatim; the runbook consumes it.**
A4. **Identity, policy & data flow** — mapped to the product's processing steps; what's verified,
    enforced, injected, logged. **Lead with the identity spine** — for each agent in scope, walk every
    spine leg (identity, authorization, policy, audit — mechanics per the product's reference pack).
    Then close with a **capability-coverage matrix** — one row per capability the design claims, tagged
    **Enforced / Simulated / Design-target** (wrap each status in a companion pill —
    `<span class="tag ok">Enforced</span>` / `<span class="tag sim">Simulated</span>` /
    `<span class="tag warn">Design-target</span>`), aligned to the reference demo's matrix so nothing
    overclaims (the reference pack's deck/design-claims section is the ground truth for **Enforced** vs
    **Design-target** on the current build). Also state which of the product's features the chosen
    pattern exercises vs leaves on the table (lead with the flagship, real differentiators).
A5. **Standards alignment** — the crosswalk table from
    `profiles/<active>/products/<product>/references/standards-crosswalk.md`: requirement → product
    control → framework control ID → evidence artifact → owner. Cite only the 2–3 frameworks this
    account uses.
A6. **Shared responsibility** — a three-column table (**Product does / Customer does / Out of scope →
    the safety product**) + an **honest-scoping** note. Apply the honesty rules in
    `standards-crosswalk.md`: state the active company's real certs accurately (per
    `profiles/<active>/knowledge/company.md`) as a trust signal, **never assert the company lacks a
    certification**, and don't self-undermine.
A7. **Trade-offs & alternatives considered** — for each material choice (managed vs self-hosted;
    policy-in-product vs app-code; build vs buy; phased vs full), honest pros/cons and the recommendation.
A8. **Internal appendix (omit from customer copy)** — persona mapping + codes (A5, persona #), ICP
    score, deal context. As a bulleted list, kept entirely out of the customer-facing body.

**Formatting:** use real bullet lists (blank line before the list) and short paragraphs — never run
assumptions, open questions, or the appendix together as a dense block. The Exec summary + Tier 1
should read comfortably on their own; Tier 2 can be denser.

### Mode B — bespoke

Structure (same three tiers; **never import gateway vocabulary** — see the guardrail):

**Executive summary (½ page).** The value in one sentence · the workflow pain in a line · the proposed
build in a line · the headline outcome · who it's for.

#### Tier 1 — Customer overview

1. **What we heard** — the workflow pain, the feature wishlist, the constraints.
2. **The problem & why now** — what the manual workflow costs today (time, error, risk), quantified
   where the numbers are real. Keep it to what the customer already feels.
3. **How this build works** — a one-paragraph plain-language summary of the proposed system, **plus who
   it's for & what they get** (the stakeholders and the concrete benefit each gets).
4. **Architecture: current → target** — current state (diagram 1, the manual workflow + walkthrough)
   and target state (diagram 2, the bespoke layers + walkthrough).
5. **How it works — end to end** — the layer-by-layer walkthrough in plain language; for each layer,
   the grounding data source or API named.
6. **What ships first — V1 / V2 / not building** — the V1 cut and the explicit **"not building (V1)"**
   list (mandatory and non-optional), framed as a **draft for discussion**.
7. **Talking points & FAQ** — a few one-line benefit points + a short FAQ. **Keep it light** — the
   deck is `build-deck`'s job.

- **Assumptions & open questions (summary box)** — a short 3–5 bullet box closing Tier 1; full detail
  in the appendix.

#### Tier 2 — Technical appendix

Banner: *"Technical detail — omit from the customer copy."* Then:

A1. **Assumptions** — every place the design assumes an API exists, a timeline is achievable, etc.
A2. **Open questions & dependencies** — what the customer must confirm before build starts.
A3. **Feature / feasibility assessment** — the full feasibility table from Step 2.
A4. **Tech choices** — the stack recommendation with brief rationale.
A5. **Trade-offs & alternatives considered** — honest pros/cons for each material choice.
A6. **Internal appendix (omit from customer copy)** — deal context, ICP, persona notes.

---

After assembling the draft, emit the gate marker on its own line:

⟦GATE:plan⟧

Do not finalise or save until the operator confirms, edits, or rejects the design above.

---

## Step 6 — Output

Save as **`solution-design-[company]-[YYYY-MM-DD].md`** in the account folder
`content/<active>/accounts/<account-slug>/` (see CLAUDE.md "Per-account outputs"; Mermaid embedded).

**Always also emit a self-contained HTML companion** (`solution-design-[company]-[YYYY-MM-DD].html`)
using `references/html-companion.md` — plain Markdown viewers don't render Mermaid, so the `.html`
guarantees the diagrams draw in any browser, and its styling renders the tiers as a polished,
readable document. The `.md` stays the source of truth.

**Two reading tiers, one file.** The saved doc carries the Exec summary + Tier 1 overview + Tier 2
appendix. For a **customer/exec copy**, offer to emit a trimmed version that drops the Tier 2 appendix
(and always the internal appendix) — the Exec summary + Tier 1 stand alone as the customer read. A
reusable, de-branded blank of this exact structure lives at `references/solution-overview-template.md`
for an SA who wants to draft one by hand.

Tell the colleague both files are saved (note the `.html` is the one to open for diagrams) and **paste
the target-state diagram source inline**. Then offer the relevant hand-offs:

After all prose, append `⟦FILE:…⟧` sentinels so the cockpit delivers both files automatically:

```
⟦FILE:/absolute/path/to/content/<active>/accounts/<account-slug>/solution-design-[company]-[YYYY-MM-DD].md⟧
⟦FILE:/absolute/path/to/content/<active>/accounts/<account-slug>/solution-design-[company]-[YYYY-MM-DD].html⟧
```

Use the real resolved absolute paths.
- **Both modes** — `solution-scope-check` for a 2-page buyer worksheet that simplifies this design and
  asks the questions to validate or reshape its scope before build.
- **Mode A** — `build-deck` for slides, the docx skill for a formal Word SAD, `gateway-runbook` for
  setup steps.
- **Mode B** — `build-deck` for a customer-facing deck.

## Guardrails

- **Ground every component in real capabilities.** Mode A: `profiles/<active>/knowledge/product.md` +
  product references — never draw a box for a feature the product doesn't have. Mode B: every layer box
  must have a named, real data source or API — never draw a box for a capability with no confirmed source.
- **Honesty rules (Mode A) are non-negotiable — accuracy first:** distinguish "product does / customer
  must do"; **state the active company's real certs accurately (per `profiles/<active>/knowledge/company.md`)
  and never assert the company lacks a certification** (treat genuinely-unconfirmed certs as "confirm
  internally"); the honest boundary is that the product produces *evidence* for the customer's compliance,
  not that the company is uncertified; mention beta status once where it matters; route content/model safety
  to the company's safety product.
- **Identity-first + honest enforcement tags (Mode A) — non-negotiable.** Centre the design on the
  product's identity spine (legs per its reference pack), and tag every claimed capability
  **Enforced / Simulated / Design-target** aligned to the reference demo's capability-coverage
  matrix. The reference pack's deck/design-claims section is the ground truth for what is
  **Enforced** vs **Design-target** on the current build — never present a design-target as
  live/green, and lead with the flagship, real differentiators it names. The product produces
  audit-ready *evidence*; never present it as itself certified against the frameworks it evidences.
- **Honesty rule (Mode B):** L-confidence capability or no confirmed data source → V2 or flagged risk. Never
  scope optimistically. "What we are NOT building" list is mandatory and non-optional.
- **Mode B: never import gateway vocabulary.** No "six gateway dimensions", no "5-step gateway flow", and
  no borrowing Mode A's product-specific component nouns for an unrelated bespoke build. All
  architecture synthesised from `references/bespoke-scaffold.md` and the functional inputs.
- **Gates are on by default.** Pass `--no-gates` only on explicit operator opt-out for fast internal runs.
  Never skip a gate on a customer-facing run.
- **Be objective about trade-offs.** Name the downside of the recommended path. A design with no stated
  trade-offs reads as a sales pitch, not advice.
- **Cite real control IDs (Mode A).** Only the 2–3 frameworks the account uses.
- **Write for the newcomer.** Don't assume the reader was in meeting 1 or knows product terms — primer +
  glossary up front (Mode A); plain-language summary up front (Mode B); internal shorthand only in the
  marked appendix.
- **Customer copy drops Tier 2.** The Exec summary + Tier 1 overview are the customer/exec read; the
  technical appendix (and always the internal appendix) is omitted from the customer copy. Lead with
  outcome and problem, not caveats — assumptions/open-questions get a short summary box in Tier 1, full
  detail in the appendix.
- **Product education is first-class (Mode A).** Surface the product primer from `primer-glossary.md`
  as its own "How [the product] works" section in Tier 1 — customers still need teaching. Don't bury it
  inside the architecture section.
- **Keep talking points & FAQ light.** A few benefit lines + the top objections in the doc; the full
  persuasion story is `build-deck`'s job. Don't turn the overview into a pitch.
- **Component inventory stays (Mode A).** Tier 2 keeps a **Component inventory** section (『the
  product's own configurable object types, per its reference pack』) under that heading —
  `gateway-runbook` consumes it.
- **Every diagram has a walkthrough.** No box-only diagrams.
- **Use the visual-component layer, keep it no-slop.** Reach for the designed blocks in
  `references/html-companion.md` where they earn their place — the **outcome band** (one, atop the Exec
  summary), the **control strip** (the product's sequential per-request steps), the **phase cards** (the
  V1/V2 cut), the **capability pills** (coverage matrix), and an optional **product screenshot**. They
  are plain inline HTML in the `.md` that degrades to readable text in a plain viewer. Bans: no gradient
  text, no colour side-stripe callouts, no big-number/small-label hero-metric cliché, no numbered
  `01/02…` markers except on the control strip (numbered scaffolding on non-sequential content is slop),
  one locked accent in the chrome (any brand *secondary* stays out — a sparing diagram data-accent at
  most), cards at the 12–14px radius scale.
- **Assumptions are explicit.** If the dossier is missing or a requirement is open, say so — don't
  silently design around a guess.
- **Respect beta reality (Mode A).** Closed beta, one gateway per project; managed service is the default
  fast path.
- **One idea per diagram.** Readability over completeness; split rather than crowd.
- **Match proof by shape, not vertical keyword (Mode A).**
- **Read-only.** No provisioning, no sends.
