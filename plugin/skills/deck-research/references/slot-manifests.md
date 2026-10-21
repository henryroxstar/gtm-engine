# Deck-Research Slot Manifests

The **contract** between the `deck-research` skill and `build-deck`. For each of the 12 templates in
`../../build-deck/references/slide-outlines.md`, this file names the *account-specific slots* that
template needs filled with researched intelligence, and which Layer-1 field each slot draws from.

`deck-research` fills these slots (Layer 2 of the dossier). `build-deck` reads the manifest for the
routed template and drops the fills into `outline.md`. Static product/company slides (e.g. company
intro, "core capabilities") are **not** slots — they come from the knowledge pack unchanged and are
omitted here.

> **Maintenance:** this file is keyed to `slide-outlines.md`. If a template's account-specific slides
> change, update the matching manifest here. Both files note this dependency.

---

## Layer-1 field IDs (what slots draw from)

Every slot references one or more of these persona-agnostic account-intelligence fields. The schema
itself is defined in the `deck-research` SKILL; this is the short reference for slot mapping.

| ID | Field | One-line |
|---|---|---|
| **L1-1** | firmographics_icp | size, revenue, segment, industry, HQ/markets, funding, headcount, ICP score |
| **L1-2** ★ | agentic_maturity | named evidence they build/run production agents (the "agentic moment") |
| **L1-3** | tech_stack | agent frameworks, cloud(s), IAM (Okta/Entra/Ping), data model |
| **L1-4** | regulatory_posture | jurisdiction → regulators; certifications held/pursued (ISO 42001, SOC 2) |
| **L1-5** | threat_hypotheses | live threats specific to their deployment/industry |
| **L1-6** | use_case_scenarios | concrete agent scenarios from *their* portfolio (gap → fix → outcome) |
| **L1-7** | incumbent_competitive | their hyperscaler/IAM, build-vs-buy posture, lock-in exposure |
| **L1-8** | proof_story | closest case study by shape→industry + "why it maps" |
| **L1-9** | why_now | the single dated trigger |
| **L1-10** | buying_committee | named people → persona → primary pain → what they must prove |
| **L1-11** | sources | numbered external sources for footnoting |

★ = highest-leverage field; if empty, flag the account as not deck-ready rather than fabricate.

## Footnote / sourcing convention

Every value that originates from an **external** source carries an inline footnote marker (`[^n]`)
tied to a numbered entry in L1-11. Markers travel with the value into the slot fill, so build-deck
can render Banner citations on-slide and reproduce the `## Sources` footer. Internal knowledge-pack
and active-company product facts are **not** footnoted — only outside-world claims about the account,
market, or regulation. Empty slot = write `null` + a one-line "no strong signal found" note; never
invent.

## Slot fill conventions

- Each manifest lists `slot_id → slide # → what it needs → source field(s)`.
- In the dossier, fills are emitted as a YAML block under the persona heading (see SKILL for the
  exact shape). Slot IDs here are the YAML keys.
- `routing` mirrors the persona signals in `slide-outlines.md` so build-deck can confirm the match.

---

# Generic templates

## A1 — Discovery Deck (mixed committee / persona unknown)

`routing:` generic enterprise or startup discovery; persona genuinely unclear.

| slot_id | slide | needs | source |
|---|---|---|---|
| `problem_pains` | 2 | the 3 pains most live for *this* account, one striking number each | L1-5, L1-2 |
| `why_now_regulatory` | 3 | the 1–2 regulatory timelines most relevant to their market/industry | L1-4, L1-9 |
| `proof_story` | 6 | matched case (shape→industry) + "why this maps to you" line | L1-8 |
| `competitive_incumbent` | 7 | which hyperscaler to frame "why not [X]" against | L1-7 |
| `poc_shape` | 9 | a scoped PoC tailored to their stack/boundary (framework + boundary) | L1-3, L1-6 |
| `cta_ask` | 10 | the one specific next-step ask for this account/stage | L1-10 |

## A2 — Tailored One-Pager (1-page leave-behind)

`routing:` leave-behind after a call, or first-touch attachment.

| slot_id | slide | needs | source |
|---|---|---|---|
| `pains_3` | top | 3 most relevant pains in the persona's language | L1-5 |
| `proof_one_line` | mid | one case study, name + one-sentence outcome | L1-8 |
| `why_now_one` | mid | the single why-now signal/trigger | L1-9 |
| `next_step_ask` | bottom | one bolded ask | L1-10 |

