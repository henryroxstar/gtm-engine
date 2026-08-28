# gtm_core/skills/demo_capture.py
"""Canonical manifest for the `demo-capture` skill.

The demo-clips lane (Phase C).
Prompt body: plugin/skills/demo-capture/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Pack-layer only: a new skill on the unmodified engine, referenced by the new
`demo-clips.toml` pack variant. No engine change.

Interactive-first by construction, same shape as `video-restyle`: the source is real
product footage the operator already captured (a screen recording), not a signal to
discover, so this variant carries no radar root and starts at the plan gate. Unlike
`video-restyle`'s browser-side `media_upload_widget`, uploading here is a Python module
(`gtm_core.reap_upload`) performing a real network PUT of a specific local file — a more
deliberate, consequential action than approving a browser widget, which is why this skill
gets its OWN gate (separate from the plan gate) rather than folding into it: the plan gate
approves editorial intent, this gate approves the exact bytes about to leave the machine.
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
