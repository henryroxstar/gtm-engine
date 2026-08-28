# gtm_core/skills/identity_kit.py
"""Canonical manifest for the `identity-kit` skill.

Prompt body: plugin/skills/identity-kit/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Onboarding-family, like `profile-onboard` (Tier.CORE, phase="onboard"): the family
allowed to write under profiles/<active>/. Pipeline skills (video-render, video-score,
...) stay read-only there. Unlike profile-onboard (which emits a ProfileDraft that
Python then writes), identity-kit writes directly — but ONLY through
`python -m gtm_core.brandkit --set identity.<key>` (gtm_core/brandkit.py), which
keeps _safe_segment in the loop and verifies every write before it lands. The skill
itself never Edits/Writes the BRAND.toml file.

phase="8" rather than "onboard":
this runs any time after initial onboarding, on request, not once at profile creation.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="identity-kit",
    capability_tier=Tier.CORE,
    version="0.3.0",
    phase="8",
    description="Audits, creates, and writes back the active profile's (or product's) render-identity handles in BRAND.toml — soul_id (a trained likeness), reference_element_ids (instant multi-subject references), voice_id (a cloned voice) with an optional voice_engine (which TTS engine renders it) and voice_grade (instant vs professional \u2014 HOW it was cloned, orthogonal to the engine; only a professional clone may carry a shipped VO, and an unrecorded grade fails closed), heygen_avatar_id (a trained digital twin), and restyle_preset_id (a brand restyle look). Zero-spend audit by default: checks each handle's liveness against the provider and reports empty/stale/failed-training. Creation is gated behind explicit consent for anyone but the operator's own likeness/voice — refuses a third-party Soul or voice clone until a dated consent_note is recorded (EU AI Act Art. 50). Writes ONLY through the gtm_core.brandkit CLI, never by editing the TOML directly, so every write is verified and dated. This skill should be used when the user says 'set up my identity kit', 'audit my identity handles', 'train my Soul', 'create a reference element', 'clone my voice', 'change my voice engine', or 'create the brand restyle preset'.",
)
