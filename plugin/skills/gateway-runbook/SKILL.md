---
name: gateway-runbook
description: >-
  Produce a parameterized, step-by-step gateway setup runbook tailored to a specific account,
  use case, and stack — grounded in the active product's verified reference pack (its real
  setup concepts, dashboard click-paths, and syntax). Trigger when the user says "write the
  gateway setup runbook for [company]", "gateway setup steps for [use case]", "implementation
  runbook for [company]", "how do we set up the gateway for [pattern]", "give [company] the
  setup guide", or "deployment runbook for [company]". Can produce either a dashboard
  click-through guide (default) or a headless / config-paste appendix (ready-to-paste config
  payloads + the product's management-API path) when asked for "API setup" or "headless
  setup". Consumes a `solution-design` dossier when present (chosen pattern + component
  inventory). Includes validation tests, troubleshooting, and a go-live checklist. Read-only
  authoring — it documents the steps, it does not provision anything itself.
metadata:
  version: "0.3.1"
  phase: "5"
  capability_tier: core
  requires_capability: [gateway]
---

# Gateway Runbook *(product-bound)*

Turn a solution design into setup steps a customer's engineer can actually follow. The runbook is
**parameterized** — it names their real targets, auth model, protocols, and sample requests in their
language — so it reads like an SA wrote it for them, not a generic doc. Every step is grounded in the
active product's verified reference pack; nothing is invented.

**Read-only authoring.** This skill writes the runbook. It does **not** log into a portal, provision a
gateway, or change any configuration — the customer (or a later, explicitly-approved step) does that.

**Where it sits in the SA chain:** `solution-design` → **`gateway-runbook`**. The design's component
inventory is this skill's input.

**This skill is product-agnostic; the reference pack is not.** "Gateway" here is a product *category* —
identity-aware proxies / governance layers that sit in front of agent runtimes. The active profile's
product supplies the actual concepts, screens, routes, and syntax; this skill assembles them into an
account-specific runbook. Text in `『corner brackets』` below is an **instruction to pull that element
from the product reference pack** — never paste it literally, and never fill it from memory of some
other vendor's product: a remembered UI path or API route from a different gateway is wrong by
definition here.

**Lead with the product's control spine.** Gateway-category products share a shape: each agent gets an
identity or credential; a caller-auth gate verifies it before policy runs; policy — not a shared
secret — decides what each verified caller may do; and every decision lands in an audit trail. The
reference pack defines the product's own version of that spine and the order to set it up in — 『the
recommended identity/auth posture and setup ordering from `gateway-reference.md`』 is the **default, not
an optional pattern**. The proxy/connectivity wiring follows from the spine — it does not replace it.

**Claim only what's enforced.** Tag every capability the runbook configures **Enforced / Simulated /
Design-target**, aligned to the product reference's ground truth, and close the runbook with a
**capability-coverage matrix**. Anything the reference marks as roadmap, preview, or known-broken is
presented as such — never as live. Every gate ships with a **stranger → deny** assertion (an
unauthenticated / forged-credential call is rejected *and* an out-of-scope agent is denied) — **never
treat a lone success response as proof**: a policy that silently fails open looks identical to a
working one from the happy path. Fail-open is the #1 category-wide trap.

---

## Step 1 — Load context

> Resolve the **active profile** (the agent provides it; everything loads from `profiles/<active>/`, never `plugin/`).
> This skill is **product-bound** (`requires_capability: [gateway]`) — it runs only when the active company has a
> product providing the `gateway` capability. Resolve that product from `PROFILE.md` → `products[]` (its `slug`,
> `name`, `reference_dir`); load its specifics from `profiles/<active>/products/<product>/`. Use the product's
> real name throughout — never a hardcoded one.

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `language`, `output_folder`. Respect language.
2. **`profiles/<active>/products/<product>/references/gateway-reference.md`** — the condensed, verified
   product reference: terminology, 『the typical setup sequence』, 『the use-case pattern recipes』, the
   verified screen/field inventory, and the quick-reference syntax (『secret-reference syntax,
   endpoint/route patterns, any cross-gateway or connection handshake』). **This is the source of truth for
   the dashboard path — never describe a screen, field, or syntax not grounded here** (or, for the headless
   path, in `surface-config-payloads.md` below).
3. **`profiles/<active>/products/<product>/references/validation-and-golive.md`** — per-step validation
   tests, troubleshooting, and the go-live checklist to append.
4. **`profiles/<active>/products/<product>/product-how-to.md` — optional but preferred; when present it is
   REQUIRED reading, every run, both paths.** If the product pack carries a how-to / gotchas file, 『its
   field-tested traps』 MUST be encoded as mandatory validation/troubleshooting steps (not optional
   reading). Category-wide trap shapes to look for and encode when the how-to confirms them: a policy that
   silently **fails open** (a non-enforcing gate still returns success — never treat a success as proof;
   always assert a stranger is denied); config that **saves without compiling or activating** (a raw paste
   that never becomes an enforcing gate); an auth mode that is **documented but broken in the shipped
   version** (encode the working alternative the how-to names); enforcement that must live **outside** the
   gateway (e.g. per-tool or per-argument rules pushed to the backend); and lifecycle operations that are
   unsafe or unavailable in the shipped version (create sparingly / adopt existing). If the file is absent,
   say so in the runbook and lean entirely on the reference pack.
