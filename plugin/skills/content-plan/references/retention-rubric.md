# Retention rubric — deciding whether a signal survives as short-form video

> **Company-neutral.** This is the *mechanism* for judging whether a chosen idea will **hold
> attention as motion**. It carries no company facts. Formats, cadence, goal mix, and platform
> tuning all come from the **active profile's** `knowledge/content-priority.md` and
> `knowledge/social-tuning.md`. Sibling of `shareability-rubric.md`, used by `content-plan` and by
> the `creator` pack's script stage.

## The idea

`shareability-rubric.md` scores **what to say** — whether an idea is worth spreading. It says
nothing about whether that idea *survives contact with a feed as video*, which is a different
question with a different failure mode. A signal can be timely, ownable, and well-evidenced and
still lose every viewer in three seconds.

The two rubrics compose: **shareability picks the idea, retention decides whether video is the
right vehicle for it and how the first seconds must be built.** A high-shareability idea that
scores badly here should ship as a carousel or a text post, not as a worse video.

## Part A — Retention craft (score the treatment, not the topic)

Score the *planned treatment* on these six dimensions, 0–2 each. Higher = more likely to hold.

| Dimension | What it rewards | Fails when |
|---|---|---|
| **Intro retention (first ~3s)** | The opening frame states stakes, shows a result, or poses a live question. Weight this **double** — it is the strongest single signal | Logo card, slow pan, "Hey guys", throat-clearing, a title that only names the topic |
| **Cold open** | Starts mid-action or mid-sentence, already inside the argument | A spoken introduction before the substance begins |
| **Micro-retention beats** | Something changes at ~1s, ~3s, then every few seconds — cut, reframe, new claim, on-screen turn | One static shot, one continuous take, one unbroken idea |
| **Completion realism** | The length matches how much the idea actually needs. A shorter, fully-watched asset beats a longer, half-watched one | Padding to hit a duration; a 60s treatment for a 20s idea |
| **Silent legibility** | Lands with sound off — burned-in captions, on-screen text carrying the claim, visible action — **and every screen is readable in the time it is held** | Audio-only payload; captions that merely transcribe rather than emphasize; **text nobody can finish before the cut** |
| **Payoff density** | Delivers the promised thing, then earns a re-watch or a save. Utility, a number, a reframe | An open loop that never closes; a hook the body does not pay off |

Sum to a **retention score out of 14** (six dimensions, each scored 0–2; intro counts twice, so it
contributes two of the seven weighted slots — say which weighting you used). Record the two weakest
dimensions: those are the specific instructions the script stage must fix, not a reason to abandon
the idea.

> **Silent legibility is not a licence for more text — it has a ceiling (added 2026-08-18).**
> This dimension used to reward on-screen text with no upper bound, and a shipped asset scored
> *well* on it while carrying **150 on-screen words in 15.55s** — a reading load of 9.6 words/s
> against a comfortable ~2.5 w/s when a voice-over is competing. Reading it needed ~60 seconds.
> The operator's first reported defect was being unable to read it and feeling overloaded.
>
> Score this dimension **0 if any screen cannot be read in the time it is held**, regardless of
> how completely the text carries the claim. The deterministic check is
> [`gtm_core.video_lint`](../../../../gtm_core/video_lint.py) **V7** (≤4 w/s per screen, ≤3 w/s
> across the asset); **V8** additionally flags captions that diverge from the spoken line, because
> two independent text streams double the load rather than reinforcing.

### Re-score the RENDERED asset, not just the plan

Part A above is scored on a *planned treatment* — that is what makes it a free pre-render gate.
It is not a verdict on what actually got made. **After the finish stage, score Part A again
against the rendered asset and report both numbers and the delta.**

This is not bookkeeping. On 2026-08-18 a plan scored **13/14** and the asset it produced scored
**10/14** — intro retention lost because the hook's payoff line rendered underneath the platform's
own UI chrome, and micro-retention lost because the first cut did not arrive until 4.2s. Neither
defect was visible in the plan, and the 13/14 was carried forward into the handoff as though it
described the finished video. A plan score that is never re-tested against the artifact is a
prediction being reported as a measurement.

### Three finish checks Part A structurally cannot see

Part A scores whether the *treatment* should hold attention. These three ask whether the finished
artifact reads as something a serious company made, and they are **flags, not scores** — they stay
out of the /14 deliberately, because that number feeds the spend gate below and a rubric whose
denominator drifts is a threshold nobody can reason about. A flag is a named fix, or an explicit
"accepted, because —" in the report.

- **Register fit.** Does the visual treatment match the register the profile actually sells in?
  Model-rendered and template-driven video defaults to a maximalist house style — chrome, glow,
  confetti, dollar-sign motifs, hard whooshes — that is *competent* and still wrong for a B2B
  security or infrastructure buyer. The useful form of the test is comparative and takes one
  second: **name a company whose brand your ICP already trusts, and ask whether they would run
  this frame.** If the honest answer is no, the finish is off-register no matter how clean the
  execution. This is the visual counterpart of the register rule `content_quality` already applies
  to prose — same failure, different surface.
