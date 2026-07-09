
# Carousel Visuals

Produce AI-generated visuals that layer on top of or replace the dark-cinematic Slidev layouts in a `carousel-pdf` deck. Four modes: **V1 cover art** (one 4:5 image for the hook card), **V2 full backgrounds** (4:5 image per card), **V3 motion teaser** (9:16 short video reel from the hook card), and **V4 Instagram carousel** (4:5 or 1:1 image per card for an IG feed carousel, 7–10 cards). Budget is a hard stop, not a warning. Higgsfield is optional — the text-only carousel is always the free fallback.

> Resolve the **active profile** (the agent provides it; everything company-specific loads from `profiles/<active>/`, never `plugin/`). Read the brand name and palette from the active profile's brand assets — never hardcode a company name or colour.

## Load context (in this order)

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `brand_name`, `language`, `monthly_tool_budget_usd`, `per_run_cap_usd`, and the Higgsfield connection status from `tools_metered` / Tools connected (`connected | not connected`).
2. **Brand assets** — `profiles/<active>/knowledge/brand/` (read `BRAND-ASSETS-README.md` and the brand PDF/template for the palette reference). Pull the active company's brand colour palette from here — the prompt recipes reference these as the brand palette. Do not hardcode a colour; if the brand assets do not declare an explicit hex palette, infer the dominant brand colours from the brand template and confirm them with the colleague before generating.
3. **The carousel deck** the user is referring to — either the just-completed `carousel-pdf` output or a named deck under `<deck-workspace>/decks/<slug>/`. Read `slides.md` to extract the topic, hook headline, and any per-card headlines if running V2.

## Pre-flight — pick the generation path

This skill renders through one of two paths. **Detect which is available, in this order:**

- **Headless path (server runtimes)** — if the in-repo **`gemini_image`** (images) and/or
  **`higgsfield_video`** (video) MCP tools are available, use them. `gemini_image` calls Gemini 3 Pro
  Image directly (model pinned server-side, exact-USD metering, saves to a file path you pass);
  `higgsfield_video` runs the Higgsfield REST DoP Standard image-to-video flow (upload → submit →
  poll). There is no Higgsfield connector and no
  `get_cost`/`balance`/`colors`/`media_upload`/`models_explore` on this path — the brand palette goes
  **in the prompt text**.
- **Interactive path (Plugin/Cowork)** — else, if the **Higgsfield connector** is connected
  (`higgsfield: connected`, MCP tools available), use the Higgsfield MCP: `nano_banana_pro` for images
  and `kling3_0` for the motion teaser (per-mode detail below). This path keeps
  `get_cost`/`balance`/`colors`/`media_upload`.

If **neither** is available:
- Tell the colleague: "No image/video generator is connected — the text-only carousel is ready as-is.
  To add visuals, connect Higgsfield from Claude's connector settings (search 'Higgsfield'), then say
  'add visuals to the carousel'."
- **Stop here.** Do not attempt to generate.

Otherwise continue.

## Gather inputs

In one short message ask:
- **Mode** (if not obvious from context):
  - **V1 — Cover art** (default): one cinematic background image for the hook card (Card 1). Cheapest.
  - **V2 — Full backgrounds**: one 4:5 image per card (all 8–10 cards). More credits.
  - **V3 — Motion teaser**: animate the hook card into a 9:16 short video (4–8s). For LinkedIn Reels or Stories.
  - **V1+V3** together is a common pairing — offer it.
- **Art direction** (optional): style modifier beyond "dark cinematic". E.g. "abstract data-mesh", "glass and light", "neon city", "blueprint tech". Leave blank to use the defaults from `references/prompt-recipes.md`.
- **Carousel topic / slug** if not already established.

If the colleague just said "add visuals" without specifying mode, default to V1+V3 and confirm.

## Budget preflight — hard stop pattern

Run before the **first** generation call of the run. No exceptions — this preflight + the colleague's
acknowledgement is the only spend gate.

### Step 0 — Monthly-cap precheck (both paths)
Run:
```bash
python -m gtm_core.ledger_cli month-total --profile <active> --cap <monthly_tool_budget_usd>
```
If it reports `over_cap: true` (or exits 2), **stop** — the text-only carousel is the free fallback.
Do not generate.

### Step 1 — Estimate the run — by path
- **Headless** — costs are **auto-logged** by the workers; there is no `balance`/`get_cost`. Estimate
  yourself: each image via `gemini_image` ≈ **$0.134** at `1K`/`2K` (× card count); each motion teaser
  via `higgsfield_video` ≈ **$0.35** (DoP Standard estimate).
- **Interactive (Higgsfield)** — call `balance` (if credits < 50, show it and ask the colleague to top
  up, then stop), then call `generate_image`/`generate_video` with `params.get_cost: true` and the
  exact params (model, aspect_ratio, count) to get the credit cost without spending. `$0.01/credit` is
  a rough guide for relating credits to the dollar cap — `show_plans_and_credits` if asked.

