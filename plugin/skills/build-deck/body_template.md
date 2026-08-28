
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

## Step 1.4 — The account dossier is binding, not background

If an `account-dossier-[company]-*.docx`/`dossier-spec.json` exists in the account folder, it is
**not** optional colour — it decides the register, the guardrails and every question the deck is
allowed to ask. Read it before writing a single headline. Three blocks bind:

1. **"WHO [buyer] IS — AND HOW TO ENGAGE" + the DO / DON'T two-column.**
   - The DO column sets the **register**: which argument frame this buyer answers to. A buyer who
     has never once raised governance, model risk or auditability in public will not buy risk
     reduction — the same facts have to arrive as throughput, growth, or differentiation.
     **State the register explicitly in the Step 5 gate, with the dossier line that justifies it.**
   - Every DON'T becomes a deck-wide guardrail, checked mechanically by `deck_lint` D3 (it compiles
     the column into banned patterns). These are the traps that lose a room: congratulating a buyer
     on an award a competitor won, claiming a platform has no governance to someone who sits on its
     advisory board, asserting a regulatory deadline that does not exist.
2. **"QUESTIONS TO ASK".** This is the **only** source of on-slide questions (see Step 4.4). If the
   deck needs a question the bank does not have, write it into
   `deck-questions-[company]-[date].md` in the account folder **with its provenance** — an
   extension a reviewer can see, rather than an invention nobody checked.
   **Open that file with a `**Register.**` paragraph** naming the frame every question in it must
   hold, and the dossier line that justifies it. One sentence, before the table. No gate can check
   the tone of a question, so the register has to be written down where the next author — or the
   next you, three weeks on — reads it before adding a row. A deck loses a room by asking a
   risk-framed question of a growth-framed buyer far more often than by asking a bad one.
   Keep the file's **slide numbers current**: `deck_lint` prints where the questions actually are
   on every run, precisely because reordering slides silently invalidates them.
3. **"HONESTY NOTES".** Each line becomes a presenter-note boundary (⛔ never say / ⚠️ verify first).
   These are the claims that would be found out in month two.

If no dossier exists, say so at the gate — the deck can still be built, but it is being written
without a read on the buyer, and the register is a guess.

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

**Cohort dossier — fallback only, never a co-equal source.** If the profile ships `knowledge/use-cases/`
and the account's workflow matches one of the cross-org cohorts in `knowledge/use-cases/README.md`'s
table, its **§10 (deck-ready block)** is a ready-shaped talking-point angle you may use to fill a slide
**only where the deck-research dossier (above) is absent or thin for that slot** — never let it override
or conflict with an existing Layer-2 fill. **Check freshness first:** past its "On refresh (Nd)" window,
treat its claims as unconfirmed and say so in the deck's source footer rather than presenting them as
current; never upgrade a `Flagged`/`(~unverified~)` tag. Every claim pulled from it still passes through
Step 1.6's capability-coverage re-tagging like any other source.

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
7. **Slide budget** — one slide per four minutes of meeting (60 min → **15 slides**), and **no
   appendix**: if the room may want framework detail, keep a trigger index in the account folder
   and pull it up on demand. `deck_lint` D6 enforces both. A deck that runs long is a deck the
   buyer stops talking in, and their talking is the point.
8. **Word budget — ~45 words a slide, and the notes carry the rest.** A slide is a prompt for a
   conversation, not a document read aloud. `deck_lint` D10 warns past 55 words and errors past 90,
   counting component props (`AskBox question=`, `:tests`, `:layers`) as on-slide text, because
   that is what the audience reads. The cut is not deletion: **every sentence you take off a slide
   moves into the presenter notes**, prefixed `SAY, don't print:` — the argument survives intact,
   it just stops competing with you for the room's attention. Notes getting longer as slides get
   shorter is the intended trade, not a regression. What never gets cut to hit the budget: sourced
   quotes, regulator language, standard numbers, and the dossier's questions — that is the
   credibility layer, and it is the reason the deck is believed.
9. **Argue from the buyer's unit of work, not yours.** Read every slide that names a cost the
   buyer bears and draft the version that names an **asset they would own** instead, then compare.
   "The template can't carry the policy" and "the policy layer is a library nobody has built" are
   the same fact; only the second tells him what to build, and only the second survives an
   architect. The same move applies to questions: "how much bigger is that engagement?" asks him to
   price *our* interest, "how heterogeneous is the stack you actually meet?" asks about his week.
   **When the audience-framed version also turns out to be the more accurate one, that coincidence
   is the tell that you have found the real argument** — a slide that only works from our side is
   usually a slide resting on something we have not checked. Do this once at the outline, before
   any of it is drawn; re-framing a slide after its diagram exists costs a component rewrite.