- **Every visual earns its place.** A shot must advance the specific claim on screen, not merely
  occupy the frame while the claim is spoken. The tell is the *generic* visual — stock-feeling
  footage, or a rendered b-roll shot that would fit any script on the topic. Generic visuals read
  as filler to a viewer even when they cannot say why, and they are what a render budget gets spent
  on when nobody asked what a beat is *for*. Prefer fewer shots that each carry a claim over full
  coverage that carries none.
- **Cold-viewer self-containment (clips especially).** Whoever scores a clip has read the source,
  and therefore cannot tell from the inside whether the clip stands alone — this is a blind spot in
  the scorer, not a dimension of the asset, which is why Part A's payoff-density row does not catch
  it. It binds hardest on **automated clip selection**: a keyword-located segment reliably lands on
  the sentence containing the topic word while the sentence that made it *mean* something sits
  outside the cut. Read the clip's transcript alone, with the source closed, and state in one line
  what a stranger learns. If that line needs a fact the clip never says, the cut is wrong — reject
  it rather than scoring it, and say which missing sentence would fix it.

*Provenance: the three checks are a taxonomy adapted from third-party creator-education critique
material (a professional edit-review teardown), restated in this system's terms. Directional craft
heuristics, same standing as the rest of this file — see the evidence note. No number on this page
comes from that source.*

## Part B — Platform objective fit (separate; never added to Part A)

Platforms optimize for different things, so one blended number would average away the very
difference that decides where an asset should run. Score fit **per platform, independently**:

| If the platform rewards… | Then the asset needs… |
|---|---|
| **Saves and shares** | A reason to keep it — a framework, a checklist, a number worth citing later |
| **Reach and profile follows** | A reason to want more from the *author* — a distinct POV or a serial format |
| **Click-through and average view duration** | A title/thumbnail promise the body actually pays off, and length matched to payoff |

Read which objective applies to each target platform from the profile's `social-tuning.md`. If it
does not say, treat the objective as unknown and say so in the rationale rather than guessing.

> **Do not collapse Part A and Part B into one number.** An asset can be well-built (high A) and a
> poor fit for a given surface (low B). That is a routing decision, not a quality verdict.

## Gate before spending render credits

Rendering costs money; scoring does not. In this order:

1. **Part A below threshold** (profile-configurable; 8/14 is a sane default) → rework the treatment
   or downgrade the format. Do not render.
2. **There is no pre-render `virality_predictor` cross-check.** `virality_predictor` takes a
   *rendered video* as input (`medias[].role = "video"`) — it has nothing to look at before
   anything is rendered. The free, pre-render gate is Part A above, alone. The paid cross-check
   with `virality_predictor` runs **after** render, at the `video-score` stage, on an asset that
   actually exists — treat it there as a second opinion, not an oracle: where it disagrees with
   Part A, say which you followed and why. *(Corrected 2026-08-15 — this section previously
   instructed an impossible ordering; see `packs/creator/graphs/short-form-video.toml`'s header
   comment, which records the same correction.)*

   **"Second opinion, not an oracle" is measured, not a hedge (2026-08-17).** A controlled test —
   three identical-prompt renders, readings taken at pinned playhead positions — found the
   predictor's result is a per-timepoint HTML readout, not a stable score: scrubbing one
   unchanged video moved its own composite reading 11-12 points, more than the 4-5 point spread
   between independently rendered seeds of the same prompt. `video-render`'s earlier hard "hook
   strength ≥ 60" spend bar has been removed for exactly this reason — a numeric threshold on a
   number that moves 11 points depending on where a scrubber sits cannot support a spend
   decision. The predictor is recorded advisory at both `video-render` (the draft pool) and
   `video-score` (the ranking), never a gate anywhere in the pipeline.
3. Only then render. Cost preflight (`get_cost`) and the §R2 budget check still apply.

Once outcome rows exist for shipped assets, the dimension weights here should be re-fitted against
what actually retained — that is the loop in PRD §5.4, and it is what stops this rubric from
staying a set of priors forever.

## Evidence note — read before quoting any of this externally

The dimensions above are **directional craft heuristics drawn from third-party agency analysis**,
not measured results from this system. The 3-second emphasis is the best-corroborated of them; the
caption and completion effects are single-vendor figures and are deliberately stated here without
numbers. **Do not publish a specific retention percentage sourced from this file.** After Phase 5
lands, prefer your own outcome data over every claim on this page.

## Output

For each item judged: the **Part A score** with its two weakest dimensions named, the **Part B fit**
per target platform, the `virality_predictor` reading, and a one-line verdict — *render as video*,
*rework the opening first*, or *ship in another format*. That line becomes the retention section of
the item's `.audit.md`, so the spend decision is auditable rather than a black box.
