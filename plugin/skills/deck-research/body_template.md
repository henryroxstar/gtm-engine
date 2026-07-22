
# Deck Research

Turn an account into a **dossier** structured for deck assembly. Research the account once
(persona-agnostic), then frame it for one or more personas by filling the slot manifests. The output
is the input to `build-deck` — it makes deck generation assembly, not improvisation.

**Two layers (see `references/slot-manifests.md` for the full schema):**
- **Layer 1 — Account Intelligence.** Eleven persona-agnostic, sourced fields. Reusable across
  personas, decks, call-prep, and outreach.
- **Layer 2 — Persona Slot-Fills.** For each requested persona, the named slots that persona's
  template needs, filled from Layer 1 and keyed to `slot-manifests.md`.

**Read-only.** This skill never sends, posts, or contacts anyone.

---

## Step 1 — Load context

> Resolve the **active profile** (the agent provides it; everything loads from `profiles/<active>/`, never `plugin/`).
> Pull company brand from the profile (`company`, `brand_name`) — never hardcode a company name.

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `target_markets`,
   `emphasize_personas/verticals`, `monthly_tool_budget_usd`, `per_run_cap_usd`, `tools_metered`,
   `firecrawl`/`vibe_prospecting` status, `output_folder`, `language`. If no PROFILE, run `setup`
   first (or ask the 3 essentials: markets, segment focus, budget).
2. **`references/slot-manifests.md`** — the slot contract. Defines the Layer-1 field IDs (L1-1…L1-11),
   the footnote convention, and which slots each template needs.
> **Knowledge resolution (product-aware).** Wherever this skill loads a per-product knowledge file —
> `icp-personas.md` or `market-scan-config.md` — resolve its path with
> `python -m gtm_core.resolve_knowledge <file> --profile <active> [--product <slug>]` and read whatever
> path it prints, instead of opening `knowledge/<file>` directly. The helper returns the product-level
> file (`products/<slug>/<file>`) when present and falls back to the profile-level `knowledge/<file>`
> otherwise. Pass `--product` when the run is bound to one product (the lead `default_product` from
> PROFILE.md, or a product the operator named); omit it for profile-wide work — a profile that keeps one
> shared knowledge pack always falls back to the profile level, so nothing changes for it.

3. **Knowledge pack** — `profiles/<active>/knowledge/icp-personas.md` (segments, persona cards, scoring
   rubric, why-now triggers), `profiles/<active>/knowledge/case-studies.md` (four shapes + selection
   map), `profiles/<active>/knowledge/product.md` (suite, five themes, discovery questions),
   `profiles/<active>/knowledge/company.md` (narrative, positioning).
4. **Reuse prior research (do NOT re-research from zero).** Look in the account folder
   `content/<active>/accounts/<account-slug>/` for a fresh
   (≤30 days) `deck-research-*[company]*.md`, `call-prep-*[company]*.md`, or `prospects-*` /
   `outreach-*[company]*.md`. If found, load it and *extend* into the dossier — keep its dated
   signals, scores, and persona detail. Only research the gaps.

---

## Step 2 — Gather inputs

Ask in **one short message** (skip anything already provided):

- **Company / account** (required).
- **Persona(s)** to build slot-fills for — name + title if known, or the role. Map to a template via
  the routing table in `slot-manifests.md`. Multiple personas are fine (one Layer-2 block each).
- **Depth** — `standard` (free web sweep, default) or `deep` (opt-in metered crawl, see Step 3).
- **Known context** — any pain, signal, incumbent, or prior thread the user already has.
- **Language** — defaults to PROFILE language.

If the user already gave all of this, proceed without asking.

---

## Step 3 — Budget pre-check (only if `deep` / metered opt-in)

Default is free paths (web search + browser) — skip this step. If the user opted into `deep`:

- Confirm the tool is in `tools_metered` **and** connected. If not, fall back to the free path and
  say so.
- Call `estimate-cost` / check balance **before** any metered fetch. Show the estimate. If the run
  would breach `per_run_cap_usd` or the remaining monthly budget, trim depth or switch to the free
  path — **never silently overspend, never auto-buy credits.**

---

## Step 4 — Research sweep → Layer 1

