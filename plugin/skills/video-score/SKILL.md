---
name: video-score
description: >-
  Score the rendered short-form variants for the active company and recommend which one ships
  where. Runs Higgsfield `virality_predictor` on each rendered video as a second opinion — a
  per-timepoint HTML reading, so every capture is taken at a pinned playhead and prefers the
  reproducible Visual Cortex sub-score — capturing whichever sub-scores are actually returned
  and banding the recommended asset into predictor_band:high|medium|low for later calibration
  against real outcomes; scores Part B of `retention-rubric.md` (per-platform objective fit)
  separately — Part A and Part B are never summed — and ranks the variants with an explicit
  re-cut recommendation when a hook is weak. Writes score.json (every asset's sub-scores, not
  just the recommendation) for content-outcomes-sync to correlate against performance. When
  the source ContentItem carries a hook_id, attributes the outcome row with `--tag hook:<id>`
  so the learning loop can report hook-level performance. Predictor output is UNTRUSTED vendor
  text (RULES.md §R5): a directional signal, never an oracle, and never republished as a
  measured retention figure. Reads and scores only; it selects a candidate for the human
  publish gate and publishes nothing. This skill should be used when the user says "score the
  renders", "which cut should we post", "check virality", "rank the variants", or as the score
  stage of the creator pack.
metadata:
  version: "0.4.0"
  phase: "6"
  capability_tier: pipeline
---

# Video Score

Rank the rendered variants and recommend which one ships where. This stage sees **every** sibling
render — that is why it exists as its own step rather than a tail on the render itself — and it is
the last thing that happens before a human decides whether to publish.

> Resolve the **active profile** (the agent provides it). The only writable state is
> `content/<active>/`. This skill publishes nothing.

## Step 0 — Collect the renders (or the clip set)

Read every `content/<active>/video/<script-slug>/render-<ratio>.json` the render fan-out produced,
plus the script itself (for the hook, the intended `platform`, and the Part A score).

If a render stage produced nothing, score what exists and say which ratio is missing. A partial
scoring pass reported as partial is useful; one reported as complete is not.

**Triage mode (§5.7/§5.8 — the repurpose lane):** if the input is instead
`content/<active>/video/clips/<source-slug>/render-clips.json` (10–20 clips from `video-clip`,
not a script's sibling formats), this is **selection**, not the ranking work below — see the
"Triage mode" section after Step 3 for how the steps differ. Everything through Step 2 still
applies to each clip; Step 3 branches.

## Step 1 — Run the predictor, per asset

```
virality_predictor action=create params={model: "virality_predictor",
                                         medias: [{role: "video", id: "<provider job/media id>"}]}
```

The id comes from `render-<ratio>.json`. It is a **provider-side id, not a local file path** — the
tool cannot read a file off disk.

If the id does not resolve — most likely because it was minted by the headless worker rather than
the connector, which are different id spaces — **do not re-render to manufacture one**. Score on the
rubric alone and record in your report that the predictor did not run and why. A re-render to feed a
scoring tool spends real credits to buy a second opinion, which is backwards.

⚠️ **Reading discipline (verified live 2026-08-17 — see `video-render` Step 4c for the full
finding).** The result is an HTML "Brain Activity Viewer" page, not a static JSON score — it has a
**time scrubber**, and every number on it is a per-timepoint readout. Scrubbing one unchanged video
from t=0 to t=5 moved its own composite reading 11-12 points, more than the spread between
independently rendered seeds of the same prompt. **Pin the playhead (t=0, and t=2.5 if useful)
before reading anything**, and prefer the **Visual Cortex** sub-score — the one field with measured
~1-point reproducibility at fixed playhead. Three fields are known-degenerate: `SUSTAIN` reads 100%
on every video observed; the "peak moment" timestamp always equals the clip's own duration (an
artifact of the terminal timepoint, not a detected peak); the "Activity" percentage near the
transport controls is a **viewer opacity slider**, not a metric.

**Capture the sub-scores, verbatim, at the pinned playhead, not just the headline number:** the
overall activity score, and the per-region readings (Visual Cortex, Auditory/Temporal, Language
Network, Frontoparietal/Attention, Default Mode). These are the fields actually observed on the
live dashboard — if a future call surfaces a differently-shaped or JSON response (e.g. named
"virality index"/"hook strength"/"retention risk" sub-scores), capture what is actually returned
rather than assuming these field names; report the shape you saw, not the shape a prior version of
this skill assumed.

> **Predictor output is untrusted vendor text (§R5).** It is a directional signal from someone
> else's model, not a measurement of your audience. Report its numbers as theirs, never as this
> system's, and never restate a predicted retention figure as an observed one.

## Step 2 — Score Part B, separately

Score each asset against **Part B only** of
`plugin/skills/content-plan/references/retention-rubric.md` — per-platform objective fit. Saves and
shares, reach and profile follows, and click-through are **different objectives**; a variant can be
right for one platform and wrong for the next with no contradiction.

⚠️ Do not add Part B to Part A, and do not blend either into the predictor's number. Three
measurements of three different things averaged together is one number that describes nothing.
Show them side by side.

