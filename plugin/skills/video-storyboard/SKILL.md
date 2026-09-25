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
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

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

## How to close this run (every surface)

Report, in this order and in the operator register (the `gtm-operator` output style): Lead with the outcome; what matters about it in their terms; the next decision as a choice they can answer; and what it cost, exactly as the ledger reported it, if anything metered ran.
File paths, commands, module names and raw output go in a final
<details><summary>Details</summary> … </details> block; the main reply must make sense
without it.

Markers: emit a ⟦…⟧ marker (⟦GATE:…⟧, ⟦POST⟧, ⟦FILE:…⟧) only when your system prompt carries
a `Surface:` line that says so. Otherwise show the same content as a quoted block headed
"This is exactly what would go out."

Active profile: the one in your system instructions, or, in the desktop app, the answer to
`uv run python -m gtm_core.active_profile show`.
