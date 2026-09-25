
# Solution Design

Convert requirements into an architecture the people who must approve it can react to. The skill
runs in one of two modes — **Mode A (product-led)** or **Mode B (bespoke)** — sharing the same
backbone: intake → feasibility → architecture → diagrams → design doc. Human-in-the-loop gates
checkpoint each phase transition.

**The output is a customer-facing Solution Overview, not an internal SAD.** Every design ships as
three files (Step 6): the **customer overview** (an Executive summary, then nine numbered sections —
the one file the customer reads), the **technical appendix** (the SA rigor: assumptions, open
questions, component inventory, standards, trade-offs, service levels, maturity tags), and the
**internal notes** (deal context, never sent). Lead with the risk the reader wants to avoid; keep the
depth, but move it to the appendix. A reader gets the whole story from the overview and only opens
the appendix if they're the architect who has to build it.

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

**Write it for the reader who approves it.** Assume a competent reader who was **not** in the first
call and may not be technical — often the buyer's decision-makers and their regulator. Avoid
internal shorthand (ICP scores, persona codes) in the customer overview — deal context goes in the
internal file.

**Accurate on the page, caveats in the appendix.** The customer overview states what the design
delivers and, plainly, where its record or coverage stops. Maturity tags, service levels not yet
agreed, assumptions, open questions and trade-offs are real and must be written down — in the
appendix or the internal file, never on the customer page. Never overclaim to fill the space they
leave: a design that names its boundaries is more persuasive than one that oversells.

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
are **Design-target** — present design-targets as roadmap, never as live. The tags live in the
appendix's capability matrix (A4); the customer overview describes only Enforced capabilities as
delivered, and names anything later as plainly later ("later, a bank can run its own"), untagged.
The product produces audit-ready *evidence*; never present it as itself certified against the
frameworks it evidences.

**Read-only.** This skill designs and documents. It never provisions, sends, or contacts anyone.

**Where it sits in the SA chain:** `solution-discovery` → **`solution-design`** → `gateway-runbook`
(Mode A) / `build-deck`. Mode B hands off to `build-deck` for the customer deck.

---

## Step 1 — Intake

> **Resolve the mode first.** Check whether the active profile's `products[]` contains a product with
> `solution-architecture` in its capabilities **and** whether all Mode-A reference files resolve. If
> yes → **Mode A**. If no → **Mode B** (state the missing file or capability that caused the soft
> fail). Operator can override with `--mode-a` or `--bespoke`.

### Two questions before drafting (both modes)

Ask both in the intake message. In a headless/unattended run, take the stated default and record
the choice in the run header.

1. **Who reads this, and who presents it?** The customer's engineers, the customer's own customers,
   or a regulator — and is it presented by us alone or as a joint pitch with a partner (an
   integrator who builds on our product)? **Default:** the buyer's decision-makers and their
   regulator, jointly presented. The answer sets the vocabulary, which diagrams lead, and which
   reader types the FAQ and the reader pass (Step 6b) cover.
2. **What risk is the reader trying to avoid?** Regulatory, legal, data-security, reputational —
   name it. §2 of the overview **leads with that risk**, not with operational pain (volume,
   deadlines, effort). **Default:** the risk the discovery dossier names; if none, the risk of
   the decision the reader signs off, and flag it as assumed.

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
   the key-terms glossary, the "what we heard" recap (internal file only), and the why-now thread (with
   sourced market stats + branded infographic paths).
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
- **Reader, presenter and the risk to avoid** — the two questions above.

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
   - **Reader, presenter and the risk to avoid** — the two questions above.
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
are **not** a Mermaid diagram — render them as the **check pipeline** (`.gate-pipe`) HTML component
(Step 5), which is more scannable than a linear flowchart and avoids duplicating the
request-sequence diagram.

