from __future__ import annotations

import sys
from pathlib import Path

# ``gtm_core`` reaching into ``tests/linter`` is deliberate and pre-existing: see
# gtm_core/build_eval_sheet.py and gtm_core/cells.py, which take the same import for
# the same reason. Re-implementing the seat resolver, the spec parser or the overlap
# tokeniser would mean two definitions of one rule, and the copy gate's definition is
# the one that ships.
# parents[2], not .parent.parent: this module sits one level deeper than the pre-split
# gtm_core/hook_coverage.py, so the old two-step walk-up would stop at gtm_core/.
_LINTER_DIR = Path(__file__).resolve().parents[2] / "tests" / "linter"
if str(_LINTER_DIR) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(_LINTER_DIR))

# Imported HERE, not at module top: they only resolve once `_LINTER_DIR` is on sys.path.
# F401 — config is the package's one home for these; every other module re-imports them
# from here rather than repeating the sys.path dance.
from outreach import (  # noqa: E402,F401
    JACCARD_MAX,
    MAX_NGRAM_EMAILS,
    NGRAM_N,
    _content_words,
    _hedge_ngram_whitelist,
    _ngrams,
    _norm_tokens,
    parse_spec,
    persona_of,
    seat_of,
)

from ..cells import load_cell_map  # noqa: E402,F401
from ..eval_calibration import draft_cell_dirs  # noqa: E402,F401

# Imported HERE, not at module top: they only resolve once `_LINTER_DIR` is on sys.path.


#: A persona needs at least this many enrolled recipients before "no spec addresses
#: it" is a finding rather than a curiosity. Config, not a constant with a view: the
#: PRD has no evidence for a specific number, so the operator supplies it.
MIN_RECIPIENTS = 40

#: How many distinct arguments a campaign should carry. Also the operator's call
#: (PRD section 8 question 1 recommends 4-6); this default only makes the report say
#: something rather than nothing.
MIN_ARGUMENTS = 4

#: Exemplars shown per aggregated finding, matching gtm_core.finding_budget.
EXEMPLARS = 3

#: Share of a spec's recipients that must sit in the declared cell's segment before the
#: aim is credible. No PRD evidence for a specific number; below half means most readers
#: are not in the grid the hook was written for, which is the weakest defensible bar.
#: Operator-tunable (``--min-segment-fit``), like ``MIN_RECIPIENTS``.
MIN_SEGMENT_FIT = 0.5

#: Share of recipients whose recorded signal evidence attests the declared signal.
#: A WARN and not an ERROR: unlike segment -- where both sides are explicit, enumerated
#: values -- this infers a signal from free text, so a miss can mean "the evidence does
#: not say" rather than "the aim is wrong". Reported with the matched terms so the
#: operator can see which reading produced the number.
MIN_SIGNAL_ATTESTATION = 0.25


#: How many specs in one campaign may argue the same capability group before it is one
#: argument in several costumes. Two is deliberate, not one: a campaign legitimately runs
#: the same capability at two different seats (a CISO and a Chief Risk seat both meet the
#: attribution gap), and forbidding that would push drafters into contrived arguments —
#: the failure this rule exists to prevent, arrived at from the other side.
#:
#: Counted per (capability, seat) since 2026-09-24, not per capability campaign-wide: an
#: angle-tagged campaign (one spec per angle) runs one capability at every seat that has an
#: angle for it, and the first such campaign — 15 specs — sat at six on `identity` across five
#: seats. Same two-per rationale, applied to the unit it was written for.
MAX_SPECS_PER_CAPABILITY = 2


#: A term matching more than this share of the candidate rows is not evidence for any
#: particular signal — it describes the list. Same reasoning and same number as
#: `outreach_pack_linter.SOFT_ANCHORS`'s 2026-08-21 recalibration ("fires on >40% of a live
#: list = describes the list, doesn't screen it").
SIGNAL_TERM_NOISE_SHARE = 0.40


#: Glob for the 1:1 outreach packs a campaign produced, relative to the profile's
#: ``accounts/`` tree. Tier-A packs are one file per account under
#: ``accounts/<slug>/``, written by ``prospect`` (Step 7.5) and ``draft-outreach``.
PACK_GLOBS = (
    "*/prospects-{date}-outreach-*.md",
    "*/email-*-{date}.md",
)
