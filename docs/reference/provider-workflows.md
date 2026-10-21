# Provider workflow register

**What this is.** Higgsfield's MCP surface ships a catalog of bundled *workflows* — each one a
`SKILL.md` that orchestrates the `generate_*` tools for a whole deliverable. `get_workflow_instructions`
lists them; called with a name, it returns the full procedure. Several of them overlap lanes this repo
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

Catalog probed **2026-10-19** (13 workflows; the 2026-08-29 probe saw 16). Verdict `decline` means
*not wired into any lane today* — it is a scope decision, never a quality judgement about the
provider's work. **Ver** is the 2026-10-19 catalog's. **Probed** is the day a row's verdict was last
decided or reopened: full instructions were reloaded on 2026-10-19 only for the rows dated that day,
and the rest were checked against the catalog listing (name, version, description) without being
re-argued. A workflow that has left the catalog keeps its row, marked *removed* with the date,
rather than being deleted — a verdict is evidence if the name returns.

| Workflow | Ver | Verdict | Reason | Probed |
|---|---|---|---|---|
| `ad-multiplier` | 1.5 | decline | Generates many edited variants of one supplied 4–30s ad by swapping people/products. No lane has that shape: every pack produces one authored cut per content item, and variant sprawl is the opposite of the hook-attribution model in `outcomes.jsonl`. **Revisit note 2026-10-19 — Recreate `sections` mode (new in 1.5), verdict unchanged.** Recreate (`ad_multiplier_v2`) can now re-roll only the hook, only the CTA, or both (`mode:"sections"`, `section` set to `hook`, `cta` or `both`) and stitch the new part into the source with hard cuts, leaving the rest of the ad untouched. That is one authored cut with one section varied — not variant sprawl, and the first shape in this workflow that could serve hook attribution. Not adopted because no lane asks for it yet and what it would need is unproven: a re-rolled cut is a second cut of one content item, so it needs a recorded identity in `outcomes.jsonl` before a result could be attributed to the hook; the backend, not us, picks the cut point and the new section's length (hook 4–15s, CTA 4–5s), so the variable is not held to the wording alone; any generated person carries the `⟦IDENTITY⟧` disclosure duty; and the model is credits-only, Pro plan or higher, with the longest length reserved up front. Reopen when a published authored cut's hook is the open question, and probe on that one cut before wiring anything. | 2026-10-19 |
| `ads-studio` | 1.2 | decline | Static ad creatives (copy plus pictures, in Meta/Instagram feed and story frames) for a product or a brand, run through its own `ads_studio_*` tools. No lane produces a static ad — the content model is founder-led commentary — and the B2B fit of consumer-style ad creatives is weak. Two properties would stop it even if one did: it researches a website into a provider-side brand kit, a second brand source beside the tenant's `BRAND.toml` when company and brand facts load from the tenant profile; and its own instructions say a creative lives on the provider's card with no image link for the caller to hold, so the exact bytes could never reach the publish gate for approval. New to the catalog 2026-10-19. | 2026-10-19 |
| `brand-asset-creation` | 1.1 | decline | Logos, brandbooks, decks, merch. Brand assets here come from the tenant's own `BRAND.toml` kit and the repo's own deck renderer; generating a brand identity is not a content-lane job. | 2026-08-29 |
| `character-sheet` | 1.0 | decline | **Load-bearing — this is the answer C4 cites.** It builds a consistent non-real character across views, which is a genuine mechanism for a presenter who is not the operator. It stops short of the actual need: it assembles an **image** prompt and runs `generate_image`. A consistent generated character still cannot *speak*, because no video model on this MCP does audio-driven lip sync (`gtm_core/render_engines.toml`, verified 2026-08-19). So it answers the likeness half of the stock-avatar request and none of the talking half. Wiring it up is a lane decision with its own disclosure analysis, not a routing one. | 2026-08-29 |
| `faceless-video` | 2.4 | decline | Its own scope note excludes ads, product demos, and anything with an on-camera presenter — which is what these shorts are. It also locks a non-photoreal style and burns its own subtitles, both of which this repo owns elsewhere (`BRAND.toml` imagery keys; Reap captions). Name-collision warning: it is **not** our `short-form-video` faceless lane. | 2026-08-29 |
| `katana` | none listed | decline | Remixes a reference edit (a third-party short-form clip, which it may choose for itself) into a finished, size-capped MP4 for the user's subject, entirely inside the provider's sandbox. Three mismatches. Only the delivered file reaches this repo — a recoverable project is saved "when supported" — so there is no shot list or timeline for `shots_lint` or `video_finish` to read or amend. Its instructions forbid asking or confirming before it spends credits, the opposite of this repo's inspect-before-spend design (the same reason as the HeyGen Video Agent decline below). And to the extent the need is motion graphics or animated product UI, deterministic HTML motion scenes (`mc_product_film`) already render it locally at zero diffusion spend. New to the catalog 2026-10-19. | 2026-10-19 |
| `narration` | 1.1 | adopt (rule only) | `narrator` under its new catalog name — same verdict, and the C9 spike below stands: workflow declined, never-time-stretch rule adopted and already held. Re-read 2026-10-19: the 2.9 words/sec rate ceiling, the ~20–23 words per 10s line, the `voice.lock` file, the sandbox join and the `curl -f` PUT are all unchanged, so no figure in the spike needed correcting. **New since the spike, and not adopted:** Mode B ("put me in this video"), which turns an existing video plus a consenting person's photo into an on-screen narrator by generating a talking clip from the photo, swapping its voice, and keying it over the base picture. That is a prompt-driven synthetic presenter of a real person — the shape `render_engines.toml` refuses (`gemini_omni`, the model it generates with, is named nowhere in that registry, and the workflow's own rule is "never add a lipsync pass") — with an Art. 50 duty, so the mode is declined. | 2026-10-19 |
| `narrator` | 1.1 | adopt (rule only) | **Renamed `narration` in the catalog (seen 2026-10-19); see that row.** **Load-bearing — the C9 spike; see below.** The workflow's execution model is declined; its central rule is adopted and already held. | 2026-08-29 |
| `product-photoshoot` | 1.0 | decline | Packshots, catalog and lifestyle product stills. No product-photography lane exists; if one is wanted it is a lane decision, and this is the first thing to evaluate for it. | 2026-08-29 |
| `subtitles` | 1.0 | decline (removed from catalog 2026-10-19) | Burns captions into pixels from Whisper timings. Captions are Reap's job — per-word timings, 50+ presets, and face-safe placement resolution that `gtm_core.captions` already reads. Recorded so the decline is a decision, not an oversight; also stated in `video-router`'s provider-boundary table. The caption-burning scope now sits inside `video-montage`, declined for the same reason. | 2026-08-29 |
| `thumbnail-generation` | 1.3 | decline | The strongest adopt candidate outside the video lane (YouTube/IG covers, 4K routing, identity lock). Declined 2026-08-29 **only** because no lane produced a thumbnail as a deliverable. **Revisited 2026-09-06**, which is when one started to: `creator-brief` decision 1 designs the cover before the content. Still declined, and now for a different and narrower reason — the cover is generated where the identity already is, as a delta edit of the approved hero still through `generate_image`'s ordered `reference_images`, so cover and video share one identity by construction. Adopting a separate cover workflow would re-invent that identity in a second place, which is the wardrobe-drift failure the one-hero rule exists to stop. Re-open if a cover is ever wanted for a piece with no hero still. | 2026-09-06 |
| `ugc-product-video` | 1.0 | decline (removed from catalog 2026-10-19) | Product-only UGC ad. No product-ad lane; the content model is founder-led commentary, not product advertising. Folded into `ugc-video`'s `product` format; see that row. | 2026-08-29 |
| `ugc-review-video` | 1.1 | decline (removed from catalog 2026-10-19) | A generated creator speaking to camera. This is precisely the synthetic-talking-head shape `render_engines.toml` refuses on this provider — mouth motion approximated from prompt text, not generated from audio (verified 2026-08-19, mouth closed in 9 of 10 sampled frames). Adopting it would route *around* the engine registry rather than through it, and it carries an unchanged Art. 50 duty. Folded into `ugc-video`'s `review` format, which inherits this refusal; see that row. | 2026-08-29 |
| `ugc-try-on-video` | 1.0 | decline (removed from catalog 2026-10-19) | Wearable try-on UGC. No lane, no product. Folded into `ugc-video`'s `try-on` format; see that row. | 2026-08-29 |
| `ugc-tutorial-video` | 1.0 | decline (removed from catalog 2026-10-19) | Step-by-step product how-to UGC. No lane, no product. Folded into `ugc-video`'s `tutorial` format; see that row. | 2026-08-29 |
| `ugc-unboxing-video` | 1.0 | decline (removed from catalog 2026-10-19) | Unboxing/first-reaction UGC. No lane, no product. Folded into `ugc-video`'s `unboxing` format; see that row. | 2026-08-29 |
| `ugc-video` | none listed | decline | The one workflow that replaced the six `ugc-*` rows around it, with six formats — `review`, `product`, `unboxing`, `try-on`, `tutorial`, `website` — each ending in one finished video, and assembly and captions handed to `video-montage`. Every earlier reason carries over per format: unboxing, try-on and tutorial stay declined for want of a lane and a product, `product` for want of a product-ad lane, and `website` because `demo-clips` already covers a real screen recording with no synthetic presenter. The five formats that put a creator on camera also inherit the lip-sync refusal recorded for `ugc-review-video`, wherever that creator speaks. The workflow locks `seedance_2_5` with native audio, and that engine has no row in `gtm_core/render_engines.toml`, so its lip sync is unverified here and the refusal is inherited, not re-measured; the Art. 50 duty is unchanged. The `product` format (off-screen voiceover, nobody speaking to camera) is the one not blocked by lip sync. Reopen only by verifying native lip sync on that engine and registering it first. | 2026-10-19 |
| `ugc-website-video` | 1.0 | decline (removed from catalog 2026-10-19) | Site/app walkthrough UGC built from captured screenshots. Closest of the six to something real (a product demo), but it needs a creator speaking to camera throughout — same engine refusal as `ugc-review-video` — and `demo-clips` already covers a real screen recording with no synthetic presenter at all. Folded into `ugc-video`'s `website` format; see that row. | 2026-08-29 |
| `video-editing` | 1.0 | decline | A file-backed edit engine (`higgsedit`) run inside the provider's `sandbox_exec`. Finishing here is local `ffmpeg` in `gtm_core/video_finish.py`. Adopting it would move mux/grade/caption work into a provider sandbox — outside the content-root file boundary and outside the §R6 egress path — for capability we already have locally. | 2026-08-29 |
| `video-montage` | none listed | decline | Assembles existing clips, changes or mixes a soundtrack, and burns or restyles speech captions — ffmpeg run inside the provider's `sandbox_exec`. Same reason as `video-editing`: finishing here is local `ffmpeg` in `gtm_core/video_finish`, inside the content-root file boundary and the §R6 egress path. It owns the caption scope the removed `subtitles` row had (transcription, placement, burning), and that reasoning holds — captions are Reap's job, read through `gtm_core.captions`. `ugc-video` hands its assembly and captions to this workflow, so declining both keeps one local finishing path. New to the catalog 2026-10-19. | 2026-10-19 |
| `website-builder-flow` | 2026-07-25-cover-compose-v3-no-model-text | decline | Builds websites/web apps/browser games. Not media, no lane, and it wants website tools this brain does not hold. | 2026-08-29 |

