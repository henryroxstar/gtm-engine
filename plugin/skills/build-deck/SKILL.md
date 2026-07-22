---
name: build-deck
description: >-
  Build an on-brand sales deck, one-pager, POC proposal, or partner brief for the active
  company. Trigger when the user says "build a deck for [company]", "make slides for
  [persona]", "create a presentation about [topic]", "put together a deck for [meeting]",
  "build a one-pager for [use case]", "write up a POC proposal for [company]", "make a partner
  brief for [company]", or any similar request for a presentation-format deliverable.
  Automatically detects the primary persona and selects the matching template. Confirms
  outline before generating. Supports Mode A (pptx) and Mode B (Slidev / on-brand).
metadata:
  version: "0.6.1"
  phase: "4"
  capability_tier: core
---

# GTM — Build Deck

Produce a tailored, on-brand deck the colleague can present, share, or export as PDF.
Content is grounded in the active company's knowledge pack and personalised to the named account and persona.

**Two output modes:**
- **Mode A** — `.pptx` via the `pptx` skill. Default. Works anywhere, no extra tooling.
- **Mode B** — Slidev, on-brand, cinematic, polished. The brain authors `slides.md`; the
  **deck-renderer sidecar** renders it via the `mcp__deck__export_deck` tool (no local toolchain).

---

## Step 1 — Load context

> Resolve the **active profile** (the agent provides it; everything loads from `profiles/<active>/`, never `plugin/`).
> Read the company brand from `PROFILE.md` (`brand_name`, `deck_byline`, `deck_speaker`) — never hardcode a company name.

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `title`, `email_signature`, `language`, `brand_name`, `deck_byline`, `deck_speaker`.
   - **Byline default = brand.** Unless `deck_byline: name` is set (or the user explicitly says "put my name on it"), decks are attributed to the **company brand (`brand_name`), not the colleague's personal name**. Use `deck_speaker` (the PROFILE's brand presenter line) for the presenter line; do **not** print the personal `name` on the cover or in `config.yml speaker.name`. This affects decks only — outreach still uses `email_signature`.
2. **`profiles/<active>/knowledge/product.md`** — product suite, solution themes, competitive positioning.
3. **`profiles/<active>/knowledge/icp-personas.md`** — ICP segments, persona cards, value props ranked by role.
4. **`profiles/<active>/knowledge/case-studies.md`** — case-study selection map; pick the closest proof story.
5. **`profiles/<active>/knowledge/company.md`** — the company story, mission, team, investors.
6. **Prior call-prep or prospect file** (if referenced) — pull account-specific signals.

---

## Step 1.5 — Deck-research dossier (consume if present)

Look for a `deck-research-[company]-*.md` dossier in the account folder
`content/<active>/accounts/<account-slug>/` (produced by the
`deck-research` skill). This is the structured account intelligence keyed to the slot manifests in
`../deck-research/references/slot-manifests.md`.

- **Present & fresh (≤30 days):** Load **Layer 1** for narrative grounding (firmographics, agentic
  maturity, regulatory posture, why-now, proof). After persona routing (Step 3), read the **Layer 2**
  slot-fill block for the routed template and drop each fill straight into the matching
  `outline.md` slide. Carry every `[^n]` footnote marker through; reproduce the dossier's `## Sources`
  in the deck's source footer. This replaces improvised slot-filling in Step 4.
- **Present but the routed persona has no Layer-2 block yet:** offer to run `deck-research` to add just
  that persona's block (cheap — fills from existing Layer 1, no re-research), or proceed with
  improvised fills.
- **Absent:** offer to run `deck-research` first for a sourced, reusable dossier. If the user
  declines, fall back to the normal flow below (full backward compatibility).

---

## Step 1.6 — Product capability claims: claim only what's enforced

