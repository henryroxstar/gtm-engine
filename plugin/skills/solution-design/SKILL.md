---
name: solution-design
description: >-
  Turn a use case and requirements into a solution architecture — either mapped onto the
  active company's flagship product (Mode A, product-led) or synthesised as a bespoke custom
  build (Mode B). Produces: requirements confirmation, feasibility assessment with V1/V2 cut,
  architecture, Mermaid diagrams, and a full design doc. Trigger when the user says "design
  the solution for [company]", "draft an architecture for [company]", "build a solution design
  / SAD for [company]", "create architecture diagrams for [company]", "map the gateway
  architecture for [use case]", "current and target state for [company]", or "bespoke build
  plan for [company]". Consumes a `solution-discovery` dossier when present. Read-only;
  produces files in `content/<active>/accounts/<account-slug>/`, and can hand off to
  `build-deck` (slides) or the docx skill (Word).
metadata:
  version: "0.4.0"
  phase: "5"
  capability_tier: core
  requires_capability: [solution-architecture]
---

# Solution Design

Convert requirements into an architecture the customer's engineers can react to. The skill runs in
one of two modes — **Mode A (product-led)** or **Mode B (bespoke)** — sharing the same backbone:
intake → feasibility → architecture → diagrams → design doc. Human-in-the-loop gates checkpoint each
phase transition.

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
first call. Open with a "what we heard" recap; avoid internal shorthand (ICP scores, persona codes)
in the customer-facing body — put deal context in a clearly-marked internal appendix.

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
tailored to the account. Always produce these, and lead with whichever fits the audience:

1. **Context / current-state** — their existing stack today, and the gap (no agent identity, no
   runtime policy, no cross-boundary trust). Shows what's broken.
2. **Target-state architecture** — the product dropped in as the intercepting proxy, showing the
   product's processing flow and the components wired to their real targets.
3. **Request sequence** — a representative request walked through the processing steps end to end
   (caller → product → target → response), so engineers see exactly what happens per call.
4. **Deployment topology** (optional) — where the product runs (managed vs self-hosted), networking,
   and federation hops if the federation pattern applies.

Keep each diagram readable — one idea per diagram, not everything on one canvas. **Every diagram ships
with a walkthrough:** a one-line "how to read this," then a per-node/per-component line in plain
language. The big target-state diagram especially must not stand alone. Apply the **Mermaid gotchas**
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

Structure the output (customer-ready ordering). **Assumptions and open questions come BEFORE the
solution** — they frame it and may change it:

1. **What we heard** — the discovery recap (use case, pains, why-now), **plus a short "systems & agents
   in scope" list defining the customer's own terms/products/agents** in one line each.
2. **Who this is for & what they get** — name the stakeholders (in plain terms, not internal persona
   codes) and the concrete benefit each gets (e.g. counsel → provable audit chain; platform → drop-in,
   no rewrite; business → ship with governance built in). Links the design to the envisioned value.
3. **Assumptions** — bulleted; every place the design assumes something not yet confirmed. Lead with a
   line that these shape the design and may shift it if wrong.
4. **Open questions & dependencies** — bulleted, grouped (**Customer to provide / Decisions to make /
   Vendor to confirm internally / Beta constraint**). State plainly that answers **may change the
   target architecture below**. Carry forward unresolved discovery items.
5. **Scope — V1 / V2 / what we are NOT building** — the V1/V2 cut from Step 2: concise bullets for
   what ships first, what's deferred to V2, and an explicit **"not building (V1)"** list. Frame it as a
   **draft for discussion** — no version-history/changelog, no rigid roadmap, just the current cut to
   align on.
6. **Current state** — their stack and the specific **pain**/gap the design closes (diagram 1 +
   walkthrough). Highlight what's broken today: no agent identity, no runtime policy, no cross-boundary
   trust.