---

## Spike: `narrator` (C9 → C1)

*Catalog rename, 2026-10-19: the provider now lists this workflow as `narration` (1.1). The spike
text below is unchanged and still describes it; the `narration` row carries its verdict forward.*

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

It was stripped downstream only conditionally: `gtm_core.video_finish.mux` maps
`-map 0:v -map 1:a`, which drops it on a cut that HAS a narration track to map, while a lane with
no narration stitched it straight through (`stitch` maps `[aout]`).

**Fixed 2026-10-19, in `stitch`.** The one place a rendered clip becomes a segment of the cut is
`stitch._normalize_shot`, and the one place a shot's `role` and `sfx` are both known there is the
shot list `stitch --transitions-from-shots` already reads. `shots.silent_from_shots` marks a shot
silent when it is `role: "broll"`, declares no `sfx` and has no `spoken` line; a silent segment has
its audio replaced with digital silence at the uniform audio format (video stream-copied unless it
is re-encoded anyway). A `sfx` shot keeps its audio and its cue; a `spoken` shot's segment is the
muxed VO, so it is left alone; `mux` and the narration track are unchanged. A segments file can
also say `"silent": true` on a segment. Tests:
`tests/media/test_video_finish_broll_silence.py`.

## Decided: b-roll stays on `wan2_7` — Wan 3.0 side-by-side (2026-10-19)

