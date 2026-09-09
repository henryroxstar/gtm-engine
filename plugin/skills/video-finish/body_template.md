
# Video Finish

Turn a raw provider render into a shippable artifact. This is the finishing pass that closes a
real gap: the one asset this system shipped before this stage existed looked decent only because
of an untested, unversioned script living in `content/` (state, not code). The mechanism here is
`gtm_core.video_finish` /
`gtm_core.video_lint` / `gtm_core.captions` — Python, tested, real ffmpeg. This skill only
invokes it and reports; **never** author an ffmpeg filter chain, a `drawtext` call, or a second
grade pass in this prompt — the module owns both and asserts the single-grade invariant itself.

> Resolve the **active profile** (the agent provides it). The only writable state is
> `content/<active>/`. This skill never re-renders and never publishes.

## Step 0 — Collect what render produced

For each ratio the render fan-out shipped, read `content/<active>/video/<script-slug>/render-<ratio>.json`
for the raw asset's local path. If a render never landed (the node failed or its cost-cap
preflight refused), skip that ratio and say so in the report — do not invent a source file.

## Step 1 — Build the finish spec

A `finish-spec.json` per ratio, next to the render:

```json
{
  "cuts": [],
  "grade": {},
  "caption_text": "<the script's on-screen caption text, if any>",
  "total_s": <render duration, if known — apportions caption timing>,
  "loudness_target": -14.0,
  "disclosure_line": "<BRAND.toml [disclosure].line, when render-<ratio>.json's identity_used is non-empty>"
}
```

`caption_text` comes straight from the approved script's caption line — do not compose new copy
here. Omit it entirely (not `""`) for a render with no burned-in caption.

`disclosure_line` — read `identity_used` from the render manifest first. Non-empty (`["soul"]`,
`["element"]`, `["voice"]`, any combination) → this field is **required**, sourced verbatim from
the resolved brand kit's `[disclosure].line` (never composed by hand — the exact line is what
`validate_disclosure` checks downstream). It's appended as one more caption screen over the
asset's own **last** ~2.5s, not a separately appended segment — this is what keeps a disclosed
asset from growing a black tail. Empty `identity_used` → omit the field entirely (not `""`); an
asset with no rendered identity has nothing to disclose.

## Step 2 — Run the finish

```
uv run python -m gtm_core.video_finish run \
  --profile <active> [--product <product slug, if the profile has one>] \
  --slug <script-slug> --ratio <9:16|4:5|1:1|16:9> \
  --spec content/<active>/video/<script-slug>/finish-spec-<ratio>.json \
  --source <the render's local asset path> \
  --out-dir content/<active>/video/<script-slug> \
  --json
```

The caption font is resolved from the profile's (and product's) `BRAND.toml` automatically — do
not pass a font path yourself. A `caption_text` spec with no `[typography.font_files].caption`
key configured fails loudly (`FontMissing`, naming the missing key); report that verbatim rather
than retrying or improvising a fallback.

Exit code 3 means ffmpeg is not on PATH — the run is genuinely incomplete (captions may have
rendered, encoding did not), not a partial success; report it as such.

Output: `<slug>-<ratio-slug>-final.mp4` plus `finish-<ratio-slug>.json` (the manifest
`gtm_core.outcomes` and `video-score` read back) next to it.

## Step 3 — Lint the finished asset

```
uv run python -m gtm_core.video_lint content/<active>/video/<script-slug>/<slug>-<ratio-slug>-final.mp4 \
  --ratio <9:16|4:5|1:1|16:9> \
  --manifest content/<active>/video/<script-slug>/finish-<ratio-slug>.json \
  --json
```

Any V1-V4 ERROR is a real, evidence-backed defect (resolution/fps/bitrate floor, exact aspect,
caption geometry, predictor duration cap) — do not ship past one silently. A legitimate exception
(e.g. a deliberate cinematic 24fps cut) is a **suppression with a real reason**, added to the
finish manifest's `lint_suppressions`, never a re-run with `--no-suppress` to make the finding
disappear from the report.

**Always pass `--manifest`, and never pass `--fast` on a final asset.** Half the lint is inert
without them: V3's primary caption-geometry check reads the manifest's per-screen boxes, V5 reads
its `audio_context`, V7/V8 read its caption text and `spoken_text`, V10/V11 need the audio and
caption-contrast measurement passes, and V6/V9 need the cut and motion passes — all four of those
passes are what `--fast` skips. On 2026-08-18 the lint was run bare against a shipped asset and
reported one finding; run with its manifest it would also have caught the hook caption sitting
under the platform's UI chrome — the operator's most damaging defect.

