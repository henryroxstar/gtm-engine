# gtm_core/skills/video_clip.py
"""Canonical manifest for the `video-clip` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-clip/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-clip",
    capability_tier=Tier.PRODUCTION,
    version="0.4.0",
    phase="10",
    description=(
        "Repurpose the operator's own long-form video into 1-20 short-form clips using Reap "
        "Video Studio's create_clips — the highest-volume, lowest-marginal-cost lane in the "
        "creator pack (a single job returns many clips instead of one render call per asset). "
        "Takes EITHER a local file the operator already has (preferred \u2014 uploaded via "
        "gtm_core.reap_upload's host-pinned, content-root-confined PUT, so a phone recording "
        "never has to be posted to YouTube first) OR a public source URL, whichever the "
        "operator supplied at the plan gate; warns before spending if a URL does not look "
        "like the tenant's own channel. create_clips has a "
        "two-step confirm flow (a plannedSettings response with no project id is a "
        "confirmation request, never a completed job) and NO cost/balance tool anywhere in "
        "Reap's surface, so the ledger is the only meter: a cost row is written the instant "
        "a job is confirmed submitted, self-tracked by clip count rather than a verified "
        "spend figure, checked against a unit-count cap (python -m gtm_core.ledger_cli "
        "month-units --tool reap --unit media_credits) instead of a dollar cap. Caption "
        "styling prefers the brand kit's declared captions.preset and falls back to a neutral "
        "Reap preset (stated as such, never implied to be brand-matched); returned captions "
        "are judged against Reap's published spec \u2014 centre 60% of frame, ~48-62px at 1080 "
        "width, 28-36 chars/line, max 2 lines. Saves every clip "
        "under content/<active>/video/clips/ with a manifest recording each clip's Reap "
        "clip/project id for the scoring stage. This skill should be used when the user says "
        "'repurpose this video', 'clip my long-form video', 'make shorts from this video', "
        "or as the clip stage of the creator pack's repurpose-clips variant."
    ),
)
