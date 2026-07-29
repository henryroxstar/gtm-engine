
# `carousel-auto` Skill

One command → one complete, publish-ready LinkedIn carousel — copy, PDF, cover art, and motion teaser — sourced from the week's market scan output. Human approval is required before rendering and before any paid generation. Everything else runs automatically.

---

## When to use this skill

- User says: *"auto-carousel"*, *"run my carousel workflow"*, *"carousel from this week's scan"*, *"what should I carousel this week"*, *"weekly carousel"*
- Market scan just ran and the colleague wants to turn the strongest signal into a post
- User wants a hands-off pipeline that picks the topic, selects the shape, and chains through to a publish package without them doing it manually step by step

### What this skill does NOT do

- Does NOT skip approval gates — arc must be approved before rendering; budget must be confirmed before paid steps
- Does NOT post to LinkedIn (human gate required — copy/upload manually)
- Does NOT replace `carousel-pdf` or `carousel-visuals` — it orchestrates them

---

## Workflow — exact sequence

### Step 1 — Read context

Read in parallel:
1. **`profiles/<active>/PROFILE.md`** — pull `name`, `language`, `monthly_tool_budget_usd`, `per_run_cap_usd`, `higgsfield`
2. **Latest market-signals file** — find the most recent `market-signals/YYYY-WW-signals.md` in the working folder. If none exists, tell the colleague: "No market-scan output found — run your market scan first (`run my market scan`) then come back."
3. **Existing carousels** — list any `carousel-*.pdf` files in the working folder so recently covered topics can be deprioritized.

### Step 2 — Score signals for carousel potential

Read each signal in the market-signals file. For each H-rated or M-rated signal, score it:

