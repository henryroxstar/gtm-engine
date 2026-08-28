
# Video Avatar

Render the **speaking presenter beats** of a shot list as a synthetic talking head. This is the
only skill allowed to do that, and it is a different skill from `video-render` on purpose.

> Resolve the **active profile** (the agent provides it). Write only under
> `content/<active>/video/<slug>/`. Never publish.

## What this skill does and does not own

| Shot `role` | Speaks? | Owner |
|---|---|---|
| `presenter` | yes | **this skill** |
| `presenter` | no | `video-render` (a held `soul_2` identity STILL) |
| `broll` | — | `video-render` (Higgsfield `wan2_7`) |
| `screen` | — | `video-render`, or a locally drawn frame sequence when the payload is legible on-screen text |

Do not render b-roll here, and do not render a presenter beat in `video-render`. The split is
enforced: `gtm_core.render_engines` resolves a *role* to an engine, and the two roles resolve to
different providers.

## Step 0 — Resolve the engine. This is a gate, not a formality.

```bash
uv run python -m gtm_core.render_engines --role presenter --speaks --disclosed
```

Exit 0 prints the engine. **Exit 2 means you may not render**, and the `reason` says why in words
meant to be read aloud. Do not proceed past a non-zero exit, and do not work around it — the two
lanes that need no engine at all (real footage; the faceless format) are named in the message.

**Why `--disclosed` is not optional here.** The bound engine carries `requires_disclosure = true`.
Dropping the flag is not a shortcut, it is a different question — *may I ship a synthetic likeness
of a real person with nothing telling the viewer?* — and the answer is still no, pending the
pre-registered panel in `gtm_core/panel_eval.py`. Pass the flag only because the render will
genuinely carry the line, and confirm the tenant has one:

```bash
uv run python -m gtm_core.brandkit --profile <active> --key disclosure.line
```

An empty `[disclosure].line` is **not** an opt-out — it is a tenant that has not met the duty, and
it fails closed at `video-finish` and again at the publish gate. Stop and say so.

## Step 1 — Resolve the identity handles, and refuse an instant clone

```bash
uv run python -m gtm_core.brandkit --profile <active> --key identity.heygen_avatar_id
uv run python -m gtm_core.brandkit --profile <active> --key identity.heygen_voice_id
uv run python -m gtm_core.brandkit --profile <active> --key identity.heygen_voice_grade
```

`heygen_voice_grade` must read `professional`. An `instant` clone does not train a dedicated model
— it guesses from prior training data, which is what produced *"voice doesn't really sound like
me"* on 2026-08-19 — and an **unrecorded** grade fails closed, because a voice whose provenance
nobody wrote down is not evidence of a good one.

**Two providers means two grades.** `identity.voice_grade` is the *Higgsfield* clone and is not
this lane's fact. Reading the wrong key is reading nothing. When you hand the shot list to the
lint, pass the grade belonging to the engine that will actually render:

```bash
uv run python -m gtm_core.shots_lint <slug>.shots.json --voice-grade "$(uv run python -m gtm_core.brandkit --profile <active> --key identity.heygen_voice_grade)"
```

If `identity-kit` reports the handle empty, stale, or still training, stop and hand off there.
Identity-**creating** verbs (`create_digital_twin`, `create_avatar_consent`, `clone_voice`) are
never called from this skill — they belong behind that skill's Article 50 consent gate.

## Step 2 — Pick the LOOK by pixels, not by name

This is the step that decides whether the finished face looks sharp, and it is the one most
easily got wrong, because the obvious choice is usually the bad one.

```
list_avatar_groups(ownership="private")
list_avatar_looks(ownership="private", groupId=<group>)
```

Each look reports `avatar_type`, `image_width`, `image_height`, `preferred_orientation` and
`supported_api_engines`. **Read those numbers against the target ratio before choosing.**

A `digital_twin` is trained on real footage and is the most faithful *motion* source, but it is
often a **landscape 1280×720** frame. Cropped to a 4:5 or 9:16 portrait it keeps roughly the
centre third of its width, so the face — the entire subject of a talking-head shot — arrives with
a few hundred pixels across and no upscale recovers detail that was never captured. This is the
same failure as framing a Soul still too wide: resolution spent on background is resolution not
spent on the face.