Keep each diagram readable — one idea per diagram, not everything on one canvas. **Every diagram ships
with a walkthrough:** a one-line "how to read this," then a per-node/per-component line in plain
language. The big target-state diagram especially must not stand alone. In the customer overview,
diagram 1 anchors §2 (The problem — for a simple channel, the `.cstate` component), diagram 2 anchors
§5 (Architecture), and diagram 3 anchors §6 (How it works — as the `.lanes` swimlane when more than
two parties act). Diagram 4 (policy decision) and diagram 5 (deployment) go in the appendix unless
the reader's risk turns on them. Apply the **Mermaid gotchas**
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

### Visual rules (both modes)

- **One visual per section** (a short Further reading is exempt; the Exec summary's outcome headline
  is typography, not a visual). A second visual in a section is a sign the section is two sections.
- **Animate only to reveal what the static version cannot** — an order that matters, a state that
  changes. Motion that only fades, dims or rises in is decoration: make it static.
- **Multi-party flows (more than two parties) are a swimlane** (`.lanes`): one column per party, a
  numbered dot per step, one connector through the dots in step order using only horizontal and
  vertical segments, an arrowhead into each next dot, and light alternating lane shading instead of
  per-lane guide lines (guide lines read as piercing the dots).
- **Coverage/traceability tables name the role** (`table.cov`): each cell is a 2–4 word label of
  what that part does for the requirement, never a bare dot. Mark a part only if the requirement
  fails without it — a part that merely *logs* a requirement it does not *meet* is not marked as
  meeting it. Group the columns under separate "technology" and "parties" headers. One amber
  "partial" state, reserved for where a real gap lies, with a note in the row saying what it is.
- **Images fit the column** (`max-width:100%`), and no text inside an exported image is smaller
  than the page's body text at that width. For simple flows prefer the native HTML components,
  sized to the page's type scale, over an exported diagram image; keep exported diagrams for true
  architecture.

---

## Step 5 — Assemble the design doc

**No metadata header.** The customer overview opens with its title,
`# <Account> × <Vendor> — Solution Overview` (this H1 is what marks the file as the customer
overview), a one-line italic meta line, and — directly under it — the **sibling pointer**, an
invisible comment naming its appendix:

```
<!-- appendix: solution-design-<account>-<YYYY-MM-DD>-appendix.md -->
```

The pointer is what lets `design_lint` count appendix-resident coverage (assumptions, open
questions, component inventory, service levels, deployment) as answered. There is **no** visible
"Tier 1" divider heading in the customer file.

### Budgets

- **One visual per section** (Step 4 visual rules); `references/html-companion.md` lists the
  components — reach for one before writing a fourth paragraph.
- **Customer overview ≤ ~2,000 words.** The appendix is a *separate file* (Step 6), never a longer
  scroll. A first draft over budget is cut, not reflowed.

### Plain language — limits, not a style note