If this deck makes **product capability claims** (any slide that asserts identity, credentials,
policy, tool-gating, federation, or audit), the capability-coverage matrix is the source
of truth for what a slide may state as live. **Pull the reference demo's `CapabilityCoverage` matrix**
(resolved from the active product's references) — it tags each flagship
capability **Enforced / Simulated / Design-target**, driven by live runtime signals — and tag every
product claim on the deck to its row. **Never present a Design-target as live or product-native.**

This is the deck-specific application of the shared **product-accuracy discipline**
(`docs/product-accuracy.md`) — the same three-state (SHIPPED/CONDITIONAL/ROADMAP ≙
Enforced/Simulated/Design-target) tag-and-carry-into-copy rule, plus: re-verify a load-bearing claim
against current docs/code/UI (tags drift either way), and verify any cited external fact.

- **Lead with the genuinely-real differentiators (Enforced):** pick them from the product's
  reference pack (its deck/design-claims section names the flagship, real differentiators and the
  capabilities **Enforced** on the current build).
- **Honest nuance (do NOT overclaim):** where the reference pack notes an enforcement caveat (a
  check that runs somewhere other than the product, or a capability Enforced only under a specific
  demo configuration), carry that caveat onto the slide — never round it up to product-native.
- **Never show as live/green:** everything the reference pack tags **Design-target** on the current
  build. These are the roadmap, not the proof — frame them as "where the platform is going",
  never as a shipped control.

The product produces **audit-ready evidence**; it is **not itself certified** against the frameworks
it evidences — a slide may say "evidence for X", never "certified / compliant with X".

---

## Step 2 — Gather inputs

Ask in **one short message** (skip any item already provided by the user):

- **Company / audience** — name + segment (Enterprise / Startup) + primary persona or role.
- **Output mode** — Mode A (pptx, default) or Mode B ("on-brand", "slidev", "polished").
- **Backgrounds** — generate AI backgrounds via Higgsfield? (yes / no / auto — auto = yes for Mode B, no for Mode A unless requested).
- **Personalisation hooks** — specific pain, why-now signal, case-study preference. Leave blank to auto-select.
- **Language** — defaults to PROFILE language.

If the user has already given all this context, proceed without asking.

---

## Step 3 — Detect persona and select template

Map the primary persona or role signals to a template using the routing table in `references/slide-outlines.md`. Always prefer the persona-specific template (A5–A10, A-S1, A-S2) over the generic A1 discovery deck.

**Quick routing (full table is in slide-outlines.md):**

| Persona signals | Template |
|---|---|
| Legal · Privacy · Compliance · Risk · Audit · Counsel · CLO · GC · DPO | **A5** |
| CTO · Head of Platform · VP Eng · Enterprise/Cloud Architect · Infra | **A6** |
| Partner Platform Owner · Enterprise Architect (cross-org) · BD partner | **A7** |
| AI/Platform Engineer · Solutions Architect · technical evaluator | **A8** |
| Head of Product · BD/Monetisation · Revenue (agent commerce) | **A9** |
| Head of AI · VP Applied AI · AI Platform Lead · AI Product | **A10** |
| Startup CEO / CPO | **A-S1** |
| Startup CTO / Lead Engineer | **A-S2** |
| Mixed committee / persona unknown | **A1** |
| One-pager leave-behind | **A2** |
| PoC proposal | **A3** |
| Partner / SI brief | **A4** |

---

## Step 4 — Build the outline

Using the selected template from `references/slide-outlines.md`:

1. **Slide titles** — lead with the problem or outcome, not a feature name. Personalise to the named company where context allows.
2. **Body** — 3–4 bullets max per slide. Ground each in `product.md` or `icp-personas.md`. No invented claims.
3. **Proof** — apply the `case-studies.md` selection map. Use exact reusable hooks from the "Reusable messaging hooks" section for direct-quote headlines.
4. **CTA** — last slide always ends with one specific ask.
5. **Visual assets** — after building the outline, open `references/slide-library.md` and check the persona mapping table at the top. For the selected template, insert the recommended library slugs at the slide positions indicated. Add a `[Library: SLUG]` note in the Visual notes field of the relevant outline slide — do not fabricate or omit the visual. For Mode B (Slidev), copy the full spec from `slide-library.md` into `outline.md`. For Mode A (PptxGenJS), use the file path from the spec (under `profiles/<active>/knowledge/brand/`) in `s.addImage()` or `s.background`.
6. **Product claims = matrix-bound (if Step 1.6 applied)** — every slide that asserts a product capability must map to an **Enforced** row of the `CapabilityCoverage` matrix. Lead the value slides with the real differentiators from the product's reference pack; keep the capabilities it tags Design-target on a clearly-labelled "roadmap / where we're going" slide, never on a "what you get today" slide. Say "audit-ready evidence", not "certified".

---

## Step 5 — HITL approval gate (always required)

**Do not generate images or render the deck until the outline is approved.**

Present the outline as a structured table:

```
## Outline for review — [Company] · [Template] · [Mode]

| # | Title | One-liner |
|---|---|---|
| 1 | [slide title] | [what this slide argues / shows] |
| 2 | ... | ... |
...

**Backgrounds:** [yes — bg-title.png / bg-problem.png / bg-cta.png] OR [none]
**Output:** [Mode A: deck-[company]-[type]-[date].pptx] OR [Mode B: slides.md → `mcp__deck__export_deck` in `content/<active>/accounts/<account-slug>/`]

→ Reply **"go"** to proceed, or tell me what to change.
```

Exception: if the user said "just build it", "go for it", or any equivalent bypass phrase — skip the approval gate and proceed directly.

---

## Step 6 — Generate backgrounds (if requested)

After outline approval, before rendering, generate backgrounds via Higgsfield.

Use the canonical prompts from `profiles/<active>/knowledge/brand-notes.md` (section: "Higgsfield Background Images"). Generate all three:
- `bg-title.png` — hero/title background
- `bg-problem.png` — problem/threat section background
- `bg-cta.png` — resolution/CTA background

Save to `inputs/images/` inside the deck folder (Mode B) or to the outputs directory (Mode A).

If Higgsfield is unavailable or the user declined backgrounds, skip this step and proceed with colour fills per `profiles/<active>/knowledge/brand-notes.md`.

---

## Step 7 — Render

### Mode A — PptxGenJS via `pptx` skill

Invoke the **`pptx` skill**. Apply:

- **Brand colours** from `profiles/<active>/knowledge/brand-notes.md` (use that file's palette table — do not hardcode hex values here).
- **Font:** the geometric sans-serif family specified in `brand-notes.md` (consistent throughout).
- **Title slide:** dark background + `bg-title.png` if generated. Headline white large. Accent sub-line. Byline + date bottom-left — use `deck_speaker` (brand) by default, the colleague's personal name only if `deck_byline: name`. Company wordmark (`brand_name`) bottom-right.
- **Content slides:** light background. Left-aligned body. One visual element per slide.
- **Backgrounds:** embed via `s.background = { path: './inputs/images/bg-title.png' }` on applicable slides (see brand-notes.md Placement guidance).

Name the output file **`deck-[company]-[template]-[YYYY-MM-DD].pptx`** and save to the account folder
`content/<active>/accounts/<account-slug>/` (see CLAUDE.md "Per-account outputs").

### Mode B — Slidev via the deck-renderer sidecar

No local workspace is required: the brain composes `slides.md` and the `mcp__deck__export_deck` tool
renders it against the sidecar's baked deck-theme. Write the deck source + input files into the
account folder under the resolved content root (`gtm_core.paths.resolve_content_root()` →
`content/<active>/accounts/<account-slug>/`). Compose `slides.md` from these inputs:

**`config.yml`** — populate with:
```yaml
deck:
  name: "[company]-[deck-type]-[date]"
  title: "[deck headline from slide 1]"
  subtitle: "[PROFILE deck_speaker] · Prepared for [persona name], [company]"
  exportFilename: "deck-[company]-[date]"
  defaultSubBrand: "[PROFILE default_product, e.g. agent-gateway]"
  aspectRatio: "16/9"
  canvasWidth: 980

speaker:
  # Default = brand attribution. Only use the colleague's personal name if deck_byline: name.
  name: "[deck_byline=brand → PROFILE brand_name; deck_byline=name → PROFILE name]"
  title: "[deck_byline=brand → PROFILE deck_speaker; deck_byline=name → PROFILE title]"
  shortBio: "[brand: one-line product framing from profiles/<active>/knowledge/product.md; name: one sentence from PROFILE voice/voice.md]"

event:
  name: "[company] · [PROFILE default_product name] [deck type]"
  location: "Remote"
  date: "[month year]"
  format: "Discovery call"
```

**`voice.md`** — populate from PROFILE `name`, `title`, and the personality framing in icp-personas.md for the selected template. Match register to persona: precise/evidence-first for legal (A5), technical-direct for engineers (A8), strategic for platform/AI leaders (A6/A10).

**`recent-news-factoids.md`** — pull the 4–6 most relevant recent signals for this account's industry and market (from the latest radar/market-scan output or `profiles/<active>/knowledge/`). Format: one signal per bullet with date and source.

**`outline.md`** — write the full slide-by-slide content spec. For each slide:

```markdown
## Slide N — [slug]

**Core message:** [one sentence]
**Layout:** [cover / content / split / table / cards / closing]
**Click count:** [N] (optional — for animated reveals)
**What to say:**
- [bullet 1]
- [bullet 2]
**Visual notes:** [layout component hints; include bg reference if applicable, e.g. "full-bleed bg (inputs/images/bg-title.png), HeroTitle layout"]
**Source references:** [inputs/recent-news-factoids.md / profiles/<active>/knowledge/product.md / etc.]
```

After writing all input files, **compose `slides.md` directly** from them (the brain's job — apply
the layout/component catalog from `profiles/<active>/knowledge/deck-composer.md`). Write `slides.md`
into the same account folder (`content/<active>/accounts/<account-slug>/`), named exactly
`slides.md`. Do **not** add a `theme:` field — the sidecar pins it. There is no `compose.js` step on
the sidecar path. Then render via the deck MCP tool (Step 7b).

> **Runtime note (headless/VPS).** The Slidev/Playwright deck toolchain (`node`, `npm`, `npx`, the
> Slidev deck workspace) is **not present in the headless VPS environment**. The following are
> all denied by the least-privilege policy and will **never** succeed regardless of rephrasing:
> - `node`/`npm`/`npx` commands
> - `python3 - <<'PY'` heredoc (stdin code execution — same policy floor as `python -c`)
>
> If any of the above is denied, **STOP — do not retry it** (retrying only burns budget and changes
> nothing). Produce the deck **source** (`slides.md` + input files) and hand off: tell the operator
> "deck build/export must be run locally — run it from your Terminal." This note applies to every
> shell command in Steps 7 and 7b below.

> **Operator-run fallback (only when the deck tool is absent).** If the `deck` MCP tool isn't
> available (sidecar not configured → `deck_renderer_url` unset → the `deck` server is omitted), fall
> back to a local build/export by the operator: hand off the deck **source** (`slides.md` + input
> files) and tell the operator to run it from their Terminal in their local Slidev deck workspace (e.g. `3-Build Deck.command`, or `node scripts/compose.js decks/[deck-name]`). Do **not**
> attempt `node`/`npm`/`npx` yourself — they are denied by the least-privilege policy; retrying only
> burns budget. This fallback applies to Steps 7 and 7b below.

---

## Step 7b — Export (Mode B default = PowerPoint, not PDF)

**Default deliverable is `.pptx`.** A raw Slidev PDF rasterises every slide's
ambient blur/gradient/canvas layers at full DPI → ~1.5 MB/slide (a 13-slide
deck is ~20 MB and painfully slow to open). The sidecar's `pptx` format flattens
the deck to compressed images natively — ~30× smaller and instant to load — so
there is no manual `slidev export` → `pdftoppm` → python recipe to run.

Render by calling **`mcp__deck__export_deck`**:

- **PPTX (default):** `mcp__deck__export_deck(slides_md_path=<abs path to slides.md>, format='pptx')`
- **Editable/vector PDF (only if the user explicitly asks):**
  `mcp__deck__export_deck(slides_md_path=<abs path to slides.md>, format='pdf')`

`slides_md_path` is the absolute path to the `slides.md` you wrote in Mode B, under
`content/<active>/accounts/<account-slug>/`. The tool returns the output path. **On a `[deck-error]`
string, DO NOT retry** — surface it to the operator and use the operator-run fallback above.

---

## Step 8 — Output

Tell the colleague:
- **Mode A:** "Deck saved as `deck-[company]-[type]-[date].pptx` in the account folder `content/<active>/accounts/<account-slug>/`." Offer to export as PDF or adjust any slide.
- **Mode B (default):** "Deck saved as `deck-[company]-[type]-[date].pptx` (flattened, fast-loading) at the path the deck tool returned, in the account folder `content/<active>/accounts/<account-slug>/`. Editable `slides.md` lives beside it — edit it and re-run `mcp__deck__export_deck` after changes." Offer to adjust any slide content.

Then append a `⟦FILE:…⟧` sentinel for the .pptx so the Telegram cockpit delivers it automatically:

```
⟦FILE:/absolute/path/to/deck-[company]-[type]-[date].pptx⟧
```

Put this on its own line at the very end of your response, after all prose.

---

## Guardrails

- **Never fabricate** customer quotes, statistics, or case-study outcomes not in `case-studies.md`.
- **Claim only what's enforced (product capabilities).** Tag every product capability claim to the `CapabilityCoverage` matrix (Enforced / Simulated / Design-target). Never present a Design-target as live; never present a caveated enforcement point as product-native. Lead with the real differentiators from the product's reference pack. The product gives audit-ready **evidence** — it is not itself certified against the frameworks it evidences.
- **Never skip the HITL gate** unless the user explicitly bypassed it.
- **Outline before images** — generate Higgsfield backgrounds only after outline is approved.
- **Slides sparse** — 3–4 bullets max per content slide. Split if longer.
- **No invented claims** — every factual claim must trace back to a knowledge file or a source in `recent-news-research.md`.
- **Mode B fallback** — if the `deck` MCP tool returns a `[deck-error]` or is absent (sidecar not configured), do not retry: either hand off `slides.md` for the operator-run local render (see the fallback note in Mode B) or fall back to Mode A and note it.
