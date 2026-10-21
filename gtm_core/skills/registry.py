"""Single enumeration point for canonical skill manifests.

Every runtime (plugin codegen, VPS agent, backend API, MCP server) discovers
skills here. Manifests are auto-discovered: any module under gtm_core/skills/
that defines a module-level `SKILL` is included, sorted by module name for
deterministic ordering. Migrating a skill = dropping in its `<name>.py` manifest;
no edit here. Skills not yet migrated remain plain SKILL.md files, untouched by
codegen (the migration is incremental).
"""

from __future__ import annotations

import importlib
import pkgutil

from .base import GTMSkill

_SKIP = {"base", "codegen", "registry"}


def all_skills() -> list[GTMSkill]:
    """Return every migrated skill manifest, ordered by module name."""
    import gtm_core.skills as pkg

    out: list[GTMSkill] = []
    for mod_info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if mod_info.name in _SKIP:
            continue
        module = importlib.import_module(f"gtm_core.skills.{mod_info.name}")
        skill = getattr(module, "SKILL", None)
        if isinstance(skill, GTMSkill):
            out.append(skill)
    return out
