# gtm_core/capability_registry.py
"""Registry of known product capability slugs.

Each slug appears in a plugin skill's `requires_capability` field.
Onboarding uses this set to validate capabilities extracted from source text.
Unknown slugs are rejected at extract() time — fail-closed, not silently accepted.

To add a capability: add the slug here, then declare it in the skill's SKILL.md.
"""

from __future__ import annotations

KNOWN_CAPABILITIES: frozenset[str] = frozenset(
    {
        # Content OS (content product)
        "content-creation",
        "content-radar",
        "content-studio",
        # Sales GTM (the billing service product)
        "prospect",
        "outreach",
        "deck-research",
        "solution-discovery",
        "pre-sales",
        # SA chain (gateway-category / generic product)
        "technical-discovery",
        "solution-architecture",
        "gateway",
    }
)
