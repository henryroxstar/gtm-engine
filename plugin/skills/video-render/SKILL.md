---
name: video-render
description: >-
  Render approved b-roll, product, environment and abstract shots for ONE target aspect ratio
  using Higgsfield, keeping the active company's look via the brand kit (`python -m
  gtm_core.brandkit`). **This skill does NOT render presenters.** A talking head of a real
  person is refused in code: `gtm_core.render_engines` serves the `presenter` role from HeyGen
  and never from this skill's engines, `shots_lint` fails any speaking presenter shot before
  spend, and `render_manifest` refuses to record one. The reason is architectural, not a
  prompt problem: `soul_id` is accepted only by IMAGE models, so a trained Soul cannot reach
  any video model and identity is re-derived from a JPEG every frame, and `audio_references`
  is a reference input, not a lip-sync switch. For a speaking presenter, route to real footage
  (the primary lane, no Article 50 disclosure duty) or the faceless format (b-roll plus held
  soul_2 stills, VO and burned captions) — see `video-router` Step 0.5. Still in scope and
  unchanged: every shot with no real person in frame, plus soul_2 identity STILLS (as stills,
  never animated into video). Every generation prompt is assembled per
  references/prompt-recipes.md and recorded verbatim with its seed in the render manifest,
  which refuses a synthetic asset without one — and now also refuses one it cannot tie to a
  costs.jsonl row — an unmetered render is spend the monthly cap never sees.
metadata:
  version: "1.1.0"
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