---

## Step 4.4 — Place the questions (a deck that asks nothing is a broadcast)

The deck is a qualification instrument. Questions go **on the slides**, at the beat where each one
lands — not gathered on the closing ask, where they read as an interrogation and arrive after the
argument is already over.

- **One question per chapter beat, one per slide, none on the CTA.** By the time the ask is up you
  should already have the answers, and the close is stronger for quoting them back.
- **Every question comes from the dossier's bank** (Step 1.4) or from the deck's own
  `deck-questions-[company]-[date].md`, which records each question with its provenance.
- **Frame every question in the buyer's register.** The same question can be asked as exposure or
  as upside; a growth-register buyer will answer one and deflect the other. "How many agents are
  stuck waiting on sign-off?" and "how many of the agents you've shipped are in production versus
  pilot?" surface the same number — only one of them is a question a proud CEO enjoys answering.
- **Prefer questions the buyer can answer from memory**, about their own work. A question that
  needs research gets a promise to follow up, which is not an answer.
- Use `<AskBox question="…" />`. `deck_lint` D2 enforces placement, count and sourcing.

---

## Step 4.5 — Plan the visuals (before any image is generated)

**A generated image cannot explain a mechanism. Only a diagram can.**

This is the rule the whole step now hangs off, and it was learned the expensive way. An August
2026 partner deck put a generated image on six argument-carrying slides, deleted the prose those
slides had used to make their case — because the image was "carrying" it now — and shipped
something the reviewer described as *"background photos and a couple of headline points."* Every
gate passed. D7 saw a visual, D10 saw a short slide. What the images actually did was restate the
headline in metaphor: a queue of cubes behind a gate is a picture of the sentence "few get signed
off". It adds nothing a reader can act on.

The failure has a precise shape, so it has a precise rule:

> **Never delete explanatory content because an image is present.** Content leaves a slide in
> exactly one direction: into a *diagram* that holds the same information as structured labels, or
> into the presenter notes. It never evaporates into a picture.

Three tiers, in this order.

1. **Diagrams as code — the default for anything that carries an argument.** Free, vector in the
   PDF, themeable, correctable, and every label stays real text. Walk the *content shape →
   component* table at the top of `references/slide-library.md` and route each argument slide to
   the component matching its shape:

   | The slide's shape | Component |
   |---|---|
   | One request, two organisations, and the wall between them | `EntityCrossing` (`state="broken"` / `"resolved"` — one drawing, two states, which is the mirror) |
   | A request crossing a boundary, and the checks it must pass | `FlowSequence` (same two states; use `EntityCrossing` when the two sides are separate companies) |
   | Two spans over the same entities, and the gap between them | `ScopeMap` |
   | A wide intake narrowing through one gate | `GateFunnel` |
   | What carries across engagements vs what restarts each time | `ReuseTrack` |
   | Self-attested vs verifiable by a counterparty | `ProofContrast` |
   | Requirements, each carrying its dated source | `ReqAnchors` |
   | A sequence · layers · before/after · tests · an audit record | `FlowTrack` · `StackDiagram compact` · `DuoGrid` · `BreakTests` · `TerminalWindow` |

   If a slide is a wall of prose, it is a slide whose shape you have not identified yet. If none of
   these fit, write a new SVG component in the theme — that is cheaper than a paid render and it
   never has to be verified by eye.

   **Draw the whole picture, not the flattering half.** A schematic persuades because it reads as a
   statement of fact, which is exactly why a partial one is worse than a paragraph: it asserts
   completeness it does not have, and the one person in the room who knows the omission will say so.
   So a review funnel shows the checks that already pass (`state: 'clear'`), a reuse track shows the
   band that genuinely does carry, and a requirements strip lists the requirements your product
   does not answer. The concession is not a cost — it is what makes the gap on the same slide
   credible. When a slide's argument is "here is what is missing", the first thing to draw is what
   is not.

   **Then audit the concession for the same fault.** A corrected diagram can still overclaim in a
   subtler way. *Not packageable is not the same as not reusable*: a delivery mechanic tells you how
   something ships, never how often it is genuinely rebuilt. Before drawing "they redo this every
   time", find the real unit of repetition — work that repeats per *client* and work that repeats
   per *pattern* are different quantities, and the second is usually far smaller. If the thing
   clusters by use case, jurisdiction, or industry, it is a library, and the honest slide names the
   library they should own rather than billing them for a rebuild they do not actually do. That
   version is both more accurate and the better offer, which is the tell that you found the real
   argument.