## A3 — PoC Proposal (Champion + Economic buyer)

`routing:` proposing a scoped pilot after discovery.

| slot_id | slide | needs | source |
|---|---|---|---|
| `what_we_heard` | 2 | 3 specific points from discovery — their pain, their agents/frameworks, their timeline | L1-5, L1-3, L1-9 |
| `poc_scope` | 3 | exact scope: which framework(s), which boundary, success criteria | L1-3, L1-6 |
| `success_criteria` | 4 | 3–5 measurable outcomes tied to their environment | L1-6 |
| `roles` | 5 | named roles: their champion / security architect / our SA | L1-10 |
| `proof_story` | 6 | closest case by shape; reference offer if available | L1-8 |
| `cta_kickoff` | 8 | agree scope + kick-off date by a specific date | L1-10 |

## A4 — Partner / SI Brief

`routing:` channel, integration, or SI partner.

| slot_id | slide | needs | source |
|---|---|---|---|
| `partner_customer_pain` | 2 | the partner's *end-customers'* agent-trust pain | L1-5 |
| `joint_motion` | 4 | referral / OEM / co-sell framing for this partner type | L1-10 |
| `proof_vertical` | 5 | 1–2 cases most relevant to the partner's vertical | L1-8 |
| `partner_ask` | 6 | one specific ask: named technical eval or joint pilot customer | L1-10 |

---

# Enterprise persona templates

## A5 — Regulated Enterprise: Legal + Compliance

`routing:` CISO · GC · Privacy Officer · Head of Compliance · Risk/Audit · DPO · CLO.

| slot_id | slide | needs | source |
|---|---|---|---|
| `agentic_moment` | 2 | 3 specific signals they run production agents + 1 scale/growth stat; frame their governance commitment as needing *runtime* evidence | L1-2 ★, L1-4 |
| `governance_gap` | 3 | what their existing frameworks/tools cover vs. miss (identity, provenance, runtime policy) | L1-3, L1-4 |
| `three_threats` | 4 | 3 live threats specific to their industry/stack (shadow agents, reg deadline, case law, relevant CVE/incident) | L1-5 |
| `legal_landscape` | 5 | 4 dated regulatory/legal signals for their market+industry, each with "what this means for [company]" | L1-4, L1-9 |
| `compliance_mapping` | 8 | pick the most-live framework (ISO 42001 / EU AI Act / NIST AI RMF / SOC 2) + 5–6 requirement rows (Requirement → Demands → How the product delivers) | L1-4 |
| `portfolio_use_cases` | 9 | 3 scenarios from their known agent portfolio (gap + product fix + outcome) | L1-6, L1-2 |
| `competitive` | 10 | their incumbent IAM/hyperscaler for the "why not [X] IAM" table | L1-7 |
| `timing` | 11 | a company-specific dated event + a regulatory deadline (+ product early-access) | L1-9, L1-4 |
| `proof_story` | (within) | closest case, regulated/compliance-as-wedge framing if applicable | L1-8 |

## A6 — Platform / Infrastructure Leader

`routing:` CTO · Head of Platform Eng · VP Eng · Enterprise/Cloud Architect.

| slot_id | slide | needs | source |
|---|---|---|---|
| `framework_sprawl` | 2 | their actual multi-framework reality across teams (who runs LangGraph/CrewAI/AutoGen/MCP-native) | L1-3, L1-2 |
| `wrapper_cost` | 3 | evidence of custom auth/observability wrapper pain + a dev-time-lost stat | L1-3, L1-5 |
| `architecture_fit` | 6 | their IAM (Entra/Okta/Ping) + cloud topology for the "where the product lives" diagram | L1-3, L1-7 |
| `proof_story` | 9 | closest case for platform-engineering context | L1-8 |
| `next_steps_offer` | 10 | architecture walkthrough → PoC scope → early access; sandbox offer | L1-10 |

## A7 — Cross-Organisation Trust

`routing:` Enterprise Architect · Partner Platform Owner · BD (partner-led) · cross-boundary agents.

| slot_id | slide | needs | source |
|---|---|---|---|
| `cross_boundary_scenario` | 2 | their actual edge case: which two orgs/clouds/identity systems an agent crosses | L1-6, L1-3 |
| `how_trust_breaks` | 3 | the failure modes + one incident from their industry (e.g. token-reuse breach) | L1-5 |
| `cross_org_scenarios` | 6 | 3 cross-org scenarios specific to them (supply chain / settlement / partner exchange / healthcare) | L1-6 |
| `next_step_boundary` | 9 | map their first cross-boundary use case (one boundary, one partner) | L1-10 |

