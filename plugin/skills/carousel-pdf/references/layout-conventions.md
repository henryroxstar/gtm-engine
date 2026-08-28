# Carousel Layout Conventions

Reference for correct `slides.md` structure and layout usage in carousel decks.

---

## Slide structure — critical rules

### Global frontmatter + slide structure

```markdown
---
theme: ../../.engine/deck-theme
aspectRatio: '4/5'          ← REQUIRED for portrait
canvasWidth: 1080           ← REQUIRED for portrait
...
---

---                         ← SLIDE 1 frontmatter opens immediately
layout: carousel-hook
page: fabric
cardNo: 1
cardTotal: 9
---

<!-- CARD 1 documentation (HTML comment — won't render) -->

<span class="eyebrow">Label</span>
<h1>Hook headline</h1>
<p class="lede">Sub-headline.</p>

---                         ← SLIDE 2 frontmatter (no blank separator needed)
layout: carousel-point
page: fabric
cardNo: 2
cardTotal: 9
---

<!-- CARD 2 documentation -->

<span class="card-num">02</span>
<h2>Point headline</h2>
<p>Body copy.</p>
```

### The #1 structure mistake

**Do NOT** use a bare `---` as a section comment separator:

```markdown
❌ WRONG — creates blank slides in the PDF:

[card 1 content]

---                    ← creates a blank slide (no layout, no content)

<!-- card 2 comment -->

---
layout: carousel-point
---
```

```markdown
✅ CORRECT — layout frontmatter IS the separator:

[card 1 content]

<!-- card 2 comment (part of next slide's content — won't render) -->

---
layout: carousel-point
---
```

---

## Canvas size

- `aspectRatio: '4/5'` → portrait canvas (1080 × 1350 at canvasWidth: 1080)
- This is ONLY for carousel decks. Keynote decks use `16/9` and `canvasWidth: 980`
- Do NOT set aspect ratio globally in the engine's package.json defaults — only in the carousel deck's frontmatter

---

## Layout family

```
.engine/deck-theme/layouts/
  carousel-hook.vue     ← Card 1 only
  carousel-point.vue    ← Cards 2–7 (value, bridge, solution)
  carousel-stat.vue     ← Proof stat card (typically card 4)
  carousel-quote.vue    ← Customer voice (typically card 8)
  carousel-cta.vue      ← Card 9 (always last)
  
  [existing landscape layouts — untouched]
  cover.vue
  chapter.vue
  statement.vue
  end.vue
  handoff.vue
  two-pane.vue
  pillars.vue
```

---

## Font sizes (portrait-optimised)

These override the landscape type scale inside carousel layouts only:

| Element | Size | Layout |
|---|---|---|
| Hook h1 | 5.5rem | carousel-hook |
| Point h2 | 3.4rem | carousel-point |
| Stat number | 7.5rem | carousel-stat |
| Quote body | 1.75rem | carousel-quote |
| CTA trigger word | 7.5rem | carousel-cta |
| Body / lede | 1.25rem | all |
| Footer | 0.82rem | all |

---

## Theming via `data-page`

Each layout sets `data-page` on its root element from `$frontmatter.page`. This activates the per-product token set:

```css
[data-page="fabric"]  → --product-accent: #AE44D5
[data-page="website"] → --product-accent: #1D58FC
[data-page="elements"]→ --product-accent: #F5AB0B
```

Set `page:` in each slide's frontmatter. Flagship trust/identity-product carousels use `page: fabric` (capability mapping table in `SKILL.md`).