| Criterion | Points |
|---|---|
| H-rated signal | +2 |
| M-rated signal | +1 |
| Clear myth or misconception to correct | +2 → myth-bust shape |
| Concrete step-by-step action available | +1 → how-to shape |
| Customer proof point or case study available | +1 → case-study shape |
| Broad framework / thought-leadership angle | +1 → framework shape |
| Topic already covered in a recent carousel (check existing PDFs) | −3 |
| Regulatory or compliance angle (high-signal for the active company's ICP) | +1 |
| Hook strength: signal contains a specific number, named entity, or contrarian claim | +1 |
| Save-worthy asset angle: a checklist, cheat sheet, or framework falls out naturally | +1 |

Sum the points. Take the top 2–3 signals. For each, note the highest-scoring arc shape and recommended theme (dark or light — see `carousel-pdf/references/light-theme.md` for guidance).

Show the colleague a short ranked list:

```
This week's top carousel picks from your market scan:

1. ★★★  [Signal headline]
   Shape: myth-bust · Theme: dark · Trigger word: TRUST
   Why: [one sentence — what makes this high-signal]

2. ★★   [Signal headline]
   Shape: framework · Theme: light · Trigger word: FABRIC
   Why: [one sentence]

Pick one (type 1 or 2), or say "something else" to choose a different signal.
```

Wait for the colleague's pick before proceeding.

### Step 3 — Read carousel-pdf knowledge sources

Once a signal is picked, do the mandatory reads from `carousel-pdf` Step 1:

| File | What to pull |
|---|---|
| `profiles/<active>/knowledge/product.md` | Feature names, pain owners, solution themes |
| `profiles/<active>/knowledge/case-studies.md` | Proof points — verbatim quotes, metrics, customer names |
| `profiles/<active>/knowledge/icp-personas.md` | Buyer context |
| `profiles/<active>/knowledge/company.md` | Brand voice, British English, "never say" list |

Also read `carousel-pdf/references/sample-carousels.md` for the arc template matching the chosen shape.

### Step 4 — Draft the copy arc (approval required)

Following the `carousel-pdf` Step 3 workflow:

1. Draft **three hook options** using the six-formula library in
   `carousel-pdf/references/platform-playbook.md` §4 and the shape-specific templates from
   `sample-carousels.md` — judge each against the 3-second test
2. Draft the **full 8–9 card arc** following the chosen shape, applying the momentum rules
   (playbook §5: ≤50 words/card, forward pull on every middle card, re-hook at card 4–5)
3. Pick the **close mode** (playbook §2): default = compliant close (recap + ONE ask + identity).
   Auto-pick a **trigger word** (logic in `carousel-pdf/SKILL.md`) only if the signal is a natural
   lead magnet AND no lead-magnet carousel shipped in the last 3–4 weeks — present it as an opt-in
4. Show **per-card summaries** (card role · headline · layout · forward pull)

Present clearly:

```
Here's the draft arc for "[Signal topic]" (myth-bust · dark):

Hook options (pick one):
  A. [hook]
  B. [hook]
  C. [hook]

Arc:
  Card 1  HOOK     [chosen hook]
  Card 2  MYTH     [...]
  ...
  Card 9  CLOSE    Recap + [single ask]

Close mode: compliant (default) — say "lead magnet" to switch to the Comment-TRIGGER close
(suggested trigger word if switched: TRUST)
Arc looks good? I'll compose the deck once you confirm.
```

**Do not compose the deck until the arc is approved.**

### Step 5 — Compose

Once the arc is approved, follow `carousel-pdf` Step 5: the brain authors `slides.md` directly — no
scaffold, no `npm install`, no knowledge-pack sync. The deck-renderer sidecar bakes the deck-theme,
so the only artefact to produce is the source.

1. **Write `slides.md`** into the carousel working folder **under the resolved content root**
   (`gtm_core.paths.resolve_content_root()` → `content/<active>/carousels/<topic-slug>/slides.md`;
   the file must be named exactly `slides.md`). Compose the approved arc using the layout conventions
   and frontmatter fields from `carousel-pdf/SKILL.md`. Set `colorSchema` to match the chosen theme;
   do **not** add a `theme:` field (the sidecar pins it).

### Step 6 — Render (via the deck MCP tool)

Render by calling **`mcp__deck__export_deck`** — this is the render path; the brain never runs
`node`/`npm`/`npx`.

- **PDF (the post):** `mcp__deck__export_deck(slides_md_path=<abs path to slides.md>, format='pdf')`
- **Per-slide PNGs:** `mcp__deck__export_deck(slides_md_path=<abs path to slides.md>, format='png')`

The tool returns the output path. **On a `[deck-error]` string, DO NOT retry** — surface it to the
operator verbatim.

> **Operator-run fallback (only when the deck tool is absent).** If the `deck` MCP tool isn't
> available (sidecar not configured → `deck_renderer_url` unset → the `deck` server is omitted),
> hand off the deck **source** (`slides.md`) and tell the operator to render it locally from their
> Terminal in their Slidev deck workspace. Do **not** attempt `node`/`npm`/`npx` yourself —
> they are denied by the least-privilege policy; retrying only burns budget. Local commands the
> operator runs:
>
> ```bash
> # From the operator's local Slidev deck workspace directory:
> npm run export -w "@<deck-scope>/<deck-slug>"                                           # → outputs/carousel.pdf
> npm run export -w "@<deck-scope>/<deck-slug>" -- --format png --output outputs/slides   # per-slide PNGs
> ```

After the render returns, write the publish text files to the carousel working folder's `outputs/`:
- `caption.md` — two caption variants (hook-led + story-led), 0–3 hashtags, alt-text line for the cover
- `dm.md` — delivery DM + one follow-up (**lead-magnet mode only** — omit in the default close)

### Step 7 — Visuals and motion teaser (optional, budget-guarded)

After the PDF is confirmed, ask:

> "PDF is ready. Want to add:
> - **V1 — Cover art**: AI-generated 4:5 background for the hook card (~X credits)
> - **V3 — Motion teaser**: 9:16 animated reel from the hook card (~Y credits)
> - **V1+V3 together** (common pairing)
> - **Skip — text-only carousel is ready**"

If they choose any visual option:
- Hand off to `carousel-visuals`, which is **runtime-aware**: headless runtimes use the in-repo
  `gemini_image` (image) + `higgsfield_video` (DoP teaser) workers; interactive runtimes use the
  Higgsfield connector (`nano_banana_pro` / `kling3_0`). Run its budget preflight — headless estimates
  from the per-image/per-teaser rate; interactive does `balance` → `get_cost: true` → confirm.
- See `references/motion-brief.md` for the motion-teaser prompt template (model routing per path).

Never generate without explicit budget confirmation. Free fallback: the text-only carousel is complete and post-ready without visuals.

### Step 8 — Assemble publish package

Once all chosen components are ready, create `carousel-[topic-slug]-[YYYY-MM-DD]/` in the carousel
working folder under the content root (`content/<active>/carousels/<topic-slug>/`) with:

| File | Source |
|---|---|
| `carousel.pdf` | The path returned by `mcp__deck__export_deck(..., format='pdf')` |
| `slides/` | The PNG folder returned by `mcp__deck__export_deck(..., format='png')` (if exported) |
| `cover-art.png` | If V1 generated |
| `teaser.mp4` (or `.gif`) | If V3 generated |
| `caption.md` | From Step 6 |
| `dm.md` | From Step 6 — lead-magnet mode only |
| `publish-checklist.md` | Write now (see template below) |

#### `publish-checklist.md` template

Write this file with the specifics filled in:

```markdown
# Publish checklist — [Topic] carousel · [Date]

## Before posting
- [ ] PDF opens — [N] pages, portrait, correct background ([dark #020617 / light #F8FAFC])
- [ ] Hook card: headline is specific and provable (not generic) — passes the 3-second test
- [ ] Stats: every number has a source — no invented figures
- [ ] Close card: exactly ONE ask, gradient-filled ([FOLLOW / SAVE / trigger word])
- [ ] Footer: handle · count · symbol on every card
- [ ] Caption variant chosen: [hook-led / story-led] — payoff inside the first 140 chars
- [ ] Lead-magnet mode only: trigger word in caption matches CTA card: [WORD]
- [ ] Hashtags: 0–3 max, never stacked (remove any that feel off-brand)
- [ ] Alt text ready (bottom of caption.md)

## Post on LinkedIn
- Upload PDF as a Document post (not an image post)
- Paste chosen caption from caption.md
- If cover art generated: use cover-art.png as the post thumbnail
- Reply to early comments in the first 60–90 minutes — distribution is decided there

## Optional — motion teaser
- [ ] If V3 generated: post teaser.mp4 as a separate Reel (link to carousel post in caption)

## DM copy (lead-magnet mode only)
- See dm.md — paste the delivery DM manually when someone comments the trigger word (never automate)
```

Then append `⟦FILE:…⟧` sentinels at the very end of your response so the Telegram cockpit delivers the PDF (and cover art if generated) automatically:

```
⟦FILE:/absolute/path/to/content/<active>/carousels/<topic-slug>/carousel-[topic-slug]-[YYYY-MM-DD]/carousel.pdf⟧
⟦FILE:/absolute/path/to/content/<active>/carousels/<topic-slug>/carousel-[topic-slug]-[YYYY-MM-DD]/cover-art.png⟧
```

Omit lines for files that were not generated. Use the real resolved absolute paths.

---

## Signal → arc shape cheat sheet

| Signal type | Best shape | Theme |
|---|---|---|
| "Everyone does X, but X doesn't work for agents" | Myth-bust | Dark |
| "How to implement [product pattern] in 3 steps" | How-to | Light or Dark |
| "We solved [problem] with [customer]" | Case study | Dark |
| "Here's our framework for [big topic]" | Framework | Light |
| Regulatory or compliance announcement | Framework or Myth-bust | Dark |
| Product launch or feature release | How-to or Framework | Dark |
| Competitive signal ("competitor lacks X") | Myth-bust | Dark |

---

## Guardrails

- **Never skip the arc approval gate.** The carousel is only as good as the script.
- **Never generate without budget confirmation.** Show the estimate and wait for yes (interactive: `get_cost: true` first; headless: estimate from the per-image/per-teaser rate).
- **Never invent stats or product claims.** All facts must trace to `profiles/<active>/knowledge/`.
- **British English** throughout (`organisations`, `recognised`, `personalised`).
- If the market-signals file has no H-rated signals: "No H-rated signals this week — I can build from an M-rated signal, or you can give me a topic directly."
- If the `deck` MCP tool returns a `[deck-error]` (or is absent): do **not** retry. Surface the error and fall back to the operator-run local render (Step 6) — hand off `slides.md` for the operator to export from their Slidev deck workspace.
