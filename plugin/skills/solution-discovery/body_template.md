
# Solution Discovery

An **internal technical research brief for the pre-sales / solution architect — NOT a customer-facing
deliverable.** It is the SA's own pre-deep-dive research on a **new customer**: how their agents/stack
work today, where the integration & identity gaps are, what to ask on the call, and what is still
unknown. It produces a **requirements question bank where each question is tied to the design decision
it unlocks** — so the deep-dive surfaces what `solution-design` needs, not generic curiosity.

It pairs with `account-dossier` (business/buyer context — link to it, never restate it) and feeds
`solution-design`.

In **Mode B** (no product to map the use case onto), the skill profiles the prospect's **manual
workflow** instead of a technical stack, and tags the agent-shaped loop (the sub-sequence that
repeats, is rules-based, has data available, and is bounded enough to automate).

**Read-only.** This skill researches and prepares. It never sends, posts, or contacts anyone.

**Where it sits in the SA chain:** `call-prep` / `deck-research` → **`solution-discovery`** →
`solution-design` → `gateway-runbook`.

> Resolve the **active profile** (the agent provides it; everything loads from `profiles/<active>/`, never `plugin/`).
> This skill is **product-bound** (`requires_capability: [technical-discovery]`) — it runs only when the active
> company has a product providing the `technical-discovery` capability. Resolve that product from `PROFILE.md` →
> `products[]` (its `slug`, `name`, `reference_dir`); load its specifics from `profiles/<active>/products/<product>/`.
> Use the product's real name throughout — never a hardcoded one. If no product with `technical-discovery` resolves,
> shift to **Mode B** (workflow profiling) — Steps 3–5 have Mode B branches.

---

## Step 1 — Load context

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `target_markets`,
   `icp_weighting`, `monthly_tool_budget_usd`, `tools_metered`, `output_folder`, `language`. Respect
   language throughout. If no PROFILE, run `setup` first (or ask the 3 essentials: markets, segment
   focus, budget). Resolve the product from `products[]` by the `technical-discovery` capability —
   use its real `name` throughout. If no such product resolves → **Mode B** for this run.
2. **`profiles/<active>/products/<product>/references/stack-signals.md`** (Mode A only) — what to look
   for and where, and how each stack signal maps to a product integration decision.
3. **`profiles/<active>/products/<product>/references/discovery-question-bank.md`** (Mode A only) — the
   seed question bank, grouped by the product's design dimensions, each question pre-tagged with the
   decision it unlocks.
> **Knowledge resolution (product-aware).** Wherever this skill loads a per-product knowledge file —
> `icp-personas.md` or `market-scan-config.md` — resolve its path with
> `python -m gtm_core.resolve_knowledge <file> --profile <active> [--product <slug>]` and read whatever
> path it prints, instead of opening `knowledge/<file>` directly. The helper returns the product-level
> file (`products/<slug>/<file>`) when present and falls back to the profile-level `knowledge/<file>`
> otherwise. Pass `--product` when the run is bound to one product (the lead `default_product` from
> PROFILE.md, or a product the operator named); omit it for profile-wide work — a profile that keeps one
> shared knowledge pack always falls back to the profile level, so nothing changes for it.

4. **Knowledge pack** — `profiles/<active>/knowledge/product.md` (the product's processing flow, features,
   themes, deployment options, competitive set, seed discovery questions),
   `profiles/<active>/knowledge/icp-personas.md`
   (persona #4 *Developers / Builders — AI/Platform Engineers, SAs* is the usual deep-dive audience;
   qualification criteria; why-now triggers), `profiles/<active>/knowledge/company.md`
   (positioning vs hyperscaler).
5. **Reuse prior work (do NOT re-research from zero).** Look in the account folder
   `content/<active>/accounts/<account-slug>/` for a fresh
   (≤30 days) `call-prep-*[company]*.md`, `deck-research-*[company]*.md`, or `prospects-*` /
   `outreach-*[company]*.md`. **Also check any deck folder** for this account —
   `<deck-workspace>/decks/*[company]*/inputs/` often holds `recent-news-factoids.md` and `outline.md`
   with sourced signals even after the dossier markdown has been cleaned up. If found, load it: keep
   the account snapshot, ICP score, persona detail, and why-now signal. Only research the **technical
   gaps** this skill needs.

---

## Step 2 — Gather inputs

Ask in **one short message** (skip anything already provided or in a prior file):

- **Company / account** (required).
- **Use case** (required) — the high-level use case agreed on the first call (e.g. "expose their
  booking API to partner agents", "govern internal multi-agent workflow", "cross-org agent exchange").
  This skill assumes the use case is roughly known; it's surfacing the *requirements underneath it*.
- **Technical attendees** — names + titles/roles of who's on the deep-dive (Platform/Infra lead,
  CISO/security architect, lead engineer, Head of AI). Map to personas in `icp-personas.md`.
