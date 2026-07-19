
# Infographic (Handwritten) — Prompt Recipes

Prompt construction for **handwritten / notebook / whiteboard style infographics**. The goal is a
photoreal image that looks like a photograph of a real notebook page, whiteboard, or formula sheet —
not a digital illustration of one. **Text must be correct and legible** — the handwritten aesthetic
is a style, not an excuse for garbled words.

> **Model routing (read first).** The model IDs and credit table below are the **interactive path**
> (Higgsfield connector — `nano_banana_pro`, fallback `recraft-v4-1`). On the **headless path** there
> is no model choice and no credit table: generation goes to the pinned `gemini_image` worker (Gemini
> 3 Pro Image, exact-USD) and the brand accent palette must be named **in the prompt text** (no
> `colors` param). The recipes apply to **both** paths. See `SKILL.md` Steps 1, 5, 6.

> **Brand palette is profile-driven.** `{BRAND_ACCENT}` resolves from the active profile's brand
> assets (`profiles/<active>/knowledge/brand/` → `BRAND-ASSETS-README.md`). For handwritten style,
> the brand accent translates to **ink / marker / highlight colour** rather than a background fill.
> Never hardcode a specific company's palette. Prior handwritten infographics in
> `profiles/<active>/knowledge/brand/infographics/` are the strongest visual reference — describe
> their look-and-feel in the prompt rather than copying another company's assets.

---

## Handwritten-style preamble (always lead with this)

```
Photograph of a real [SURFACE] covered in hand-lettered notes. [INSTRUMENT] ink on [PAPER_TONE] paper.
Slightly imperfect letterforms — natural human handwriting, not a font. PRINT lettering, not cursive —
every word individually legible, consistent baseline. Authentic texture.
Photoreal, not illustrated. Warm ambient light. Slight shallow depth-of-field around the edges.
```

Choose the right `[SURFACE]`, `[INSTRUMENT]`, and `[PAPER_TONE]` for the desired aesthetic:

| Style | SURFACE | INSTRUMENT | PAPER_TONE |
|---|---|---|---|
| Notebook (default) | `notebook page` | `black ballpoint pen` | `cream ruled` |
| Formula sheet | `loose-leaf paper` | `fine-tip black pen` | `white unlined` |
| Whiteboard | `whiteboard` | `{BRAND_ACCENT} dry-erase marker` | `white matte surface` |
| Grid / engineering | `graph paper` | `0.3mm black technical pen` | `pale blue grid` |
| Chalkboard | `dark chalkboard` | `white chalk + {BRAND_ACCENT} chalk` | `slate black` |
| Sticky notes | `cork board with sticky notes` | `felt-tip marker` | `yellow and white stickies` |

Then immediately spell out every element:

```
TITLE (large, top-centre or top-left, underlined or circled): "<spec.headline>"
SUBTITLE / TAGLINE (smaller, below title): "<spec.takeaway>"
ELEMENTS — hand-lettered as a labelled list or formula layout:
  1. "<sections[0].heading>" → "<sections[0].value>"
     annotation: "<sections[0].caption>"
  2. "<sections[1].heading>" → "<sections[1].value>"
     annotation: "<sections[1].caption>"
  ... (repeat for all elements)
BYLINE (bottom corner, smaller, slightly lighter): "<spec.byline>"
FOOTER ANNOTATION (bottom, underlined or bracketed): "<spec.cta>"
```

**Legibility rules:**
- 4–8 elements maximum per image (≤5 on X) — more is unreadable at scroll speed.
- **One sketch metaphor per image** — a single diagram/drawing anchors the page; a second one
  splits attention (clarity over art: the idea carries the image, not the rendering).
- Print lettering, not cursive; consistent baseline; every word individually legible.
- Each heading ≤ 40 chars; each value / formula ≤ 60 chars; each annotation ≤ 60 chars — short
  strings render accurately, long ones garble (playbook §9).
- Short hand-lettered lines only — no paragraph text, no run-on sentences.
- Critical values (formulas, percentages) should be slightly larger or circled to draw the eye —
  the page needs its own BAN (one dominant element).
- Paper tone off-white, never pure white — the image must read on dark timelines without glare.

---

## Per-platform layout guidance

### LinkedIn 4:5 (1080×1350) — vertical page

```
aspect_ratio: "4:5"
```

Layout instruction to include in prompt:
```
Portrait orientation (4:5 ratio). Notebook page layout:
[TOP ZONE] — title (large, centred or left-aligned), underlined or circled.
[MIDDLE ZONE] — elements listed vertically, equally spaced. Each element on 1–2 lines.
  Annotation written beside or below each element in smaller script.
  Optional: a simple sketch arrow or bracket between related elements.
[BOTTOM ZONE] — byline (bottom-left corner, small) + footer annotation / CTA.
Enough whitespace to look like a real page, not a wall of text.
```

### X (Twitter) 16:9 (1600×900) — wide page

```
aspect_ratio: "16:9"
```

