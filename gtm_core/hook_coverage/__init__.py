"""Is the message axis actually varied? — measurement, not opinion.

A campaign's copy is written against a *seat* (three of them: security / exec /
technical) while its messaging asset, ``hook-matrix.md``, is written against a
*persona*, crossed with a "why now" signal — a grid an order of magnitude wider than the
seat axis (run ``--profile <p>`` for the live count). The narrower axis wins silently, and the wider one is
never consulted, because ``email-sequence`` *tells* the model to "pick the hook from
the matrix at the persona x signal intersection" and **nothing checks that it did**.

The result, measured across the four staged specs of ``agent-gateway-cross-org``: 397
recipients, one argument. The campaign manifest's own ``[experiment]`` block had
already written that down ("Only one pitch was written") and nothing changed — which
is the whole reason this module exists. An instruction with no check is a suggestion.

This module is the *measurement* half (phase H0). It changes no behaviour and is wired
into no gate; it only makes four questions answerable:

* :func:`parse_matrix` — what cells does the tenant's matrix actually offer?
* :func:`resolve_declared_cell` — which cell does a spec implement, derived from its
  declared ``angle:`` where it has one and read off a legacy ``hook_cell:`` where it does
  not? (:func:`declared_cell` is the second half of that answer, and reads the legacy
  field alone — call the resolver, not it, unless you specifically mean the legacy field.)
* :func:`argument_distinctness` — are two specs the same argument wearing two subjects?
* :func:`persona_coverage` — which personas hold recipients that no spec addresses?

The design follows :mod:`gtm_core.signal_record`: a judgement call ("did you vary the
message?") becomes reliable only once it is a *declared, checkable property* rather
than prose to be re-read. Nothing here carries hook text of its own — the matrix is
the only source of a hook, and it is the tenant's asset (see ``CLAUDE.md``: no tenant
copy in ``gtm_core/``). What this module hardcodes is a generic *role* vocabulary,
exactly as ``outreach_pack_linter._SEAT_RULES`` already does for seats.
"""

from __future__ import annotations

from .audit import audit_campaign, campaign_packs  # noqa: F401
from .backlog import Backlog, BacklogCell, BacklogUnreadable, backlog, render_backlog
from .cli import main  # noqa: F401

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .config import (  # noqa: F401
    _LINTER_DIR,
    EXEMPLARS,
    JACCARD_MAX,
    MAX_NGRAM_EMAILS,
    MAX_SPECS_PER_CAPABILITY,
    MIN_ARGUMENTS,
    MIN_RECIPIENTS,
    MIN_SEGMENT_FIT,
    MIN_SIGNAL_ATTESTATION,
    NGRAM_N,
    SIGNAL_TERM_NOISE_SHARE,
    parse_spec,
    persona_of,
    seat_of,
)
from .coverage import Coverage, persona_coverage  # noqa: F401
from .declared import (  # noqa: F401
    _ARGUMENT_ID_RE,
    _CAPABILITY_RE,
    _HOOK_CELL_RE,
    _PREMISE_RE,
    _SIGNAL_COLUMN_RE,
    _STAKES_RE,
    DeclaredCell,
    declared_cell,
    resolve_declared_cell,
)
from .distinctness import (  # noqa: F401
    _GREETING_RE,
    _MERGE_TAG_RE,
    PairOverlap,
    SharedPhrase,
    _authored_lines,
    argument_distinctness,
    shared_phrases,
)
from .fit import (  # noqa: F401
    _SIGNAL_STOPWORDS,
    SegmentFit,
    SignalFit,
    _discriminating_terms,
    _norm_segment,
    segment_fit,
    signal_columns_for_segment,
    signal_fit,
    signal_terms,
)
from .matrix import (  # noqa: F401
    _BULLET_RE,
    _FRONTMATTER_RE,
    _HEADING_RE,
    _SEPARATOR_RE,
    _TABLE_ROW_RE,
    Cell,
    Matrix,
    MatrixShape,
    RowAxis,
    UnknownHookCell,
    _cells_equal,
    _clean_cell,
    _column_index,
    _detect,
    _join_bullets,
    _parse_grid_section,
    _parse_rows_section,
    _parse_sections_shape,
    _sections,
    _split_row,
    parse_matrix,
    persona_key_of_label,
    row_key_of_label,
    row_key_of_title,
    seat_key_of_label,
)
from .premise import (  # noqa: F401
    _PREMISE_VOCAB_FILE,
    Premise,
    PremiseFinding,
    capability_monotone,
    capability_slug,
    capability_vocab,
    declared_capability,
    declared_premise,
    declared_signal_column,
    declared_stakes,
    load_premise_vocab,
    premise_attestation,
    premise_unsupported,
)
from .render import render  # noqa: F401
from .rows import RowCell, classify_rows, derive_row_cell  # noqa: F401

__all__ = [
    "Backlog",
    "BacklogCell",
    "BacklogUnreadable",
    "backlog",
    "render_backlog",
    "JACCARD_MAX",
    "MAX_NGRAM_EMAILS",
    "NGRAM_N",
    "SharedPhrase",
    "Cell",
    "Coverage",
    "DeclaredCell",
    "Matrix",
    "MatrixShape",
    "PairOverlap",
    "RowCell",
    "SegmentFit",
    "SignalFit",
    "UnknownHookCell",
    "argument_distinctness",
    "audit_campaign",
    "campaign_packs",
    "classify_rows",
    "declared_cell",
    "resolve_declared_cell",
    "declared_stakes",
    "derive_row_cell",
    "segment_fit",
    "signal_columns_for_segment",
    "signal_fit",
    "signal_terms",
    "main",
    "parse_matrix",
    "persona_coverage",
    "persona_key_of_label",
    "persona_of",
    "render",
    "shared_phrases",
]
