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
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`video-render`, tier `production`).
Pack graph node(s) that invoke it: `creator/presenter-video`, `creator/short-form-video`.

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/reference/provider-workflows.md`.

## Interface Contract

- **Target & Output:** Renders approved b-roll, product, environment, and abstract shots for ONE target aspect ratio using Higgsfield (`wan2_7` / `seedance_2_0`). Emits render manifest for `video-finish`.
- **Refusal Boundary:** Strictly refuses speaking presenters (`presenter` role is HeyGen/`video-avatar` only). Still renders `soul_2` identity stills without video animation.
- **Spend & Cap:** Runs monthly-cap check (`gtm_core.ledger_cli month-total`). Logs spend rows to `costs.jsonl`.
- **Prompt Recipes & Seeds:** Prompts assembled per `references/prompt-recipes.md`, recorded verbatim with seed in render manifest.
