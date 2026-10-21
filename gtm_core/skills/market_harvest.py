"""Canonical manifest for the `market-harvest` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/market-harvest/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="market-harvest",
    capability_tier=Tier.CORE,
    version="0.8.1",
    phase="4",
    description=(
        'Continuously harvest competitor, ecosystem, customer, and regulatory market signals into structured knowledge topics. Trigger when the user says "harvest market signals", "run market harvest", "collect competitor intel", or "update ecosystem tracking".'
    ),
    fallback_note=(
        "Lanes degrade independently — a harvest is never all-or-nothing. If the Firecrawl "
        "connector is absent, skip the web lanes and record each as `failed` (never `empty`) so "
        "the watermark does not advance and the brief reports 'pull failed' rather than 'nothing "
        "new'; the `gh` CLI still covers the standards lane, and the Syften lane still runs over "
        "`mcp__syften__*` or the in-repo REST wrapper. Before recording a lane `failed`, tell the "
        "two failure modes apart: a **403 because we look like a robot** (EDGAR and other "
        "User-Agent-gated hosts) is recoverable for free through the **browser MCP tools**, which "
        "present a real user agent — see Step 4 for the resolve-then-window method that reads a "
        "10-K without pulling it into context; a block because we have **no key** (a Pro-gated API) "
        "is not recoverable and stays `failed`. If `gh` is unavailable, record "
        "`standards_watch` failed and continue. Always finish the lanes that CAN run, then state "
        "plainly which lanes did not and why — a partial harvest reported honestly is useful; a "
        "partial harvest reported as complete silently overstates the brief's coverage."
    ),
)
