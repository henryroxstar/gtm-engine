# Provider workflow register

**What this is.** Higgsfield's MCP surface ships a catalog of bundled *workflows* — each one a
`SKILL.md` that orchestrates the `generate_*` tools for a whole deliverable. `get_workflow_instructions`
lists them; called with a name, it returns the full procedure. Six of them overlap lanes this repo
built by hand.

**Why it exists.** Until 2026-08-29 this repo had never called `get_workflow_instructions` — not
once. So every overlap was an accident nobody had decided about, and the same rediscovery was
available to every future session. This file is the decision, made once, in writing, dated. A
workflow with **no row here has not been declined — it has been overlooked**, which is the failure
this register exists to make visible.

**The rule it enforces.** *Before hand-rolling a multi-step generation flow, call
`get_workflow_instructions` and check this file.* Adopting is not the default and declining is not
a failure; an undated, unwritten, re-improvised answer is.

**How to keep it current.** Re-run `get_workflow_instructions` (free, no arguments, returns the
catalog). A name in the catalog that is not in the table below needs a row before anything is built
against it. `tests/lint/test_provider_workflow_register.py` pins the shape of this file, not its
verdicts — a verdict is a human decision and this repo does not auto-generate them.

**One home per fact.** Skill bodies **cite** this file; they never copy the table. A workflow list
restated in a skill body drifts from the catalog the moment the provider ships version 1.2.

---

## The register

Catalog probed **2026-08-29** (16 workflows). Verdict `decline` means *not wired into any lane
today* — it is a scope decision, never a quality judgement about the provider's work.

