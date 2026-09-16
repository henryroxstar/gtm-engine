---
name: video-storyboard
description: >-
  Generate operator-reviewable storyboard hero stills and reference frame compositions before
  video render spend. Trigger when the user says "storyboard this script", "show me video
  frames before render", "preview shot composition", or as the storyboard stage of creator
  pack.
metadata:
  version: "0.9.0"
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
Docs it draws on that ship in this distribution: `docs/reference/provider-workflows.md`.

## Interface Contract

- **Target & Output:** Generates operator-reviewable storyboard still(s) for an approved video script before video spend. Writes `content/<active>/video/<script-slug>/storyboard.json` and emits `⟦GATE:plan⟧`.
- **Inputs:** Reads approved script (`content/<active>/scripts/<YYYY-MM-DD>-<slug>.md`) and optional `<slug>.shots.json`. Brand kit identity anchors (`uv run python -m gtm_core.brandkit`).
- **Hero Anchor Discipline:** Generates 1 hero still for the whole video with `generate_image`. Delta-edit chains via ordered `reference_images` (identity at position 1). Folds shot `expression` into prompts.
- **Spoiler Check:** Reads pixels of existing real assets via `vision`'s `extract_text` + `gtm_core.spoiler_check` to prevent premature product reveals before script timecodes.
- **Handoff:** Approved stills are read by `video-render` as image-to-video start frames.
