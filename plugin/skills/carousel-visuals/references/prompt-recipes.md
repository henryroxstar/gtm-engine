# Carousel Visuals — Prompt Recipes

Constructed prompts for each product theme + each visual mode. Insert the `{HOOK}` token from the carousel deck's Card 1 `<h1>`. Append art direction modifier if supplied by the colleague. The brand enforcement suffix is always appended last.

> **Model routing (read first).** The model IDs and credit costs in the tables below are the
> **interactive path** (Higgsfield connector). On the **headless path** there is no model choice and
> no credit table: images go to the pinned `gemini_image` worker (Gemini 3 Pro Image, exact-USD) and
> the motion teaser goes to the pinned `higgsfield_video` DoP Standard worker (upload → generate →
> poll). The prompt recipes themselves apply to **both** paths — on headless, the brand palette must
> be named in the prompt text (there is no `colors` param). See `SKILL.md` Pre-flight + Mode V3.

> **Brand palette is profile-driven.** The colour tokens below — `{BRAND_PRIMARY}` (the dark base) and `{BRAND_ACCENT}` (the highlight) — resolve from the **active profile's** brand assets (`profiles/<active>/knowledge/brand/`, e.g. `BRAND-ASSETS-README.md` + the brand template). Substitute the active company's real hexes before generating. Never hardcode a company's palette into a prompt. The recipes name a secondary accent (e.g. amber for a safety theme, turquoise for a payments theme) only as a *thematic* distinction within one carousel — keep the primary brand palette dominant throughout.
>
> The product themes below (gateway / trust-fabric, safety stream, payments) are the **active company's products**, resolved from `PROFILE.md` → `products[]`. Match the theme to whichever product the carousel is about; if the active company's products differ, map by capability (trust/identity, safety, payments, multi-product) rather than by the example names.

**Brand enforcement suffix (always append):**
> `Colour palette: deep {BRAND_PRIMARY} background, {BRAND_ACCENT} highlights. Solid opaque background — no transparency. No text, no typography. No faces, no people. Dark cinematic atmosphere. High contrast. Futuristic abstract. Clean composition.`

**X-bound images** (V4 sets reused as an X carousel, or any card posted natively to X): keep the
background solid and away from pure #000/#FFF extremes, and check the render against both a white
and a near-black surround — a large share of X users run dark timelines (playbook §3, in
`carousel-pdf/references/platform-playbook.md`).

---

## V1 / V2 — Image prompts by product theme

### Trust / identity product (gateway, trust-fabric capability) (`page: fabric`)

**Base prompt:**
> `Abstract digital infrastructure scene. A central glowing accent-colour node surrounded by interconnected agent flows — cryptographic trust chains rendered as luminous accent-colour filaments crossing a deep dark void. The hook concept: "{HOOK}". Cinematic dramatic lighting. Data-mesh aesthetic. Futuristic but grounded.`

**Variations (use for V2 per-card diversity):**
- Hook card (Card 1): Full-bleed dramatic scene, strong accent-colour glow emanating from centre.
- Problem card (Card 2–3): Fragmented, broken links, agents as isolated nodes, darker and more chaotic.
- Stat card (Card 4): Minimal, one dominant visual element (glowing lock, shield, chain), lots of negative space.
- Bridge card (Card 5): Transition from dark chaos to ordered accent-colour structure — split composition.
- Solution card (Card 6–7): Clean, ordered lattice of agent identities, trust mesh fully formed.
- Quote card (Card 8): Dark and intimate — single light source illuminating abstract glass/crystal surface.
- CTA card (Card 9): Maximum negative space — dark background with a single accent-colour pulse/halo. Leaves room for the trigger-word overlay.

### Safety product (llm-safety capability) (`page: elements`)
> `Abstract AI safety pipeline visualisation. A stream of glowing amber data flows through a structured filter — dark background, amber-gold secondary accents. Some data streams blocked/redirected, others flow cleanly. The concept: "{HOOK}". Cinematic, high-contrast, technical abstraction.`

