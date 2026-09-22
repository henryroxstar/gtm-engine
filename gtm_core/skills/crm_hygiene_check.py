"""Canonical manifest for the `crm-hygiene-check` skill.

Prompt body: plugin/skills/crm-hygiene-check/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="crm-hygiene-check",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3D",
    description=(
        'Perform a read-only audit of CRM opportunities to flag missing MEDDPICC fields, stale close dates, inactive stages, and output a clean rep nudge list. Trigger when the user says "check CRM hygiene", "audit pipeline cleanliness", "find stale deals in CRM", or "CRM hygiene check".'
    ),
)