2. **Generated imagery — chapter dividers and the cover. That is the whole list.** These carry no
   argument, so an atmospheric image costs nothing there and gives the deck its series identity.
   Plan them as a set from one style seed. **Do not put a generated image on a slide that is making
   a point** — if you think one belongs there, the real finding is that the slide's diagram has not
   been drawn yet.

3. **Anything with text baked into pixels — gated and verified.** Pin every label in a spec at the
   plan gate *before* any paid call, then run a **vision accuracy check** against that spec before
   the image may ship: look at the generated image and confirm each label is spelled correctly and
   says what the spec says. Precise text — regulator names, standard numbers, quotes, statistics,
   product names — never goes in pixels at all; it stays in the DOM. A garbled regulator name in
   front of a buyer costs more than the text it saved. You cannot see the image you just generated
   unless you go and look at it.

State the plan in the Step 5 gate as one line per visual: purpose · slide · component *or* label
spec · prompt intent.

---

## Step 5 — HITL approval gate (always required)

**Do not generate images or render the deck until the outline is approved.**

**Approve the argument, not the titles.** A title list cannot be rejected on the thing that
actually goes wrong: a deck pitched in the wrong register, resting on a claim nobody can defend,
or asking questions the buyer will not answer. Every round of "this feels weak / this feels
generic" is a round the gate should have caught. So the gate shows the **spine**:

```
## Outline for review — [Company] · [Template] · [Mode]

**Register:** [throughput / growth / differentiation / risk] — because [the dossier line that says so]
**The ask:** [what we are asking for, in one sentence — and what makes it symmetric]
**Budget:** [N] slides for a [M]-minute meeting · no appendix
**Visual spine:** [image 1 purpose] · [image 2 purpose] · … (or: none — diagrams only)

| # | Slide | The claim | Rests on | Asks |
|---|---|---|---|---|
| 1 | [title] | [what this slide asserts] | [source / knowledge file / dossier line] | — |
| 4 | [title] | [assertion] | [source] | [the on-slide question] |
...

**Claims needing a precedent or a caveat:** [outcome claims · regulatory status · borrowed terms]
**Output:** [Mode A: deck-[company]-[type]-[date].pptx] OR [Mode B: slides.md → `mcp__deck__export_deck` in `content/<active>/accounts/<account-slug>/`]

→ Reply **"go"** to proceed, or tell me what to change.
```

The "Rests on" column is the honest one: if a row reads "our own framing", that slide is an
opinion, and the reviewer deserves to see that before it is rendered in a typeface.

Exception: if the user said "just build it", "go for it", or any equivalent bypass phrase — skip the approval gate and proceed directly.

---

## Step 6 — Build the diagrams, then generate the atmosphere

**Diagrams first, and they are the part that matters.** Write every component the Step 4.5 table
routed to, wire the real content into its props, and look at each one in the dev server before
moving on. A diagram slide is finished when someone who has not read the notes can point at the
picture and say what breaks and why. This step costs nothing and needs no provider.

Then — and only for dividers and the cover — generate imagery via Higgsfield **as a series**,
using the canonical prompts and palette from `profiles/<active>/knowledge/brand-notes.md` (section:
"Higgsfield Background Images"). Backgrounds (`bg-title.png` · `bg-problem.png` · `bg-cta.png`) are
the baseline. A `problem-resolution-mirror` is served by **one component in two states** —
`EntityCrossing` when the two sides are separate organisations, `FlowSequence` otherwise — never by
a generated before/after pair. Same geometry, same labels, only the marks and the path change,
which is a parallel no two renders can hold.

**Rules that come from things that went wrong:**

- **One seed, one series.** Generate the spine in a single batch with a shared style sentence.
  Images from different sessions read as clip art next to each other; a sealed-towers image and a
  bridged-span image from the same batch read as one argument.
- **Wordless.** No text in the prompt, none expected in the output — the model cannot spell
  reliably at this size, and text in pixels cannot be re-themed, translated, or corrected.
