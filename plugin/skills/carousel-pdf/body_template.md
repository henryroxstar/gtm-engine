
# `carousel-pdf` Skill

Produce a professional LinkedIn carousel — a **4:5 portrait PDF document post** — from any topic, market signal, or source document. Dark-cinematic brand, the active company's design system, Slidev engine. Every carousel ships with a full publish package: PDF + PNGs + caption + trigger word + DM copy.

> Resolve the **active profile** (the agent provides it; everything company-specific loads from `profiles/<active>/`, never from `plugin/`). Read brand identity from `profiles/<active>/PROFILE.md` (`brand_name`, `social_handle`, `deck_byline`) and all content facts from `profiles/<active>/knowledge/`.

---

## When to use this skill

- User says: *"make a carousel about X"*, *"turn this blog into a carousel"*, *"lead-magnet PDF for X"*, *"post about [topic] from the knowledge pack"*, *"make a light carousel"*, *"make a myth-bust carousel"*, *"make a how-to carousel"*, *"make a case-study carousel"*, *"make a framework carousel"*
- `market-scan` output lists a carousel as an output type for a signal
- User wants a LinkedIn document post on any product, use-case, or industry angle
- A `builder-studio` article needs repurposing (*"make a carousel from this article"*): treat
  `content/<active>/journey/assets/<item-id>.article.md` as the source document — same facts,
  one more surface; every claim stays traceable to the article's evidence pack

**Arc shapes available** (see `references/sample-carousels.md`): myth-bust · how-to · case-study · framework · default/product-led

**Theme options**: dark (default, cinematic) · light (white background — set `colorSchema: light` in the global frontmatter; see `references/light-theme.md`)

### What this skill does NOT do

- Does NOT auto-send DMs or post to LinkedIn (draft only — human gate required)
- Does NOT generate images/visuals (handled by `carousel-visuals` skill)
- Does NOT invent product facts — all claims pulled from `profiles/<active>/knowledge/`

---

## Workflow — exact sequence

### Step 0 — Read current product facts

The carousel must be built on current product facts, not stale snapshots. There is **no sync
step** on the sidecar render path: the brain composes every card's copy inline from
`profiles/<active>/knowledge/` (the catalog the next step reads), and the deck-renderer sidecar
bakes the deck-theme into its own image. Nothing to refresh — go straight to reading the sources.

### Step 1 — Read the knowledge sources

Mandatory reads before drafting any copy:

| File | What to pull |
|---|---|
| `profiles/<active>/knowledge/product.md` | Feature names, pain owners, solution themes, discovery questions |
| `profiles/<active>/knowledge/case-studies.md` | Proof points — verbatim quotes, metrics, customer names |
| `profiles/<active>/knowledge/icp-personas.md` | Buyer context — who this carousel is for |
| `profiles/<active>/knowledge/company.md` | Brand voice, tagline, language conventions, "never say" list |
| `references/platform-playbook.md` | Platform mechanics + attention craft — hook formulas, swipe momentum, CTA compliance, retention curve. The shared authority for every LinkedIn/X fact below. |

**Never invent product claims.** If a stat or feature name isn't in `profiles/<active>/knowledge/`, omit it or ask.

### Step 2 — Choose theme and arc shape

**Theme:** Ask (or infer from context) whether the colleague wants **dark** (default, cinematic) or **light** (white background, editorial). Default to dark if unspecified.

- Dark: `colorSchema: dark` in global frontmatter. No other changes.
- Light: `colorSchema: light` in global frontmatter. The sidecar's baked deck-theme honours it — see `references/light-theme.md` for the light palette details.

**Arc shape:** Check `references/sample-carousels.md` for the four arc templates (myth-bust, how-to, case-study, framework). If the topic maps clearly to one shape, say which shape you're using and proceed. If ambiguous, ask: "This could work as a [myth-bust] or a [framework] — which angle do you want?"

### Step 3 — Draft the copy arc (show for approval before rendering)

