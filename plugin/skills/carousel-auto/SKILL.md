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
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

Its declared interface is above (`carousel-auto`, tier `production`).

See `docs/SKILLS.md` for the full skill roster.

## Interface Contract

- **Target & Output:** Automated orchestration from market scan signal to publish-ready LinkedIn carousel (PDF deck, cover art, motion teaser).
- **Inputs:** Reads latest market signals, scores signals for carousel potential, selects arc shape (myth-bust, how-to, case-study, framework).
- **Chains:** Orchestrates `carousel-pdf` (render deck) and optional `carousel-visuals` (cover art, teaser).
- **Gates:** Preserves human approval gates before deck rendering and before any metered generation spend.

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
