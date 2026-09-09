"""The GTMSkill manifest — the canonical, runtime-agnostic description of a skill.

A manifest is **data, not an executor**. Every runtime still executes a skill the
same way it does today: by handing the prompt (the generated SKILL.md / its body)
to the Claude Agent SDK, which interprets it and calls MCP tools. The manifest
exists so that metadata (tier, schemas, requires_capability) is structured and
shared, and so SKILL.md can be generated rather than hand-maintained.

Pure Python, zero Agent-SDK imports (gtm_core is the foundation, never a consumer).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tiers import Tier


@dataclass(frozen=True)
class GTMSkill:
    """Canonical manifest for one skill.

    Fields that drive SKILL.md frontmatter: name, description, version, phase,
    capability_tier, requires_capability. The rest are consumed by code runtimes
    (Phase B tier resolution, Phase D/E request/response validation) and are not
    yet emitted into frontmatter — keeping the generated file minimal.
    """

    name: str
    description: str  # normalised single-line prose (folded into frontmatter)
    version: str
    capability_tier: Tier

    requires_capability: tuple[str, ...] = ()  # product capabilities this skill needs
    phase: str | None = None  # historical build phase (informational)
    license: str | None = None  # per-skill license, if the SKILL.md declared one

    # References to the canonical contracts in schemas/<name>.schema.json — NOT a new
    # schema system. Used by code runtimes to validate I/O at the API boundary.
    input_schema: str | None = None
    output_schema: str | None = None

    # Informational; the real tool surface is resolved by the runtime / MCP config.
    tools: tuple[str, ...] = ()

    # The fallback INSTRUCTION emitted into the prompt for SDK runtimes when paid
    # connectors are absent (one spec, two emitters — see the program plan, fix #2).
    # A pure-Python fallback() hook for non-SDK callers arrives with the backend (Phase D).
    fallback_note: str | None = None