- **No slogans.** Ban the "X, not Y" construction ("one use case, not two"; "a named authoriser, not
  just a verified agent") and slogan headings ("Clear about the boundary"). Say the fact.
- **No working-note phrases** on the customer page: "stated plainly", "the honest answer", "this
  design", "to be clear", "what we heard". They narrate the drafting, not the solution.
- **Everyday words.** "The other bank", not "counterparty"; "logged", not "governed exchange";
  "shows", not "surfaces". Outside §4 and §5, no undefined jargon; define each technical term where
  it is first used (or in the §1 Key terms). Count a repeated abstract noun — one used more than
  five times is doing a plain word's job.

### Claims — verified against a source, or off the customer page

- **A named regulation or framework is read, not remembered.** Fetch its primary document (the
  web-fetch MCP tools) and cite section and page for every requirement mapped to it (`small.cite`
  in `.req-groups`). Never characterise its legal status — rule, guidance, non-binding — from
  memory: quote how the document describes itself. A framework that says it is not regulatory
  guidance must not be called guidance.
- **Every "no X" claim is checked against how things work today.** "No record of what was sent" is
  false wherever an email trail exists; write what is actually missing (proof of who asked, proof
  of approval).
- **FAQ answers come only from stated requirements or the design.** An answer you inferred (who
  does which IT task, who supplies which fact, whether the record covers every case) goes to the
  internal file as an open question, not onto the customer page.

### Mode A — product-led

**Define the customer's own terms, not just the active company's.** A second reader won't know the
*account's* product names and acronyms either. §1 closes with a collapsible **Key terms** glossary
(`details.gloss`) defining each customer system/agent/acronym, the regulation or framework, and the
product terms the overview uses — one line each.

#### The customer overview — section order

**Executive summary (top of the doc, ≤150 words after the headline).** No diagrams, no jargon:

- **Headline** — the `.outcome` band: one line (`.o-after`) that names the problem the design
  solves, then a subline (`.o-sub`) that says how, and names the governing framework if there is one.
- Optionally the **trust strip** (`.trust-strip`) when the design turns on a request passing a
  short sequence of checks.
- **3–4 bullets, one or two sentences each:** **The problem** (the risk from intake question 2) ·
  **The solution** (who does what, in a line) · **The constraint** (the one limit that shaped the
  design) · **Who it's for**.

Then `---` and nine numbered sections:

1. **Requirements** — what the design must do, as `.req-groups` **grouped by driver**: the
   regulation it must meet, the framework it aligns with, ease of adoption (or whatever the drivers
   are — no more than four). Number R1…Rn **in group order** (renumber after any regroup). Each
   framework-derived requirement carries its citation (section + page) as `small.cite`. Close with
   the Key terms glossary.
2. **The problem** — lead with the risk the reader is trying to avoid (intake question 2), not with
   volume or effort. Diagram 1 / `.cstate` shows today's channel and what it lacks; then 2–4 bullets
   on what the design has to solve. Every "no X" claim checked (Claims, above). Quantify only where
   the numbers are real.
3. **The solution** — who does what, as `.who` cards: the customer, the integrator/partner (when
   jointly presented), and us. **Say explicitly who stays accountable** for the decision the
   regulator cares about — the design moves work, not accountability.
4. **How [the product] works** — the product primer from the product's `primer-glossary.md`, in
   plain language. A **first-class section, not a footnote** (Mode A only). The per-request steps
   are the `.gate-pipe`, each step tagged with the framework component it maps to; the steps walk
   the identity spine's legs in the order a request meets them.
5. **Architecture** — diagram 2 (target state) + walkthrough + `.suite-key`. Answer the one "why not
   the obvious alternative?" a reader will ask in two sentences. A real product screenshot may go
   here as a `<figure class="shot">` (from `profiles/<active>/knowledge/brand/product-screenshots/`,
   see that folder's `INDEX.md`, referenced relatively); it illustrates the *proposed solution*,
   not the customer's systems.
6. **How it works — end to end** — diagram 3: the representative request as numbered steps, a
   `.lanes` swimlane when more than two parties act, with `.ln-notes` for the fallback path and
   slow replies. Then state plainly **what is recorded and what is not** — this is where the
   customer page says where the record stops.
7. **Common questions** — FAQ accordions grouped by reader type (e.g. regulator, compliance,
   business owner, IT & security — from intake question 1). **At most two per reader type, each
   answer ≤ ~40 words**, answered only from stated requirements or the design.
8. **How each requirement is met** — `table.cov`: one row per requirement, one column per part,
   columns grouped under "technology" and "parties" headers, each cell a role label (Step 4 visual
   rules).
9. **Further reading** — a few categorized links: product docs, the primary documents of every
   regulation or framework the overview cites, the regulatory driver.

**Not on the customer page** — each has a home in the appendix or internal file, and moving it there
is the fix, not deleting it:

| Belongs elsewhere | Where |
|---|---|
| Maturity and enforcement tags, beta status | appendix A4 / A8 |
| Service levels or residency "not yet agreed" | appendix A9 (and the open question in A2) |
| Assumptions & open questions — any summary box | appendix A1 / A2 |
| The V1 / V2 / not-building cut; what the product does not do | appendix A2 (shipping board) |
| The "what we heard" discovery recap; talking points | internal file |
| Tier divider headings ("Tier 1 — …") | nowhere — the H1 marks the file |

#### The technical appendix (its own file)

Open the appendix with a one-line banner: *"Technical detail — for the customer's architects; omit
from the exec/customer copy."* This is where every caveat the customer page leaves out is written
down in full. Then:



A1. **Assumptions** — bulleted; every place the design assumes something not yet confirmed.
A2. **Open questions & dependencies** — bulleted, grouped (**Customer to provide / Decisions to make /
    Vendor to confirm internally / Beta constraint**). Carry forward unresolved discovery items.
    Close with the V1/V2 cut from Step 2 as the **shipping board** (`<div class="board">` — V1, V2
    recessed with `is-next`, "not building" dashed with `is-out`; see `references/html-companion.md`),
    framed as a **draft for discussion**.
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
A7. **Trade-offs & alternatives considered** — written as a **decision record** per material
    choice, four lines rather than a pros/cons blob: **Decision** · **Date** · **Alternatives** ·
    **What it cost us**. The last line earns the section — a trade-off with no named cost is a
    recommendation wearing a trade-off's heading. For each material choice (managed vs self-hosted;
    policy-in-product vs app-code; build vs buy; phased vs full), honest pros/cons and the recommendation.
A8. **Constraints** — what is fixed and not ours to choose, separately from A1's assumptions:
    systems that stay, the regulator and jurisdiction, the change window, licence or seat limits,
    data that may not leave a boundary, and any beta limitation of our own product. One line each,
    with **who owns it** — a constraint the customer can lift is a negotiation, and one we imposed
    is a roadmap item. An assumption is ours to be wrong about; a constraint never moves.
A9. **Quality requirements** — the numbers the design is accountable for: **availability** (a
    percentage over a stated window), **latency** (a percentile, never a mean — "average 200 ms" is
    satisfied by a system unusable one call in twenty), **throughput** (sustained, and the burst it
    must survive), **recovery** (RPO and RTO, separately), and **residency** where data may not
    cross a border. A figure, or "not yet agreed" and an A2 open question — never "fast", "highly
    available" or "scalable", each of which is a placeholder that reads as a commitment.
A10. **Deployment topology** — where each component runs and in whose tenancy: our side, their
    side, region, and every boundary a request or record crosses. Not optional: "where does it run"
    is the first question security asks and the last one a design usually answers.
A11. **Glossary** — every term that means something specific here, in one line. Include the ones
    both sides think they share. A reader who guesses a definition disagrees with the design
    without knowing it, and that surfaces at implementation rather than at review.
A12. **Internal appendix (omit from customer copy)** — persona mapping + codes (A5, persona #), ICP
    score, deal context, the "what we heard" discovery recap, talking points, and any FAQ answer
    that was inferred rather than stated. As a bulleted list, kept entirely out of the customer file.

**Formatting:** use real bullet lists (blank line before the list) and short paragraphs — never run
assumptions, open questions, or the appendix together as a dense block. The customer overview
should read comfortably on its own; the appendix can be denser.

### Mode B — bespoke

Same three files, the same Executive summary shape, the same plain-language limits and visual rules
(**never import gateway vocabulary** — see the guardrail).

#### The customer overview (Mode B)

1. **Requirements** — the jobs to be done and the constraints, stated as what the build must do
   (`.req-groups`), with a Key terms glossary.
2. **The problem** — the risk the reader is trying to avoid first, then what the manual workflow
   costs today (time, error), quantified where the numbers are real. Diagram 1 (the manual workflow).
3. **How this build works** — a one-paragraph plain-language summary of the proposed system, plus
   who does what (`.who`) and who stays accountable.
4. **Architecture** — diagram 2 (the bespoke layers + walkthrough).
5. **How it works — end to end** — the layer-by-layer walkthrough in plain language; for each layer,
   the grounding data source or API named. A swimlane when more than two parties act.
6. **What ships first — V1 / V2 / not building** — the V1 cut and the explicit **"not building (V1)"**
   list (mandatory and non-optional), framed as a **draft for discussion**. Mode B keeps this on the
   customer page: for a bespoke build the scope cut is what the customer is buying, not a caveat
   about a product.
7. **Common questions** — grouped by reader type, at most two per type, each answer ≤ ~40 words.
8. **How each requirement is met** — `table.cov`.

Assumptions, open questions, flagged risks and talking points go to the appendix / internal file,
as in Mode A.

#### The technical appendix (Mode B)

Banner: *"Technical detail — omit from the customer copy."* Then:

A1. **Assumptions** — every place the design assumes an API exists, a timeline is achievable, etc.
A2. **Open questions & dependencies** — what the customer must confirm before build starts.
A3. **Feature / feasibility assessment** — the full feasibility table from Step 2.
A4. **Tech choices** — the stack recommendation with brief rationale.
A5. **Trade-offs & alternatives considered** — honest pros/cons for each material choice.
A6. **Constraints** — fixed and not ours to choose, separately from A1's assumptions, each with
    who could lift it.
A7. **Quality requirements** — availability, latency at a percentile, throughput, RPO and RTO
    separately, residency. A figure or "not yet agreed"; never an adjective.
A8. **Deployment topology** — where each component runs, in whose tenancy, and every boundary
    crossed.
A9. **Glossary** — every term that means something specific here, in one line.
A10. **Internal appendix (omit from customer copy)** — deal context, ICP, persona notes.

---

After assembling the draft, emit the gate marker on its own line:

⟦GATE:plan⟧

Do not finalise or save until the operator confirms, edits, or rejects the design above.

---

## Step 6 — Output

Save as **`solution-design-[company]-[YYYY-MM-DD].md`** in the account folder
`content/<active>/accounts/<account-slug>/` (see CLAUDE.md "Per-account outputs"; Mermaid embedded).

**Always also emit a self-contained HTML companion** (`solution-design-[company]-[YYYY-MM-DD].html`)
using `references/html-companion.md` — it guarantees the diagrams and components draw in any
browser, as a polished, readable document. **The `.md` is the single source.** Create the `.html`
once from the template, then after **every** `.md` edit re-render it:

```bash
uv run python -m gtm_core.design_render content/<active>/accounts/<slug>/solution-design-[company]-[YYYY-MM-DD].md
```

It re-embeds the `.md` into the companion and inlines relative `.svg`/`.png` images. Never hand-edit
the markdown embedded in the `.html` — the next render overwrites it, and until then the two
disagree.

**Three audiences, three files — not one scroll.** One document serving an exec, an architect and
our own deal notes serves none of them. Emit the split by default; do not offer it as an extra:

| File | Audience | Contains |
|---|---|---|
| `solution-design-[company]-[date].md` | the customer's decision-makers (and their regulator) | Exec summary + §1–9, the sibling pointer to the appendix |
| `solution-design-[company]-[date]-appendix.md` | the customer's architects | A1…An, and every caveat the overview leaves out |
| `solution-design-[company]-[date]-internal.md` | **us only — never sent** | the "omit from customer copy" material (A12 in Mode A, A10 in Mode B) |

Each gets its own HTML companion the same way. Two hard rules:

- **A section whose own heading says "omit from customer copy" must not be in the customer file.**
  Writing that phrase means you are writing the internal file — put it there.
- **Never ship a version log.** Solution designs revise **silently**: no changelog, no "v2.3 —
  what changed" section, no revision table. A customer never saw v1, so narrating the path to v2.5
  is pure scroll cost. Version history lives in git.

A reusable, de-branded blank of this structure lives at `references/solution-overview-template.md`
for an SA who wants to draft one by hand.

Tell the colleague both files are saved (note the `.html` is the one to open for diagrams) and **paste
the target-state diagram source inline**. Then offer the relevant hand-offs:

After all prose, only when running under the Telegram cockpit, append `⟦FILE:…⟧` sentinels so the cockpit delivers both files automatically:

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

## Step 6b — Definition of Done (two gates; neither of them is "it reads well")

A design is not done because it saved. Run both, in order, and report the results.

**1 · Lint — deterministic, runs everywhere, including headless.**

```
uv run python -m gtm_core.design_lint \
    content/<active>/accounts/<slug>/solution-design-[company]-[YYYY-MM-DD].md \
    content/<active>/accounts/<slug>/solution-design-[company]-[YYYY-MM-DD]-appendix.md \
    content/<active>/accounts/<slug>/solution-design-[company]-[YYYY-MM-DD]-internal.md
```

Pass all three cuts — the linter knows the split and holds each file to what that file is for
(an appendix is not missing the executive summary it never had; the sibling pointer tells it
where the overview's appendix-resident coverage lives). Then confirm each HTML companion matches
its `.md`:

```bash
uv run python -m gtm_core.design_render content/<active>/accounts/<slug>/solution-design-[company]-[YYYY-MM-DD].md --check
```

**SD1** the three-tier read is present and in order · **SD2** every coverage dimension has a
section that answers it · **SD3** service levels are stated here, not only in the commercial
proposal · **SD4** a glossary exists · **SD12** a stated count matches the list it introduces ·
**SD13** every diagram ships with a walkthrough.

**Three severities, and the third one is not for you to clear.** Errors block delivery. Warnings do
not, but they are graded — read them, then either fix or say why not; a rule genuinely wrong about
one document can be suppressed with `<!-- lint-ok SD4: reason -->` in the section it fires on (a
document-level finding may be suppressed from anywhere in the file), and naming the tier **and the
reason** is the price of the exemption — a bare suppression is not one.

Judgement calls, printed under their own heading and marked `~`, are the claim checks: **SD6** a
capability matrix row that leaves its own Status column blank · **SD7** a design-target written in
the present tense while the matrix labels it ahead of the build · **SD8** a claim that writes to
something the design's own Constraints section froze · **SD9** one action attributed both to the
operator and to the principal.

Do not suppress these and do not try: there is no `lint-ok SD6`, `--strict` does not promote them,
and they never block. **Report them to the operator verbatim, as their own list, and let them
decide.** All four reason about what the design claims rather than how it is shaped, and on a claim
the operator is the more current source — a capability that was a design target when these patterns
were written may have shipped since, and a beta constraint may have lifted. A linter comment inside
a document the customer reads, recording that our tooling is out of date, is the wrong artefact;
the report is where that disagreement belongs.

The run also prints a **coverage line** — how many of the twelve dimensions the document answers,
and which it does not. Nothing there is necessarily wrong. It prints every time because coverage is
the one thing a reader cannot see by scrolling: a design reads complete right up until the
architect asks the question it never answered. `--dimensions` prints the whole taxonomy with the
question each one asks.

**2 · A read-through against the questions a demanding architect asks** — whose identity is on each
action, whose system of record holds each output, what is enforced (tagged in the appendix, never
shown as live on the customer page) versus a design target, and whether any body claim contradicts
a constraint you documented. Judgement, not mechanism; the linter cannot ask any of them.

**Reader pass.** Read the customer overview once as each reader type from intake question 1 (e.g.
regulator, business owner, compliance, IT & security) and list the questions each would ask. Answer
each in the overview from the requirements or the design, or route it to the appendix / internal
file — never leave it for the meeting.

**Sweep after any change of design position.** A routing, ownership or scope decision appears in
several places at once. When one changes, grep the `.md`, the diagrams' source text (Mermaid and
SVG labels), the swimlane, the coverage table and the FAQ for every place the old claim appears,
update all of them, then re-render. A change applied in one place is a contradiction the reader
finds before you do.

## Guardrails

- **Ground every component in real capabilities.** Mode A: `profiles/<active>/knowledge/product.md` +
  product references — never draw a box for a feature the product doesn't have. Mode B: every layer box
  must have a named, real data source or API — never draw a box for a capability with no confirmed source.
- **Honesty rules (Mode A) are non-negotiable — accuracy first:** distinguish "product does / customer
  must do"; **state the active company's real certs accurately (per `profiles/<active>/knowledge/company.md`)
  and never assert the company lacks a certification** (treat genuinely-unconfirmed certs as "confirm
  internally"); the honest boundary is that the product produces *evidence* for the customer's compliance,
  not that the company is uncertified; state beta status once, in the appendix (A8); route content/model
  safety to the company's safety product. These rules decide what is *true*; the customer/internal
  split (below) decides *which file* it goes in.
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
- **Be objective about trade-offs.** Name the downside of the recommended path (appendix A7). A design
  with no stated trade-offs reads as a sales pitch, not advice.
- **Cite real control IDs (Mode A).** Only the 2–3 frameworks the account uses.
- **Write for the reader from intake question 1.** Don't assume they were in meeting 1 or know product
  terms — Key terms in §1, product primer in §4 (Mode A); plain-language summary up front (Mode B);
  internal shorthand only in the internal file.
- **Customer / internal split.** The customer overview carries the Exec summary and §1–9 and nothing
  else — no discovery recap, talking points, maturity tags, unagreed service levels, assumptions box or
  tier dividers (Step 5 "Not on the customer page"). Those go, in full, to the appendix or the internal
  file. Lead with the reader's risk, not caveats.
- **Product education is first-class (Mode A).** Surface the product primer from `primer-glossary.md`
  as its own "How [the product] works" section (§4) — customers still need teaching. Don't bury it
  inside the architecture section.
- **FAQ from the record, not from inference.** At most two questions per reader type, each answer
  ≤ ~40 words, drawn from the stated requirements or the design; the persuasion story is
  `build-deck`'s job.
- **Plain language has limits** (Step 5): ≤ ~2,000 words, no "X, not Y" slogans, no working-note
  phrases, every technical term defined where first used.
- **Component inventory stays (Mode A).** The appendix keeps a **Component inventory** section (『the
  product's own configurable object types, per its reference pack』) under that heading —
  `gateway-runbook` consumes it.
- **Every diagram has a walkthrough.** No box-only diagrams.
- **Use the visual-component layer, keep it no-slop.** Reach for the designed blocks in
  `references/html-companion.md`, one per section (Step 4 visual rules) — on the customer page the
  **outcome headline**, **trust strip**, **requirement groups**, **current-state flow**,
  **who-does-what cards**, **check pipeline**, **swimlane**, **coverage table** and **suite key**; in
  the appendix the **shipping board**, **status board** and **capability pills**. They are plain
  inline HTML in the `.md` that degrades to readable text in a plain viewer. Bans: no gradient
  text, no colour side-stripe callouts, no big-number/small-label hero-metric cliché, no numbered
  `01/02…` markers except where the order is real (the check pipeline, the trust strip, swimlane
  steps), one locked accent in the chrome (owner colours only where they mean an owner), cards at
  the 12–14px radius scale.
- **Assumptions are explicit.** If the dossier is missing or a requirement is open, say so — in the
  appendix or the internal file — and don't silently design around a guess.
- **Respect beta reality (Mode A).** Closed beta, one gateway per project; managed service is the default
  fast path.
- **One idea per diagram.** Readability over completeness; split rather than crowd.
- **Match proof by shape, not vertical keyword (Mode A).**
- **Read-only.** No provisioning, no sends.