**Declare the mix, or V5 stays blind.** `has_music_bed`, `has_voice`, `ducking_applied`,
`music_lufs` and `voice_lufs` are spec keys the finish plan lifts into the manifest's
`audio_context`; `spoken_text` is the VO line V8 compares the captions against. No analysis of a
finished mono-sum can recover whether a bed was present or ducked, so if you do not declare it,
nobody can check it. Until 2026-08-28 nothing wrote either key and the linter read both
unconditionally, which meant **V5 and V8 could never fire on any asset** — two tiers that looked
live and were structurally dead. Write them.

**`caption_segments` without `caption_text` is now refused at plan time.** It used to skip the
entire captions stage in silence, which is how a fully linted asset shipped on 2026-08-27 with no
burned captions at all. The segments are the caption text; if the spec has segments, it needs
`caption_text` too (the joined segment texts are the obvious value).

The V5-V9 WARN tiers are not decoration. Read them out loud in the report:

- **V7 caption load / V8 caption-voice divergence** — too much text, or text competing with the
  voice-over instead of reinforcing it. Both are re-writes, not encode settings; hand them back to
  `video-script`.
- **V9 static shots** — a shot where nothing moves. V6 counts *cuts* and will happily pass a
  slideshow that cuts five times; V9 is what catches "this is five still cards in a trench coat".

**V10 and V11 are ERRORs and neither is an encode setting.**

- **V10 dead air** — measured silence, not a missing stream. The asset that forced this tier had a
  perfectly good AAC track carrying nothing under ten of its eighteen shots. Fixing it means going
  back to the mix and laying room tone, a bed, or a cue under the silent stretches — never
  re-encoding. `shots_lint`'s `audio_bed` rule catches the same defect one stage earlier, where
  the report can name *which* shot is silent; by the time V10 sees it, only the film as a whole
  can be described.
- **V11 caption contrast** — burned type that does not clear WCAG AA against what is actually
  behind it. Usually one shot, not all of them: an unplated caption style passes over dark
  graphics and fails the moment the backdrop turns bright, which is exactly what makes it a latent
  defect rather than an obvious one. The fix is a scrim, plate or stroke on the caption style, not
  a different grade.

## Step 3.5 — Caption treatment: **Reap first**, local burn-in as the fallback

**Reap is the default route as of 2026-08-20 (PRD W7.7).** It was the fallback for the first three
months of this pipeline's life, during which every caption defect the operator reported — text over
the face, text under the platform's UI chrome, a wall of words nobody could read in time — came out
of the local path, while **the paid Reap caption route sat essentially untouched**. We were
hand-rolling captions against a vendor spec we already owned and had not read.

Resolve the tenant's preset first:

```
uv run python -m gtm_core.brandkit --profile <active> --key captions.preset
```

**If it resolves**, take the Reap route:

1. `transcribe` the finished asset — this returns **real per-word timings**, which
   `gtm_core.captions.split_screens_from_word_timings` already consumes. The local path's even
   apportionment is an *estimate*; this is measurement, and it is the difference between captions
   that land on the beat and captions that drift.
2. `add_captions` with that `captionsPreset` (`get_caption_styles` lists the catalogue).
   Word-by-word motion (`system_kinetic_typography`) is what short-form retention actually depends
   on: text arrives *in step with the speech* instead of landing as a block the viewer must race.
3. Check `get_plan_usage` before a large job — captions and transcription bill 1 media credit per
   billed minute, reframe 2.

**If the key is absent**, the local Pillow path is the only route and there is nothing to
suppress — say so in the report and never invent a preset id. The local path is correct,
deterministic and free; it is static text with estimated timing, which is a weaker product, not a
broken one.

**If the Reap call FAILS, do not stop and do not ask — fall back, and record why.** This is a
route, not a decision: a vendor tool that will not run is not a reason to ship an uncaptioned
asset, and it is not a reason to wait for a human either. On 2026-08-30 `add_captions` became
uncallable mid-run and the v6 pair shipped with no captions at all, because a person had to notice
and switch by hand. The ladder, in order:

1. Burn locally, per shot, before the stitch:

   ```bash
   uv run python -m gtm_core.video_finish burn-captions \
     --shots <the shot list> --ratio <ratio> --profile <active> \
     --out-dir content/<active>/video/<slug>/shots-captioned --json
   ```

   **Per shot, never onto the assembled master.** A concat's output measured 0.637s longer than
   the sum of its own inputs' video durations (container/AAC padding is not stream duration), so a
   caption timed against the master drifts about half a second late by the closing card — largest
   exactly where the call to action is. A window computed inside one shot cannot drift out of it.
   The verb honours a shot's `caption_text_override`, which is how the closing card says
   `acme.com` on screen while the voice says "acme dot com".

