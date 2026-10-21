# Build-Deck — Reusable Slide Library

Pre-built slide specs for visual brand assets. Reference these by slug in `outline.md` rather than
rebuilding from scratch each time.

> **Asset contract:** the file paths below are *contract names* that a profile fills — drop your
> real brand assets in under these exact names (`profiles/<active>/knowledge/brand/infographics/…`
> and `…/brand/product-screenshots/…`), or edit the Visual notes to point at what you have. Skip
> any library slide whose asset the profile hasn't supplied.

**How to use:** in `outline.md`, add a `[Library: SLUG]` note in the Visual notes field of the
relevant slide. The composer will substitute the spec below. You can also copy-paste the full spec
as a standalone slide. Text in `『corner brackets』` is an instruction to write that element from
`profiles/<active>/knowledge/product.md` — never paste it literally.

---

## Persona mapping — which library slides to include by default

| Template | Include | On which slide |
|---|---|---|
| **A5** (Legal/Privacy) | `hero-infographic`, `screenshot-dashboard` | Product intro (slide 6); Core capabilities (slide 7) |
| **A6** (Platform Leader) | `product-flow`, `ecosystem-diagram`, `screenshot-dashboard` | Product intro (slide 4); Architecture fit (slide 6); Operational view (slide 7) |
| **A7** (Cross-Org) | `ecosystem-diagram` | Cross-boundary mechanism (slide 4) |
| **A8** (Developer/Builder) | `product-flow`, `screenshot-dashboard`, `screenshot-feature` | How it works (slide 3); Day-one value (slide 5); Then turn on more (slide 6) |
| **A9** (Commercial) | `product-card` | Product + commerce (slide 4) |
| **A10** (Pilot to Production) | `hero-infographic`, `screenshot-dashboard` | Core product (slide 4); Day-one visibility (slide 5) |
| **A-S1** (Startup CEO) | `ecosystem-diagram` | What you get (slide 5) |
| **A-S2** (Startup CTO) | `product-flow`, `screenshot-feature` | How it works (slide 4); Value first (slide 5) |
| A1, A2, A3, A4 | None required | — |

---

## SLUG: `hero-infographic`

**Purpose:** executive / C-suite orientation before going deeper. Use as a visual anchor when introducing the product to legal, compliance, or board-level audiences.

```markdown
## Slide — hero-infographic

**Core message:** the 3 pillar-level things the product delivers — take the pillar framing from product.md (or PROFILE.md `content_pillars` if product.md has no pillar section).
**Layout:** cards (3-column)
**Visual notes:** full-width infographic — profiles/<active>/knowledge/brand/infographics/hero-infographic.png — no additional text overlay; let the image speak. Place headline above image.
**What to say:**
- One line per pillar: name it, then the buyer outcome it produces (『pillar name』: 『what it means the buyer can now do or prove』).
- Keep it to three. An executive slide with four pillars has none.
- Match each pillar to a phrase the persona already uses — this slide is orientation, not detail.
**Source references:** profiles/<active>/knowledge/product.md — pillar / top-level benefits section
```

---

## SLUG: `ecosystem-diagram`

**Purpose:** shows the full product suite and how the products relate. Use when the audience needs product scope, partner/commercial framing, or cross-org context.

```markdown
## Slide — ecosystem-diagram

**Core message:** 『the flagship `default_product`』 is the lead — the rest of PROFILE.md products[] complete the suite.
**Layout:** split (image left, bullets right) or full-width image with headline overlay
**Visual notes:** ecosystem diagram — profiles/<active>/knowledge/brand/infographics/ecosystem-diagram.svg (prefer SVG for crisp rendering). Alternatively use individual product cards (product-card.png per product) in a columns layout.
**What to say:**
- Lead product first: its one-line value proposition from product.md.
- One line per companion product: name + what it adds to the suite.
- Close with "work independently or as a suite" only if product.md supports it.
**Source references:** profiles/<active>/knowledge/product.md — products / suite section; PROFILE.md products[]
```

---

## SLUG: `product-flow`

**Purpose:** technical architecture visual showing the product's end-to-end flow. Use for developer/SA audiences and any slide explaining how the product works mechanically.

