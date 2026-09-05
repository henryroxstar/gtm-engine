
# Video Router

Route a "make a video" request into the correct creator-pack variant. This skill does **not** create a graph node and does **not** spend credits — it runs before pack selection and emits a directive to start the right lane.

> Resolve the **active profile** (the agent provides it). Do not write under `content/<active>/` — only emit a routing directive.

## Step 0 — Lane preflight (one command, zero spend, zero writes)

**Run this first, before any creative work and before asking anything:**

```bash
uv run python -m gtm_core.video_preflight --profile <active> [--product <slug>]
```

It resolves, from disk, which lanes can actually run and what bounds the ones that can: lane
existence, pack activation, the identity handles **that lane** needs, engine resolution,
disclosure line, the kit's imagery constraints, the caption preset, the shot-mix ceiling, and the
reusable assets the profile already holds. Exit 0 = at least one lane is runnable; exit 2 = every
lane is blocked, which is a reportable state, not a crash.

**The requirement is keyed to the LANE, not to a fixed handle pair.** `presenter-video` renders on
HeyGen and needs `heygen_avatar_id` + `heygen_voice_grade` — auditing `soul_id`/`voice_id` says
nothing about whether it can run, and on 2026-08-28 that mismatch let this step pass clean on a
profile that could not render the lane it then routed to. Note `heygen_voice_grade` is a different
key from `voice_grade`; they belong to different providers.

- **A blocked lane is not a stop.** Report it with the one unlock step the preflight names, and
  offer the lanes that are ready. Only hand off to `identity-kit` if the operator chooses a lane
  that needs a handle they don't have.
- **Never route to a lane the preflight did not mark ready.** That produces a script refused at
  `shots_lint`, or a spend on a lane that cannot finish, after the writing is done.
- Existing `reference_element_ids` are visibility only. **Do not gate on a missing
  environment/prop element** — which non-person B-roll a video needs depends on the shot list,
  and the shot list doesn't exist until `video-script` runs. That gap is real only once a shot
  names a role that needs one; it's caught there (shot role tagging) or at `video-storyboard`
  (per-shot identity resolution), not here.

### The default lane, and progressive identity

A profile that has just been onboarded has **no footage and no identity handles** — that is day
one for every tenant, not an edge case. It is still not blocked: `short-form-video` requires no
identity handles at all (its shots resolve through the `broll` role, which binds a general
image-to-video engine with no identity constraint), and `video-storyboard` generates the stills
`video-render` then animates. The lane is self-supplying.

So **treat the preflight's `default_lane` as the answer for anyone who has nothing**, and do not
front-load `identity-kit` on a new profile. Identity is progressive: ship faceless first, offer
`soul_2` stills once a few have shipped, offer the HeyGen twin when the operator wants a face on
camera. Each upgrade unlocks exactly one role, and the preflight names which.

**Asset ladder: reuse > capture > generate.** The preflight lists what the profile already holds.
A real screen recording or product screenshot carries the story better than any generated
abstract and costs nothing — prefer it before proposing a generated shot.

## Step 0.5 — Production preflight (still zero spend, zero writes)

Six defects shipped on 2026-08-18 — broken lip sync, captions over the face, wardrobe changing
shot to shot, motionless talking-head shots, and a weak hook. Every one traced to a decision that
was *knowable before a single word of script existed* and was instead taken by default, late, at
render time. This step takes them explicitly, up front. Every check here is a free read.

Report the answers in one short block, then route. Do not stop the lane on any of them —
they are decisions to carry forward, not gates.

**1. Presenter engine — which one, and on what condition? Decided here, never at render.**

Ask the registry both questions, because they have different answers:

```bash
uv run python -m gtm_core.render_engines --role presenter --speaks --disclosed   # the disclosed case
uv run python -m gtm_core.render_engines --role presenter --speaks               # the undisclosed case
```

Exit 0 prints the engine; **exit 2 means that case may not be rendered**, and the `reason` it
prints is written to be read aloud to the operator.

As of 2026-08-20 the answers are:

- **Disclosed → `heygen_avatar`.** A synthetic talking head is renderable by the `video-avatar`
  skill, on a trained digital twin with native lip sync, *provided* the finished post carries the
  tenant's `[disclosure].line`. Check the tenant actually has one
  (`uv run python -m gtm_core.brandkit --profile <active> --key disclosure.line`) — an empty line is a
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

