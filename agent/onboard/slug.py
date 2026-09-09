from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ── Slug constants ────────────────────────────────────────────────────────────

_RESERVED_SLUGS: frozenset[str] = frozenset({".staging", "_system"})
_MAX_SLUG_LEN: int = 40
_STAGING_DIR: str = ".staging"


def slugify(value: str) -> str:
    """Normalise a company name to a safe profile slug (PRD §4a).

    Steps:
    1. Lowercase
    2. Replace runs of non-alphanumeric chars with a single '-'
    3. Strip leading/trailing dashes
    4. Truncate at the last '-' boundary at or before 40 chars
    5. Reject empty strings and reserved names
    6. Pass through _safe_segment (rejects path traversal chars)
    """
    from gtm_core.paths import _safe_segment

    # Check reserved names against the stripped original before normalising
    stripped = value.strip()
    if stripped in _RESERVED_SLUGS:
        raise ValueError(f"slug {stripped!r} is reserved and cannot be used as a profile name")

    slug = stripped.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    slug = re.sub(r"-+", "-", slug)

    if len(slug) > _MAX_SLUG_LEN:
        truncated = slug[:_MAX_SLUG_LEN]
        last_dash = truncated.rfind("-")
        slug = truncated[:last_dash] if last_dash > 0 else truncated
        slug = slug.rstrip("-")

    if not slug:
        raise ValueError(f"slugify produced empty string from {value!r}")
    if slug in _RESERVED_SLUGS:
        raise ValueError(f"slug {slug!r} is reserved and cannot be used as a profile name")

    return _safe_segment(slug, "profile slug")
