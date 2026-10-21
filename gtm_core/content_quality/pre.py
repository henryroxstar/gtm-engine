from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import hooks as hk
from ..ledgers import Ledgers
from ..paths import PathConfig, resolve_content_root, resolve_profiles_root
from .guards import _month_budget_remaining, _resolve_ban_file
from .model import _VIDEO_FORMATS
from .sources import (
    _find_item,
    _load_person_hook_matrix,
    _matrix_by_id,
    load_profile_facts,
)


def pre_check(profile: str, item_id: str, content_root: Path | None = None) -> dict[str, Any]:
    """Pre-generation quality check for a planned ContentItem.

    Returns ``{"proceed": bool, "blocking": [...], "warnings": [...], "checks": {...}}``.
    """
    content_root = content_root or resolve_content_root()
    profiles_root = resolve_profiles_root()
    item = _find_item(content_root, profile, item_id)

    blocking: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool | None] = {}

    if item is None:
        blocking.append(f"ContentItem {item_id!r} not found in content/{profile}/plans/")
        return {"proceed": False, "blocking": blocking, "warnings": warnings, "checks": checks}

    facts = load_profile_facts(profiles_root, profile)
    # Resolve the id against the HOOK BANK, not hook-matrix.md. The two files are not two views
    # of one set: hooks.toml is the 1:many feed bank that owns the id namespace, while
    # hook-matrix.md holds 1:1 outreach openers and in some tenants (acme) is an id-less
    # persona x signal grid. Checking the matrix meant every valid feed hook_id warned "not
    # found" on those tenants, so a real typo was indistinguishable from that noise.
    # `load_hooks` still falls back to parsing the matrix when a tenant has no hooks.toml, so
    # id-keyed matrices keep resolving exactly as before.
    bank = hk.load_hooks(profiles_root, profile, content_root=content_root)

    hook_id = item.get("hook_id")
    if hook_id:
        if bank.by_id(hook_id) is not None:
            checks["hook_id_in_matrix"] = True
        elif hook_id.startswith("henry-"):
            person_rows = _load_person_hook_matrix()
            checks["hook_id_in_matrix"] = hook_id in _matrix_by_id(person_rows)
            if not checks["hook_id_in_matrix"]:
                warnings.append(f"hook_id {hook_id!r} not found in person-layer hook matrix")
        else:
            checks["hook_id_in_matrix"] = False
            warnings.append(f"hook_id {hook_id!r} not found in the hook bank")
    else:
        checks["hook_id_in_matrix"] = None
        warnings.append("no hook_id assigned — attribution to the hook bank will be missing")

    pillar = item.get("pillar")
    content_pillars = facts.get("content_pillars") or []
    checks["pillar_valid"] = isinstance(pillar, str) and pillar in content_pillars
    if not checks["pillar_valid"]:
        blocking.append(f"pillar {pillar!r} not in profile content_pillars: {content_pillars!r}")

    platform = item.get("platform")
    playbooks = facts.get("platform_playbooks") or {}
    checks["platform_playbook_present"] = platform in playbooks
    if not checks["platform_playbook_present"]:
        warnings.append(f"no platform playbook found for {platform!r}")

    audience = ((item.get("brief") or {}).get("audience") or "").strip()
    personas_text = facts.get("icp-personas") or ""
    checks["audience_persona_named"] = bool(audience) and bool(
        personas_text and re.search(rf"\b{re.escape(audience)}\b", personas_text, re.IGNORECASE)
    )
    if not checks["audience_persona_named"]:
        warnings.append("brief.audience is not a named persona from icp-personas.md")

    ban_file = _resolve_ban_file(facts)
    checks["voice_bans_present"] = ban_file is not None and ban_file.is_file()
    if not checks["voice_bans_present"]:
        warnings.append("voice-bans.txt not present")

    fmt = item.get("format")
    brand_kit = facts.get("brand_kit") or {}
    disclosure_line = (
        brand_kit.get("disclosure", {}).get("line") if isinstance(brand_kit, dict) else None
    )
    is_video = fmt in _VIDEO_FORMATS
    checks["video_disclosure_configured"] = disclosure_line is not None if is_video else None
    if is_video and not disclosure_line:
        blocking.append(
            "video/reel format requires a configured disclosure.line in merged BRAND.toml"
        )

    budget = facts.get("monthly_tool_budget_usd")
    cfg = PathConfig(
        content_root=content_root,
        profiles_root=profiles_root,
        default_profile=profile,
    )
    ledgers = Ledgers(cfg, profile)
    under_budget, spent = _month_budget_remaining(ledgers, budget)
    checks["budget_ok"] = under_budget
    if not under_budget:
        blocking.append(f"monthly tool budget exceeded: ${spent:.2f} spent vs ${budget} cap")

    proceed = not blocking
    return {
        "proceed": proceed,
        "blocking": blocking,
        "warnings": warnings,
        "checks": checks,
    }
