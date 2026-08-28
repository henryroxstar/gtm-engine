---
name: carousel-visuals
description: >-
  Generate AI visuals for the active company's LinkedIn and Instagram carousels using
  Higgsfield — cinematic 4:5 cover art for the carousel-pdf hook card, per-slide background
  images for full image carousels, a 9:16 motion teaser video animated from the hook card,
  4:5/1:1 per-card images for an Instagram feed or X multi-image carousel (7–10 cards; ≤4 for
  X), and a full-text-card mode (V5) that renders every card's copy directly in the image as a
  complete Slidev/deck-renderer bypass. `get_cost` preflight before every call; monthly-cap
  precheck + hard-stops at PROFILE budget cap; free fallback is text-only carousel. Higgsfield
  connector is optional. This skill should be used when the user says "add visuals to my
  carousel", "generate cover art for the carousel", "make an image carousel", "make an
  Instagram carousel", "make an X carousel", "create a motion teaser", "animate the hook
  card", "make it visual", "add images to [carousel topic]", or "switch to Higgsfield, not
  Slidev". Pairs with carousel-pdf (V1–V4), or replaces its render step entirely (V5).
metadata:
  version: "0.6.0"
  phase: "3B"
  capability_tier: production
---
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`carousel-visuals`, tier `production`).

See `docs/SKILLS.md` for the full skill roster.