**The question.** Should `roles.broll` and `roles.keyframe_broll` move from `wan2_7` to `wan3_0`?
Wan 3.0 has an audio switch (`generate_audio`), runs 2–30 s and is the newer model. It also costs
more per second. The design sets a bar for switching, and the trial did not clear it, so the
b-roll roles stay on `wan2_7`.

**The answer: keep `wan2_7`.** The operator judged the pairs blind and Wan 3.0 did not clear the
PRD's bar: the same or better on three of four shot types, and better on the keyframe shot.

**Method.** Four fictional shot types, each rendered once per engine from the same start still and
the same prompt, 9:16, 5 s, 720p: a product push-in (S1), an office dolly (S2), abstract light
ribbons (S3), and a red-to-blue cube keyframe pair (S4). Plus S1 at 1080p on both, one Wan 3.0
arm with `enable_thinking`, and Wan 3.0 duration probes at 10, 15 and 30 s (480p). Pairs were shown
unlabelled in a randomised order; the key was opened only after the verdicts were written. Total
168.75 credits, metered. Clips and the full record:
`content/<operator profile>/video/_engine-trials/2026-10-wan3/` (`trial.json`).

**Operator verdicts, unblinded**

| Pair | Better |
|---|---|
| S1 push-in, 720p | Wan 3.0 |
| S1 push-in, 1080p | `wan2_7` |
| S2 office dolly | not judged |
| S3 abstract ribbons | `wan2_7` |
| S4 keyframe cube | same |

