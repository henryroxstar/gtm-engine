
# Video Router

Route a "make a video" request into the correct creator-pack variant. This skill does **not** create a graph node and does **not** spend credits — it runs before pack selection and emits a directive to start the right lane.

> Resolve the **active profile** (the agent provides it). Do not write under `content/<active>/` — only emit a routing directive.

## Step 0 — Identity preflight (audit only, zero spend)

Before routing, audit the active profile's render-identity handles — the same zero-spend check
`identity-kit` Step 1 runs: `python -m gtm_core.brandkit --profile <active> [--product <slug>]`,
then check `soul_id`/`voice_id` liveness against the provider. This is a **universal
precondition**: any lane that uses a trained Soul or cloned voice needs it live before any
script/storyboard spend, and — unlike shot-level assets — it is knowable before a script exists.

- If the request names an identity (a Soul, a voice) and its handle is empty, stale, or
  failed-training: **stop and hand off to `identity-kit`** before emitting a lane directive.
  Do not create anything here — this skill still never spends and never writes.
- If the identity handles it needs are live (or the request doesn't use a trained identity at
  all): report their status in one line and continue to Step 1.
- List existing `reference_element_ids` (id, name, category) for visibility only. **Do not
  gate on a missing environment/prop element** — which non-person B-roll a video needs depends
  on the shot list, and the shot list doesn't exist until `video-script` runs. That gap is
  real only once a shot names a role that needs one; it's caught there (shot role tagging) or
  at `video-storyboard` (per-shot identity resolution), not here.

## Step 0.5 — Production preflight (still zero spend, zero writes)

Six defects shipped on 2026-08-18 — broken lip sync, captions over the face, wardrobe changing
shot to shot, motionless talking-head shots, and a weak hook. Every one traced to a decision that
was *knowable before a single word of script existed* and was instead taken by default, late, at
render time. This step takes them explicitly, up front. All four checks are free reads.

Report the four answers in one short block, then route. Do not stop the lane on any of them —
they are decisions to carry forward, not gates.

**1. Presenter engine — which one, and on what condition? Decided here, never at render.**

Ask the registry both questions, because they have different answers:

```bash
python -m gtm_core.render_engines --role presenter --speaks --disclosed   # the disclosed case
python -m gtm_core.render_engines --role presenter --speaks               # the undisclosed case
```

Exit 0 prints the engine; **exit 2 means that case may not be rendered**, and the `reason` it
prints is written to be read aloud to the operator.

As of 2026-08-20 the answers are:

- **Disclosed → `heygen_avatar`.** A synthetic talking head is renderable by the `video-avatar`
  skill, on a trained digital twin with native lip sync, *provided* the finished post carries the
  tenant's `[disclosure].line`. Check the tenant actually has one
  (`python -m gtm_core.brandkit --profile <active> --key disclosure.line`) — an empty line is a
  tenant that has not met the duty, not one that opted out, and it fails closed downstream.
- **Undisclosed → nothing.** Still refused, still waiting on the pre-registered panel in
  `gtm_core/panel_eval.py`. That panel asks whether people who know the operator can tell the
  avatar from real footage — a question with stakes only when the viewer is *not* told.

*Why the engine had to change rather than the prompt:* identity cannot cross the image→video
boundary. A live `models_explore` query (2026-08-20) confirmed `soul_id` is accepted **only** by
image models — no Higgsfield video model takes a trained Soul — so every such model re-derives the
face from a JPEG, frame by frame. And a general image-to-video model animates the mouth from
*prompt text*, which has no relationship to the words in the VO: with `audio_references` passed,
the mouth was **closed in 9 of 10 sampled frames** across a full spoken sentence (verified
2026-08-19). `audio_references` is a reference input, not a lip-sync switch. No prompt fixed
either defect; a purpose-built avatar engine does.

**When the request implies a speaking presenter, name all three lanes and let the operator pick.**
Do not silently default to the synthetic one just because it now resolves:

- **Real footage** (still preferred, and still the primary lane): the operator records themselves;
  Reap clips and captions it. Clears the bar by construction, and carries **no Article 50
  disclosure duty** because nothing was synthesised.
- **Disclosed avatar** (`video-avatar`): weekly cadence without weekly filming, at the cost of a
  visible "made with AI" line on every post that uses it. That trade is the operator's to make,
  not yours to assume.
- **Faceless format**: b-roll and product shots plus held `soul_2` stills, voice-over, and burned
  captions. No synthetic *talking* head at all.

Full reasoning: the relevant PRD under `docs/prds/`, if this repo has one — the summary above
captures the operative decision.

**2. Caption route — Reap or local.** Read the tenant's declared preset:

```bash
uv run python -m gtm_core.brandkit --profile <active> --key captions.preset
```

If it resolves, the caption route is Reap (`get_caption_styles` for the catalogue,
`transcribe` for real per-word timings, `add_captions` with that preset). Check `get_plan_usage`
first — as of 2026-08-19 the plan held **600 media credits with zero used** while captions were
being hand-rolled locally, which is the whole reason this check is here.