**2. Caption route — Reap or local.** Read `captions_preset` off the Step 0 preflight. If it
resolved, the route is Reap (Reap's `get_caption_styles` for the catalogue, `transcribe` for real
per-word timings, `add_captions` with that preset). Read the plan live with Reap's
`get_plan_usage` — it is free — and quote what it returns, never a remembered figure. The durable
point, which is what this check exists for: **a paid caption route sits effectively unused while
captions are hand-rolled locally.**

If it did **not** resolve, declare one in the brief and say so explicitly — `captions.preset` is
not writable through `brandkit --set` (which accepts `identity.*` only), so it is a brief-level
decision until someone edits the kit deliberately.

Either way, **say which placement the preset implies — and read it off the preflight, do not
recite a list.** The preflight resolves `captions_placement` for the tenant's actual preset
through `gtm_core.captions`, and prints `upper_placement_presets` if you need the whole set.

That module is the only place the mapping is correct. A hand-copied version of it in this file
held **eight** of the nine upper-placement presets and had been silently missing
`system_ember_duo` — so a tenant on that preset would have had its captions placed as if they
were lower-placement, across the speaker's face, which is the exact defect the placement rule
exists to prevent. Derive it; do not restate it.

The *reasoning* is still worth stating, because no field carries it: upper placement is
**face-safety, not taste**, and the animated presets are **not** upper-placement — so motion and
face-safety are a genuine trade-off in the catalogue rather than a free choice. Call that out
instead of picking silently. (One geometric caveat the preset alone does not settle: on 9:16 and
16:9 the face band starts above the safe box, so `upper` has no clear room and
`resolve_placement` returns `lower` for a ratio anyway.)

**3. Shot mix — set the target before the script is written, from the rules the linter actually
runs.** The preflight relays them in `shot_rules`, with the ceiling in
`presenter_share_ceiling` and the required role in `required_shot_role`. For the full set with
each rule's own one-line summary:

```bash
uv run python -m gtm_core.shots_lint --rules
```

**Relay those values; do not restate them here or from memory.** This step used to name the
ceiling and the required role as literals, and on 2026-08-28 the linter grew two new rules
(`audio_bed`, the look/ratio check) while the literals did not — so a script written to the
documented target was refused by rules it had never been told about. The linter publishes its own
rule set precisely so this file does not have to guess at it.

State the target now — roughly half presenter, at least one real screen capture of the thing being
discussed, the rest b-roll/data/motion-graphic. A script written to that mix is cheap; one
retrofitted to it is a rewrite.

**4. Existing assets — what can be reused instead of generated.** The preflight's
`existing_assets` lists them. Name the ones that could serve a `screen` or `broll` shot and prefer
them over a generated equivalent: a real screen recording of the actual product carries the story
better than any generated abstract and costs nothing. This is the highest-leverage line in the
step — the 2026-08-18 asset had 17 unused product screenshots and 9 flow diagrams sitting in the
profile, and on 2026-08-28 a brief weighed generating abstract b-roll while 8 unused product
screenshots sat in the same profile.

**Also carry forward the kit's `imagery_style` and `imagery_negative`.** Read them as a
**checklist, not flavour**: the 2026-08-28 concept proposed a rig implying extra hands against a
kit reading `no distorted hands, no extra fingers`, and staged it as frantic desk chaos against
`calm, unhurried` / `no cluttered desk-setup cliches`. Both are mechanical checks that a human
reads past and a checklist does not.

**5. Approved avatar look — ASK, EVERY RUN. This is the one decision that is not yours to take.**

Only the presenter lane spends on this, but it is decided here, before the lane is chosen, because
after routing it is decided by a render. Read `look_proposals` off the Step 0 preflight — two rows,
`landscape` and `portrait`, each with its own `ask` string. **Relay the `ask` verbatim on whichever
orientation the request implies, and wait for an answer.**

- **`identity.heygen_look_landscape` / `identity.heygen_look_portrait` set** → a one-line confirm:
  name the look and its native pixels (`list_avatar_looks` — free, and the only place the pixels
  live) and get an explicit yes.
- **Either key empty** → list that orientation's candidate looks with their native pixels and let
  the operator pick. Then hand the pick to `identity-kit`, which records it through the `brandkit`
  CLI so the next run is a confirm rather than a menu.

Three rules, and each of them is the whole point rather than a caveat on it:

- **A recorded look is a proposal, never a silent default.** Storage exists to make the ask cheap,
  not to skip it. On 2026-08-29 a render went out on the pinned digital twin while 40+ looks — six
  of them native 1920×1080 landscape — sat unexamined in the same avatar group at identical cost,
  and the operator saw the result only after it was paid for. **That render was not wrong because
  the look was badly chosen; it was wrong because nobody was asked.** A stored look that applied
  itself would reproduce that exactly, with a config file in front of it.
