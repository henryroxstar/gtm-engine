# gtm_core/skills/video_clip.py
"""Canonical manifest for the `video-clip` skill.

The clipping lane (Phase 10), repointed to Reap Video Studio in Phase D.
Prompt body: plugin/skills/video-clip/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Pack-layer only: this is a new skill on the unmodified engine (like every skill in this
repo), referenced by the new `repurpose-clips.toml` pack variant. No engine change.

Repointed from Higgsfield's personal_clipper to Reap's create_clips (Phase D): a REGRESSION
in cost visibility, not an improvement — Reap has no balance/get_cost tool anywhere in its
24-tool surface (personal_clipper at least had `balance` before/after). The ledger is now
the ONLY meter, which is why the cost row must be written the instant a job is confirmed
submitted, not after it completes — a mid-run failure must still leave an audit trail. See
`gtm_core.ledger_cli month-units` (Phase D, F10) for the unit-count cap this lane checks in
place of a dollar cap. Caption styling is a neutral Reap preset (operator decision,
2026-08-15) — Reap's 55 styles have no brand-matching mechanism and this lane does not spend
one of the account's 3 custom-font slots on it.
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
