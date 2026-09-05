"""Deterministic local quality gates for content items.

Performs file/knowledge checks before generation spend (``pre_check``), on a written script
before storyboard/render spend (``script_check``), and before Gate 2 publish
(``post_check``). All checks are local file reads — no external I/O, no secrets.

Skills invoke this via CLI because they are markdown and cannot import Python::

    uv run python -m gtm_core.content_quality pre    --profile <p> --item <id>
    uv run python -m gtm_core.content_quality script --profile <p> --item <id>
    uv run python -m gtm_core.content_quality post   --profile <p> --item <id>

Returns JSON::

    {"proceed": true, "blocking": [], "warnings": [], "checks": {...}}
"""

from __future__ import annotations

from .cli import main  # noqa: F401
from .guards import (  # noqa: F401
    _asset_text,
    _load_bans,
    _month_budget_remaining,
    _resolve_ban_file,
    _run_linter,
    _safe_to_share,
    _voice_bans_violated,
)

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .model import (  # noqa: F401
    _BLOCK,
    _PLATFORM_PLAYBOOKS,
    _RATIO_SLUG_TO_COLON,
    _VIDEO_FORMATS,
    _WARN,
)
from .post import (  # noqa: F401
    _find_video_manifests,
    _text_post_check,
    _video_post_check,
    post_check,
)
from .pre import pre_check  # noqa: F401
from .register import (  # noqa: F401
    _CONNECTIVES,
    _MIN_CONNECTIVE_SHARE,
    _MIN_SENTENCE_LEN_STDEV,
    _MIN_SENTENCES_FOR_REGISTER,
    _NATURAL_REPEAT_OPENERS,
    _SPOKEN_RE,
    _sentences,
    extract_spoken,
    register_check,
    register_findings,
)
from .script import (  # noqa: F401
    _CLAIMS_RE,
    _SCRIPT_REQUIRED_FRONT_KEYS,
    _find_script,
    _parse_front_block,
    script_check,
)
from .sources import (  # noqa: F401
    _find_item,
    _load_json,
    _load_person_hook_matrix,
    _load_text,
    _matrix_by_id,
    _parse_profile_md,
    _person_hook_matrix_path,
    _product_slugs,
    _repo_root,
    hook_id_to_product,
    load_hook_matrix,
    load_profile_facts,
)

__all__ = [
    "load_profile_facts",
    "load_hook_matrix",
    "hook_id_to_product",
    "pre_check",
    "extract_spoken",
    "register_findings",
    "register_check",
    "script_check",
    "post_check",
    "main",
]