### Step 2 — Show + confirm
Tell the colleague the run total — headless: *"~$X across <N> images + <M> teaser(s)"*; interactive:
*"X credits (balance: Y); per-run cap $Z."* If it exceeds the per-run cap, stop and offer to reduce
scope (e.g. V1 only) or use the text-only carousel.

### Step 3 — Wait for explicit "yes" or "proceed"
Never generate without acknowledgement. "just do it" / "go for it" in the original request counts as
pre-authorisation — skip the confirm step and note "auto-approved per your instruction."

## Mode V1 — Cover art (4:5 hook card image)

### Model & call — by path

Resolve the output directory first:
```bash
python -c "from gtm_core.paths import resolve_content_root; print(resolve_content_root())"
```
All outputs for this skill go under `<content_root>/<active>/carousel-visuals/<deck-slug>/`. Create the directory before the first call:
```bash
mkdir -p <content_root>/<active>/carousel-visuals/<deck-slug>
```

- **Headless (`gemini_image`)** — call `generate_image(prompt, output_path, resolution="2K",
  aspect_ratio="4:5")`. Model is pinned server-side — **do not pass a model**. `output_path` =
  `<content_root>/<active>/carousel-visuals/<deck-slug>/cover-art.png` (directory must exist); use the **returned** path.
- **Interactive (Higgsfield)** — call `generate_image` with model **`nano_banana_pro`** (fallback
  `recraft-v4-1`), `aspect_ratio: "4:5"`, `params.resolution: "2k"`, `params.model_type: "standard"`,
  and `params.colors` = the active brand palette hexes (from `profiles/<active>/knowledge/brand/`).

### Prompt construction
Read the hook headline from the carousel deck's `slides.md` (Card 1 `<h1>`). Then:

1. Start with the **base prompt template** from `references/prompt-recipes.md` for the product theme.
2. Embed the hook headline as the `{HOOK}` token.
3. Append the art direction modifier if supplied.
4. Append the brand enforcement suffix from `references/prompt-recipes.md`, naming the active company's
   brand palette (resolved from `profiles/<active>/knowledge/brand/`). On the **headless path the
   palette must be named in the prompt text** (there is no `colors` param). E.g. `"Colour palette:
   <primary brand colour> background, <accent brand colour> highlights. No text. No faces. Dark
   cinematic. High contrast. Futuristic abstract."`

Show the constructed prompt to the colleague before generating. If they want to adjust, do so first.

### Output
Save the image as `<content_root>/<active>/carousel-visuals/<deck-slug>/cover-art.png` (on the headless path this is the `output_path` you pass; on the interactive path download the returned URL there).

After saving, emit the file sentinel so Telegram delivers it automatically:
```
⟦FILE:<content_root>/<active>/carousel-visuals/<deck-slug>/cover-art.png⟧
```

Then instruct the colleague: "Drop `cover-art.png` into `<deck-workspace>/decks/<slug>/images/` and update Card 1's frontmatter with `background: images/cover-art.png` or per the layout conventions in `${CLAUDE_PLUGIN_ROOT}/skills/carousel-pdf/references/layout-conventions.md`."

## Mode V2 — Full backgrounds (4:5 per card)

Same path + model as V1 but run one generation per card. Cards that are text-heavy (stat, quote, CTA) often work better with a semi-abstract or blurred background rather than a detailed scene — note this in the prompt per card.

**Preflight covers the full batch**: headless → estimate `count × per-image rate`; interactive → call `get_cost` with `count = number of cards` before running anything.

Name outputs: `<content_root>/<active>/carousel-visuals/<deck-slug>/card-01.png`, `card-02.png`, etc. (headless: one `output_path` per card; interactive: download each returned URL).

Per-card prompt construction: pull the headline from each card in `slides.md`. Apply the same base template + brand suffix. For CTA cards, use a simpler, darker version (less detail, more negative space for the trigger word overlay).

After saving all cards, emit one file sentinel per image:
```
⟦FILE:<content_root>/<active>/carousel-visuals/<deck-slug>/card-01.png⟧
⟦FILE:<content_root>/<active>/carousel-visuals/<deck-slug>/card-02.png⟧
… (one line per card)
```

## Mode V3 — Motion teaser (9:16 video)

### Flow — by path
- **Headless (`higgsfield_video`)**:
  1. **Source image** — use the V1 cover art if already generated; otherwise generate it first via
     `gemini_image` (cover art is always the motion teaser source — do not skip V1 for V3).
  2. **Upload** — call `higgsfield_video.upload_image(<local cover-art path>)` to get a public
     `image_url`. DoP needs a hosted image; there is no local-file or `media_id` input.
  3. **Generate + poll** — call `generate_video(image_url, prompt, duration=5)`; it returns
     `request_id:<id>` immediately. Then poll `check_video_status(request_id)` every ~30–60s until
     `status:completed video_url:<url>`, and download that URL to disk. The model is pinned
     (`higgsfield-ai/dop/standard`) — **do not pass a model**.