5. **`profiles/<active>/products/<product>/references/surface-config-payloads.md` — load only for the
   headless / config-paste path.** Skip it for a pure dashboard click-through runbook. Load it when the
   customer wants to create the product's configurable objects by **pasting config** or via 『the product's
   management API』: it carries verbatim config payloads for the starter templates, 『the per-object config
   shapes』, minimal parameterized templates, and the auth/headless caveats. Syntax and config reproduced
   from here are **in-scope and authoritative** alongside `gateway-reference.md` — but apply the item-4
   gotchas before emitting any payload (captured templates may pre-date fixes the how-to documents). If
   this file is absent, fall back to the dashboard path only and say so.
6. **Knowledge** — `profiles/<active>/knowledge/product.md` (deployment options and the product's adoption
   flow, for framing).
7. **Consume the design dossier (strongly preferred).** Look for a fresh `solution-design-[company]-*.md`.
   If present, pull the **chosen pattern(s)** and the **component inventory** (『the product's configurable
   object types — entry points, proxies, credentials, policies, connections, secrets, whatever the
   reference names them』) — that's exactly what the runbook implements. If absent, you can still run from
   a described use case, but flag that the runbook rests on assumptions and recommend running
   `solution-design` first.

---

## Step 2 — Confirm the runbook inputs

Confirm (don't re-ask what the design dossier answers) in one short message. Where an input is genuinely
unresolved, pull the matching question from
`profiles/<active>/products/<product>/references/discovery-question-bank.md` rather than improvising:

- **Use case / pattern(s)** — which of 『the reference's pattern recipes』 apply (compose if needed).
- **Setup method** — dashboard click-through (default) vs **headless / config-paste** (config paste or 『the
  product's management API』). Headless adds the payloads/templates from `surface-config-payloads.md` as an
  appendix; the default omits it.
- **Deployment model** — 『the deployment options from `knowledge/product.md`』; default to the pack's
  stated fast path.
- **Target(s) & protocol** — the service(s) agents will reach, classified per 『the product's target
  taxonomy from the reference』 (e.g. a REST API fronted by a proxy, an existing MCP server,
  agent-to-agent).
- **Agent identity & caller-auth (confirm first)** — how each agent is identified to the gateway, and
  which caller-auth mode gates the entry point — only modes the reference documents, preferring 『the mode
  the pack marks as the working / recommended path』.
- **Least privilege / forbidden actions** — the **≥2 agents** in scope and which action a verified agent
  must be **denied** (the sensitive tool or scope its credential does not cover) — the live deny is the
  proof.
- **Outbound credentials** — does an agent need a downstream token or credential? where do secrets live?
- **Cross-org / federation** — single gateway, or cross-boundary (only if the reference documents such a
  capability)?
- **Payment / metering** — any paywall or usage metering (only if the reference documents it)?
- **Sample-code language** — for the validation requests (curl by default; their language if known).

## Step 3 — Select and compose the recipe

From `profiles/<active>/products/<product>/references/gateway-reference.md`, pick the base pattern(s) and
merge them into one ordered sequence following 『the reference's typical setup sequence』. **The control
spine is non-negotiable** — agent identity/credential issuance, the caller-auth gate, the least-privilege
deny, and the audit trail are always in; drop only the *optional* patterns (cross-org, payment, and the
like) the use case doesn't need. Never reorder dependencies (secrets before the things that reference
them; identities and credentials before the gates that verify them; 『any ordered handshake the reference
defines』 in its stated order).

## Step 4 — Write the runbook

Produce, in order:

1. **Overview** — use case, chosen pattern(s), deployment model, and the end-state in one paragraph.
2. **Prerequisites** — account/tenant provisioning per 『the onboarding path named in the reference』 (note
   the dashboard URL and login mechanism), and **what the customer must supply** (API specs, IdP details,
   downstream client credentials, target URLs). Call these out explicitly — they're the usual blockers.
3. **Setup steps** — each step has: **(a)** the exact dashboard navigation (section → action, from the
   reference), **(b)** the **field values for *their* case** (real route, target URL, auth type, issuer,
   policy intent), and **(c)** 『any exact syntax the reference defines — secret references,
   endpoint/route patterns, connection strings』. Use parameterized placeholders only where the customer
   must fill a value, and mark those clearly. **Include the control-spine steps explicitly:** provision or
   register each agent's identity/credential, bind the entry point's caller-auth to its verification
   config, and apply any compile/activate step the how-to warns is required before a policy actually
   enforces. Run ≥2 agents so the least-privilege **deny** is demonstrable.
4. **Validation tests** — after the relevant steps, a concrete check that it works (from
   `profiles/<active>/products/<product>/references/validation-and-golive.md`): a sample request in their
   language, the expected response, and where to confirm in 『the product's monitoring / logs view』.
   **Every gate MUST ship a `stranger → deny` assertion, not just a `valid → success` one:** an
   unauthenticated / forged-credential call is rejected at the gate, **and** a fully-verified agent is
   denied the sensitive action its credential doesn't cover, on a real call. A lone success response (or
   a lone tool listing) is **not** proof — a fail-open policy makes an unasserted success meaningless.