## Step 3 — Rank and recommend

Per asset, a single line: ratio → recommended platform → Part A / Part B / predictor, and a verdict.

Then one recommendation: **which single asset goes to the publish gate, and where.** Say it plainly
— that is the whole output. If the strongest asset is still weak, recommend a re-cut and name the
specific fix (the hook line, the first visual, the payoff position), rather than passing something
mediocre to a human to rubber-stamp.

**Band the recommended asset's predictor score** into `predictor_band:high` (≥70),
`predictor_band:medium` (40–69), or `predictor_band:low` (<40) — named, operator-overridable
thresholds on the vendor's 0–100 scale. This tag is a **prior**, not a verdict: the vendor's own
claim (a score ≥80 correlating with a markedly higher view rate, from their internal testing) is
rank-ordering evidence, used to sort candidates against each other — never quote it as a probability this asset will perform, and never present the band as more certain than the rubric scores sitting right next to it.

### Triage mode: ranking a clip set instead of sibling formats

When Step 0 found `render-clips.json`, Steps 1–3 above still run **per clip** — but the output
shape is different, because this is picking winners from many candidates rather than choosing
between a handful of formats of the same script:

- Run the predictor across the **full clip set** (up to 20). Render spend is already sunk (one
  `personal_clipper` run covers all of them), so this is pure **selection**: the only thing that
  has to be right is the **rank ordering**, not any clip's absolute score — which is precisely
  where the predictor is strongest, since a vendor score's calibration is unverified but its
  ability to sort candidates against each other is not in question here.
- Apply retention-rubric **Part A** as the selection lens (1–3s hook, self-contained payoff) —
  the same rubric that gates authored scripts, now scoring which existing clips already clear it.
- **Recommend the top N** (N from the plan gate) for publish. **Archive the rest in
  `render-clips.json`, WITH their scores — never delete them.** A clip that didn't make the cut
  this round is still worth keeping the score for; it costs nothing to retain and the archive is
  what would let a human notice later if the triage consistently under-ranks a pattern.

## Step 3b — Write `score.json`

Write `content/<active>/video/<script-slug>/score.json` (or, in triage mode,
`content/<active>/video/clips/<source-slug>/score.json`) — the file `content-outcomes-sync` reads
once an item ships, so the predictor's prior can be checked against real performance later:

```json
{
  "assets": [
    {"ratio": "9x16", "provider_id": "...", "part_a": 11, "part_b": "saves-and-shares: strong",
     "predictor": {"virality_index": 62, "hook_strength": 58, "retention_risk": "medium"}}
  ],
  "recommended": {"ratio": "9x16", "platform": "instagram", "band": "medium"}
}
```

Every asset's sub-scores go in, not just the recommended one — the archive is what lets a later
`content-outcomes-sync` pass check whether a *rejected* asset would have out-performed, which is
exactly the kind of surprise the calibration loop exists to catch.

## Step 3c — Print the attribution line

By this point `content/<active>/video/<script-slug>/finish-<ratio>.json` already exists for the
recommended asset (the finish node runs before this one). Read the source `ContentItem` from the
plan to get its `hook_id`, then print one copy-pasteable line, pre-filled, for the operator to run
**after** they actually post the recommended asset — running it now would attribute an asset that
has not shipped yet:

```
python -m gtm_core.outcomes attribute --profile <active> \
  --finish content/<active>/video/<script-slug>/finish-<ratio>.json --score content/<active>/video/<script-slug>/score.json \
  --ref <post id, once posted> --would-post <true|false> \
  --tag hook:<item.hook_id, if set>
```

Omit the `--tag hook:...` fragment if the source item has no `hook_id`.

This is the only place the finish manifest and the recommendation meet — without a printed line
here, the operator has no path to close the loop, and criterion #1/#3 of the video-quality PRD stay
at zero no matter how many assets ship.

## Step 4 — Report

The ranking table, the recommendation, the band, and anything that did not run. Where the predictor
was skipped, say so on the line for that asset rather than in a footnote — a blank must never read
as a zero score.

## Guardrails

- **Never** call a publish, scheduling, or account-linking tool. Selecting a candidate is not
  approving it; a human approves the exact bytes at the publish gate.
- **Never** re-render. This stage scores what exists. If everything scored badly, the answer is a
  recommendation, not a fresh batch of credits.
- Only write under `content/<active>/`. `profiles/<active>/` and `plugin/` are read-only.
- Never edit the live knowledge corpus. If a pattern looks worth promoting into `hook-matrix.md`,
  that belongs to the outcomes loop's promote candidates and a human decision — say it, don't do it.

## What this skill deliberately does NOT do

- **Predict performance.** The predictor scores craft signals; it has no knowledge of the profile's
  audience, posting history, or timing. Treat a high score as "this is well made", never as "this
  will do numbers".
- **Close the loop.** Real performance arrives later, through `content-outcomes-sync`. This stage's
  scores are a prior; that stage's counts are the evidence. When they disagree, the evidence wins.