- **What's already known** — anything from the first call: stated pain, incumbent tools, constraints,
  deployment preference, timeline.
- **Current state user journey** — the jobs-to-be-done (what manual process is being automated, step by step),
  the feature wishlist (all imagined capabilities, unconstrained), and an initial V1/V2 intuition
  (what's the minimum useful version?). Always gather this — it feeds `solution-design` Step 1 and in
  Mode B it is the primary input. Skip if already captured in a prior call-prep or dossier.
- **Depth** — `standard` (free web sweep, default) or `deep` (opt-in metered crawl within PROFILE
  budget; only if `tools_metered` allows).

---

## Step 3 — Profile the technical stack (read-only sweep)

### Mode A — product-led

Run a focused, free web sweep (search + browser). Use metered tools only on explicit `deep` opt-in.
Work through `profiles/<active>/products/<product>/references/stack-signals.md`; gather what's *publicly
evidenced* and mark the rest as **unknown (ask on the call)** — never invent stack facts. Capture:

1. **Agent maturity** — building / piloting / production? Evidence: eng blog, conference talks,
   careers page, product changelog.
2. **Agent frameworks & protocols** — LangGraph, CrewAI, AutoGen, LlamaIndex; MCP, A2A, AP2/UCP.
   Strongest signal is job posts ("hiring an agent platform engineer, MCP experience").
3. **Cloud & runtime** — AWS / Azure / GCP / multi-cloud (and therefore which hyperscaler agent
   offering is the incumbent threat: Bedrock AgentCore / Azure AI Foundry / Vertex AI Agents).
4. **Identity & auth stack** — Okta / Auth0 / Ping / Entra / homegrown; OAuth/OIDC, JWT, mTLS, SSO.
   This drives the inbound-auth and credential-delegation design.
5. **API & gateway layer** — Kong / Apigee / Zuplo / AWS API GW; REST vs GraphQL; existing OpenAPI
   specs (candidates for REST→MCP hosting).
6. **Data & tool surface** — the actual services/APIs agents would call (booking, records, payments).
7. **Observability & compliance** — Datadog / Arize / Langfuse / OpenTelemetry; SOC 2 / ISO / HIPAA /
   EU AI Act exposure inferred from HQ jurisdiction + industry.

**Surface the identity-first picture (the differentiated spine).** Beyond the seven stack areas, note
what's publicly evidenced (and mark the rest *unknown (ask)*) on the questions the gateway's identity
story turns on — these become must-ask questions in Step 5:

- **Per-agent identity vs shared secret** — does each agent have its own identity today, or do
  multiple agents share one `app_id` / API key / service account? (Shared secret → no per-agent
  attribution → the gateway's identity-at-birth DID is the fix.)
- **Where credentials / keys live** — does the agent hold the long-lived credential to the downstream
  system, or is it injected at call time? (Agent-held key → candidate for credential injection +
  scoped W3C-VC authorization.)
- **Least privilege / forbidden actions** — which actions must a given agent NEVER take even if it
  tries (the sensitive/destructive tool)? Is there ≥1 sub-agent that should be denied it?
- **Per-merchant / per-tenant boundary** — is the workload multi-tenant? Where is the boundary that
  one tenant's agent must not cross into another's data or actions?
- **Compliance actor-attribution (audit)** — can they prove *which agent* and *which human operator*
  took a specific action, end to end? Who asks for that evidence (an enterprise customer's security
  review, an auditor, a regulator)?

Stop once the integration-relevant picture is clear. This is a profile, not a full prospect run.

### Flag the best-practice frameworks this account will be measured against
From jurisdiction + industry + audience, name the 2–3 frameworks the design should cite (so
`solution-design` pulls the right `profiles/<active>/knowledge/guidance/*-gateway-alignment.md` packs):
- **US / baseline → NIST** (AI RMF + SP 800-53). **EU → EU AI Act / ENISA.** **APAC/Singapore → IMDA-PDPC MGF.**
- **Technical/security audience → OWASP Agentic Top 10** and/or **AIRQ** (benchmark) and/or **CSA MAESTRO** (layer threat model).
Also capture the account's **why-now** drivers (regulatory dates, incidents) and any cert claims (e.g.
ISO 42001) — noting that the product produces *evidence underneath* such certs, it isn't the cert.

### Mode B — bespoke (when no product references resolve)

Instead of profiling a technical stack for product fit, map the prospect's **manual workflow** step by
step. For each step:

1. What does the human do? (the action)
2. What tool or platform do they use? (the interface)
3. What data does this step consume or produce? (the data source)
4. Is this step agent-shaped? Tag with: **Repetitive** (runs repeatedly) · **Rules-based** (clear
   inputs → deterministic output) · **Bounded** (scoped domain, not open-ended) · **Data available**
   (the data exists and is reachable) · **Human gate** (requires human judgement at some point).

Identify the **core agent-loop**: the sub-sequence of steps where all five tags are Y, or at least
three are Y with the remaining as design choices (e.g. a human gate becomes an approval node).

Note the **data sources** each step consumes, even informal ones (a spreadsheet, a manual check of
multiple tabs, a mental model of market pricing). These become the Intelligence/Data sources layer in
`solution-design` Mode B.

Stop once the workflow is mapped and the agent-shaped loop is identified.

---

## Step 4 — Map signals to the integration surface

### Mode A — product-led

For each confirmed signal, state the **design implication** (use the mapping table in
`profiles/<active>/products/<product>/references/stack-signals.md`). Example: "Uses Okta + OIDC → inbound
auth via a JWT Verification Strategy (Expected Issuer + JWKS); no new IdP needed." Produce a short
**integration-surface map**: where the product sits relative to their existing stack, what it
intercepts, and what it leaves untouched (the "drop it in, don't rewrite" story for persona #4).

### Mode B — bespoke

No product integration surface to map. Instead, identify the **agent-loop entry and exit points**:
- **Entry** — what triggers the agent? (a message, a schedule, a threshold, an event)
- **Exit** — what does the agent produce? (an action, an update, a notification, a structured output)
- **Boundaries** — what does the agent NOT touch? (what the human still decides, what's out of scope)

This entry/exit/boundary map feeds `solution-design` Mode B's architecture step.

---

## Step 5 — Build the requirements question bank

### Mode A — product-led

This is the core deliverable. Start from
`profiles/<active>/products/<product>/references/discovery-question-bank.md` and **tailor** to
their use case, stack, and attendees. Organize by the product's **design dimensions** (loaded from
the discovery question bank):

For **each question**, show three things: the question (in their language), the **decision it unlocks**,
and the **`solution-design` slot it fills**. Prioritise: tag each as **must-ask** (blocks the design)
or **nice-to-have**. Keep the must-ask list to ~8–12 — enough to fully scope, few enough to actually
get through in one call.

**Lead the must-ask list with the identity-first cluster** — these scope the product's
differentiated security spine (see its reference pack) and almost always block the
design:

- **Per-agent identity vs shared secret** — "Does each agent have its own identity, or do they share
  an `app_id` / key today?" → unlocks identity-at-birth (gateway-issued DID per agent) vs a shared
  service account; fills the *Identity, policy & data flow* + *Component inventory* slots.
- **Where the credential lives** — "Does the agent hold the long-lived key to your downstream API, or
  is it injected per call?" → unlocks credential injection + scoped W3C-VC authorization design.
- **Forbidden actions / least privilege** — "Which actions must this agent NEVER take even if it
  tries — and is there a sub-agent that should be denied the sensitive tool?" → unlocks the
  least-privilege policy and the ≥2-agent design.
- **Per-tenant / per-merchant boundary** — "Is this multi-tenant? Where must one tenant's agent not
  cross into another's data?" → unlocks the policy + isolation design.
- **Actor-attribution for audit** — "Can you prove which agent AND which human operator took a given
  action — and who asks for that evidence?" → unlocks the tamper-evident audit + caller-context
  design and names the compliance buyer.

Note honestly while scoping: identity-at-birth, scoped-VC, least privilege, and tamper-evident audit
are **enforced** on v0.3.x; gateway-side MCP per-tool RBAC and identity-binding VP are **design-
target** — so don't let a discovery question promise per-tool RBAC as a live gateway control.

### Mode B — bespoke

Produce a focused question list for the deep-dive, organised by the workflow profiling gaps:

- **Workflow clarity** — any step that was tagged with unclear data source or unclear boundary. "Walk
  me through how you currently check prices — which tabs, in what order?"
- **Data sources** — confirm which APIs/platforms are available (official vs unofficial), what access
  looks like, whether rate limits apply.
- **Decision points** — which steps require human judgement and why? Could that judgement be encoded
  as rules?
- **V1/V2 cut** — validate the operator's initial V1 intuition: "If we could only automate one step in
  the next 6 weeks, which would give you the most time back?"

For each question: the question (in their language), the **gap it fills**, and the **`solution-design`
Mode B slot it feeds** (feasibility table row / V1 cut / architecture layer). Keep the list to ~6–10.

---

## Step 6 — Output

Save as **`solution-discovery-[company]-[YYYY-MM-DD].md`** in the account folder
`content/<active>/accounts/<account-slug>/` (see CLAUDE.md "Per-account outputs"). Instead of a full
snapshot, emit a single **header line** — `company · use case · date · link to the account-dossier`.
Then the structure:

1. **Current state user journey** — how their workflow / agents work *today*, step by step, plus the
   feature wishlist and initial V1/V2 intuition — framed as current state.
   Mode A: this feeds `solution-design` Step 1 alongside the technical stack profile. Mode B: this is
   the primary input.
2. **Technical stack profile** (Mode A) — the seven areas; each line marked *confirmed* (with source)
   or *unknown (ask)*.
   **Workflow profile** (Mode B) — the step-by-step manual workflow map with agent-shaped tags;
   the identified agent-loop; the data sources per step; entry/exit/boundary points.
3. **Integration-surface map** (Mode A) — where the product sits; what it intercepts; what stays
   untouched.
   **Agent-loop summary** (Mode B) — the agent-shaped loop with entry, exit, boundaries, and the
   confidence assessment for each step.
4. **Deployment hypothesis** (Mode A) — best-guess deployment model + chosen base pattern(s), stated
   as a hypothesis to confirm on the call.
   - **Frameworks to cite** — the 2–3 best-practice frameworks `solution-design` should align to for
     this account, plus the why-now drivers and any cert claims.
5. **Requirements question bank** — Mode A: grouped by the product's design dimensions; must-ask first,
   **led by the identity-first cluster** (per-agent identity vs shared secret · where credentials live
   · forbidden actions / least privilege · per-tenant boundary · actor-attribution for audit); each
   with decision-unlocked + design-slot. Mode B: grouped by workflow gaps; each with gap-filled +
   design-slot.
6. **Open technical risks / unknowns** — the gaps that, if unanswered, block the design.

**Also emit a self-contained, INTERNAL HTML companion** —
`solution-discovery-[company]-[YYYY-MM-DD].html` next to the `.md`, using the **same** generator the
design skill uses (`plugin/skills/solution-design/references/html-companion.md` — it states the same
generator works for any SA deliverable). The `.md` stays the source of truth; the `.html` is an
internal artifact. Append `⟦FILE:…⟧` sentinels for **both** files (real resolved absolute paths),
mirroring `solution-design`'s Output step — e.g.:

    ⟦FILE:content/<active>/accounts/<account-slug>/solution-discovery-[company]-[YYYY-MM-DD].md⟧
    ⟦FILE:content/<active>/accounts/<account-slug>/solution-discovery-[company]-[YYYY-MM-DD].html⟧

Then tell the colleague it's saved and **paste the must-ask question bank inline** so they can scan it
without opening the file. Mode A: also paste the integration-surface map. Mode B: also paste the
agent-loop summary. Close by offering to run `solution-design` once the deep-dive answers are back.

## Guardrails

- **Read-only.** Never send, post, or contact anyone. This is prep.
- **Free paths by default.** Metered tools (Firecrawl, Vibe) only on explicit `deep` opt-in and within
  PROFILE budget.
- **Never invent stack facts.** If a tool/framework isn't publicly evidenced, mark it *unknown (ask)* —
  unknowns become must-ask questions, which is the point.
- **Ground capabilities in the product (Mode A).** Only map to product features that exist in
  `profiles/<active>/knowledge/product.md` / the product's reference. Don't promise capabilities the
  product doesn't have.
- **Surface the identity-first questions (Mode A).** Every gateway deep-dive must surface the spine:
  per-agent identity vs shared secret, where credentials/keys live, which actions each agent must
  never take (least privilege), the per-merchant/tenant boundary, and compliance actor-attribution
  (audit). These lead the must-ask bank. Keep the honesty line: identity-at-birth, scoped-VC, least
  privilege, and tamper-evident audit are enforced on v0.3.x; MCP per-tool RBAC and identity-binding
  VP are design-target — don't frame a design-target as a live gateway control in a question.
- **Ground workflow profiling in observable signals (Mode B).** Only describe workflow steps the prospect
  confirmed or that are publicly evidenced. Mark inferred steps as *inferred (confirm on call)*.
- **Respect beta reality (Mode A).** If the product is in closed beta, frame deployment options accordingly.
- **Every question earns its place.** If a question doesn't unlock a design decision or fill a gap, cut it.
- **Be concise — this is research, not narrative.** No multi-paragraph caveats. Flag a docs-vs-live-
  product gap in **one line** (e.g. "public docs lag the live app — treat the app as ground truth").
- **Link to the dossier, never restate it.** No "reframe vs the dossier" prose — `account-dossier`
  owns business/buyer context; link to it.
- **Organize around the questions an SA needs answered when researching a new customer.**
