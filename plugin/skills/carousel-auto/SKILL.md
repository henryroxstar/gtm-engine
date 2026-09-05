---
name: carousel-auto
description: >-
  Automate the weekly carousel pipeline from market-scan signals to publish-ready package.
  This skill should be used when the user says "auto-carousel", "run my carousel workflow",
  "weekly carousel", "carousel from market scan", "carousel from this week's scan", "generate
  this week's carousel", "automate my carousel", "what should I carousel this week", "build a
  carousel from the scan", or "turn this week's signal into a carousel". Reads the latest
  market-signals file, scores signals for carousel potential, picks the strongest arc shape
  and theme, chains through carousel-pdf to render the deck, then optionally chains to
  carousel-visuals for cover art and motion teaser — all in one guided run.
license: MIT
metadata:
  version: "0.6.0"
  phase: "4C"
  capability_tier: production
---
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

Its declared interface is above (`carousel-auto`, tier `production`).

See `docs/SKILLS.md` for the full skill roster.
