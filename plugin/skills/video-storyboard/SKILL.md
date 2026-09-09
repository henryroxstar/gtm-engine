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
  wardrobe, lighting and setting, so N stills render as N unexplained costume changes. A
  second hero still is generated only for a deliberate look change, and `gtm_core.storyboard
  approve` refuses more than one distinct image_job_id without an explicit `--allow-anchors N
  --reason` — a delta-edit CHAIN is exempt without the flag, because each derivative records
  `derived_from` and approval walks that lineage to the root, so one hero plus N
  frame-compatible edits stays one anchor. Alternatives (a wider angle, another outfit, the
  cover) are generated as delta edits of the approved hero via `generate_image`'s ordered
  `reference_images`, one concern per pass, identity always at position 1. Always folds the
  shot's `expression` field into the prompt when present — an unstated expression defaults to
  whatever the identity anchor's source photo happens to hold, which is the still-generation
  half of the same 'prompt text, not luck' lesson wardrobe already carries. READS THE PIXELS
  of every shot bound to an existing REAL asset (`vision`'s `extract_text`, then
  `gtm_core.spoiler_check`) and refuses one whose visible text names a product the script has
  not introduced by that timecode: a reused console capture at 1:48 showed a DID reading
  `...:fabric-gateway-...`, naming the product 75 seconds before the script says it at 3:02,
  and every gate passed it because the asset was real, current and on-brand — nothing asked
  what the picture SAID. The term vocabulary is closed to the profile's own products, so a
  screenshot can never introduce, widen or steer the check, and an unreadable asset is
  reported NOT CHECKED rather than clean (`extract_text` never raises; it returns
  `[vision-error] ...`). Writes `content/<active>/video/<script-slug>/storyboard.json`
  recording shot number, local image path, provider job/media id, identity anchor kind, aspect
  ratio, width, and height. Ends its turn with the `⟦GATE:plan⟧` sentinel so the pack's
  frontier pauses for operator approval. On approval, `video-render` reads the approved
  storyboard and uses those stills directly as the image-to-video start frame instead of
  generating new ones. This skill should be used as the `storyboard` node of the creator
  pack's `short-form-video` variant, or when the user says 'storyboard this script', 'show me
  the video frames before render', or 'preview the shot composition'.
metadata:
  version: "0.8.0"
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
