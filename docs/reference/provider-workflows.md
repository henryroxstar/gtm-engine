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
| `thumbnail-generation` | 1.3 | decline | The strongest adopt candidate outside the video lane (YouTube/IG covers, 4K routing, identity lock). Declined **for now** only because no lane produces a thumbnail as a deliverable — nothing here is a judgement on the workflow. Revisit when one does. | 2026-08-29 |
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