| Workflow | Ver | Verdict | Reason | Probed |
|---|---|---|---|---|
| `ad-multiplier` | 1.4 | decline | Generates many edited variants of one supplied 4–30s ad by swapping people/products. No lane has that shape: every pack produces one authored cut per content item, and variant sprawl is the opposite of the hook-attribution model in `outcomes.jsonl`. | 2026-08-29 |
| `brand-asset-creation` | 1.1 | decline | Logos, brandbooks, decks, merch. Brand assets here come from the tenant's own `BRAND.toml` kit and the repo's own deck renderer; generating a brand identity is not a content-lane job. | 2026-08-29 |
| `character-sheet` | 1.0 | decline | **Load-bearing — this is the answer C4 cites.** It builds a consistent non-real character across views, which is a genuine mechanism for a presenter who is not the operator. It stops short of the actual need: it assembles an **image** prompt and runs `generate_image`. A consistent generated character still cannot *speak*, because no video model on this MCP does audio-driven lip sync (`gtm_core/render_engines.toml`, verified 2026-08-19). So it answers the likeness half of the stock-avatar request and none of the talking half. Wiring it up is a lane decision with its own disclosure analysis, not a routing one. | 2026-08-29 |
| `faceless-video` | 2.4 | decline | Its own scope note excludes ads, product demos, and anything with an on-camera presenter — which is what these shorts are. It also locks a non-photoreal style and burns its own subtitles, both of which this repo owns elsewhere (`BRAND.toml` imagery keys; Reap captions). Name-collision warning: it is **not** our `short-form-video` faceless lane. | 2026-08-29 |
| `narrator` | 1.1 | adopt (rule only) | **Load-bearing — the C9 spike; see below.** The workflow's execution model is declined; its central rule is adopted and already held. | 2026-08-29 |
| `product-photoshoot` | 1.0 | decline | Packshots, catalog and lifestyle product stills. No product-photography lane exists; if one is wanted it is a lane decision, and this is the first thing to evaluate for it. | 2026-08-29 |
| `subtitles` | 1.0 | decline | Burns captions into pixels from Whisper timings. Captions are Reap's job — per-word timings, 50+ presets, and face-safe placement resolution that `gtm_core.captions` already reads. Recorded so the decline is a decision, not an oversight; also stated in `video-router`'s provider-boundary table. | 2026-08-29 |
| `thumbnail-generation` | 1.3 | decline | The strongest adopt candidate outside the video lane (YouTube/IG covers, 4K routing, identity lock). Declined 2026-08-29 **only** because no lane produced a thumbnail as a deliverable. **Revisited 2026-09-06**, which is when one started to: `creator-brief` decision 1 designs the cover before the content. Still declined, and now for a different and narrower reason — the cover is generated where the identity already is, as a delta edit of the approved hero still through `generate_image`'s ordered `reference_images`, so cover and video share one identity by construction. Adopting a separate cover workflow would re-invent that identity in a second place, which is the wardrobe-drift failure the one-hero rule exists to stop. Re-open if a cover is ever wanted for a piece with no hero still. | 2026-09-06 |
| `ugc-product-video` | 1.0 | decline | Product-only UGC ad. No product-ad lane; the content model is founder-led commentary, not product advertising. | 2026-08-29 |
| `ugc-review-video` | 1.1 | decline | A generated creator speaking to camera. This is precisely the synthetic-talking-head shape `render_engines.toml` refuses on this provider — mouth motion approximated from prompt text, not generated from audio (verified 2026-08-19, mouth closed in 9 of 10 sampled frames). Adopting it would route *around* the engine registry rather than through it, and it carries an unchanged Art. 50 duty. | 2026-08-29 |
| `ugc-try-on-video` | 1.0 | decline | Wearable try-on UGC. No lane, no product. | 2026-08-29 |
| `ugc-tutorial-video` | 1.0 | decline | Step-by-step product how-to UGC. No lane, no product. | 2026-08-29 |
| `ugc-unboxing-video` | 1.0 | decline | Unboxing/first-reaction UGC. No lane, no product. | 2026-08-29 |
| `ugc-website-video` | 1.0 | decline | Site/app walkthrough UGC built from captured screenshots. Closest of the six to something real (a product demo), but it needs a creator speaking to camera throughout — same engine refusal as `ugc-review-video` — and `demo-clips` already covers a real screen recording with no synthetic presenter at all. | 2026-08-29 |
| `video-editing` | 1.0 | decline | A file-backed edit engine (`higgsedit`) run inside the provider's `sandbox_exec`. Finishing here is local `ffmpeg` in `gtm_core/video_finish.py`. Adopting it would move mux/grade/caption work into a provider sandbox — outside the content-root file boundary and outside the §R6 egress path — for capability we already have locally. | 2026-08-29 |
| `website-builder-flow` | 2026-07-25 | decline | Builds websites/web apps/browser games. Not media, no lane, and it wants website tools this brain does not hold. | 2026-08-29 |

---

## Spike: `narrator` (C9 → C1)

**The question.** `narrator` owns speech-duration fitting with retries and states, as a hard rule,
*never time-stretch*. `gtm_core.video_finish.mux` time-stretches by design (`atempo`, bounded). If
the workflow's take-fitting lands VO inside the window, `atempo` becomes a fallback rather than a
routine path. Is it worth adopting?

**The answer: decline the workflow, adopt the rule — which this repo already holds.**

*Why the workflow is declined.* Its machinery is Higgsfield-sandbox-shaped and does not compose
with this architecture. It measures takes with a bundled script under `sandbox_exec`, keeps a
`voice.lock` file, batches through `generate_audio_batch`/`jobs_wait`, and — in continuous mode —
`curl -f` PUTs the joined WAV from inside that sandbox. This repo's VO is rendered by `video-render`
through its own MCP calls, and every byte that moves goes through `gtm_core.media_fetch` /
`gtm_core.reap_upload` under a host-pinned allowlist (§R6). Adopting the workflow means adopting its
egress and its file layout, for a fitting loop this repo can express in its own linter.

*Why the rule is adopted.* "Fit by rewriting the line, not by processing the audio" is the correct
ordering and `gtm_core.shots_lint` already gives it: the estimate-band error says *round up the
duration, **or cut words***, and the measured-parity error says *render at duration=Ns*. Neither
offers `atempo` as a remedy — the mux's bounded stretch is the last resort it was built as, not the
routine path. **No code change falls out of this spike**, which is the honest result and is recorded
so the question is not reopened from scratch.

