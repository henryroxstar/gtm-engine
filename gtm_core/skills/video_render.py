"""Canonical manifest for the `video-render` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-render/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-render",
    capability_tier=Tier.PRODUCTION,
    version="1.1.0",
    phase="6",
    description=(
        "Render approved b-roll, product, environment and abstract shots for ONE target aspect ratio "
        "using Higgsfield, keeping the active company's look via the brand kit (`python -m "
        "gtm_core.brandkit`). **This skill does NOT render presenters.** A talking head of a real "
        "person is refused in code: `gtm_core.render_engines` serves the `presenter` role from HeyGen "
        "and never from this skill's engines, `shots_lint` fails any speaking presenter shot before "
        "spend, and `render_manifest` refuses to record one. The reason is architectural, not a prompt "
        "problem: `soul_id` is accepted only by IMAGE models, so a trained Soul cannot reach any video "
        "model and identity is re-derived from a JPEG every frame, and `audio_references` is a "
        "reference input, not a lip-sync switch. For a speaking presenter, route to real footage (the "
        "primary lane, no Article 50 disclosure duty) or the faceless format (b-roll plus held soul_2 "
        "stills, VO and burned captions) — see `video-router` Step 0.5. Still in scope and unchanged: "
        "every shot with no real person in frame, plus soul_2 identity STILLS (as stills, never "
        "animated into video). Every generation prompt is assembled per references/prompt-recipes.md "
        "and recorded verbatim with its seed in the render manifest, which refuses a synthetic asset "
        "without one — and now also refuses one it cannot tie to a costs.jsonl row — an unmetered "
        "render is spend the monthly cap never sees."
    ),
)