Say which placement the preset implies. Reap's own catalogue marks several as upper placement
(`system_indigo`, `system_prism`, `system_lumina`, `system_ember`, `system_halo`, `system_crimson`,
`system_trophy`, `system_ticker`) — `gtm_core.captions.resolve_placement` reads that automatically,
but naming it here is what stops a centred caption landing on the speaker's mouth. Note that the
animated presets (`system_kinetic_typography`, `system_glitch`, `system_typewriter`, …) are **not**
upper-placement, so motion and face-safety are a real trade-off in the system catalogue, not a
free choice — call it out rather than picking silently.

**3. Shot mix — set the target before the script is written.** `gtm_core.shots_lint` fails a shot
list whose `presenter` share exceeds 60%, and warns when no shot carries `role: "screen"`. Say the
target now: roughly half presenter, at least one real screen capture of the thing being discussed,
the rest b-roll/data/motion-graphic. A script written to that mix is cheap; one retrofitted to it
is a rewrite.

**4. Existing footage inventory — what can be shot instead of generated.** Name what the profile
already holds that could serve as a `screen` or `broll` shot (product screenshots, recorded flows,
diagrams under `profiles/<active>/knowledge/`). A real screen recording of the actual product
carries an enterprise story better than any generated abstract, and costs nothing to use. This is
the single highest-leverage line in this step — the 2026-08-18 asset had 17 unused product
screenshots and 9 gateway-flow diagrams sitting in the profile.

## Step 1 — Ask the one question

If the user's intent is already explicit, skip the question and route directly. Otherwise, ask exactly this:

> **Do you have footage already, or should I write something from scratch?**

(Ask it in that order. Footage is the better lane and burying it makes the worse one the default.)

Accept these answers (case-insensitive, synonyms allowed):

Footage lanes are listed first because they are the primary lanes now — better output, lower cost,
no disclosure duty. If the operator has usable footage, prefer it over generating.

| User intent | Route to |
|---|---|
| "I have long-form footage to clip" / "Clip my video" / "I recorded myself" / "Repurpose a webinar" | `packs/creator/graphs/repurpose-clips.toml` |
| "I have a screen recording" / "Demo clip" / "Product demo" / "Screen capture" | `packs/creator/graphs/demo-clips.toml` |
| "I have footage I want restyled" / "Restyle my clip" / "Make my footage match the brand" | `packs/creator/graphs/restyle-shorts.toml` |
| "From scratch" / "Write a script" / "I want a reel" / "Make a short from an idea" | `packs/creator/graphs/short-form-video.toml` plan node — the **faceless** from-scratch lane: b-roll, screen shots, held stills, VO, captions. No disclosure duty. |
| "From scratch, with me on camera" / "A talking head" / "Use my avatar" / "Presenter video" — **and the operator accepted the disclosure trade in Step 0.5 item 1** | `packs/creator/graphs/presenter-video.toml` — the intercut lane: presenter beats on `video-avatar`, inserts on `video-render`, rendered concurrently |

## Step 2 — Emit the routing directive

Reply with a concise confirmation and the directive:

- **short-form-video:** "Routing to the short-form-video lane — from-scratch script, storyboard gate, then render." Then emit: `Run the creator pack short-form-video variant for the active profile.`
- **presenter-video:** "Routing to presenter-video — your avatar on camera, cut against b-roll and screen shots. Every post from this lane carries your disclosure line." Then emit: `Run the creator pack presenter-video variant for the active profile.`
- **repurpose-clips:** "Routing to repurpose-clips — clipping your long-form footage into shorts." Then emit: `Run the creator pack repurpose-clips variant for the active profile.`
- **restyle-shorts:** "Routing to restyle-shorts — restyling your existing footage." Then emit: `Run the creator pack restyle-shorts variant for the active profile.`
- **demo-clips:** "Routing to demo-clips — turning your screen recording into clips." Then emit: `Run the creator pack demo-clips variant for the active profile.`

## Guardrails

- **Never guess.** If the user's answer does not clearly match one of the four lanes, re-ask the one question. Do not pick a default.
- **Never route a speaking synthetic presenter without an explicit disclosure decision.** The
  engine resolves only for a disclosed render, so routing one without asking produces a script
  `shots_lint` refuses before render, after the writing is already done. Ask, then carry the
  answer forward: a shot list for this lane declares `synthetic_disclosure` at the top level, and
  the presenter shots are rendered by `video-avatar`, never `video-render`.
- Do not call any image, video, or publish tool from this skill. The Step 0 and Step 0.5 audits are read-only brand-kit/provider/plan checks — they cost nothing and create nothing. `get_cost`, `balance`, `get_plan_usage`, `models_explore` and `get_caption_styles` are all free; an actual `generate_*` call from this skill is a bug.
- **Step 0.5's four answers are decisions, not gates.** Report them and route. A missing screen-capture asset or an undeclared caption preset does not block the lane — it is context the downstream skills need, and withholding the route to chase it just moves the work later.
- Do not write files or modify the plan. Identity gaps route to `identity-kit`, which owns the writes and the Art. 50 consent gate.
- Do not explain the four lanes unless the user asks — the goal is to hide the taxonomy, not teach it.