**Measured** (repo probe helpers, not by eye)

| | `wan2_7` | `wan3_0` |
|---|---|---|
| Audio with nothing asked for | present on 5 of 5 clips, peak −16 to −75 dBFS | no audio stream on 9 of 9 with `generate_audio: false` |
| Size at 720p / 1080p | 716×1284 / 1076×1926 | 720×1280 / 1080×1920 exactly |
| Duration vs request | 0.04–0.06 s long | exact at 5, 10, 15 and 30 s |
| Keyframe end frame | honoured | honoured |
| Credits, 5 s 720p / 1080p | 7.5 / 12.5 | 8.75 / 17.5 |

`enable_thinking` measured within 5% of the plain Wan 3.0 clip on sharpness and motion and cost the
same. Laplacian sharpness favoured `wan2_7` on S1–S3, which agrees with two of the three verdicts,
but it also rewards over-sharpening and is recorded as a hint only.

**What it does not establish.** One render per shot per engine from one prompt, so a 1-2 lean is
real but noisy. The prompts follow this repo's recipes, which end on a negative clause written for
`wan2_7`; Wan 3.0 under positive-only prompting was not tested.

**What follows.** The registry is unchanged and so is `verified_on`. The real reason to switch was
the stray audio on captions-only cuts, and that is now a finishing fix in its own right rather than
an engine change (see PENDING). **Re-open** if a lane needs one continuous b-roll shot longer than
15 s (`wan2_7`'s ceiling), if the audio fix proves impossible in finishing, or if a re-run with
positive-only prompts and S2 judged changes the count.

## Verified: vendor facts behind the 2026-10-19 adoption PRD

Read 2026-10-19 from the vendors' own documentation and live tool schemas; the design that uses
them is the video vendor feature adoption design.
Each line says how it was confirmed, because a vendor page is a claim, not a measurement.

**HeyGen brand glossary** (docs: `developers.heygen.com/docs/brand-glossary`, `/docs/on-brand`; live
`create_video_from_avatar` schema).
- The video field is `brandGlossaryId` (`brand_glossary_id` on the REST body). It applies only where
  speech is **synthesised from a `script`**: not with `audioUrl` or `audioAssetId`, and **not** on
  `cinematic_avatar`.
- A glossary entry changes the audio only. **Captions and subtitles keep the original spelling.**
  Confirmed by a render: the subtitle file of a probe clip carried the true spelling.
- Terms match case-insensitively. Names are unique per workspace, ignoring case (a duplicate returns
  409) and are 1-64 characters. A `PATCH` replaces a list in full, so a changed table is a new
  glossary, never an edit of a shared one.
- **Measured, 2026-10-19:** two probes, one with an invented term and one with a tenant's real,
  harder term and its existing respelling. The clip with the glossary and the control without it
  sounded the same to the operator, and correct. HeyGen needed no help with either term, so the
  glossary's pronunciation benefit is **unproven**, not disproven. Cost about 3 credits per 5.6 s
  clip (photo avatar, 720p, default engine; one sample).

**HeyGen avatar engines and price** (HeyGen API pricing page, G2; the repo's own note agrees). For a
digital twin, Avatar IV and Avatar V cost the same per second. The 33% premium sometimes quoted
compares a photo avatar on IV with a twin on V. The engine comparison itself is already designed
inside the `gtm_core/panel_eval.py` panel; see `video-avatar`.

**Higgsfield refusals and charges** (`docs.higgsfield.ai/docs/concepts/errors`). A request can end
`failed` or `nsfw` (input or output rejected by content moderation). **Neither is charged, and any
reserved credits are refunded.** So a refusal costs time, not credits. Not yet confirmed by a metered
refusal here.

**Seedance and real people.** Seedance 2.0 refuses reference photographs of real, identifiable people.
**Community-reported only** (forum threads and third-party write-ups); no vendor document found. The
filter is described as identity pattern-matching rather than a ban on all realistic faces. Treat as a
warning, never a rule.

**Higgsfield catalog notes.** `kling3_0` accepts only `start_image` and `end_image` media roles; there
is no element or `image_references` role on it (the Kling 3.0 Omni variants take `image_references`).
Seedance 2.5 appears in Higgsfield's API documentation but returned nothing from the MCP catalog
search, so it is **not reachable in our lane** and is deferred.

**Higgsfield plan card** (`show_plans_and_credits`, free read). Ultra is 3,000 credits a month and was
$99 a month billed annually (list $129) on 2026-10-19; `credit_rates.py` held $70 from 2026-06-19.
The monthly price ($129) is unchanged. Starter and Plus were not re-read.

**Higgsfield training on content.** Its Terms of Use (section 4) allow training on inputs and outputs;
Enterprise agreements are the exemption. Recorded as residual DR-09 in the security self-assessment.

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
