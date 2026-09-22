"""Search-term vocabulary loading and the 6-source query generator for
:mod:`gtm_core.web_sweep` (PSK-019).

Split out of ``web_sweep.py`` to keep that module under the §R10 500-line ratchet — this is
a coherent, single-purpose block (tenant vocabulary + query string assembly) with no
dependency on the hit-normalization side of the sweep.

Only :func:`generate_queries` is public; it is re-exported from ``gtm_core.web_sweep`` so
existing callers and tests are unaffected.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any

from gtm_core.merge_hygiene import clean_company
from gtm_core.paths import resolve_knowledge_file, resolve_profiles_root

_DEFAULT_HIRING_SITE = "linkedin.com/jobs"

#: Neutral, tenant-agnostic search-term groups. A tenant overrides these via its own
#: `web-sweep.toml` knowledge file (see profiles/_template/knowledge/web-sweep.toml),
#: resolved product-first/profile-fallback through `gtm_core.paths.resolve_knowledge_file`.
_NEUTRAL_QUERY_GROUPS: dict[str, str] = {
    "newsroom": "(launch OR announces OR partnership OR expansion)",
    "hiring": '("head of" OR director OR "vice president")',
    "eng": '("engineering blog" OR github OR "open source")',
    "regulatory": '(regulator OR compliance OR standards OR "earnings call")',
    "funding": '(raised OR "Series")',
    "incident": "(breach OR audit OR incident OR outage)",
}

_QUERY_LABELS: tuple[tuple[str, str], ...] = (
    ("newsroom", "Newsroom / PR"),
    ("hiring", "Hiring"),
    ("eng", "Engineering Signal"),
    ("regulatory", "Regulatory & Standards"),
    ("funding", "Funding"),
    ("incident", "Pressure & Incidents"),
)


def _validate_query_value(key: str, value: Any) -> str:
    """A web-sweep.toml value is concatenated into a query string, never `str.format`-ed —
    refuse anything that is not a plain, single-line string."""
    if not isinstance(value, str):
        raise ValueError(
            f"web-sweep.toml key {key!r} must be a string (got {type(value).__name__})"
        )
    if "\n" in value or "{" in value or "}" in value:
        raise ValueError(
            f"web-sweep.toml key {key!r} must not contain a newline or '{{'/'}}' characters"
        )
    return value


def _load_query_vocab(
    profiles_root: Path, profile: str, product: str | None
) -> tuple[dict[str, str], str]:
    """Resolve this profile's search-term vocabulary, or the neutral in-code defaults.

    Prints one stderr note when the knowledge file is absent (defaults used) or when the file
    carries an unrecognized key (ignored). Raises ValueError on a malformed value.
    """
    groups = dict(_NEUTRAL_QUERY_GROUPS)
    hiring_site = _DEFAULT_HIRING_SITE
    path = resolve_knowledge_file(profiles_root, profile, "web-sweep.toml", product=product)
    if not path.is_file():
        print(
            f"Note: no web-sweep.toml for profile {profile!r}; using neutral default query "
            "vocabulary",
            file=sys.stderr,
        )
        return groups, hiring_site

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    table = data.get("queries", {})
    if not isinstance(table, dict):
        raise ValueError(f"{path}: [queries] must be a table")
    for key, value in table.items():
        if key == "hiring_site":
            hiring_site = _validate_query_value(key, value)
        elif key in groups:
            groups[key] = _validate_query_value(key, value)
        else:
            print(f"Note: unknown web-sweep.toml key {key!r} ignored", file=sys.stderr)
    return groups, hiring_site


def generate_queries(
    company: str,
    segment: str = "startup",
    domain: str | None = None,
    profiles_root: Path | None = None,
    profile: str | None = None,
    product: str | None = None,
) -> list[dict[str, str]]:
    """Generate the fixed 6-source signal hunt search queries.

    With no `profile`, uses the neutral, tenant-agnostic vocabulary in-code. Pass `profile`
    (and optionally `product`) to load a tenant's own vocabulary from `web-sweep.toml`.
    """
    cleaned = clean_company(company)
    if profile:
        groups, hiring_site = _load_query_vocab(
            profiles_root or resolve_profiles_root(), profile, product
        )
    else:
        groups, hiring_site = dict(_NEUTRAL_QUERY_GROUPS), _DEFAULT_HIRING_SITE

    queries: list[dict[str, str]] = []
    for q_type, label in _QUERY_LABELS:
        group = groups[q_type]
        if q_type == "eng" and domain:
            q_str = f'"{cleaned}" OR site:{domain} {group}'
        elif q_type == "hiring":
            q_str = f'"{cleaned}" {group} site:{hiring_site}'
        else:
            q_str = f'"{cleaned}" {group}'
        queries.append({"type": q_type, "label": label, "query": q_str})

    return queries
