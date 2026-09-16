---
name: carousel-auto
description: >-
  Automate weekly carousel production from market scan signals to finished on-brand slide
  decks and visuals. Trigger when the user says "auto-carousel", "weekly carousel", "carousel
  from market scan", "what should I carousel this week", or "build a carousel from the scan".
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

## Interface Contract

- **Target & Output:** Automated orchestration from market scan signal to publish-ready LinkedIn carousel (PDF deck, cover art, motion teaser).
- **Inputs:** Reads latest market signals, scores signals for carousel potential, selects arc shape (myth-bust, how-to, case-study, framework).
- **Chains:** Orchestrates `carousel-pdf` (render deck) and optional `carousel-visuals` (cover art, teaser).
- **Gates:** Preserves human approval gates before deck rendering and before any metered generation spend.
