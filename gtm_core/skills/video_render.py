"""Canonical manifest for the `video-render` skill.

Phase 6 defined the `render_*` nodes;
§5.6 (Phase 8) added the Soul→video bridge; §5.8 (Phase 9) added the two-stage
draft→predict→batch spend gate; §5.6/§6.2 (Phase 12) added the voice-over mux and
`identity_used` manifest field; §5.6 (Phase 13) added the `identity.voice_engine` branch
(Higgsfield's `text2speech_v2` model + `variant`) so the same `voice_id` can render through a
non-default TTS engine (e.g. `elevenlabs`) without a new provider integration.
Prompt body: plugin/skills/video-render/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

**One skill, many sibling nodes.** §5.5's `render_*` row is a fan-out across formats and
platforms, which the repo already expresses by pointing several sibling nodes at the same
skill with different prompts (see packs/marketing/graphs/long-form-blog.toml, where
`research-evidence` and `research-competitive` are both `content-research`). Variant count
*within* one format is a skill-level batch (`generate_video_batch`), so the graph never
needs to know N.

**Soul→video bridge (Phase 8).** Soul renders only as a still (`soul_2` /
`soul_cinema_studio`) — there is no Soul video model — so a trained identity reaches video
only by generating ONE identity-faithful still and feeding it as the image-to-video start
frame. `identity.reference_element_ids` still takes priority when present (an Element embeds
directly in the generation prompt, no bridge needed); the Soul path is the fallback for a
profile that trained a Soul but has no reference elements.

**Two-stage draft/score/batch flow (Phase 9; no longer a numeric spend gate — see Phase 17).**
Render ONE draft variant, score it with `virality_predictor`, then spend the remaining N−1 —
bounded by construction (`revisable_from`/`max_visits` are refused at load, so this is a
single round, never a loop).

**Voice-over + disclosure (Phase 12).** `[SPOKEN]` script lines + a non-empty
`identity.voice_id` → `generate_audio` + a local `ffmpeg` mux, once per batch. Every asset's
manifest now records `identity_used` (`soul`/`element`/`voice`, any combination, or none) —
the Article 50 disclosure signal `content-publish` checks before staging.

**Voice engine (Phase 13).** `identity.voice_engine` (empty = provider default) picks which
`generate_audio` model renders `voice_id`: default `seed_audio`, or `text2speech_v2` +
`variant` for a named engine (`elevenlabs`/`minimax`/`seed_speech`/`vibe_voice`/`cozy_voice`).
Same voice, same call site — only `model`/`variant` change, so this needed no new provider,
consent surface, or MCP tool.

**Audio-driven lip sync (Phase 14, found live 2026-08-15).** The Phase 12 VO step generated video
and audio independently and only muxed them together afterward — the video model never saw the
words, so mouth motion had no relationship to the VO's actual phonemes. Fixed by reordering: the
VO now generates FIRST (4a), and 4b passes it as an `audio_references` media input to the video
model when that model declares the role (`seedance_2_0` and siblings do; not every video model
does) — the model then animates the mouth against the real audio, and the same VO is still muxed
in afterward (4e) to attach the actual track. `generate_audio:false` stays set on the video call
so the model's own uncontrolled audio never overwrites the cloned voice. Every VO-bearing asset's
manifest now records `lip_sync_source` (`audio_references` or `text_prompt_only`) so a render
without a lip-sync-capable model is auditable, not silently indistinguishable from one that had
it. Also fixed at the same time: VO script phrasing (a leading fragment/ellipsis reads as garbled
prosody to several TTS engines — the spoken text should be a complete sentence even when the
on-screen caption wants a cold-open feel) and mux audio bitrate (explicit `-b:a 192k` instead of
the encoder default, plus `atempo` instead of `-shortest` truncation when the VO slightly
overruns the video). One more thing found live: on `seedance_2_0`, `mode: "fast"` rejects an
`audio_references` job outright (no charge on the failure) — `mode: "std"` is required whenever
`audio_references` is used, even though `fast` is this skill's default for a plain silent draft.

**Long-form multi-shot composition (Phase 15).**
A single Higgsfield call is capped at 5-15s; a `<slug>.shots.json` from `video-script` (present only
for a request longer than one clip) switches Step 4 into a multi-shot branch that reuses 4a/4b/4e
unchanged, just scoped to one shot at a time instead of one batch: each shot gets its own VO (from
that shot's exact `[SPOKEN]` line, not a shared track sliced afterward — no alignment guesswork),
its own `audio_references`-driven lip sync, and its own mux, all against the SAME identity anchor
locked once in Step 1 for the whole video. Shot 1 is the draft the existing predictor gate scores;
shots 2..N render sequentially (heterogeneous per-shot batching via `generate_video_batch` is an
open, unverified question, not assumed). Once every shot is finished, a new stitch step
concatenates the already-fully-muxed shot files and applies exactly **one** color-grade pass over
the joined asset — never per shot, which is what produces a visible seam at every cut. 5 minutes is
the architectural ceiling; a 90s soft default (profile-overridable) keeps an accidental 30-minute
render from surprising anyone.

**Draft-pool budget, on by default (Phase 16 + external research pass, 2026-08-15).** The two-stage gate above scored ONE draft — a single sample
of a stochastic generation process, so a weak score could mean a weak concept or just an unlucky
seed. Replaced with a K-variant draft pool (default 3, operator-overridable via `draft_pool_size`
in `PROFILE.md`, mirroring the professional ~3-generations-per-usable-shot norm this pass's
external research surfaced): Step 4b submits K identical-prompt variants as one
`generate_video_batch` call, Step 4c scores each, Step 4d gates on the BEST of the K. The pool
members are not discarded on a pass — they count toward the requested N and ship, with the winner
tagged `draft_rank: 1` as the lead recommendation. Still bounded by construction: one pool, one
decision, no reroll loop. Also resolved (no live spend needed — settled by the live
`generate_video_batch` tool schema, which shows independent `params` per `requests[]` item): the
long-form multi-shot section's "heterogeneous batch, unverified" hedge is gone — shots 2..N
dispatch as one heterogeneous batch call. And: every render whose `identity_used` is non-empty now
threads `disclosure_line` (from the brand kit's `[disclosure].line`) through to the finish-spec
`video-finish` builds next, so the Article 50 disclosure line is burned onto the asset's own final
seconds instead of riding on a separately-appended black card.

**Predictor de-gated + reading discipline (Phase 17, 2026-08-17 — found live during a
creator-pack trial).** A controlled test — three identical-prompt `seedance_2_0` renders, readings
taken at pinned playhead positions — established that the `virality_predictor` dashboard's numbers
are a **per-timepoint readout of a live HTML viewer**, not a stable summary: scrubbing one
unchanged video moved its own composite score 11-12 points, more than either the 4-5 point spread
between independently rendered seeds of the same prompt or the 10-point gap a real design decision
was previously (wrongly) made on. Three fields proved degenerate: `SUSTAIN` (100% on every video
scored), the "peak moment" timestamp (always equal to clip duration — an artifact, not a detected
peak), and an "Activity" reading that turned out to be a viewer opacity slider, not a metric at
all. Only the Visual Cortex sub-score reproduced (~1-point spread at fixed playhead) and tracked a
real manipulation (a background-complexity change moved it 9 points in the predicted direction).
Consequently: Step 3's hard "hook strength ≥ 60" bar is removed — the predictor is now a recorded
advisory read at a pinned playhead, preferring Visual Cortex, never a spend gate (matching the
predictor's original framing, which `video-render` had drifted from); `pool_predictor_score` may be `null` for a scored pool member with no usable
reading (`gtm_core.render_manifest._validate_draft_pool` updated to allow this while still
requiring `draft_pool_size`/`draft_rank` together). Also found and fixed in the same pass: on
`seedance_2_0`, `audio_references` alone returns HTTP 422 (it requires an accompanying image
media) — the Element path now passes the element's own source media as `image_references`
alongside `audio_references`, and Step 1 gained an "inspect the anchor before spending" check
(read the element's source photos before rendering, not after paying for a take). The `ffmpeg`
fetch of a provider CDN URL that 4b/4e/the multi-shot stitch all rely on is now routed through
`gtm_core.media_fetch` (a host-pinned, size-capped downloader mirroring `dataset_fetch.py`) rather
than a bare shell `ffmpeg -i https://…` call, which was invisible to the §R6 semgrep egress scan.

**Engineered-prompt layer (Phase 18).**
The gap assessment found there was no prompt template anywhere in the repo (assembly was one
sentence of prose — "camera + motion_prompt + visual as the prompt"), no negative prompt on any
video call, the brand kit's `[imagery]` fields dead-wired for video, and the exact prompt bytes
sent for every shipped asset unrecorded and unrecoverable. Now: every prompt is assembled per
`references/prompt-recipes.md` — shape A (frame-anchored: motion + camera + stability + negative
only, the cross-vendor i2v collapse rule) vs shape B (Element/t2v: full scene + scaffold verbatim
+ negative), with per-model grammar notes; `[imagery].style`/`.negative` stand in for the
scaffold on single-clip scripts; the manifest records the verbatim `prompt` and `seed` per asset
and `gtm_core.render_manifest` refuses a synthetic asset without one (Step 5 now ends with a
`validate --synthetic` call); the headless worker's `seed` param is passed and recorded so a
winning draft can be reproduced or refined one-variable-at-a-time; and the multi-shot lane —
which previously had zero variant selection — now renders shot 1 (the hook) as a K-member pool,
stitches the best take, and records the K−1 alternates without shipping them.

**Native audio discipline (Phase 19, VPE-10, 2026-08-17).** `generate_audio` — a model-declared
param independent of `audio_references` — defaults to `true` on `seedance_2_0`'s own schema
("Generate native audio... Independent of audio_references"), confirmed live. An omitted value is
not silence; it is the model adding its own uncontrolled audio track, on every call, whether or not
a VO was involved — a gap the earlier "keep `generate_audio: false`" note (Phase 14) only covered
for the VO+`audio_references` branch. Now sent explicitly on every call a declaring model receives:
`false` by default (every presenter shot, and every b-roll/screen shot with an empty `sfx` field),
`true` only for a b-roll/screen shot whose `sfx` field (`schemas/shots.schema.json`) is non-empty —
that shot's `sfx` text is appended verbatim as a trailing `SFX: <text>.` prompt line
(`references/prompt-recipes.md`). Never true on a presenter shot: native audio cannot be the
cloned voice, and would fight the `audio_references`/mux path.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-render",
    capability_tier=Tier.PRODUCTION,
    version="1.0.0",
    phase="6",
    description=(
        "Render approved b-roll, product, environment and abstract shots for ONE target aspect "
        "ratio using Higgsfield, keeping the active company's look via the brand kit "
        "(`python -m gtm_core.brandkit`). **This skill does NOT render presenters.** A talking "
        "head of a real person is refused in code: `gtm_core.render_engines` leaves the "
        "`presenter` role unbound, `shots_lint` fails any speaking presenter shot before spend, "
        "and `render_manifest` refuses to record one. The reason is architectural, not a prompt "
        "problem \u2014 a live models_explore query proved `soul_id` is accepted only by IMAGE "
        "models, so a trained Soul cannot reach any video model and identity is re-derived from a "
        "JPEG every frame; and `audio_references` is a reference input, not a lip-sync switch "
        "(mouth closed in 9 of 10 sampled frames across a full spoken sentence, verified "
        "2026-08-19). For a speaking presenter, route to real footage (the primary lane, no "
        "Article 50 disclosure duty) or the faceless format (b-roll plus held soul_2 stills, VO "
        "and burned captions) \u2014 see `video-router` Step 0.5. Still in scope and unchanged: "
        "every shot with no real person in frame, plus soul_2 identity STILLS (as stills, never "
        "animated into video). Every generation prompt is assembled per "
        "references/prompt-recipes.md and recorded verbatim with its seed in the render manifest, "
        "which refuses a synthetic asset without one \u2014 and now also refuses one it cannot tie "
        "to a costs.jsonl row, because ~580 credits of August 2026 video spend never reached the "
        "monthly cap."
    ),
)