*The one divergence found, measured not assumed.* The provider calibrates its own TTS at a **2.9
words/sec** ceiling, above which it rejects a take as `RUSHED`. `shots_lint`'s estimate band runs to
**3.51 wps** at its fast end (`VO_WORDS_PER_SEC_FAST`, measured from our own 2026-08-19 renders). The
two do not contradict — 3.51 is what the engine *did* deliver, 2.9 is what the provider will
*accept* — but it means a line sitting at the top of our band would pass our parity check and be
rejected as rushed by the provider's own gate.

Checked against two real shot lists before writing this down:

```
2026-08-18-agent-gateway-auditor-v2   5 spoken shots   max 2.86 wps   0 over 2.9
2026-08-17-ai-disclosure-wrong-number 7 spoken shots   max 2.80 wps   0 over 2.9
```

Twelve spoken shots, none within 0.04 wps of the ceiling and none over it. Authored density is
already inside the provider's calibration, so tightening `VO_WORDS_PER_SEC_FAST` would be a change
with no observed defect behind it — and re-pinning a measured constant on a docs figure is the
restated-numbers-go-stale error running the other way. Recorded, not acted on. Revisit if a parity
warning ever fires on a line above 2.9 wps.

*Useful figures carried over* (provider's own calibration, for authoring — not copied into any skill
body): ~20–23 words per 10s line, at most two sentences, comma-light; a period costs ≈0.7s and a
comma ≈0.5s of dead air; performed brackets (`[scoffs]`) cost ≈1s.

---

## Spike: explainer style presets (C12a)

**The question.** `get_explainer_presets` returns a CMS-managed catalog of named styles, each
resolving via `resolve_explainer_preset` into a **style-reference media id**. Style consistency is
currently hand-written prose per shot. Is a preset id a durable style anchor worth adding to
`BRAND.toml` as `imagery.explainer_preset_id`?

**The answer: decline for the video lanes; keep the finding for a future explainer lane.**

Probed 2026-08-29: **22 presets, every one `aspect: "9:16"`**, and every one a named illustration
style — `Stickman Cartoon`, `Watercolor Chronicle`, `Paper Diorama`, `Claymotion`, `Pixel Art`,
`Whiteboard Doodle`, `Low Poly`, `Fluffy Toy`. Two things follow, and neither needed a spend to
establish:

1. **The catalog is illustrated-explainer styling.** Not one preset is a photoreal or
   cinematic-live-action look. The lanes that would consume a style anchor (`short-form-video`
   b-roll, `presenter-video` cutaways) are photoreal by construction — they intercut with a real
   person, or with a trained Soul still of one. Pinning an illustration style there would fight the
   look, not stabilise it.
2. **`resolve_explainer_preset` writes.** Its own description says the backend *imports the preset's
   style image into the user's media storage*. So it is not a free read: it mutates the account's
   media library. That does not make it wrong, but it does mean it cannot sit in `video-router`'s
   Step 0.5, where every check is a zero-write read.

**Not verified, deliberately:** whether a resolved media id is accepted as a style reference on the
specific calls `video-storyboard` makes. That test costs a write plus a render, and the answer only
matters for a lane whose look this catalog does not serve. If an illustrated-explainer lane is ever
built, this is the first thing to check and `BRAND.toml` is where the id belongs.

---

## Decline: HeyGen Video Agent (C14)

`create_video_agent` and its scene-by-scene prompting guide are documented and good — for a
**chat-mode** conversation with the provider. This pipeline is deterministic: `video-avatar` sets
avatar, look, voice, and `voice_settings` explicitly, and every one of those is a value some gate or
linter reads before a render is paid for. An agent-shaped conversation owns those choices instead,
which is the wrong trade for a lane whose whole design is that decisions are inspectable before
spend. Declined 2026-08-29 as a decision, not an oversight.

## Unlock: HeyGen templates (C12b)