Fill the eleven Layer-1 fields. Free web paths by default; stop each field when the top signals are
clear (extend call-prep's 5-minute discipline — this is deeper, but still bounded). **Prioritise
L1-2 and L1-6** (most-referenced across templates), then L1-8 and L1-10 (in every template).

| Field | Hunt for |
|---|---|
| **L1-1** firmographics_icp | size, revenue, segment (Ent/Startup), industry, HQ/markets, funding, headcount; compute ICP score via the rubric in `icp-personas.md` (show number + top factors) |
| **L1-2** agentic_maturity ★ | named evidence they build/run production agents — product launches, named agents, eng blogs, conference talks, job posts citing frameworks. The "agentic moment." |
| **L1-3** tech_stack | agent frameworks (LangGraph/CrewAI/AutoGen/LlamaIndex/MCP/A2A), cloud(s), IAM (Okta/Entra/Ping), data model |
| **L1-4** regulatory_posture | jurisdiction + industry → regulators (MAS/HKMA/SEC/FINRA/HIPAA/EU AI Act/PDPA…); certifications held or pursued (ISO 42001, SOC 2) |
| **L1-5** threat_hypotheses | the live threats specific to their deployment/industry — shadow agents, cross-org handoffs, incident/CVE exposure |
| **L1-6** use_case_scenarios | 3+ concrete agent scenarios from *their* portfolio (gap → product fix → outcome) |
| **L1-7** incumbent_competitive | their hyperscaler/IAM; build-vs-buy posture; lock-in exposure |
| **L1-8** proof_story | closest case study by **shape first, then industry**, + one-line "why this maps" |
| **L1-9** why_now | the single dated trigger making this timely (never invented) |
| **L1-10** buying_committee | named people → persona → primary pain → what they must prove |
| **L1-11** sources | every external claim numbered for footnoting |

**Sourcing rule:** every external fact gets an inline `[^n]` marker tied to a numbered L1-11 entry.
Internal knowledge-pack / active-company product facts are not footnoted. An empty field = `null` + a
one-line "no strong signal found"; **never fabricate.** If L1-2 (★) is empty, flag the account as
possibly not deck-ready.

---

## Step 5 — Fill Layer 2 (per persona)

For each requested persona:

1. Route to a template via `slot-manifests.md`. Confirm the match against the persona signals.
2. Read that template's manifest. For each `slot_id`, pull from the named Layer-1 field(s) and write
   the fill, carrying `[^n]` markers through.
3. Empty slot → `null` + the "no signal" note. Never invent to fill a slot.

---

## Step 6 — HITL review gate (always)

Present the dossier for review **before** saving as final and **before** any deck is built:

```
## Deck research — [Company]  ·  ICP [score]  ·  personas: [list]

LAYER 1 (account intel)
- agentic moment: [2–3 line summary]
- regulatory posture: …
- why-now: …
- proof story: …
[+ any field with no signal, flagged]

LAYER 2 (slot-fills)
- [Persona / template]: [N of M slots filled; any nulls listed]

→ Reply "save" to write the dossier, or tell me what to fix.
   (Or "save and build the [persona] deck" to chain into build-deck.)
```

Exception: if the user said "just do it" / "go" / any bypass phrase, skip the gate.

---

## Step 7 — Save

Write **`deck-research-[company]-[YYYY-MM-DD].md`** to the account folder
`content/<active>/accounts/<account-slug>/` (see CLAUDE.md "Per-account outputs"). Structure:

```markdown
# Deck Research — [Company] · [date]
> Reusable account dossier. Layer 1 = account intel (persona-agnostic).
> Layer 2 = per-persona slot-fills consumed by build-deck. External facts footnoted.

## Layer 1 — Account Intelligence
### Firmographics & ICP   (score N/12 or N/10 — [segment])
### Agentic maturity ★
### Tech & framework stack
### Regulatory posture
### Threat hypotheses
### Use-case scenarios
### Incumbent / competitive
### Matched proof story
### Why-now
### Buying committee
## Sources
[^1]: …

## Layer 2 — Persona Slot-Fills
### [Persona name] — [template]
```yaml
persona: [template id]
slots:
  [slot_id]: [fill, with [^n] markers]   # null + note if no signal
```
```

---

## Step 8 — Output & optional chain

Tell the colleague: "Dossier saved as `deck-research-[company]-[date].md` — Layer 1 is reusable
account intel; Layer 2 has slot-fills for [persona(s)]." Then:

- **Discrete (default):** offer the next step — "Run `build-deck` for [company] and it'll consume
  this dossier automatically." Do not auto-build.
- **Opt-in chain:** only if the user said "and build it" / "save and build" — hand off to `build-deck`
  for the named persona, which reads this dossier per its Step 1.5.

To add another persona later, re-run and the skill fills just the new Layer-2 block from existing
Layer 1 — no re-research.

---

## Guardrails

- **Never fabricate** — empty fields/slots are `null` with a note. No invented signals, quotes, or stats.
- **Product-accuracy discipline** — tag any product-capability claim SHIPPED/CONDITIONAL/ROADMAP for
  build-deck, and verify cited facts before they enter a slot: `docs/product-accuracy.md`.
- **Footnotes travel** — every external claim carries `[^n]` into Layer 1 and through to the slots.
- **Free paths by default** — metered tools only on explicit `deep` opt-in, and budget is a hard stop
  (estimate first, trim or fall back, never auto-buy).
- **Reuse, don't duplicate** — extend a fresh prospect/call-prep/dossier file rather than re-research.
- **Read-only** — never send, post, or contact anyone.
- **Discrete by default** — produce the reviewable dossier; only chain into build-deck on explicit
  opt-in.
- **Manifest is the contract** — slot IDs and template routing come from `references/slot-manifests.md`;
  if it changes, this skill follows.
```