- **Cost preflight, every time.** `get_cost` before the call, checked against the PROFILE budget
  cap. Generation is metered and the **daily cap is real** — it has blocked a requested image
  mid-build.
- **On refusal or cap:** do not silently drop the visual. Reuse the nearest image from the existing
  series, and record in `IMAGES.md` what was intended and what to re-roll when the cap resets.
- **Prompt hygiene:** avoid phrases that trip the provider's safety filter on ordinary subjects —
  "financial district" has produced a false positive on architectural photography. Reword rather
  than retry.
- **Write `VISUALS.md`** into the deck folder, diagrams first: for each argument slide, the
  component carrying it and what it draws; then for each generated image, the slide it serves, the
  full URL, and the **localisation steps** (save into `inputs/images/`, switch the `image:` line to
  the local path). A deck presented offline without that last part falls back to a flat background
  mid-meeting.

Save to `inputs/images/` inside the deck folder (Mode B) or to the outputs directory (Mode A).

If Higgsfield is unavailable or the user declined imagery, **nothing about the argument changes** —
the dividers get colour fills per `profiles/<active>/knowledge/brand-notes.md` and every explanatory
slide is untouched, because none of them depended on a generated image in the first place.

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

**Default deliverable is `.pptx`.** The sidecar's `pptx` format flattens the deck to
compressed images natively — small and instant to load, no manual `slidev export` →
`pdftoppm` → python recipe to run. A Slidev PDF export is a legitimate ask (the user
may want an editable/vector artifact) and, since the 2026-08-14 theme fix, no longer
bloats itself — the deck-theme used to rasterize its own ambient/blur/gradient-text
layers with no print-mode fallback (`gtm_core.deck_lint --theme`, D9, now catches
that pattern before export); a stale/reverted theme can still regress, which is what
`deck_export_verify`'s bytes-per-page ceiling backstops.

Render by calling **`mcp__deck__export_deck`**:

- **PPTX (default):** `mcp__deck__export_deck(slides_md_path=<abs path to slides.md>, format='pptx')`
- **Editable/vector PDF (only if the user explicitly asks):**
  `mcp__deck__export_deck(slides_md_path=<abs path to slides.md>, format='pdf')`

`slides_md_path` is the absolute path to the `slides.md` you wrote in Mode B, under
`content/<active>/accounts/<account-slug>/`. The tool returns the output path. **On a `[deck-error]`
string, DO NOT retry** — surface it to the operator and use the operator-run fallback above.

---

## Step 7c — Definition of Done (three gates; none of them is "it looks fine")

A deck is not done because it rendered. Run these in order and report the results — every one of
them exists because its failure shipped into a review.

**1 · Lint — runs everywhere, including headless.**

```
uv run python -m gtm_core.deck_lint <slides.md> \
    --dossier <account-dossier-spec.json> \
    --inputs <deck inputs/ dir> \
    --template <A1–A10> --minutes <meeting length>
```

Errors block. Warnings are judgement calls — read them, then either fix or say why not. A rule
that is genuinely wrong about a slide can be suppressed with `<!-- lint-ok D4: reason -->` inside
that slide; naming the tier and the reason is the price of the exemption.

**2 · Fit probe — local only (needs node + chromium; both are denied on the VPS).**

```
node scripts/deck_fit_probe.mjs <deck-dir>
```

Walks every slide at its **max click count** and measures the real DOM against the clipping
boundary. `✗` is a clipped slide with a screenshot to prove it; `!` is under 24px of headroom —
one edit from breaking. This is the authority: where it disagrees with the linter's D1 estimate,
the probe is right. **A `v-click` element still occupies its box, so clicks never buy space.**

> ⛔ **A SCREENSHOT IS NEVER EVIDENCE ABOUT LAYOUT. Measure the DOM.**
> This probe exists because six banners shipped clipped when the only check was someone looking
> at pictures — and the lesson still had to be re-learned three more times on one deck, where
> captures showed SVG labels displaced from their rects, whole text nodes missing, and two
> consecutive captures of an unchanged DOM disagreeing with each other. Every time, measurement
> showed the render was correct. The scaled canvas and transformed SVG text do not survive a
> downscaled capture reliably. Do not conclude a component is broken from an image, do not
> "fix" one, and **never hand a screenshot to the user as proof that a slide fits.**
>
> The probe covers slide overflow. For the case it does not — *is this label inside the box it
> belongs to?* — compare each text node's `getBBox()` against its container's y-range:
>
> ```js
> [...document.querySelectorAll('.my-diagram svg text')].map(t => {
>   const b = t.getBBox()
>   return { txt: t.textContent.trim().slice(0, 24), y: b.y, bottom: b.y + b.height }
> })
> ```
>
> Predicted screen position is `svg.getBoundingClientRect().top + viewBoxY * scale`, where
> `scale` is the CSS custom property `--slidev-slide-scale`. If measured matches predicted, the
> drawing is correct no matter what the picture looks like. (A related trap: Slidev can compute
> that scale as **0** when the pane mounts at 0×0, which makes every downstream capture garbage
> while the DOM stays perfectly valid — check the scale before believing anything visual.)