- **Do not pre-select, and do not fetch previews here.** Which look someone wants to be seen in is
  an aesthetic call about their own face. Offer the candidates with their pixels; the pick is
  theirs. (`video-avatar` fetches a preview later, once a candidate is in play and the wardrobe
  question is real.)
- **A lane is never blocked for lacking an approved look** — this is a decision, not a gate, and
  the preflight deliberately does not list either key as a lane precondition. An unanswered ask
  costs nothing; an unasked question costs a render.

**Landscape and portrait are two decisions, not one.** There is no fallback between the keys, on
purpose: a portrait look rendered to a 16:9 master fills a third of the frame and pads the rest,
which is what shipped on 2026-08-27. If the brief wants both ratios, ask twice.

### Which provider does which job

Four media providers are connected and their tool namespaces overlap. This is the boundary; it is
stated once, here, and every video-lane skill inherits it.

| Job | Provider | Why not the others |
|---|---|---|
| Speaking presenter (disclosed) | **HeyGen** | The only engine with native audio-driven lip sync on a trained twin. |
| B-roll / product / abstract motion | **Higgsfield** | Its reviewed strength, and the only image-to-video surface we hold. |
| Identity stills, held cutaways | **Higgsfield** (`soul_2`) | A trained Soul's only reachable surface — image models only. |
| Clipping, captions, reframe, transcription | **Reap** | Per-word timings, 50+ presets, face-safe placement resolution. Higgsfield's `subtitles` workflow is **declined** for this — not overlooked; Reap's timing and placement are better and the decision is recorded so nobody re-litigates it. |
| Voice-over | **Higgsfield TTS** on `identity.voice_id` | Engine picked by `identity.voice_engine`; not a second integration. |
| Second opinion on text-in-image | **Gemini** (`gemini-image`) | Narrow by design: it is stronger at legible in-image text. Anything else goes to Higgsfield. |
| Publish / schedule | **Reap publisher**, behind the gate | Never brain-initiated (`agent/permissions.py`). |

**Before hand-rolling a multi-step generation flow, check the provider workflow register.**
Higgsfield ships bundled workflows that orchestrate its own tools for whole deliverables, and
several overlap lanes built here by hand. Every one of them already has a dated adopt/decline
verdict with a reason in [`docs/reference/provider-workflows.md`](../../../docs/reference/provider-workflows.md)
— read it there rather than re-deriving it, and add a row before building against a workflow that
has none. Do not copy that table into a report: it is one probe away from stale, and the register
is the copy that gets re-probed.

**Name the server whenever you name a colliding tool.** Four tool names are served by more than
one connected server — `list_templates` (HeyGen + Reap), `get_video` (HeyGen + Reap), `reframe`
(Higgsfield + Reap), `list_voices` (Higgsfield + HeyGen). Write "Reap's `reframe`", never bare
`reframe`. A bare name is not a crash; it is a call to the wrong server that returns a plausible
404, or a real record for something unrelated.

## When there is no operator

**Invoked inside a graph run — from a `[[nodes]]` prompt rather than a conversation — never
present the menu.** There is nobody to answer it: `cross-modal-campaign.toml`'s `video-script`
node hands off here and is **not a gate**, so a question emitted from this skill either hangs the
run or gets answered by whatever the model guesses.

Three rules, in order:

1. **If the item names a lane explicitly, take it** — provided the preflight marked it ready.
2. **Otherwise take the preflight's `default_lane`, and say so in the node report.** Not a silent
   default: the report names the lane and the fact that nobody chose it.
3. **If the preflight exits 2 (every lane blocked), fail the node with the blockers.** Do not pick
   one anyway. A blocked lane inside a graph is a run that cannot finish, and failing here shows
   the operator the blockers at the node report instead of at the first spend.

## Step 1 — Offer the lanes the preflight verified

**Do not ask a question whose answers you have not verified.** The old form of this step asked
"do you have footage, or should I write from scratch?" and offered lanes without checking any of
them — which is how a 2026-08-28 session selected a lane that could not run, after the concept was
already written twice.

If the user's intent is already explicit **and** the preflight marked that lane ready, route
directly. Otherwise present the preflight's own verdict as a short menu — ready lanes first,
blocked lanes named with their one unlock step, so the operator sees the whole board rather than
guessing at it:

> **Ready now**
> - Faceless short — I generate the b-roll and use your existing screenshots. **Captions and
>   music, no voice-over** (add one anytime with `identity-kit`, ~5 min).
> - Clip footage you already have — if you have a recording.
>
> **Needs one step first**
> - You on camera (disclosed avatar) — needs a HeyGen twin, ~10 min in `identity-kit`.