2. Generate the suppression string with the named vocabulary — never hand-write one:

   ```bash
   uv run python -c "from gtm_core.video_finish import vendor_fallback_suppression as v; \
     print(v('schema_marshalling', detail='<the vendor error, VERBATIM>', observed='<YYYY-MM-DD>'))"
   ```

   Codes: `tool_unavailable`, `schema_marshalling`, `quota_exhausted`, `vendor_error`,
   `preset_unresolved`. An unknown code, a blank `detail`, or a missing `observed` date is refused
   — a suppression with no evidence and no date is the bypass the gate exists to catch, and an
   undated one silently becomes permanent.

3. Pass it through as `caption_route: "local"` + that `caption_route_suppression`, and report the
   route **and the verbatim vendor error** in one line.

**The known failure, so you recognise it rather than re-diagnosing it.** `add_captions` advertises
an empty `{"type":"object"}` input schema, so every argument is marshalled as a string and its
two-step `confirm` is rejected by a zod `literal(true)` — `Invalid literal value, expected true`.
Verified server-side on 2026-08-30: a fresh session saw the same empty schema, every string
argument validated, and the integer `resolution` failed identically. It is **not** credits
(580/600 remained), not the 229 MB upload (HTTP 200), not the token, and not the settings. That is
`schema_marshalling`. Retrying it in the same session will not work.

Report the route you took, every run, in one line.

**Judge the result against Reap's published spec on either route**, not by eye: captions inside the
**centre 60%** of frame (top and bottom are where platform UI overlaps), **~48–62 px** at 1080
width, **28–36 characters per line**, at most **two lines**, 1.5–2.5s per screen.
`gtm_core.captions` enforces the last three by construction since 2026-08-20 — `BRAND_MAX_PX` is 62
(it was 72, and that oversize is what forced two-line wrapping into the face band, which forced the
placement clamp), `_wrap_greedy` breaks on characters as well as pixels, and `fit` **refuses a
third line** rather than shrinking type nobody can read. A `DoesNotFit` naming the line ceiling is
the gate working: split the text into more screens, do not raise the ceiling.

**Placement is the defect this step exists to prevent, on either path.** The local renderer used
to centre the caption block inside the safe box. That sounds safe and is the worst possible
position: the safe area reserves *edges*, so the centre is simultaneously furthest from every
margin and squarely on the speaker's mouth. For 4:5 it computed to y=595 in a 1350px frame — 44–52%
of frame height. Twenty-four caption screens shipped there on 2026-08-18 and the operator's second
note was *"text covering face"*.

`gtm_core.captions.render` now takes `placement` (`upper` / `center` / `lower`, default `lower`)
and `gtm_core.captions.resolve_placement` reads it from the kit — an explicit
`[captions].placement` wins, otherwise a declared `[captions].preset` known to be upper-placement
implies `upper`. `video_finish` wires this automatically; you do not pass it by hand. What you
**do** owe the report is the resolved value, because it is a visible creative choice.

Note the trade-off honestly when picking a Reap preset: in Reap's system catalogue the
upper-placement presets (`system_indigo`, `system_prism`, `system_lumina`, `system_ember`,
`system_halo`, `system_crimson`, `system_trophy`, `system_ticker`) are all **static**, and the
animated ones (`system_kinetic_typography`, `system_glitch`, `system_typewriter`, `system_phantom`,
…) are **not** upper-placement. Motion and face-safety are not both available from the system
presets — say which one the tenant's kit chose and why, rather than picking silently. A custom
Reap studio template (Reap's `list_templates`, not HeyGen's) is the way to get both.

V3's `caption_over_face` rule fails a finished asset whose caption boxes overlap the face band
(22–62% of frame height) — ERROR when the finish manifest's `identity_used` is non-empty, WARN
otherwise. A `center` placement on a presenter asset will not get past it.

Check Reap's `get_plan_usage` before a large job, and quote what it returns — it is free, and a
remembered balance is wrong by the time anyone reads it. The durable point is the one that made
this check exist: **a paid caption surface sat idle while captions were hand-rolled locally.**

## Step 4 — Report

Per ratio: finished path, whether ffmpeg ran, and the lint verdict (clean / findings /
suppressed-with-reason). If a ratio was skipped in Step 0, say which and why. This report is what
`score` reads next — it scores the FINISHED asset, never the raw render.

## Guardrails

- **Never** call a publish, scheduling, or account-linking tool. Finishing is not shipping.
- **Never** re-render. A weak render is `score`'s and the human gate's call, not this stage's.
- Only write under `content/<active>/`. `profiles/<active>/` and `plugin/` are read-only.
- Never draw text, apply a filter chain, or grade the video yourself — every pixel operation goes
  through `gtm_core.video_finish`/`gtm_core.captions`, which is what keeps the single-grade and
  caption-geometry invariants machine-checkable instead of a review note.