**Match the look's NATIVE ORIENTATION to the target ratio first, resolution second.** Orientation
is the binding constraint and resolution is the tiebreak, because no amount of source resolution
survives being scaled into the wrong shape. A 608×1080 portrait look rendered for a 16:9 master
occupies 608 of 1920 columns — **32% of the frame is picture and 68% is whatever you pad it with**
— and a blurred copy of the footage is the most common thing to pad it with, which reads as a
phone video someone stretched. That shipped on 2026-08-27, and six native 1920×1080 landscape
looks existed in the same avatar group, at identical cost, unexamined.

`shots_lint` now refuses a shot list whose `identity_bindings.<role>.source_px` contradicts a
declared `deliverable_ratios` entry, so this is checkable before any spend — but only if you
record the look's native pixels there.

**A DUAL-RATIO DELIVERABLE IS TWO DECISIONS, NOT ONE.** When the brief asks for both a 16:9 master
and a 9:16 cut, no single look serves both. Pick one of:

- **Render the master ratio native and design the second ratio as a layout.** A landscape panel
  set into a vertical frame with a live data/caption zone beneath it is a deliberate composition;
  the same footage scaled up behind itself is not. Say which you did in the report.
- **Render twice, once per orientation.** Under per-render billing (Step 5) this is a second
  credit-minute, not a second full cost — often the right call, and cheaper than it sounds.

Never let the answer be "render portrait and pad the landscape" by default. That is the decision
that was made silently, and it is the one that cost the most.

So, in order:

1. **A look whose native orientation matches the target** wins outright — landscape source for a
   16:9 target, portrait for 9:16 or 4:5. `list_avatar_looks` reports `preferred_orientation`
   alongside `image_width`/`image_height` on every look; read it, do not assume the group is
   portrait-only.
2. **Among those, the one at or above the target's long edge.** A 4:5 1080×1350 target wants a
   source at least 1350 tall; several `photo_avatar` looks run far past that.
3. **Use `engine: {"type": "avatar_v"}` with `reference_look_id` set to the `digital_twin`** so
   the high-resolution photo avatar borrows its motion from the trained twin. The reference must
   be a `digital_twin` **in the same avatar group** — `studio_avatar` and `photo_avatar`
   references are rejected. This is how you get the twin's motion at the photo's resolution
   instead of choosing between them.
4. Only fall back to rendering the `digital_twin` directly when no look of the right
   orientation clears the target, and say in your report that you did and why.

Check `supported_api_engines` on the chosen look before setting `engine` — eligibility is per
look, not per account.

**Reviewing a look before you spend.** `preview_image_url` and `preview_video_url` come back on
`resource2.heygen.ai`, allowlisted in `gtm_core.media_fetch` (operator-requested 2026-08-20)
specifically so a look can be checked before a render is spent:

```bash
uv run python -m gtm_core.media_fetch --profile <active> \
  --url "<preview_image_url from list_avatar_looks>" \
  --dest "video/<slug>/heygen/look-preview-<look_id>.jpg"
```

Fetch a candidate's preview before committing a render to it whenever the shot list's wardrobe/
framing description is specific enough that guessing wrong would waste real spend — this is a
free read against an already-allowlisted host, not a reason to widen anything further. Do **not**
propose widening the allowlist to any *other* HeyGen host on the strength of this precedent: each
entry here was pinned from its own live response naming it, and the next one needs the same.
Confirm `preferred_orientation`/`image_width`/`image_height` from the `list_avatar_looks` record
alongside the image — a look whose wardrobe is right but whose SOURCE is landscape still carries
the resolution tradeoff in point 1 above; fetching the preview settles wardrobe, not geometry.

## Step 3 — Render ONE continuous take and find the cuts in the edit

**Reversed 2026-08-28. This step used to say the opposite** — "never render the whole script as one
clip and cut it up", on the reasoning that a re-take then costs one beat rather than the video.
The reasoning was sound and the premise was wrong, and the premise is the part that costs money.