From the topic and chosen shape, draft:
1. **Three hook options** (different formulas — pick from the six-shape library in `references/platform-playbook.md` §4: factual gap, contrarian, numbered promise, outcome + timeframe, stop-doing-X, how-[entity]-does-it). Judge each against the 3-second test. See `references/sample-carousels.md` for shape-specific templates, and `docs/hook-craft.md` for the cross-surface discipline every option must still pass: zero-context self-containment, one real fact from `profiles/<active>/knowledge/`, and a belief (never a named vendor) as any fault-line.
2. **The arc**: 8–9 cards following the chosen shape from `references/sample-carousels.md`. Apply the momentum rules (playbook §5): ≤50 words per card, every middle card ends with a forward pull, re-hook at card 4–5.
3. **The close mode** (playbook §2): **default = compliant close** (recap + ONE ask — save, follow, or a genuine question — + identity block). Offer the **trigger-word lead-magnet close** ("Comment [WORD] → DM") only as an explicit opt-in when the colleague wants lead capture — auto-pick the word (`TRUST` / `GATEWAY` / `AGENTS` / `IDENTITY` / `FABRIC` / `PROOF`, logic below) and note the cadence cap (≤1 lead-magnet post per 3–4 weeks, manual DM fulfilment only).
4. **Per-card summaries** (one line each: card role, headline, component/layout, and the card's forward pull)

Show this draft arc to the user. **Do not render until the arc is approved.** This is the most important step — the script is the product.

#### Default arc (standard/product-led — use when no shape fits cleanly)

```
Card 1  — HOOK     : [hook option chosen]
Card 2  — PROBLEM  : [specific pain — mapped to a persona]
Card 3  — RISK     : [consequence if unaddressed]
Card 4  — STAT     : [hard evidence — from profiles/<active>/knowledge/, not invented]
Card 5  — BRIDGE   : [reframe — "this isn't X, it's Y"]
Card 6  — SOLUTION : [product answer — feature name + one sentence]
Card 7  — HOW      : [mechanism — one proof point or detail]
Card 8  — QUOTE    : [verbatim customer voice — from profiles/<active>/knowledge/case-studies.md]
Card 9  — CLOSE    : [recap + ONE ask + identity block — or Comment TRIGGER → promise in lead-magnet mode]
```

For myth-bust, how-to, case-study, and framework arcs — use the templates in `references/sample-carousels.md` instead.

Minimum 8 cards, maximum 10. Default: 9.

### Step 5 — Compose the deck source

Create the carousel working folder **under the resolved content root** (resolve it via
`gtm_core.paths.resolve_content_root()` — never the repo root, never the deck workspace):
`content/<active>/carousels/<topic-slug>/`. Write into it:
- `slides.md` — the approved arc, composed with the layout conventions below. **The file must
  be named exactly `slides.md`** (the deck-renderer tool requires that name). No `package.json`
  and no scaffold step — the sidecar bakes the deck-theme; the brain only authors `slides.md`.
- `outputs/` — for caption.md, dm.md

In the global frontmatter of `slides.md`, set `colorSchema` to match the chosen theme:

```yaml
colorSchema: dark   # default — dark cinematic
# OR
colorSchema: light  # white background — honoured by the baked deck-theme (see references/light-theme.md)
```

Do **not** add a `theme:` field — the deck-renderer sidecar pins the theme to its baked copy at
render time.

**Critical structure rule**: No bare `---` separators between cards. Each slide's frontmatter block (`---\nlayout:...\n---`) IS the separator. See `references/layout-conventions.md`.

### Step 6 — Render (export via the deck MCP tool)

Render by calling the deck-renderer sidecar — **this is the render path**; the brain never runs
`node`/`npm`/`npx`.

Call **`mcp__deck__export_deck`** with:
- `slides_md_path` = the **absolute path** to the `slides.md` you wrote in Step 5 (e.g.
  `<content-root>/<active>/carousels/<topic-slug>/slides.md`)
- `format='pdf'` → the carousel PDF (the post deliverable)

For per-slide PNGs (social preview), call it again with `format='png'`.

The tool returns the output file/folder path. **On a `[deck-error]` string, DO NOT retry** —
surface it to the operator verbatim. No Chromium install, no `npm` — the sidecar owns the
toolchain.

> **Operator-run fallback (only when the deck tool is absent).** If the `deck` MCP tool is not
> available — the sidecar isn't configured (`deck_renderer_url` unset), so the `deck` server is
> omitted — fall back to a local export by the operator. Hand off the deck **source** (`slides.md`)
> and tell the operator to render it from their Terminal in their local Slidev deck workspace.
> Do **not** attempt `node`/`npm`/`npx` yourself — they are denied by the least-privilege policy;
> retrying only burns budget. The local commands (operator runs these, not the brain):
>
> ```bash
> # From the operator's local Slidev deck workspace directory:
> npm run export -w "@<deck-scope>/<deck-slug>"                                    # → outputs/carousel.pdf
> npm run export -w "@<deck-scope>/<deck-slug>" -- --format png --output outputs/slides   # per-slide PNGs
> npx playwright install chromium   # first export only — install headless Chromium once
> ```

### Step 7 — Publish package

After export, write to `outputs/`:

| File | Contents |
|---|---|
| `caption.md` | 2 caption variants: Hook-led (cold audience) + Story-led (warm audience). First ~140 chars carry the hook; close with a genuine question; **0–3 hashtags** (never stacked); alt-text line for the cover. |
| `dm.md` | **Lead-magnet mode only.** Delivery DM + one follow-up. Draft only — fulfilment is manual, never automated. Omit this file in the default close. |

See `references/script-conventions.md` for copy rules and `references/platform-playbook.md` §7 for caption mechanics.

Then append `⟦FILE:…⟧` sentinels so the Telegram cockpit delivers the PDF automatically:

```
⟦FILE:/absolute/path/to/content/<active>/carousels/<topic-slug>/outputs/carousel.pdf⟧
```

Put this on its own line at the very end of your response, after all prose. Use the real resolved absolute path.

---

## Layout catalog (carousel family)

All layouts live in `.engine/deck-theme/layouts/`. They are **additive** — keynote decks are untouched.

| Layout | Use for | Key elements |
|---|---|---|
| `carousel-hook` | Card 1 — opening (every carousel) | Full-bleed ambient, oversized h1 (5.5rem), sub-headline, swipe prompt |
| `carousel-point` | Cards 2–3, 5–7 — value & bridge | Numbered pill, h2 (3.4rem), body (1.25rem), optional left accent bar |
| `carousel-stat` | Card 4 — proof stat | Giant gradient number (7.5rem), context label, source attribution |
| `carousel-quote` | Card 8 — customer voice | Glass card, quote-body (1.75rem), attribution with — separator |
| `carousel-cta` | Card 9 — close + trigger | trigger-word class (7.5rem gradient), cta-verb, cta-body, cta-promise |

**Persistent footer on every layout** (no extra code needed):
```
[handle]    [cardNo / cardTotal]    [brand symbol]
```
The `[brand symbol]` is the active company's mark (resolve from `profiles/<active>/PROFILE.md` → `brand_name`; brand assets in `profiles/<active>/knowledge/brand/`). Set `handle`, `cardNo`, `cardTotal` in each slide's frontmatter.

### Frontmatter fields (all layouts)

```yaml
layout: carousel-hook    # or carousel-point / carousel-stat / carousel-quote / carousel-cta
page: fabric             # product theme — sets --product-accent, --product-mesh, etc.
handle: '<social_handle>'  # footer left — resolve from profiles/<active>/PROFILE.md social_handle
cardNo: 1                # footer center left
cardTotal: 9             # footer center right
halo: true               # carousel-hook only — show PulseHalo (default: true)
accent: true             # carousel-point only — left gradient bar (default: false)
triggerWord: 'TRUST'     # carousel-cta only — used in caption.md
```

### CSS classes (slot content)

These go in the slide's markdown/HTML content, not the layout itself:

```html
<!-- carousel-hook -->
<span class="eyebrow">Label here</span>
<h1>Big claim</h1>
<p class="lede">Sub-headline</p>

<!-- carousel-point -->
<span class="card-num">02</span>  <!-- or <span class="chip-tag">[Product name]</span> -->
<h2>Point headline</h2>
<p>Body copy. <strong>Emphasis.</strong></p>

<!-- carousel-stat -->
<span class="eyebrow">Label</span>
<div class="divider" />
<p class="stat-number">#1</p>
<h2>Stat headline</h2>
<p class="stat-label">Description</p>
<p class="stat-source">Source: ... · Year</p>

<!-- carousel-quote -->
<div class="glass-quote">
  <p class="quote-body">"Quote text here."</p>
  <p class="attribution">Customer name · context</p>
</div>

<!-- carousel-cta -->
<p class="cta-verb">Comment</p>
<p class="trigger-word">TRUST</p>
<p class="cta-body">...and I'll DM you the <strong>Asset name</strong>...</p>
<p class="cta-promise">↓ Save this for [use case]</p>
```

---

## Copy rules

(Attention-craft authority: `references/platform-playbook.md` §4–§7 — the rules below are the working summary.)

### One idea per card. No exceptions.

If a card needs two ideas, it's two cards. If it needs a paragraph, it's two sentences.
**≤50 words per card** (target 25–40); max 6–8 words per line, broken at phrase boundaries.

### Every card earns the next swipe

Middle cards (2–8) end with a forward pull — an open loop, a transition phrase ("Here's why →",
"That's not the worst part."), or the next visible number in a sequence. Re-hook at card 4–5:
readers decay along the retention curve (playbook §1), so place a second strong beat mid-deck
rather than front-loading everything.

### Headings

- h1 (hook): 3–8 words. Factual, specific, provable. No corporate buzzwords. Must pass the
  3-second test — the cover competes as a feed thumbnail, not a page.
- h2 (point/stat/quote): 5–8 words. Statement, not question (unless the question IS the hook).
- Never generic: "AI is changing everything." Always specific: "Every agent looks like a ghost."

### Language conventions

Match the active company's language/spelling conventions (e.g. British English: `organisations`, `authorised`, `recognise`, `personalised`). Consistent with `profiles/<active>/knowledge/company.md`.

### Never invent stats

Every number must come from `profiles/<active>/knowledge/`. If no stat exists for the topic, use a qualitative claim or a verbatim quote. No fabricated percentages.

### The hook card (Card 1) — six formulas

From the playbook §4 library:

1. **Factual gap**: "X doesn't have Y. Yet." (specific, provable absence)
2. **Contrarian**: "Everyone is building [X]. Nobody is building [what matters]."
3. **Numbered promise**: "5 things your AI governance stack is missing." (specific numbers lift CTR ~36%; odd numbers read credible)
4. **Outcome + timeframe**: "[Customer] fixed [problem] in [timeframe]. Here's how."
5. **Stop doing X**: "Stop [common practice]. Do [better thing] instead."
6. **How [known entity] does it**: "How [recognisable company/framework] handles [problem]."

Always write 3 options (different formulas). Judge each against the 3-second test. Show to user. Pick together.

### The close card (Card 9) — recap + ONE ask

Default close: one-line recap of the takeaway + exactly one ask (save / follow / genuine
question) + identity block. Trigger-word lead-magnet close only on explicit opt-in — see
playbook §2 for the compliance rules. Never stack asks on one card.

---

## Product → page theme mapping

The deck-theme ships six fixed `page:` token sets (engine slots with baked accent colours — the
`data-page` tokens in `references/layout-conventions.md`). Map the active company's products onto
them **by capability**, resolved from `profiles/<active>/PROFILE.md` → `products[]` and
`profiles/<active>/knowledge/product.md` — never by any one tenant's product names:

| Capability of the product the carousel is about | `page:` value |
|---|---|
| Trust / identity / governance (typically the flagship) | `fabric` |
| Safety / moderation / guardrails | `elements` |
| Data / developer platform | `radix` |
| Messaging / payments / transactional | `messaging` |
| Creation / builder tooling | `forge` |
| Corporate / multi-product / platform story | `website` |

Flagship-product carousels default to `page: fabric`; multi-product stories to `page: website`.

---

## Trigger word auto-pick logic (lead-magnet mode only)

Applies **only when the colleague opts into the lead-magnet close** (playbook §2: grey-zone
tactic — ≤1 per 3–4 weeks, manual DM fulfilment, never automated). In the default compliant
close there is no trigger word; the big gradient word on the `carousel-cta` layout carries the
single ask instead (e.g. `FOLLOW`, `SAVE`, or the asset name).

Pick the most on-theme trigger word from:

The words below are **exemplars** — when the active company's own product vocabulary
(`profiles/<active>/knowledge/product.md`) offers a more on-theme single word, prefer it.

| Word | Use when |
|---|---|
| `TRUST` | Identity, governance, compliance, audit — when trust is the thesis |
| `GATEWAY` | Product-led posts for a gateway/proxy-shaped flagship, developer audience — swap for the active product's own category word |
| `AGENTS` | Multi-agent, agentic AI, "what to do about agents" framing |
| `IDENTITY` | DID, verifiable credentials, identity-first positioning |
| `FABRIC` | Full-stack / platform story — swap for the active platform's own name-word |
| `PROOF` | Case study carousels, customer evidence, ROI |

In lead-magnet mode the trigger word must appear on the CTA card AND in both caption variants
AND in dm.md — one word, everywhere identical.

---

## Export — quick reference

Render is one MCP call (see Step 6 for full detail):

- **PDF (the post):** `mcp__deck__export_deck(slides_md_path=<abs path to slides.md>, format='pdf')`
- **Per-slide PNGs:** `mcp__deck__export_deck(slides_md_path=<abs path to slides.md>, format='png')`

Returns the output path; on a `[deck-error]` string DO NOT retry — surface it to the operator. The
operator-run local fallback (`npm`/`npx` in their Slidev deck workspace) applies **only** when
the `deck` tool is absent — see Step 6.

---

## Example carousel (validated reference)

`content/<active>/carousels/ai-agent-identity/` — "Why AI agents need verifiable identity"

- 9 cards · dark · `page: fabric` · trigger word: TRUST
- Sources: `profiles/<active>/knowledge/product.md`, `profiles/<active>/knowledge/case-studies.md`
- Caption + DM: `content/<active>/carousels/ai-agent-identity/outputs/`

---

## Pre-flight checklist

Before rendering:
- [ ] `slides.md` written under the content root (`content/<active>/carousels/<slug>/slides.md`), named exactly `slides.md`
- [ ] Arc approved by user — script confirmed before render
- [ ] Cover passes the 3-second test — insight-stating headline, 3–8 words
- [ ] Every card ≤50 words; every middle card ends with a forward pull; re-hook present at card 4–5
- [ ] Every stat has a source line (`stat-source` class or `profiles/<active>/knowledge/` reference)
- [ ] No invented claims — all facts traceable to `profiles/<active>/knowledge/`
- [ ] Close card carries exactly ONE ask; lead-magnet mode only if explicitly opted in
- [ ] Lead-magnet mode: trigger word identical across CTA card, caption A, caption B, and dm.md
- [ ] `cardNo` / `cardTotal` correct on every slide
- [ ] No bare `---` separators (creates blank slides in the PDF)
- [ ] `handle` set — matches PROFILE or user instruction

After export:
- [ ] PDF opens — 9 pages, portrait orientation
- [ ] Background matches chosen theme: dark `#020617` or light `#F8FAFC`
- [ ] Fonts rendered — FatFrank display, Figtree body
- [ ] Footer visible on every card — handle · count · symbol
- [ ] Close card: the single big gradient word (ask or trigger word) renders large and legible
- [ ] No blank pages in the PDF
