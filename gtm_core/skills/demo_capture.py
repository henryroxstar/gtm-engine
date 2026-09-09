# gtm_core/skills/demo_capture.py
"""Canonical manifest for the `demo-capture` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/demo-capture/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="demo-capture",
    capability_tier=Tier.PRODUCTION,
    version="0.2.0",
    phase="C",
    description=(
        "Turn LOCAL footage the operator already has \u2014 a phone recording of themselves, a "
        "camera file, a webinar export, or a product screen recording \u2014 into finished "
        "short-form clips via Reap. This is the repo's local-file ingest lane: the file is PUT "
        "through `gtm_core.reap_upload` (host-pinned, content-root-confined, signature-freshness "
        "checked) and clipped by `create_clips`, so nothing has to be uploaded to YouTube or "
        "Vimeo first. Use it when the operator says 'I recorded myself', 'here's my phone video', "
        "'clip this file', 'I have footage on my laptop', 'turn this recording into shorts', "
        "'make a demo clip', or 'clip this screen recording'. Real footage carries NO EU AI Act "
        "Article 50 disclosure duty because nothing is synthesised, and it clears the likeness "
        "bar by construction \u2014 which is why this is the preferred lane for anything showing "
        "the operator's face. Reads the clip/caption plan from the operator gate; never "
        "publishes."
    ),
)