**HeyGen bills per render, per minute, rounded up — not per second.** Measured from this tenant's
own cost ledger on 2026-08-27: nine renders totalling ~61s of footage cost **209 credits**, and a
separate re-render of a single **3.7-second** shot cost **23**. That is ~23 credits per job
whatever its length.

Two consequences, and the second one is the reason the old rationale collapses:

1. **Eight beats rendered separately cost 8× one take of the same footage.** 184 credits versus 23.
2. **Per-beat rendering does not even protect the re-take.** A bad beat costs 23 credits to redo;
   a bad line inside a single take costs 23 credits to redo the take. The saving the old rule was
   buying does not exist under this billing model.

So: **render the presenter's whole script as one take, then cut it locally.** It is 8× cheaper
under per-render billing and exactly the same price under per-second billing — there is no model
in which the old way wins. It is also better direction: performance energy carries across a cut
instead of resetting at every shot boundary, you choose out-points in the edit instead of guessing
them in a prompt, and every cutaway becomes free once the take underneath it is continuous.

**Sixty seconds is the line worth designing to.** Under a minute of presenter speech is one
credit-minute; crossing it is a second one. That is also roughly where a talking head starts to
sprawl, so the billing boundary and the editorial one point the same way. Crossing it is a
legitimate decision — say in your report that you did, and what the extra minute bought.

Keep per-beat rendering for the case it actually suits: a shot that needs a **different look,
wardrobe, or background** from its neighbours cannot share their take, and that is a craft reason,
not a cost one.

`create_video_from_avatar` takes **either** `script` + `voiceId` **or** `audioUrl` /
`audioAssetId` — never both.

- **`script` + `voiceId`** — HeyGen synthesises the voice and generates the mouth from it. Native
  sync, tenant's cloned voice. This is the default path. Record `lip_sync_source = "native_script"`.
- **`audioUrl` / `audioAssetId`** — you supply the audio and HeyGen lip-syncs to it. Use when the
  VO already exists as a file. Record `lip_sync_source = "native_audio"`.

Both are genuine sync. Neither is `audio_references`, which is the Higgsfield reference input that
left the mouth closed in 9 of 10 sampled frames — do not record that token here, ever. The
manifest refuses a native claim over an engine the registry does not mark `lip_sync = "native"`,
and it should: an unbacked sync claim in the audit record is worse than no record, because a
reviewer trusts it instead of watching the frames.

Set explicitly on every call — the defaults are wrong for this lane:

| Field | Value | Why |
|---|---|---|
| `aspectRatio` | the target ratio (`"4:5"`, `"9:16"`) | Native, so no reframe pass. `"auto"` silently follows the SOURCE, which is how a landscape twin produces a landscape render. |
| `resolution` | `"1080p"` or `"4k"` | `video_lint` V1 floors at 1080×1350. `"720p"` fails it and needs an upscale. |
| `fit` | usually `"cover"` | `"contain"` letterboxes the subject into the frame against background. Omit only when you want the server to choose. |
| `voiceSettings.speed` | on `VoiceSettingsInput`, range 0.5–1.5 | **Not** inside `engine_settings`. |
| `voiceSettings.engine_settings` | `{"engine_type": "elevenlabs", ...}` | Must match the engine backing the voice, or the request is rejected. |

`motionPrompt` is accepted **for photo avatars on either engine, and for video avatars only when
`engine.type` is `"avatar_v"`** — it is rejected for a video avatar on the default Avatar IV. It
drives body motion and gesture, never the mouth. Keep it to what the shot list's `motion_prompt`
says and let the audio own the lip sync. `expressiveness` is photo-avatars-only and Avatar IV only
(rejected on `avatar_v`), so it and `motionPrompt` are usually alternatives, not a pair.

Poll `get_video(videoId=…)` until `status` is `completed`; it returns `video_url`, `duration`, and
failure detail. A failed job reports why — read it rather than re-submitting the same request.

## Step 4 — Fetch through the pinned host, never a raw URL