5. **Troubleshooting** — lead with 『the how-to's field-tested gotchas』 (mandatory whenever
   `product-how-to.md` exists — see Step 1 item 4 for the trap shapes). Then the common failures (auth
   rejections from verification config, a secret not resolving, a proxy route 404, a cross-org handshake
   stalling) and the first thing to check for each.
6. **Go-live checklist** — the final gate before production traffic.
7. **Capability-coverage matrix (always — the honesty artifact).** One row per capability the runbook
   configures, tagged **Enforced / Simulated / Design-target**, aligned to 『the reference's ground truth
   on what is live vs roadmap』 — so the customer doc claims only what the setup actually proves. Where a
   `solution-design` dossier or a demo coverage/smoke output exists, reconcile against it so the runbook
   never out-claims what was demonstrated.
8. **Headless / config-paste appendix (only if the setup method is headless).** From
   `surface-config-payloads.md`, give ready-to-paste config for *their* case (start from the matching
   minimal template, substitute their placeholders and any per-object configs) plus 『the management-API
   flow as the reference documents it — create/list/read/update routes and expected responses』. Reproduce
   per-object config shapes verbatim and emit the **full** config (never empty stubs when the file warns
   the server stores what you send). Note 『any auth caveats the file documents for the management API』.
   Map the prerequisite objects the payload only *references* back to their dashboard steps.

Frame steps as a guide the customer's engineer executes — not as actions this skill performs.

## Step 5 — Output

Save as **`gateway-runbook-[company]-[YYYY-MM-DD].md`** in the account folder
`content/<active>/accounts/<account-slug>/` (see CLAUDE.md "Per-account outputs"). Tell the colleague it's
saved and **paste the prerequisites + the step outline inline** (so they can sanity-check what the
customer must supply before sending). Offer a docx export for a customer-ready document, and note that
any actual sending to the customer is a separate, explicitly-approved step.

Then append a `⟦FILE:…⟧` sentinel at the very end of your response so the cockpit delivers the runbook automatically:

```
⟦FILE:/absolute/path/to/content/<active>/accounts/<account-slug>/gateway-runbook-[company]-[YYYY-MM-DD].md⟧
```

If the docx export is also produced, add a second sentinel for it. Use the real resolved absolute paths.

## Guardrails

- **Source of truth is `profiles/<active>/products/<product>/references/gateway-reference.md`** for the
  dashboard path, the product's **`product-how-to.md`** field-tested gotchas (mandatory whenever the file
  exists), **plus `surface-config-payloads.md`** when the runbook takes the headless / config-paste path.
  Never describe a screen, field, section, config key, or API route that isn't in one of those. If the
  design needs something none of them covers, flag it as "confirm with the product team / reference"
  rather than inventing it.
- **Never substitute another vendor's product.** If a fact is missing from the pack, the answer is a flag,
  not a memory — remembered UI paths, API routes, or version numbers from other gateway-category products
  are wrong by definition here.
- **Control-spine first + claim only what's enforced.** Centre the runbook on the spine (agent identity ·
  verified caller-auth · policy, not a shared secret · audit trail) as the reference defines it, and close
  with the capability-coverage matrix tagged **Enforced / Simulated / Design-target**. Never present a
  roadmap or preview capability as live.
- **Fail-open is the #1 category trap — every gate ships a `stranger → deny` assertion.** Never treat a
  lone success response as proof; require a forged/unauthenticated call → rejected and an out-of-scope
  agent → denied. Apply any compile/activate gotcha the how-to documents before calling a gate enforcing.
- **Respect release reality** — 『the product's availability constraints from the reference — beta limits,
  per-tenant quotas, "coming soon" features』. Don't promise capabilities the pack doesn't claim as live.
- **Exact syntax matters.** Reproduce the reference's secret-reference, route, and link syntax verbatim.
- **Name what the customer must supply.** API specs, IdP details, downstream secrets, target URLs —
  surface these in prerequisites so the runbook doesn't stall mid-setup.
- **Authoring only.** This skill never provisions, logs in, or sends. Sending the runbook to the
  customer is a separate approved action.
- **Honesty rules** (same as `solution-design`): the gateway produces audit-ready *evidence* — it is not
  itself the customer's certification, and the customer still owns log retention and their compliance
  program. Claim certifications and compliance posture only as `profiles/<active>/knowledge/product.md`
  and `profiles/<active>/knowledge/company.md` state them. Where the runbook claims a control is
  satisfied, say what evidence the validation produces, not that compliance is achieved.
- **Don't fabricate validation output.** Show the *expected* response shape; mark anything environment-
  specific as "varies by your deployment."
