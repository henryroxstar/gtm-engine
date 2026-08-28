---
name: video-storyboard
description: >-
  Generate operator-reviewable storyboard still(s) for an approved video script before any
  video render spend. Reads the approved script
  (`content/<active>/scripts/<YYYY-MM-DD>-<slug>.md`) and optional `<slug>.shots.json`,
  resolves the same identity anchor as `video-render` via the brand kit (`python -m
  gtm_core.brandkit --profile <active> [--product <slug>]`), runs the monthly-cap precheck
  (`python -m gtm_core.ledger_cli month-total`), then generates ONE hero still for the whole
  video with `generate_image` — never one per shot: every independent still re-invents
  wardrobe, lighting and setting, so N stills render as N unexplained costume changes
  (verified 2026-08-18: five stills, five different jackets in 32 seconds). A second hero
  still is generated only for a deliberate look change, and `gtm_core.storyboard approve`
  refuses more than one distinct image_job_id without an explicit `--allow-anchors N
  --reason`. Always folds the shot's `expression` field into the prompt when present — an
  unstated expression defaults to whatever the identity anchor's source photo happens to hold,
  which is the still-generation half of the same 'prompt text, not luck' lesson wardrobe
  already carries. Writes `content/<active>/video/<script-slug>/storyboard.json` recording
  shot number, local image path, provider job/media id, identity anchor kind, aspect ratio,
  width, and height. Ends its turn with the `⟦GATE:plan⟧` sentinel so the pack's frontier
  pauses for operator approval. On approval, `video-render` reads the approved storyboard and
  uses those stills directly as the image-to-video start frame instead of generating new ones.
  This skill should be used as the `storyboard` node of the creator pack's `short-form-video`
  variant, or when the user says 'storyboard this script', 'show me the video frames before
  render', or 'preview the shot composition'.
metadata:
  version: "0.3.0"
  phase: "6"
  capability_tier: production
---
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`video-storyboard`, tier `production`).
Pack graph node(s) that invoke it: `creator/presenter-video`, `creator/short-form-video`.

See `docs/SKILLS.md` for the full skill roster.