```bash
uv run python -m gtm_core.media_fetch --profile <active> \
  --url "<video_url from get_video>" \
  --dest "video/<slug>/heygen/shot-<n>.mp4"
```

`get_video` returns a URL, not bytes, and nothing else in this repo may fetch it. Shell `curl` and
`wget` are denied by design; a denial here is the design working, not an obstacle to route around.
If the host is refused, that is a **new** host — report the exact `EgressRefused` message and stop.
Widening the allowlist is a separate, deliberate change (one host, never a wildcard, pinned from
the live response that named it, with `CLAUDE.md` and `SECURITY-SELF-ASSESSMENT.md` updated in the
same commit) and it is not this skill's to make mid-render.

## Step 5 — Snapshot the balance BEFORE you spend, then log the cost, then write the manifest.

**The pricing shape, measured (2026-08-27):** ~23 premium credits **per render**, per minute of
output, rounded up — not per second. Nine renders totalling ~61s cost 209 credits; a lone 3.7s
re-render cost 23. Estimate a batch as `23 × ceil(seconds ÷ 60) × jobs`, and treat every extra
job as a whole extra minute you are buying. This is why Step 3 renders one continuous take.

Unlike Higgsfield, **HeyGen's MCP has no per-job cost readout and no preflight** (`get_cost`
doesn't exist here) — `get_current_user` returns only a point-in-time `subscription.credits`
snapshot, not a per-render charge. That makes the delta the only signal, and a delta needs two
points: call `get_current_user` and record `premium_credits.remaining` **before** submitting any
`create_video_from_avatar` call in this batch, then again **after** every clip in the batch has
completed. Skipping the first call is how a whole batch ends up with no honest cost figure — the
`remaining` count moves for reasons besides this batch (other work on the account), so an
after-only reading can't be attributed to these clips with any confidence.

```bash
uv run python -m gtm_core.ledger_cli append-cost --profile <active> --json '{...}'
```

Take the returned `ts` and put it in the manifest's `cost_ledger_ts`. The order is the point: ~580
credits of August 2026 video spend never reached the monthly cap because the spend drew on a
pre-purchased balance and nothing logged it, so reading the ledger to answer *"what did video
cost"* returned zero — and zero is indistinguishable from "we didn't render anything". If the
before-snapshot was missed for a batch already rendered, do not fabricate a delta — log the
ledger row against the operator's own documented per-minute rate (check the provider's current
pricing) with `cost_source="post-hoc-balance-delta"` and say plainly in the report that the
figure is an estimate, not a measured delta, and why.

The render manifest for each presenter clip records `render_engine = "heygen_avatar"`,
`identity_used` including `"soul"` and `"voice"`, the `lip_sync_source` from Step 3, the verbatim
`prompt`/`script` sent, the approved storyboard path, and the cost triple. Each of those is
refused when missing — the manifest is the only durable record that this clip was synthetic, and
Article 50 is a claim about a fact, not about a caption.

## Step 6 — Hand off

Report per shot: look id + why that look, engine, resolution, duration returned vs `vo_seconds`
asked, cost, and the manifest path. Then hand to `video-finish` for stitch, grade, captions and
lint. Do not stitch here and do not caption here.

## Guardrails

- **Never render a presenter beat without `--disclosed` resolving first.** The flag asserts the
  finished post will carry the tenant's disclosure line. Passing it when that is not true is a
  false statement to a gate, not a workaround.
- **Never record `audio_references` or `text_prompt_only` for a render from this engine.** Those
  describe a model that could not sync. Preserving the distinction in the manifest is the only
  reason the August 2026 failure is legible now.
- **Never call an identity-creating verb** (`create_digital_twin`, `create_avatar_consent`,
  `clone_voice`, or any asset-delete verb). Those belong to `identity-kit`, behind its consent
  gate. This skill reads handles and renders.
- **Never publish, schedule, or upload anywhere.** No publish verb exists on this surface and none
  should be sought; the publish gate is the only route out.
- **Never widen an egress allowlist mid-render** (Step 4).
- One clip per presenter shot. A single long render cut up afterwards makes every retake cost the
  whole video and desynchronises the shot list's stated timing from what actually exists on disk.
