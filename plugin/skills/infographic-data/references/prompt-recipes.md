
# Infographic (Data) — Prompt Recipes

Prompt construction for **data-dense editorial infographics**. Unlike carousel backgrounds,
**accurate in-image text is the entire deliverable** — every section heading, value, and label in
`spec.json` must appear verbatim in the prompt so the model renders the exact text.

> **Model routing (read first).** The model IDs and credit table below are the **interactive path**
> (Higgsfield connector — `nano_banana_pro`, fallback `recraft-v4-1`/`gpt_image`). On the **headless
> path** there is no model choice and no credit table: generation goes to the pinned `gemini_image`
> worker (Gemini 3 Pro Image, exact-USD) and the brand palette must be named **in the prompt text**
> (no `colors` param). The recipes apply to **both** paths. See `SKILL.md` Steps 1, 5, 6.

> **Brand palette is profile-driven.** `{BRAND_PRIMARY}` and `{BRAND_ACCENT}` resolve from the
> active profile's brand assets (`profiles/<active>/knowledge/brand/` → `BRAND-ASSETS-README.md`
> + the brand template). Substitute the active company's real hexes before generating. Never
> hardcode a specific company's palette. Prior infographics in
> `profiles/<active>/knowledge/brand/infographics/` are the visual style reference — describe
> their look-and-feel in the prompt rather than copying another company's palette.

---

## Data-style preamble (always lead with this)

```
Editorial data infographic poster. Clean, professional, high-contrast layout on a deep {BRAND_PRIMARY} background.
Bold {BRAND_ACCENT} typographic hierarchy. Numbered sections with big stat anchors. Legible, accurate in-image text.
Design system: modern editorial (like a Bloomberg Visual / Axios visual). No decorative clutter.
```

Then immediately spell out every section with its exact text:

```
HEADLINE (large, dominant): "<spec.headline>"
TAKEAWAY SUBHEAD (smaller): "<spec.takeaway>"
SECTIONS — render each as a labelled zone with a chart element and a big stat:
  1. "<sections[0].heading>" — value: "<sections[0].value>" — visual: <visual_hint> — caption: "<sections[0].caption>"
  2. "<sections[1].heading>" — value: "<sections[1].value>" — visual: <visual_hint> — caption: "<sections[1].caption>"
  ... (repeat for all sections)
FOOTER: "<spec.cta>"  |  Source: <first source domain if enriched>
```

**Legibility rules** (floors from the platform playbook §6, at a 1080-px-wide canvas):
- 4–7 stat values maximum per image — more than 7 is unreadable at scroll speed (split into a
  carousel or thread instead).
- **One value is the BAN** — the dominant stat anchor, visibly larger than everything else.
- Each section label ≤ 40 chars (≤8 words — short strings render accurately, long ones garble);
  each caption ≤ 60 chars; no paragraph text in-image.
- Type floors: headline 72–96px · section headings 60–72px · values large/dominant · captions
  ≥24px — never below. If it doesn't fit, cut words, not point size.
- Contrast: WCAG AA — 4.5:1 body, 3:1 large text, checked against the actual background.
- Safe margins ≥60px sides / ≥80px top-bottom.
- Abbreviate only standard units (`M`, `B`, `%`, `×`, `ms`) — spell out everything else.
- **Direct-label every chart** — no legends (eye travel fails at scroll speed).

---

## Per-platform layout guidance

### LinkedIn 4:5 (1080×1350) — vertical numbered stack

```
aspect_ratio: "4:5"
```

Layout instruction to include in prompt:
```
Vertical portrait layout (4:5). Zones stacked top to bottom:
[HEADER ZONE] — full-width headline + subhead, centred or left-aligned.
[SECTION ZONES] — numbered 1 through N, each occupying a horizontal band.
  Each band: number + heading on the left, big value stat on the right, small chart element (donut/bar/icon) between them, caption below the value.
[FOOTER ZONE] — CTA + source line. Smaller, subtle.
Generous vertical whitespace between zones. Clean grid. No visual clutter.
```

### X (Twitter) 16:9 (1600×900) — 2–3 column grid

```
aspect_ratio: "16:9"
```

Layout instruction to include in prompt:
```
Landscape widescreen layout (16:9). Do NOT pad a portrait design — re-flow for wide format:
[LEFT COLUMN ~35%] — headline (large) + takeaway + CTA. Vertical text block.
[RIGHT AREA ~65%] — section grid. If ≤4 sections: 2×2 grid. If 5 sections: 2×3 with the BAN double-width.
  Each cell: section number (top-left micro), heading, big value, chart element (compact).
[FOOTER STRIP] — source line at the very bottom, full width, small text.
No wasted horizontal space. The headline must be readable in a 1600×900 timeline preview without cropping.
Solid opaque background — no transparency, no pure #000 or #FFF edges (must read on X's light AND dark timelines).
```

**X density cap:** ≤5 sections on the 16:9 image. If the spec carries more, don't densify —
offer the split (an X carousel of 2–4 uniform-ratio companion images, payoff first, or a 3–5
post thread with one stat each; playbook §3).