## A8 — Developer / Builder

`routing:` AI/Platform Engineer · SA · senior dev · technical evaluator running the PoC.

| slot_id | slide | needs | source |
|---|---|---|---|
| `production_blindspot` | 2 | what *they specifically* can't see today (which agents, which tools, injection visibility, versioning) | L1-3, L1-5, L1-2 |
| `protocol_stack_map` | 4 | map the product's protocol coverage to the frameworks they actually run | L1-3 |
| `proof_story` | (within) | closest case for a developer-led/technical deployment | L1-8 |
| `next_steps_sandbox` | 9 | technical walkthrough → sandbox → PoC scope (one framework, one boundary) | L1-10 |

## A9 — Agent Commerce

`routing:` Head of Product · BD/Partnerships · Monetisation/Revenue lead.

| slot_id | slide | needs | source |
|---|---|---|---|
| `commerce_gap` | 2 | their unattributed-agent-traffic problem + a per-day call/spend stat if available | L1-2, L1-5 |
| `monetisation_models` | 5 | which of pay-per-call / subscription gate / metered usage fits their model, with their example | L1-6 |
| `commerce_use_cases` | 7 | 3 commerce scenarios relevant to them (API marketplace / agentic SaaS / partner revenue-share / dealer-supplier) | L1-6 |
| `next_step_workflow` | 8 | map their first monetisable workflow | L1-10 |

## A10 — AI to Production: Head of AI

`routing:` Head of AI · VP Applied AI · AI Platform Lead · AI Product Manager.

| slot_id | slide | needs | source |
|---|---|---|---|
| `production_gap` | 2 | which of the 3 failure modes (safety failure / invisible sprawl / governance debt) is most live for them | L1-5, L1-2 |
| `multi_framework_estate` | 7 | which teams run which frameworks + FinOps attribution angle | L1-3 |
| `proof_story` | 8 | closest case with pilot→production timeline + governance/safety metric | L1-8 |
| `why_now` | 9 | their reg pressure + board/audit pressure + early access window | L1-9, L1-4 |
| `next_steps` | 10 | pilot review → governance PoC (one framework, one week) → early access | L1-10 |

---

# Startup persona templates

## A-S1 — Startup: CEO / CPO

`routing:` founders/CEOs/CPOs, Seed–Series C, governance as enterprise-sales wedge.

| slot_id | slide | needs | source |
|---|---|---|---|
| `enterprise_wall` | 2 | the real procurement/security question blocking *their* deals + how many cycles it's cost | L1-5, L1-9 |
| `build_vs_buy` | 3 | what building this would cost them (engineers, months) vs. what they'd rather build | L1-3 |
| `trust_differentiation` | 4 | their public trust/responsibility positioning to amplify (or the gap if absent) | L1-2, L1-9 |
| `proof_traction` | 6 | closest case in their sector + closed-beta relevance | L1-8 |
| `next_step_scoping` | 7 | scoping call; what we need: stack, one cross-boundary use case, enterprise customer case | L1-10, L1-3 |

## A-S2 — Startup: CTO / Lead Engineer

`routing:` CTO/Lead Eng, Seed–Series C, build-vs-buy decision-maker.

| slot_id | slide | needs | source |
|---|---|---|---|
| `build_vs_buy_calc` | 2 | honest build breakdown sized to their stack (DID infra, policy, audit, cross-boundary, standards upkeep) | L1-3 |
| `standards_risk` | 3 | which evolving standards they're building on (A2A/MCP/AP2, DID/VC, NIST) = rebuild risk | L1-3 |
| `enterprise_readiness` | 6 | what their enterprise security reviews ask for + procurement time saved | L1-5, L1-9 |
| `proof_story` | 7 | closest case for a startup/developer-led deployment + integration time | L1-8 |
| `next_steps_sandbox` | 8 | technical walkthrough → sandbox → PoC scope; SA pairing offer | L1-10 |

---

## Coverage summary

12 templates · all account-specific slides mapped to slots · every slot sourced from a defined
Layer-1 field. `L1-2` (agentic maturity) and `L1-6` (use-case scenarios) are the most-referenced
fields across personas — prioritise them in the research sweep. `L1-8` (proof) and `L1-10`
(committee/ask) appear in every template.
