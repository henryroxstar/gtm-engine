"""Merge-field hygiene for mail-merge outreach — the gate the copy linter can't see.

The outreach copy linter (``tests/linter/outreach_pack_linter.py``) reads a *rendered*
email and judges the prose. It is blind to the thing that actually breaks a merge send:
the **field values** substituted into ``{{First Name}}`` / ``{{Company}}``. A template
that lints perfectly still ships "Hi 🍦," or "agents at Canopy GBS | SAP Consulting |
AI & Automation | move..." if the CSV carries a scraped LinkedIn headline in ``company``.

Found the hard way on 2026-07-28: 334 rows in ``ready-to-load.csv`` passed the copy gate
with **0 errors across 1,002 renders**, while 9 rows would have sent a visibly broken
email and 70 more read as an obvious mail-merge ("Once agents at MediPath, Inc. move...").

Two responsibilities, deliberately separate:

* :func:`clean_first_name` / :func:`clean_company` — **repair** the mechanical defect
  classes that have a single unambiguous fix (a title prefix, a pipe-delimited headline,
  a trailing legal suffix). Applied at ingestion so the defect never reaches the load file.
* :func:`check_row` — **judge** what is left. A ``block`` finding means the row must not
  reach ``ready-to-load.csv``; guessing a human's name is not a repair this module will
  make up. ``warn`` is advisory and never gates.

Repair is conservative by construction: every helper returns the ORIGINAL value when its
transform would produce something empty, absurdly short, or less informative than what it
started with. A wrong "fix" that silently renames a prospect is worse than a flagged row.

Stdlib-only, tenant-agnostic, no I/O — the same reasons ``slugify.py`` looks the way it does.
"""

from __future__ import annotations

from .api import blocks, check_row, clean_row  # noqa: F401
from .cli import main  # noqa: F401
from .company import (  # noqa: F401
    _COMPANY_NON_NAMES,
    _LEGAL_SUFFIX_RE,
    _PARENTHETICAL_RE,
    _SENTENCE_PUNCT_RE,
    _TRADEMARK_RE,
    _TRANSACTION_ENTITY_RE,
    SEGMENTS,
    clean_company,
    clean_segment,
    ends_in_sibilant,
    starts_with_article,
)
from .email import (  # noqa: F401
    _EMAIL_RE,
    _FREEMAIL,
    _JUNK_DOMAIN_TOKENS,
    _ROLE_LOCAL_RE,
    _URLISH_RE,
    bare_host,
)

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .model import Finding  # noqa: F401
from .names import (  # noqa: F401
    _CREDENTIAL_RE,
    _NAME_CHARS_RE,
    _NON_NAMES,
    _TITLE_PREFIX_RE,
    _TITLES,
    _has_letters,
    _strip_symbols,
    _titlecase,
    clean_first_name,
    clean_last_name,
)
from .signal_clean import (  # noqa: F401
    _INTENT_LABEL_RE,
    _MONTH,
    _NO_SIGNAL_RE,
    _SEGMENT_JOINS,
    _TRAILING_DATE_RE,
    _TRAILING_SOURCE_RE,
    EM_DASH,
    SIGNAL_MAX_CHARS,
    SIGNAL_MIN_CHARS,
    SIGNAL_SUBSTANCE_CHARS,
    _drop_trailing_provenance,
    _split_top_level,
    signal_clause,
)
from .signal_dates import (  # noqa: F401
    _ISO_DATE_RE,
    _MONTH_NAMES,
    _MONTH_YEAR_RE,
    SIGNAL_MAX_AGE_DAYS,
    row_signal_freshness,
    signal_is_fresh,
    signal_latest_date,
)
from .signal_terms import (  # noqa: F401
    _EMBEDDED_AI_RE,
    _EVENT_RE,
    _TOPIC_RE,
    SIGNAL_EVENT_VERBS,
    SIGNAL_TOPIC_TERMS,
    _term_re,
    signal_is_event,
    signal_on_topic,
    signal_stray_digits,
)
from .titles import _TITLE_BIO_RE, _TITLE_PIPE_RE, clean_title  # noqa: F401

__all__ = [
    "Finding",
    "signal_latest_date",
    "signal_is_fresh",
    "row_signal_freshness",
    "clean_first_name",
    "clean_last_name",
    "clean_company",
    "clean_title",
    "clean_segment",
    "SEGMENTS",
    "check_row",
    "clean_row",
    "blocks",
    "ends_in_sibilant",
    "starts_with_article",
    "signal_clause",
    "signal_on_topic",
    "signal_is_event",
    "signal_stray_digits",
    "SIGNAL_TOPIC_TERMS",
    "SIGNAL_EVENT_VERBS",
]