---

## Chart/visual element guidance (by `visual_hint`)

Tell the model exactly what chart type each section uses:

| `visual_hint` | Prompt phrase |
|---|---|
| `donut` | `"Donut chart filled to {value}. Ring style. Large percentage number centred in the hole."` — donuts only for 2–4 categories; if precise comparison matters, use `bar` (length beats angle) |
| `bar` | `"Horizontal progress bar at {value} fill. Bold bar on {BRAND_ACCENT}. Label at the right end."` |
| `progress` | `"Segmented progress bar showing {value} of total. Muted background track."` |
| `icon` | `"Simple flat icon representing {heading_concept}. {BRAND_ACCENT} coloured. Beside the value."` |
| `arrow` | `"Bold directional arrow (up/down based on value direction). Stat anchored at arrow tip."` |
| `sketch` | `"Hand-drawn style underline / tick / circle annotation around the value. Ink texture."` |
| `none` | `"Big typographic stat only. No chart element. Maximum white space around the number."` |

---

## Brand enforcement suffix (always append last)

```
Colour palette: deep {BRAND_PRIMARY} background, solid and opaque. {BRAND_ACCENT} is the ONLY accent —
spend it on the values and chart fills, keep everything else neutral (grey-everything, accent-the-point).
Mid-tone secondary for section backgrounds / dividers. White or near-white for body text and captions.
No faces. No logos (unless a brand-supplied icon set is the source). No decorative photography.
High contrast for accessibility (WCAG AA: 4.5:1 body, 3:1 large text) — every text element legible at 600px wide.
Editorial poster aesthetic. Modern, confident, trustworthy.
```

---

## Art direction modifiers (append when the colleague specifies a style)

| Modifier | Append after base + sections |
|---|---|
| `dark + neon` | `"Dark mode with {BRAND_ACCENT} neon glow on values. Slight text-shadow. Cyberpunk editorial."` |
| `light / airy` | `"Light cream background with dark ink typography. Minimal. Like a premium print report."` |
| `textured` | `"Subtle paper texture overlay. Slightly imperfect grid. Premium print feel."` |
| `data-heavy` | `"Dense information grid. Tight spacing. Financial-report aesthetic. Newspaper data-viz column."` |
| `bold / impact` | `"Maximum type weight. Condensed font. High-impact editorial. Think magazine cover data story."` |

---

## Model selection quick reference

| Use case | Model | Aspect ratio | Key params |
|---|---|---|---|
| Data infographic (default) | `recraft-v4-1` | `4:5` or `16:9` | `colors: [<active brand palette hexes>]`, `resolution: 2k` |
| Data infographic (fallback) | `nano_banana_pro` | `4:5` or `16:9` | `resolution: 2k` |

Confirm model availability with `models_explore(action:'recommend')` citing "data infographic with
legible in-image text and chart elements" before generating. Recraft is strongly preferred — its
`colors` parameter enforces the brand palette at the model level, not just in the prompt.

---

## Approximate credit costs (June 2026 — always verify with `get_cost: true`)

| Job | Approximate credits |
|---|---|
| 1x `recraft-v4-1`, 4:5, 2k | ~8–15 credits |
| 1x `recraft-v4-1`, 16:9, 2k | ~8–15 credits |
| Both platforms (2 images) | ~16–30 credits |
| 1x `nano_banana_pro` (fallback), any ratio | ~10–20 credits |

Never quote a cost from this table — run `get_cost: true` first. These are orientation estimates only.

---

## Accuracy check protocol (Step 7 reference)

After saving the PNG, Read the file (vision) and verify:

1. **Headline** — matches `spec.headline` exactly, character-for-character.
2. **Each section value** — zero tolerance. `72%` ≠ `27%`, `$4.2B` ≠ `$42B`.
3. **Section headings** — all present; no heading swapped or missing.
4. **Chart fill** — donut at `72%` should look ~¾ full; bar at `30%` should look ~⅓ full.
5. **CTA / footer** — present and legible.
6. **Brand palette** — `{BRAND_PRIMARY}` dominant; `{BRAND_ACCENT}` on values and charts.
7. **No hallucinated text** — no extra labels, statistics, or words not in the spec.

Report any mismatch with: *"Mismatch — spec says [X], image shows [Y]. Offer: (a) one free regen
with a corrected prompt, or (b) revise the spec first."* If a number mis-renders after one retry,
stop and recommend the colleague complete it in a design tool.

**Accuracy-first prompting (playbook §9):** wrap every must-render string in double quotes;
describe placement spatially ("headline top-left", "BAN centred, dominant") — spatial language
beats arrows/callouts, which the model mislabels; keep every label ≤8 words (short strings render
~100%, long ones garble); digit swaps (72% → 27%) are the characteristic failure, so re-verify
every number on every generation. The durable artefact is the spec — the hybrid finish (AI layout
+ design-tool text overlay) is a legitimate ending, not a failure.