**3 · Export verify — the export can succeed and be empty, or succeed and be bloated.**

```
uv run python scripts/deck_export_verify.py <deck.pdf> --slides <slides.md>
```

Page count, a bytes-per-page **floor** (a build racing an export once produced a 15-page, 4 KB
PDF that reported success) and **ceiling** (the same deck later exported at 1633 KB/page from
theme CSS Chromium's print path could only rasterize — `filter`, `backdrop-filter`,
`background-clip: text` with no print-mode fallback), and embedded ink. A ceiling trip points at
the theme, not the deck's content — see gate 4 below. If you authored a **new** theme component
(rare — Step 4.5 normally routes to an existing one), also run:

```
uv run python -m gtm_core.deck_lint --theme .engine/deck-theme
```

before calling it done — this is D9, the static check that catches the CSS pattern before anyone
exports a deck with it.

On the headless path, gates 2 and 3 belong to the operator: hand off `slides.md` and say plainly
which gates you ran and which you could not.

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
- **A generated image never carries an argument** — dividers and the cover only. An argument slide
  gets a diagram component or it is not finished (Step 4.5, D7 `image is doing the explaining`).
- **Never cut prose because a visual appeared** — content moves into a diagram's labels or into the
  presenter notes, never into nothing. A slide can fail by saying too little just as easily as too
  much (D10 `under-explained`).
- **Slides sparse** — 3–4 bullets max per content slide. Split if longer.
- **No invented claims** — every factual claim must trace back to a knowledge file or a source in `recent-news-research.md`.
- **Mode B fallback** — if the `deck` MCP tool returns a `[deck-error]` or is absent (sidecar not configured), do not retry: either hand off `slides.md` for the operator-run local render (see the fallback note in Mode B) or fall back to Mode A and note it.

### The four claim classes (`deck_lint` D4 checks all of them)

Product capabilities are covered by the matrix above. These four are the ones that slipped past
it, each on a real deck:

- **Outcome claims** — "shorter security review", "more margin", "higher win rate". A claim about
  what happens to the buyer's business needs a **precedent** in the presenter notes (`PRECEDENT: …`
  or a source link) or it must be reworded as a question the buyer answers themselves. Say what the
  design does; let them draw the conclusion.
- **Outcome accuracy** — say what actually happens. Security review rarely *kills* an agent; it
  **descopes** it, and the descoped version ships without the capability that justified the build.
  The precise version is both more defensible and more alarming.
- **Regulatory claims** — a slide naming a framework carries its binding status in the notes
  (voluntary · non-binding · no deadline · consultation not yet issued). **Never sell on "the
  regulator requires this."** A compliance officer who reads their own regulator will correct you,
  and the room is gone.
- **Quotes and borrowed terms** — a quoted string must exist **verbatim** in the deck's `inputs/`,
  and a borrowed term must be attributed on the slide. Two regulator phrasings and one buyer quote
  reached slides on one partner deck without existing anywhere; that is what the check is for.

### Questions and visuals

- **A deck that asks nothing is a broadcast** — questions on the slides, from the dossier's bank,
  one per beat, none on the CTA (Step 4.4, D2).
- **A slide of prose is a slide whose shape you haven't found** — route it through the content
  shape → component table before writing another paragraph (Step 4.5, D7).
- **~45 words a slide.** Past that you are asking the buyer to read while you talk, and they will
  do neither. Cut into the presenter notes, never into the sourced evidence (Step 4 item 8, D10).
- **Never bake precise text into a generated image.** Short uppercase labels are fine and make an
  image explanatory; regulator names, standard numbers, quotes and statistics stay in the DOM
  where they are crisp and correctable. Every label-bearing image passes a vision check first.