- **Interactive (Higgsfield)**:
  1. **Source image** — as above (cover art).
  2. **Upload** — via `media_upload` / `media_import_url`; save the returned `media_id`.
  3. **Generate** — run `get_cost: true` first (same hard-stop pattern), then `generate_video` with
     model **`kling3_0`** (9:16, image-to-video; multi-shot, audio, motion transfer),
     `aspect_ratio: "9:16"`, `duration: 5`, `params.medias: [{value: <media_id>, role: "start_image"}]`.

### Motion prompt construction
From `references/prompt-recipes.md` V3 section. The motion prompt describes camera move + atmosphere, not the static content (the image handles that). Keep it short: `"Slow cinematic dolly in. Teal particles drift upward. Dark atmospheric. No text."`

### Output
Save video as `<content_root>/<active>/carousel-visuals/<deck-slug>/motion-teaser.mp4`.

After saving, emit the file sentinel so Telegram delivers it automatically:
```
⟦FILE:<content_root>/<active>/carousel-visuals/<deck-slug>/motion-teaser.mp4⟧
```

Tell the colleague: "Post `motion-teaser.mp4` as a LinkedIn Reel or native video in the same post window where you upload the PDF carousel — or as a teaser Story the day before. Caption: use the hook-led variant from `outputs/caption.md`."

## Mode V4 — Instagram / X multi-image carousel (4:5 or 1:1 per card)

For an **Instagram feed carousel** (`platform: instagram, format: carousel`) rather than a LinkedIn
deck. Source the per-card copy from a `content-studio` Instagram carousel asset (`slides[]`, 7–10
entries) or the deck's `slides.md`.

The same image set doubles as an **X organic carousel** (2+ images render swipeable on iOS since
Apr 2026, grid on web/Android — see `${CLAUDE_PLUGIN_ROOT}/skills/carousel-pdf/references/platform-playbook.md`
§3). X rules: **≤4 images**, one uniform aspect ratio across all of them (1:1 or 4:5), centre-safe
composition (the grid crops from centre), **payoff image first**. For X, select the 4 strongest
cards rather than exporting all 7–10.

### Model & parameters
Same path + model as V1/V2 (headless `gemini_image` / interactive `nano_banana_pro` with
`params.colors` = active brand palette). Set `aspect_ratio: "4:5"` (1080×1350, the IG feed default) or
`"1:1"` (1080×1080) — confirm which with the colleague. Run one generation per card.

### Flow
- IG carousels are **7–10 cards** (vs LinkedIn 8–12) — match the slide count of the content-studio
  asset; the content linter already enforces the 7–10 range on the copy.
- **Preflight covers the full batch**: headless → estimate `count × per-image rate`; interactive →
  call `get_cost` with `count = number of cards` once, as in V2.
- Keep text overlays minimal in the image (IG carousels are caption-led) — the same "no text, no
  faces, brand palette dominant" rules as V1/V2 apply.
- Save outputs to `<content_root>/<active>/carousel-visuals/<deck-slug>/ig-card-01.png` … `ig-card-NN.png` (headless: one `output_path` per card; interactive: download each returned URL).

After saving all cards, emit one file sentinel per image:
```
⟦FILE:<content_root>/<active>/carousel-visuals/<deck-slug>/ig-card-01.png⟧
⟦FILE:<content_root>/<active>/carousel-visuals/<deck-slug>/ig-card-02.png⟧
… (one line per card)
```

Tell the colleague: "Upload `ig-card-01..NN.png` as an Instagram carousel; use the caption from the
content-studio asset (caption-led, SEO keywords in the first line)."

## Free fallback

If Higgsfield is not connected, budget is exceeded, or the colleague declines the cost: the text-only carousel from `carousel-pdf` is complete and ready to post. Visuals are additive. Tell the colleague the carousel works without them and offer to proceed with posting prep (caption variants, DM copy from `outputs/dm.md`).

## Guardrails

- Budget preflight before **every** generation call — no exceptions, not even for a single image
  (interactive path runs `balance` + `get_cost`; headless path estimates from the per-image/per-teaser rate).
- Never generate without the cap precheck + cost preflight + colleague acknowledgement.
- No faces in brand visuals — use `"No faces, no people"` in every prompt.
- No text in generated images — the carousel layouts handle all text overlays.
- Brand colours in every image prompt — the active company's brand palette (from `profiles/<active>/knowledge/brand/`) must dominate.
- **Dark-mode-safe for X-bound images**: solid background always (never transparent), avoid pure
  #000/#FFF edges, and check the image reads against both a white and a near-black timeline
  (playbook §3).
- Draft alt text (one plain sentence per image) alongside the upload instructions — required for X, good practice everywhere.
- Never auto-post or upload to LinkedIn — draft only.
- Keep metered scope minimal — V1 (one image) is the default; only expand to V2 if explicitly requested.