Layout instruction to include in prompt:
```
Landscape orientation (16:9 ratio). Do NOT stack tall — re-flow for wide format:
[LEFT COLUMN ~30%] — title (large) + byline + footer CTA. Vertical text block.
[RIGHT AREA ~70%] — elements in 2–3 columns. Each element: heading + value + annotation.
  Column dividers as hand-drawn lines or brackets.
Full-bleed page texture. Nothing cropped in the timeline preview.
```

---

## Annotation style guidance (by `visual_hint`)

| `visual_hint` | Prompt phrase |
|---|---|
| `circle` | `"Key value circled in {BRAND_ACCENT} ink. Slightly imperfect circle."` |
| `underline` | `"Heading underlined with a hand-drawn {BRAND_ACCENT} line."` |
| `arrow` | `"Hand-drawn arrow connecting the element to its annotation."` |
| `box` | `"Value boxed with a {BRAND_ACCENT} hand-drawn rectangle."` |
| `star` | `"A small hand-drawn star beside this element — marks it as key."` |
| `bracket` | `"Hand-drawn curly brace or square bracket grouping this element."` |
| `sketch` | `"Simple hand-drawn thumbnail sketch beside the label — a rough icon."` |

---

## Brand accent usage in handwritten prompts

Unlike the data style (where `{BRAND_ACCENT}` fills charts), in handwritten style the accent is
the **ink / marker / highlight colour**:

- Whiteboard / chalkboard → accent = marker colour (`{BRAND_ACCENT} dry-erase marker` / chalk).
- Notebook → accent = highlight pen or pen colour for headings / circles.
- Formula sheet → accent = coloured annotations while main text is black pen.

Example brand enforcement suffix for notebook style:
```
Ink: black ballpoint for body text. {BRAND_ACCENT} for title, underlines, and key circles.
No stock photography. No digital graphic overlays. Authentic notebook aesthetic.
Paper tone: <inferred from brand assets — e.g. cream, white, or lightly ruled>.
Lighting: warm overhead, casting a faint page shadow. Slight page curl at one corner (optional).
```

---

## Prop and atmosphere modifiers

Append when the colleague wants a specific visual setting or mood:

| Modifier | Append after elements |
|---|---|
| `minimal` | `"Clean white paper. Only the text and annotations. No props. Minimal negative space."` |
| `desk prop` | `"A coffee mug, pencil, and eraser visible in the corner. Authentic working-desk feel."` |
| `urgent / rapid` | `"Fast hand — slightly hurried letterforms, arrows drawn quickly, double-underlines on critical items."` |
| `considered / precise` | `"Careful, deliberate lettering. Ruler-straight lines. Each element exactly spaced."` |
| `chalkboard` | `"Dark slate background. White and {BRAND_ACCENT} chalk. Slight chalk dust smear around erased areas."` |
| `vintage` | `"Aged yellowed paper. Faded ink. Slight watermark texture. Nostalgic editorial feel."` |

---

## Model selection quick reference

| Use case | Model | Aspect ratio | Key params |
|---|---|---|---|
| Handwritten / notebook (default) | `nano_banana_pro` | `4:5` or `16:9` | `resolution: 2k` |
| Handwritten / notebook (fallback) | `recraft-v4-1` | `4:5` or `16:9` | `colors: [<active accent hexes>]`, `resolution: 2k` |

Confirm model availability with `models_explore(action:'recommend')` citing "photoreal photograph of
a handwritten notebook page with hand-lettered text". `nano_banana_pro` is preferred — its
photoreal output produces the paper-and-ink texture more convincingly than a generative
illustration model.

---

## Approximate credit costs (June 2026 — always verify with `get_cost: true`)

| Job | Approximate credits |
|---|---|
| 1x `nano_banana_pro`, 4:5, 2k | ~10–20 credits |
| 1x `nano_banana_pro`, 16:9, 2k | ~10–20 credits |
| Both platforms (2 images) | ~20–40 credits |
| 1x `recraft-v4-1` (fallback), any ratio | ~8–15 credits |

Never quote a cost from this table — run `get_cost: true` first. These are orientation estimates only.

---

## Accuracy check protocol (Step 7 reference)

After saving the PNG, Read the file (vision) and verify:

1. **Title** — present, readable, recognisably the `spec.headline`.
2. **Each element heading and value** — all present; no mis-spelled words; no swapped formulas.
3. **Legibility** — each hand-lettered item legible at 600px wide (X timeline thumbnail size).
4. **Byline** — present if specified; correct name / handle.
5. **Annotations** — present; readable; no hallucinated additions not in the spec.
6. **Accent colour** — `{BRAND_ACCENT}` visible on headings / underlines / circles.
7. **No hallucinated text** — no extra formulas, words, or annotations outside the spec.

Handwritten imperfection (ink wobble, slight slant, natural variation in letterform) is fine and
expected. Report only correctness failures: *"Element 3 value shows '[X]', spec says '[Y]' —
illegible / wrong."* Offer (a) one regen with a prompt nudge for cleaner lettering on that element,
or (b) revise the spec. If text keeps mis-rendering after one retry, stop and recommend the
colleague complete it in a design tool.