### Payments product (agent-payments capability) (`page: messaging`)
> `Abstract payment governance scene. Micro-transactions as turquoise particles flowing through a secure channel. Cryptographic verification symbols overlaid in turquoise. Deep dark void. The concept: "{HOOK}". Cinematic fintech aesthetic, no currency symbols, no logos.`

### Corporate / multi-product (`page: website`)
> `Abstract full platform visualisation. Multiple interconnected systems rendered as glowing nodes across a dark space — identity, governance, payments, safety as distinct clusters connected by luminous filaments. The active company's primary brand accent dominant. The concept: "{HOOK}". Cinematic architectural scale.`

---

## V3 — Motion teaser prompts (video, 9:16)

Motion prompts describe **camera move + atmosphere only**. The start_image supplies the content. Keep under 25 words.

### Default (all themes)
> `Slow cinematic push-in. Accent-colour particles drift upward through dark void. Subtle light pulse from centre. No text. No faces. Atmospheric.`

### Trust / identity product (trust/identity focus)
> `Slow dolly in toward the central node. Accent-colour filaments pulse rhythmically. Particles orbit inward. Deep atmospheric haze. Cinematic grade.`

### Safety product (safety focus)
> `Slow tracking shot across the data stream. Amber glow intensifies then stabilises. Particles filter through invisible barrier. Atmospheric.`

### Payments product (commerce focus)
> `Gentle drift through the transaction field. Turquoise particles accelerate, settle. Depth of field shift from background to foreground. Cinematic.`

### High-energy variant (for aggressive hook topics)
> `Cinematic quick push-in. Rapid particle convergence. Accent-colour flare at impact. Hold on the node. Atmospheric haze. No text.`

---

## Art direction modifiers (append when the colleague specifies a style)

| Modifier | Append after base prompt |
|---|---|
| abstract data-mesh | `"Dense interconnected mesh of luminous nodes. Geometric precision. Wireframe aesthetic."` |
| glass and light | `"Crystalline glass surfaces refracting accent-colour light. Light caustics on dark background. Translucent layers."` |
| neon city | `"Futuristic cityscape abstracted to pure light — accent-colour and dark neon streaks. No recognisable buildings."` |
| blueprint tech | `"Technical blueprint aesthetic. Line-art schematics on dark background. Accent-colour highlighted circuits and nodes."` |
| organic / living | `"Bioluminescent neural network. Organic flowing forms. Accent-colour glow like deep-sea life. Cinematic."` |
| minimal / clean | `"Extreme minimalism. One central accent-colour element on dark background. Maximum negative space. Zen-like clarity."` |

---

## Model selection quick reference

| Use case | Model | Aspect ratio | Key params |
|---|---|---|---|
| 4:5 cover art (default) | `recraft-v4-1` | `4:5` | `model_type: standard`, `colors: [<active brand palette hexes>]`, `resolution: 2k` |
| 4:5 cover art (fallback) | `nano_banana_pro` | `4:5` | `resolution: 2k` |
| Per-card backgrounds (V2) | `recraft-v4-1` | `4:5` | same as above; vary prompt per card |
| Motion teaser (interactive) | `kling3_0` | `9:16` | `start_image: <cover art media_id>`, `duration: 5` |
| Motion teaser (headless) | `higgsfield-ai/dop/standard` (pinned) | — | `image_url: <upload_image(...)>`, `duration: 5` (no model param) |

---

## Approximate credit costs (as of June 2026 — verify with `get_cost: true`)

These are rough benchmarks only. Always run `get_cost: true` — never quote costs from this table without confirming.

| Job | Approximate credits |
|---|---|
| V1: 1x `recraft-v4-1` image, 4:5, 2k | ~8–15 credits |
| V2: 9x `recraft-v4-1` images, 4:5, 2k | ~70–135 credits |
| V3: 1x `kling3_0` video, 9:16, 5s (interactive) | ~7–8 credits; headless DoP ≈ $0.35 est. |
| V1+V3 combined | ~60–115 credits |

At ~$0.01/credit (rough guide), V1+V3 ≈ $0.60–$1.15 per carousel. Verify with actual preflight.
