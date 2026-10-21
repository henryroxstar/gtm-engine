"""Stdlib-only JSON-Schema subset validator — the test tree's import path.

The implementation moved to :mod:`gtm_core.minischema` on 2026-09-03 so runtime code
(``gtm_core.shots_lint``) can validate against ``schemas/`` too — a schema only CI can reach is
how ``shots.schema.json`` came to be dead weight. This module stays as the import path the ~10
existing test importers already use; it is a re-export, never a second copy.
"""

from __future__ import annotations

from gtm_core.minischema import is_valid, validate  # noqa: F401

__all__ = ["validate", "is_valid"]
