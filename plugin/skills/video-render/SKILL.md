---
name: video-render
description: >-
  Render b-roll and screen visuals from shot prompts and start images using video generation
  models. Trigger when the user says "render video shots", "generate b-roll video", "render
  visual beats", or as the render stage of the creator pack.
metadata:
  version: "1.2.0"
  phase: "6"
  capability_tier: production
---
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

Its declared interface is above (`video-render`, tier `production`).
Pack graph node(s) that invoke it: `creator/presenter-video`, `creator/short-form-video`.

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/reference/provider-workflows.md`.

## Interface Contract

- **Target & Output:** Renders approved b-roll, product, environment, and abstract shots for ONE target aspect ratio using Higgsfield (`wan2_7` / `seedance_2_0`). Emits render manifest for `video-finish`.
- **Refusal Boundary:** Strictly refuses speaking presenters (`presenter` role is HeyGen/`video-avatar` only). Still renders `soul_2` identity stills without video animation.
- **Spend & Cap:** Runs monthly-cap check (`gtm_core.ledger_cli month-total`). Logs spend rows to `costs.jsonl`.
- **Prompt Recipes & Seeds:** Prompts assembled per `references/prompt-recipes.md`, recorded verbatim with seed in render manifest.

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