`list_templates` returns `{"items": [], "has_more": false}` — re-confirmed 2026-08-29. This is not a
missing capability: HeyGen templates are authored in HeyGen's **web editor**, and only templates that
define variables are exposed to the API. So `generate_from_template` — a repeatable presenter render
with variable swaps instead of a fresh per-render prompt — is unavailable **for a reason an operator
can fix in an afternoon**. It is recorded as a named optional step in `identity-kit`'s HeyGen path,
alongside the twin and the voice.

## Verified: Gemini reference-image conditioning (C1)

`gemini-3-pro-image-preview` **does** condition on an inline reference image passed ahead of the text
part. Verified live **2026-09-06**, because no fake can assert it: the worker's tests prove we *send*
N `inlineData` parts in the author's order, and whether the model *honours* them is provider behaviour
observable only against the real endpoint (PRD §3.5).

**Method.** One fixed prompt — a ceramic mug on a marble countertop — run twice at 1K through
`agent/mcp/gemini_image`, once bare and once with a single deliberately synthetic reference: a flat
teal mug carrying two columns of magenta chevrons and a **square** handle, a combination no real
product has, so its presence in the output cannot be coincidence or prompt leakage.

**Result.** The bare render returned a generic speckled cream stoneware mug with a round handle. The
reference-bearing render returned a teal mug with both chevron columns and the square handle,
photographed under the prompt's lighting and depth of field — the *subject* came from the reference,
the *scene* from the text. Conditioning is real and is strong enough to carry an unnatural shape, which
is the property the delta-edit chain in `video-storyboard` depends on.

**Two things the same run confirmed.** The return shape is additive — the bare call returned a bare
absolute path, byte-identical to its pre-C1 contract, and only the reference-bearing call appended the
`references sent (…)` manifest, in basenames. And metering is no longer flat: the two `costs.jsonl`
rows carry `n_references` 0 and 1, and the pair totalled the amount predicted from the published input
rate before the calls were made, so the per-reference price constant is not a guess.

**What it does not establish.** That every reference in a longer list is weighted equally, or that a
reference is honoured at all resolutions — one pair of 1K calls cannot show either. Re-verify here if a
lane starts depending on positional weighting rather than on presence.

## Audit: first/last-frame video, and where it may be rendered (C10)