**Name the approved look on the you-on-camera row, before the operator picks the lane.** Step 0.5
item 5 has the answer already; the menu is where it becomes visible while the lane is still free to
change. A stored look reads as *"you on camera — as look `<id>` (1920×1080 landscape); confirm or
pick another"*; an unrecorded one reads as *"you on camera — I'll show you the looks and you
choose"*. Either way the operator sees the face they are about to buy **before** they buy it, which
is the entire fix: the look is the most visible thing in the finished video and was the one thing
nobody was asked about.

**Relay every `caveats` entry the preflight prints, verbatim, on the lane it belongs to.** A
caveat says what a ready lane will *not* do, and the operator is choosing between lanes on exactly
that. The one live today: with no `identity.voice_id` in the kit, the faceless lane renders every
shot and then plays **silent** — `video-render` generates the VO only when a `voice_id` is present
and has no else-branch, so the gap surfaces at `video_lint` V10 on the finished asset, after the
whole spend. A bare profile has no voice by definition, so this is the *default* state of the lane
this menu recommends most confidently, not an edge case.

A caveat is a **disclosure of scope, not a refusal**. Captions-and-music is a legitimate
deliverable — do not downgrade the lane to blocked, and do not send the operator to `identity-kit`
before they asked for a voice. Say what they get, say what it costs to add, and let them choose.
(`shots_lint` enforces the same line from the other end: a script with `[SPOKEN]` lines against a
kit with no voice is refused before any render, unless the list declares its own `vo_source`.)

**Show the preflight's `estimated_credits` beside each lane that has one**, phrased as the
estimate it is ("~23 credits/min"), never as a quote. Where the field is `None`, **say nothing
about cost for that lane** — no "unknown", no "0", no guess. A coarse figure read as a price is
the failure mode: an operator told "~23" who then spends 200 trusts the next estimate less than
one who was told nothing at all. Credit burn nobody authorised is the loudest complaint in this
market, and a lane chosen blind to spend is how it happens.

Keep it to the lanes that apply. If the operator has nothing and no preference, **take the
preflight's `default_lane`** — do not make a cold-start user answer a question about footage they
do not have.

### A request for N videos is one routing decision, not N

Run the preflight once, choose the lane once, and emit **one** directive carrying the item count —
the pack graph fans out from there. Three clips asked for in one breath is one lane decision with
a count of three, not three trips through this skill.

Re-route per clip **only when the clips genuinely differ in lane** — one from footage you already
have, one written from scratch — and when you do, say so explicitly rather than letting the split
look accidental.

Two ceilings bound any Reap-backed fan-out, and queuing against them is correct behaviour while
erroring is not: **`maxConcurrentProjects: 3`** on the plan, and **10 requests/minute per API
key**. Space the calls; do not widen the batch to beat them.

Accept these answers (case-insensitive, synonyms allowed):

Footage lanes are listed first because they are the preferred lanes when footage exists — better
output, lower cost, no disclosure duty. But a lane needing footage is never the default for
someone who has none.

| User intent | Route to |
|---|---|
| "I have long-form footage to clip" / "Clip my video" / "I recorded myself" / "Repurpose a webinar" | `packs/creator/graphs/repurpose-clips.toml` |
| "I have a screen recording" / "Demo clip" / "Product demo" / "Screen capture" | `packs/creator/graphs/demo-clips.toml` |
| "I have footage I want restyled" / "Restyle my clip" / "Make my footage match the brand" | `packs/creator/graphs/restyle-shorts.toml` |
| "From scratch" / "Write a script" / "I want a reel" / "Make a short from an idea" | `packs/creator/graphs/short-form-video.toml` plan node — the **faceless** from-scratch lane: b-roll, screen shots, held stills, VO, captions. No disclosure duty. |
| "From scratch, with me on camera" / "A talking head" / "Use my avatar" / "Presenter video" — **and the operator accepted the disclosure trade in Step 0.5 item 1** | `packs/creator/graphs/presenter-video.toml` — the intercut lane: presenter beats on `video-avatar`, inserts on `video-render`, rendered concurrently |
| "Write it and I'll shoot it myself" / "Write me a script to film" / "From scratch, on a real camera" / "I'll record it once you've written it" | `packs/creator/graphs/live-action-video.toml` — write first, shoot after: the run **parks at a capture gate** until you have footage, then Reap clips it. No render nodes, nothing synthesised, **no disclosure duty** |

## Step 2 — Emit the routing directive

Reply with a concise confirmation and the directive:

- **short-form-video:** "Routing to the short-form-video lane — from-scratch script, storyboard gate, then render." Then emit: `Run the creator pack short-form-video variant for the active profile.`
- **presenter-video:** "Routing to presenter-video — your avatar on camera, cut against b-roll and screen shots. Every post from this lane carries your disclosure line." Then emit: `Run the creator pack presenter-video variant for the active profile.`
- **repurpose-clips:** "Routing to repurpose-clips — clipping your long-form footage into shorts." Then emit: `Run the creator pack repurpose-clips variant for the active profile.`
- **restyle-shorts:** "Routing to restyle-shorts — restyling your existing footage." Then emit: `Run the creator pack restyle-shorts variant for the active profile.`
- **demo-clips:** "Routing to demo-clips — turning your screen recording into clips." Then emit: `Run the creator pack demo-clips variant for the active profile.`
- **live-action-video:** "Routing to live-action-video — I'll write the script, then the run pauses at a capture gate until you've filmed it, and Reap clips what you shoot. Nothing is synthesised, so no AI-disclosure line." Then emit: `Run the creator pack live-action-video variant for the active profile.`

## Guardrails

- **Never guess.** If the user's answer does not clearly match one of the six lanes, re-ask. Do not pick a default other than the preflight's `default_lane` for an operator who has stated no preference.
- **If the request matches no lane, say so — do not bend it into the nearest one.** Not every
  shape of video work has a graph. Report the gap and let the operator choose how to proceed;
  routing it anyway burns a plan gate and a storyboard gate before failing at the first node that
  spends.

  **Check the preflight's lane list before calling anything a gap.** A guardrail that names a
  shape as uncovered while a graph for it sits in the pack is worse than the original gap — it
  stops the operator looking. When a shape genuinely has no lane, report it; when one gets a lane,
  delete the example from this block in the same change.

  **A stock avatar — a presenter who is not the operator — is one of these, and it is not
  `presenter-video`.** That lane renders the operator's own trained twin, which is precisely what
  an operator asking for "someone else on camera" has said they do not want; faceless is not it
  either, because that drops the presenter they asked for. Today no lane covers it. Report the gap
  and let them choose — do not bend the request into the twin lane, and do not talk them into
  faceless because it is the lane that runs.

  Two facts to state when you report it, because they are what makes the decision, and both are
  recorded in [`docs/reference/provider-workflows.md`](../../../docs/reference/provider-workflows.md):
  a generated character *can* be held consistent across shots (Higgsfield's `character-sheet`), so
  the likeness half of the request is solvable — but that surface generates **images**, and no
  video model here does audio-driven lip sync, so the *talking* half is not. And a stock avatar
  needs no likeness consent, which is exactly why it reads as the cheap option, yet it is still
  synthetic media: the Art. 50 disclosure duty is unchanged. Building it is a lane decision with
  its own disclosure analysis, not a routing one.
- **Never route a speaking synthetic presenter without an explicit disclosure decision.** The
  engine resolves only for a disclosed render, so routing one without asking produces a script
  `shots_lint` refuses before render, after the writing is already done. Ask, then carry the
  answer forward: a shot list for this lane declares `synthetic_disclosure` at the top level, and
  the presenter shots are rendered by `video-avatar`, never `video-render`.
- Do not call any image, video, or publish tool from this skill. Step 0's preflight is a read-only disk check and Step 0.5's are read-only registry/plan checks — they cost nothing and create nothing. `get_cost`, `balance`, `get_plan_usage`, `models_explore` and `get_caption_styles` are all free; an actual `generate_*` call from this skill is a bug.
- **Gates and decisions are different, and must not be mixed.** Step 0's lane feasibility is a
  **gate** — a lane missing its handles cannot run. Step 0.5's answers are **decisions to
  carry forward**: a missing screen-capture asset, an undeclared caption preset, or an unrecorded
  avatar look does not block the lane, and withholding the route to chase one just moves the work
  later. Treating a decision as a gate stalls; treating a gate as a decision ships a brief that
  cannot be built.

  **A lane is never blocked for lacking an approved look.** The ask still happens — every run,
  before spend — but an unanswered aesthetic preference is not a missing capability, and the
  preflight lists neither look key as a lane precondition precisely so this cannot drift into
  one.
- Do not write files or modify the plan. Identity gaps route to `identity-kit`, which owns the writes and the Art. 50 consent gate.
- Do not explain the lane taxonomy unless the user asks — the goal is to hide it, not teach it. The Step 1 menu names outcomes ("faceless short", "you on camera"), never `.toml` filenames.
