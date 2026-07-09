# Motion Teaser Brief — Reference

How to generate the Higgsfield V3 motion teaser from a completed carousel. This is the template for constructing the `generate_video` prompt from the carousel's hook card content.

> **Brand palette is profile-driven.** `{BRAND_PRIMARY}` (the deep base colour) and
> `{BRAND_ACCENT}` (the highlight) resolve from the **active profile's** brand assets
> (`profiles/<active>/knowledge/brand/` → `BRAND-ASSETS-README.md` + the brand template).
> Substitute the active company's real hexes before generating — never hardcode any company's
> palette into a prompt. The secondary hues named per `page:` value below (amber, lime,
> turquoise, pink) are *thematic* capability flavours only — the primary brand palette stays
> dominant.

---

## What the motion teaser is

A **9:16 short video (4–8 seconds)** animated from the carousel's hook card — designed for LinkedIn Reels or Stories. It's a teaser that drives people to the carousel post, not a standalone explainer.

The motion teaser is **always a companion to the PDF carousel** — never built before the carousel is rendered and approved.

---

## Model — by path

The teaser is **runtime-aware** (see `carousel-visuals` Mode V3):

- **Headless** — `higgsfield-ai/dop/standard` (DoP Standard) via the in-repo `higgsfield_video`
  worker. The model is pinned server-side — you do not pass it. Flow:
  `upload_image(<cover-art path>)` → `generate_video(image_url, prompt, duration=5)` →
  poll `check_video_status` until completed.
- **Interactive** — `kling3_0` via the Higgsfield connector (9:16, image-to-video). Run
  `get_cost: true` first, then `generate_video` with the uploaded `start_image`.

Do not generate a motion teaser with an image model.

---

## Parameters template

Headless (DoP via the REST worker):
```
image_url: <returned by upload_image(<cover-art path>)>
prompt:    <motion prompt — see below>
duration:  5
```

Interactive (Kling via the connector):
```
model: kling3_0
aspect_ratio: "9:16"
duration: 5
params.medias: [{value: <media_id>, role: "start_image"}]
get_cost: true   ← run first with identical params
```

---

## Prompt construction

Build the `prompt` field from the carousel's hook card. The prompt has three parts:

### 1 — Scene description (from the hook card's theme/product page)

Use the `page:` value from the hook card frontmatter to pick the ambient style. The `page:`
values are fixed deck-engine slots — map the active company's products onto them **by
capability** (see the product → page theme mapping in `carousel-pdf/SKILL.md`):

| `page:` value | Scene prompt |
|---|---|
| `fabric` | "Deep {BRAND_PRIMARY} ambient space, slow mesh gradient in {BRAND_ACCENT} tones, cinematic depth of field, no text, no people" |
| `elements` | "Deep {BRAND_PRIMARY} ambient space, slow amber-gold secondary gradient, warm cinematic glow, no text, no people" |
| `radix` | "Deep {BRAND_PRIMARY} ambient space, slow lime-green data-mesh secondary gradient, technical blueprint feel, no text, no people" |
| `messaging` | "Deep {BRAND_PRIMARY} ambient space, slow turquoise-silver light scatter, glass reflections, no text, no people" |
| `forge` | "Deep {BRAND_PRIMARY} ambient space, vibrant pink-magenta secondary gradient pulse, electric feel, no text, no people" |
| `website` | "Deep {BRAND_PRIMARY} ambient space, {BRAND_ACCENT} radial gradient with slow particle motion, corporate cinematic, no text, no people" |

If theme is light: replace the deep `{BRAND_PRIMARY}` base with "off-white clean background" and reduce gradient saturation: "soft watercolour wash, editorial, no text, no people."

### 2 — Motion direction

Keep this short and specific. The motion should complement the hook card, not fight it:

```
Slow camera push-in, ambient gradient shift, 5 seconds, seamless loop.
```

Standard motion brief that works for all carousel topics. Do not invent elaborate scene descriptions — the gradient is the visual anchor.

### 3 — Brand constraint

Always append (with `{BRAND_PRIMARY}` / `{BRAND_ACCENT}` substituted for the active company's
real hexes from `profiles/<active>/knowledge/brand/`):
```
No text overlays, no people, no logos, no product screenshots. Brand colours: {BRAND_PRIMARY} (deep base), {BRAND_ACCENT} (highlights). Cinematic, professional.
```

### Full assembled prompt example (fabric page, dark theme — tokens already substituted)

```
Deep {BRAND_PRIMARY} ambient space, slow mesh gradient in {BRAND_ACCENT} tones, cinematic depth of field, slow camera push-in, ambient gradient shift, 5 seconds, seamless loop. No text overlays, no people, no logos, no product screenshots. Brand colours: {BRAND_PRIMARY} (deep base), {BRAND_ACCENT} (highlights). Cinematic, professional.
```

---

## Usage in LinkedIn

After the teaser is generated:

1. **Post the teaser as a Reel** — separate post, not attached to the carousel PDF
2. In the Reel caption: reference the carousel — "Full carousel in the comments / link in bio / see [carousel post link]"
3. The trigger word from the carousel CTA card should appear in the Reel caption too, to unify the content series

---

## Budget note

The V3 motion teaser costs more credits than V1 cover art. Always run `generate_video` with `get_cost: true` first, show the estimate, and wait for explicit approval before generating. The text-only PDF carousel is a complete, post-ready deliverable without the teaser — the teaser is an amplifier, not a requirement.
