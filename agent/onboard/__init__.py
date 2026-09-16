"""Profile onboarding ingestion — URL / file / text → staged ProfileDraft → live profile.

The brain emits a single validated ProfileDraft JSON (Approach B — PRD §2 D1).
Python renders deterministically into profiles/.staging/<slug>/. The operator
reviews per-file diffs and confirms; promote() renames the staging tree atomically.

Security invariants enforced here:
  - slugify() rejects reserved names and validates through _safe_segment (PRD §4a)
  - All staging writes stay inside cfg.profiles_root / ".staging" / slug (no path traversal)
  - Source text is UNTRUSTED INPUT (RULES.md §R5): passed as data to the brain, never executed
  - onboarding_cap_usd checked before any paid call (RULES.md §R2, cfg.onboarding_cap_usd)
  - promote() raises ValueError if profile already exists — never overwrites a live tenant
  - URL ingestion (httpx / Firecrawl REST) lives in gtm_core/ingest.py outside the §R6 boundary
"""

from __future__ import annotations

from .errors import OnboardingExtractError, OnboardingInputError  # noqa: F401
from .extract import (  # noqa: F401
    _REQUIRED_DRAFT_FIELDS,
    _VALID_CONFIDENCE,
    _parse_and_validate_draft,
    _run_brain_query,
    _strip_fence,
    extract,
    extract_product,
)
from .ingest import _ingest_file, _ingest_pdf, ingest  # noqa: F401
from .knowledge import (  # noqa: F401
    _ensure_knowledge_frontmatter,
    _knowledge_topic_relpath,
    _source_label,
    _supplement_from_template,
)
from .render import render  # noqa: F401
from .render_icp import (  # noqa: F401
    _DEFAULT_OPERATOR_CONFIRM,
    _derive_icp_signals,
    _render_buyer_journey_full,
    _render_buyer_journey_md,
    _render_buyer_journey_skeleton,
    _render_competitors_md,
    _render_icp_md,
    _render_pillars_md,
)
from .render_product import (  # noqa: F401
    _render_audience_psych_stub,
    _render_case_studies_stub,
    _render_per_product_md,
    _render_product_md,
    _render_voice_bans_txt,
)
from .render_profile import (  # noqa: F401
    _render_brand_notes_md,
    _render_company_md,
    _render_connectors_block,
    _render_market_scan_config_md,
    _render_profile_md,
    _render_voice_md,
)

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .slug import _MAX_SLUG_LEN, _RESERVED_SLUGS, _STAGING_DIR, slugify  # noqa: F401
from .staging import (  # noqa: F401
    DraftNotStagedError,
    ProfileAlreadyExistsError,
    _staged_root_for_draft_id,
    cancel,
    diff,
    promote,
    stage,
)

__all__ = [
    "slugify",
    "ingest",
    "extract",
    "extract_product",
    "render",
    "stage",
    "diff",
    "promote",
    "cancel",
]