7. **Target architecture** — **open by stating how the design realises the identity spine** (every
   leg, per the product's reference pack) — identity is the core of the design, not a
   feature listed later — then the design itself (diagram 2 + walkthrough), the processing flow
   (diagram 3 + walkthrough), and the case-study shape it mirrors. **The product explainer lives here
   now:** weave in what the product does and the concrete **benefits** as the design shows where each
   capability closes the gap from Current state. For a concrete design, the HTML companion may embed a real
   product screenshot from `profiles/<active>/knowledge/brand/product-screenshots/` (e.g. the Surface canvas
   or dashboard — see that folder's `INDEX.md`); it illustrates the *proposed solution*, not the customer's systems.
8. **Component inventory** — table of what gets configured: the product's components (from the product
   references). The bridge to `gateway-runbook`.
9. **Identity, policy & data flow** — mapped to the product's processing steps; what's verified,
    enforced, injected, logged. **Lead this section with the identity spine** — for each agent in
    scope, walk every spine leg (identity, authorization, policy, audit — mechanics per the
    product's reference pack). Then close with a
    **capability-coverage matrix** — one row per capability the design claims, tagged **Enforced /
    Simulated / Design-target**, aligned to the reference demo's matrix so nothing overclaims (the
    reference pack's deck/design-claims section lists which capabilities are **Enforced** vs
    **Design-target** on the current build). Also state which of the product's features the chosen
    pattern exercises vs leaves on the table (lead with the flagship, real differentiators from
    that section).
10. **Standards alignment** — the crosswalk table from
    `profiles/<active>/products/<product>/references/standards-crosswalk.md`: requirement → product control →
    framework control ID → evidence artifact → owner. Cite only the 2–3 frameworks this account uses.
11. **Shared responsibility** — a three-column table (**Product does / Customer does / Out of scope →
    the safety product**) + an **honest-scoping** note. Apply the honesty rules in `standards-crosswalk.md`:
    state the active company's real certs accurately (per `profiles/<active>/knowledge/company.md`) as a trust
    signal, **never assert the company lacks a certification**, and don't self-undermine.
12. **Trade-offs & alternatives considered** — for each material choice (managed vs self-hosted;
    policy-in-product vs app-code; build vs buy; phased vs full), honest pros/cons and the recommendation.
13. **Internal appendix (clearly marked, omit from customer copy)** — persona mapping + codes (A5,
    persona #), ICP score, deal context. As a bulleted list, kept out of the customer-facing body.

**Formatting:** use real bullet lists (blank line before the list) and short paragraphs — never run
assumptions, open questions, or the appendix together as a dense block.

### Mode B — bespoke

Structure (same discipline — assumptions before solution):

1. **What we heard** — the workflow pain, the feature wishlist, the constraints.
2. **How this build works** — one-paragraph plain-language summary of the proposed system.
3. **Assumptions** — every place the design assumes an API exists, a timeline is achievable, etc.
4. **Open questions & dependencies** — what the customer must confirm before build starts.
5. **Feature / feasibility assessment** — the full feasibility table from Step 2.
6. **V1 scope** — what ships first and why. Frame it as a **draft for discussion** — no version-history,
   just the current cut to align on.
7. **What we are NOT building (V1)** — the explicit exclusion list (mandatory and non-optional). Same
   draft-for-discussion framing.
8. **Architecture** — current state (diagram 1 + walkthrough) and target state (diagram 2 + walkthrough).
9. **How it works** — layer-by-layer walkthrough in plain language; for each layer, the grounding data
   source or API named.
10. **Tech choices** — the stack recommendation with brief rationale.
11. **Trade-offs & alternatives considered** — honest pros/cons for each material choice.
12. **Internal appendix (clearly marked)** — deal context, ICP, persona notes.

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
guarantees the diagrams draw in any browser. The `.md` stays the source of truth.

Tell the colleague both files are saved (note the `.html` is the one to open for diagrams) and **paste
the target-state diagram source inline**. Then offer the relevant hand-offs:

After all prose, append `⟦FILE:…⟧` sentinels so the cockpit delivers both files automatically:

```
⟦FILE:/absolute/path/to/content/<active>/accounts/<account-slug>/solution-design-[company]-[YYYY-MM-DD].md⟧
⟦FILE:/absolute/path/to/content/<active>/accounts/<account-slug>/solution-design-[company]-[YYYY-MM-DD].html⟧
```

Use the real resolved absolute paths.
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
- **Mode B: never import gateway vocabulary.** No "six gateway dimensions", no "5-step gateway flow", no
  component names (Surfaces/Proxies/Credentials/Policies/Connections). All architecture synthesised from
  `references/bespoke-scaffold.md` and the functional inputs.
- **Gates are on by default.** Pass `--no-gates` only on explicit operator opt-out for fast internal runs.
  Never skip a gate on a customer-facing run.
- **Be objective about trade-offs.** Name the downside of the recommended path. A design with no stated
  trade-offs reads as a sales pitch, not advice.
- **Cite real control IDs (Mode A).** Only the 2–3 frameworks the account uses.
- **Write for the newcomer.** Don't assume the reader was in meeting 1 or knows product terms — primer +
  glossary up front (Mode A); plain-language summary up front (Mode B); internal shorthand only in the
  marked appendix.
- **Every diagram has a walkthrough.** No box-only diagrams.
- **Assumptions are explicit.** If the dossier is missing or a requirement is open, say so — don't
  silently design around a guess.
- **Respect beta reality (Mode A).** Closed beta, one gateway per project; managed service is the default
  fast path.
- **One idea per diagram.** Readability over completeness; split rather than crowd.
- **Match proof by shape, not vertical keyword (Mode A).**
- **Read-only.** No provisioning, no sends.