A full live `models_explore` enumeration on **2026-09-07** (`action=list`, `type=video`,
`has_more=false` — the whole catalog, not a query's ranked page) answered a question the keyframe
lane had been treating as open: which models take a **last** frame as well as a first.

**The answer was already in the registry.** `wan2_7` — the engine bound to `roles.broll` since
August — declares `medias[].roles` of `["start_image", "end_image", "audio_references"]`. The
keyframe lane therefore needed no new engine, no second model to keep verified, and no new pricing
surface. `roles.keyframe_broll` binds straight back to `higgsfield_i2v`; the role exists so the
capability is **checked** (`requires = { accepts_end_frame = true }`), not to introduce a rival.

Nineteen other catalog models declare both keyframe roles, `wan3_0` / `wan3_0_prime` tagging
`first-last-frame` outright. None is bound. Choosing between them is a side-by-side with the
operator's eyes on the output, not a tag comparison — see PENDING.

### Verified: wan2_7 honours the end frame

Accepting a media role and **honouring** it are different claims, and only the second is worth
binding a lane to. Verified live **2026-09-07** against the connected Higgsfield MCP.

**Method.** Two stills built as a delta-edit pair, exactly as `video-storyboard` produces them: a
red cube on a white table (`nano_banana_pro`), then the same image edited to change only the cube's
colour to deep blue. Measured before spending anything on video — the start still was 47.6% red /
0.0% blue over its centre third, the end still 0.0% red / 45.6% blue. No overlap, so an ignored end
frame cannot be mistaken for a honoured one. Then one `wan2_7` render at 9:16 / 5 s / 7.5 credits
carrying both roles and a deliberately terse motion prompt ("the cube shifts colour, slow and
steady"), downloaded through `gtm_core.media_fetch` and sampled at both ends.

**Result.** First frame 47.6% red / 0.0% blue; last frame 0.0% red / 45.1% blue. The clip starts on
the start still and arrives on the end still. Output was 716×1284, 30 fps, 5.06 s.

**What it does not establish.** That the *path between* the two frames is directable — a one-clause
prompt on a colour change says nothing about a model asked to interpolate a camera move and a
subject action at once. It also says nothing about the **headless** DoP Standard endpoint, which is
a different surface (below).

### Verified: the headless DoP Standard endpoint honours `end_image_url` — under that exact name

Probed **2026-09-07**, replacing the fail-closed refusal that shipped earlier the same day when no
credentials were reachable. Three arms against the live endpoint, same start still and same
prompt, each measured on the clip's **last frame** rather than on its HTTP status:

| arm | request | last frame | reading |
|---|---|---|---|
| A (control) | no end frame | 45.3% red / 0.0% blue | stays on the start still, as it should |
| B | `end_image_url` | 0.0% red / **45.4% blue** | **arrives on the end still — honoured** |
| B | `end_image` | 0.3% red / 0.0% blue | arrived nowhere |

**All three returned HTTP 200 `queued`.** That is the finding that matters more than the pass:
the endpoint accepts an unknown key without complaint, renders, and charges — so `end_image`
produced a plausible, fully-priced clip that went nowhere in particular. The status code carries
no information about whether the field was read, which is why the verdict is measured on pixels.

Two consequences. `agent/mcp/higgsfield_video.generate_video` now passes `end_image_url` through
(added to the body only when supplied, so a request without one is byte-identical to every render
that predates keyframes, asserted by dict equality). And **the field name is pinned by an
exact-equality test, not a substring check** — a rename would satisfy a laxer assertion and fail
silently in production, which is exactly what arm B's second spelling did.

**What it does not establish.** That the *path between* the two frames is directable on this
endpoint — one colour change says nothing about a model asked to interpolate a camera move and a
subject action at once. Nor anything about duration or ratio interactions with the end frame.

### Incidental, and worse than the catalog suggests: wan2_7 returns audio nobody asked for

The same verification render came back with a stereo AAC track at **mean −22.6 dB / max −5.5 dB** —
real content, not silence — with no `audio_references` passed and **no parameter that can switch it
off** (`wan2_7` exposes `duration` and `resolution` only). B-roll is scored in post, so this is
unwanted every time.

It is stripped downstream, but only conditionally: `gtm_core.video_finish.mux` maps
`-map 0:v -map 1:a`, which drops it — on a cut that HAS a narration track to map. A lane with no
narration stitches it straight through, because `stitch` maps `[aout]`. Recorded here and in
PENDING; not fixed in C10, which changes no audio path.

## Open: music-bed reuse rights (C6)

**Verdict: PENDING. Owner: the operator.** Raised 2026-09-07 by C6, which added `start_at_s` to
`build_music_bed` — the bed can now open on a cue's drop rather than its intro. That is a craft
change and it changes nothing about the licence, but it is the moment the question became worth
writing down rather than assumed.

The catalogue is HeyGen's, reached through `search_audio_sounds` and downloaded from the
bucket-scoped host already pinned in `gtm_core.media_fetch.ALLOWED_HOSTS`
(`heygen-product.s3-accelerate.amazonaws.com`, pinned 2026-08-29 — **re-verified 2026-09-07, still
the only music host and unchanged by C6**). What is NOT established:

- whether a bed extracted from that catalogue may be **looped, trimmed and re-timed** the way
  `build_music_bed` does, or only used as delivered;
- whether the licence follows the **finished asset** once it leaves HeyGen's own render;
- whether it covers **client-facing** work, or only the account holder's own posts.

Nothing here blocks a render — the bed is already being built and shipped, and has been since
August. It is recorded so the answer is looked up once, by a person reading the terms, rather than
re-assumed by each session. **Do not resolve this by reading a vendor marketing page.**