```markdown
## Slide — product-flow

**Core message:** every request / workflow passes through 『N』 identifiable steps — take the step count and names from product.md.
**Layout:** full-width diagram with headline above and step labels below
**Visual notes:** flow diagram — profiles/<active>/knowledge/brand/infographics/product-flow.png — full width, no text overlay. Label each step beneath the image with the step names from product.md.
**What to say:**
- One line per step: what happens at that step, and the guarantee it adds.
- Be precise about inputs and outputs — this is the slide technical evaluators will interrogate.
- No marketing language here; mechanism only.
**Source references:** profiles/<active>/knowledge/product.md — flow / how-it-works section
```

---

## SLUG: `screenshot-dashboard`

**Purpose:** product UI proof for the main operational dashboard. Use on any slide claiming "single pane of glass" or "full visibility" — shows it, doesn't just claim it.

```markdown
## Slide — screenshot-dashboard

**Core message:** this is what the product's operational view actually looks like.
**Layout:** split (headline + 2-3 bullets left; screenshot right) OR screenshot full-width with caption bar below
**Visual notes:** product screenshot — profiles/<active>/knowledge/brand/product-screenshots/ss-product-dashboard.png — anchor right or full-width. Do not crop. Caption: "[brand_name] [product name] — 『dashboard name』".
**What to say:**
- 2–3 bullets naming what is visible in the screenshot, drawn from product.md's feature list.
- Never claim something the screenshot doesn't show — the audience will look.
- Note any standard export / interop point the dashboard feeds, if product.md lists one.
**Source references:** profiles/<active>/knowledge/product.md — features list (reporting / operational view)
```

---

## SLUG: `screenshot-feature`

**Purpose:** product UI proof for a specific headline feature. Use on slides covering that feature's claim in depth.

```markdown
## Slide — screenshot-feature

**Core message:** 『the feature's one-line claim from product.md』
**Layout:** split (headline + bullets left; screenshot right)
**Visual notes:** product screenshot — profiles/<active>/knowledge/brand/product-screenshots/ss-product-feature.png — anchor right. Caption: "[brand_name] [product name] — 『feature name』".
**What to say:**
- 3 bullets, each stating one property of the feature the buyer cares about (what it does, where it travels/applies, how it's controlled) — from product.md.
- Tie the last bullet to the slide's persona: why this property matters to *them*.
**Source references:** profiles/<active>/knowledge/product.md — the feature's section
```

---

## SLUG: `product-card`

**Purpose:** branded product card for a companion product from products[]. Use on suite overviews and on slides where a companion product carries the argument (e.g. the commercial slide in A9).

```markdown
## Slide — product-card

**Core message:** 『the companion product's one-line value proposition from product.md』
**Layout:** split (headline + bullets left; product card right) or product card full-width
**Visual notes:** product infographic — profiles/<active>/knowledge/brand/infographics/product-card.png — anchor right or center. Caption: "[brand_name] 『companion product name』 — 『its category label』".
**What to say:**
- 3 bullets from that product's section in product.md: what it establishes, what it makes attributable or provable, what record it leaves.
- State how it composes with the flagship — one line, from product.md.
**Source references:** profiles/<active>/knowledge/product.md — the companion product's section
```

---

## Step-by-step flow slides (advanced — Mode B Slidev only)

For developer/SA decks (A8, A-S2) in Mode B, you can use per-step flow images to create an
animated reveal sequence — one slide per step of the product-flow diagram. Adjust the number of
rows to the product's actual step count from product.md; the table below shows the 5-step shape.

| Slug | File | Step |
|---|---|---|
| `flow-step-1` | `product-flow-step1.png` | Step 1: 『step name from product.md』 |
| `flow-step-2` | `product-flow-step2.png` | Step 2: 『step name from product.md』 |
| `flow-step-3` | `product-flow-step3.png` | Step 3: 『step name from product.md』 |
| `flow-step-4` | `product-flow-step4.png` | Step 4: 『step name from product.md』 |
| `flow-step-5` | `product-flow-step5.png` | Step 5: 『step name from product.md』 |

In outline.md, reference as `[Library: flow-step-1]` through `[Library: flow-step-5]`. Each step
slide follows the same pattern: image full-width, step name as headline, one sentence of
explanation as the subhead.
