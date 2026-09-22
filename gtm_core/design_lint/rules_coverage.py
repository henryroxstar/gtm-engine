from __future__ import annotations

import re

from .catalog import EXEC_MARKERS, TIER1_MARKERS, TIER2_MARKERS, Dimension, dimensions
from .model import ERROR, WARN, Finding, Section

# Dimensions that carry their own rule rather than being reported through SD2, so a
# document with no Quality Requirements gets one loud finding instead of a warning lost in
# a list. Keyed on `guid` — the stable identity — never on `id`, which may be renumbered.
_QUALITY_REQUIREMENTS = "1ec44d20-3347-463c-8cda-b44307bb76e0"  # COV-10
_GLOSSARY = "bb61d433-042b-4747-8209-2e991a8b2cdf"  # COV-12
_OWN_RULE = {_QUALITY_REQUIREMENTS: "SD3", _GLOSSARY: "SD4"}


# ══════════════════════════════════════════════════════════════════════════════════════
# SD1 — tier order
# ══════════════════════════════════════════════════════════════════════════════════════
#
# The three-tier read is the shape of the document: a ½-page executive summary, then the
# Tier 1 customer overview, then the clearly-marked Tier 2 technical appendix. A reader
# gets the whole story from the first two and descends only if they are the architect who
# has to build it. Out of order, that promise breaks silently.


# A solution design ships as THREE files — the main overview, a `-appendix` and an
# `-internal` cut — because `solution-design` Step 6 mandates that split. Two of the three
# legitimately carry no executive summary and no Tier 1, so demanding all three tiers of
# every file made the rule fire 21 times across a corpus where it was wrong every time.
_FRAGMENT_BANNER = "omit from customer copy"


def document_kind(sections: list[Section]) -> str:
    """`"fragment"` for an appendix/internal cut, `"whole"` for a full design.

    Content-only, because `lint()` takes text and a rule needing a path is a rule the
    library cannot run. Where the path IS available (`calibrate.stratum`) the filename
    suffix is exact and is preferred — this is the fallback.

    A fragment is defined by what it LACKS — no executive summary and no Tier 1 — plus
    positive evidence that it is a *part*: appendix-numbered headings, or the
    omit-from-customer-copy banner in its opening. Both halves are load-bearing, and the
    calibration harness proved it twice: counting appendix headings alone called every
    complete design a fragment (a complete design carries a full A1…A8), and matching the
    banner anywhere did the same, because a whole design's own A8 contains it.
    """
    headed = [s for s in sections if s.heading]
    if not headed:
        return "whole"
    has_front = (
        _first_index(sections, EXEC_MARKERS) is not None
        or _first_index(sections, TIER1_MARKERS) is not None
    )
    if has_front:
        return "whole"
    opening = sections[:2]
    banner_up_front = any(
        _FRAGMENT_BANNER in s.body.lower() or _FRAGMENT_BANNER in s.slug for s in opening
    )
    if any(s.appendix_id for s in headed) or banner_up_front:
        return "fragment"
    return "whole"


def _first_index(sections: list[Section], markers: frozenset[str]) -> int | None:
    for section in sections:
        if any(marker in section.slug for marker in markers):
            return section.index
    return None


def _sd1(sections: list[Section]) -> list[Finding]:
    out: list[Finding] = []
    fragment = document_kind(sections) == "fragment"
    found = {
        "Executive summary": _first_index(sections, EXEC_MARKERS),
        "Tier 1 (customer overview)": _first_index(sections, TIER1_MARKERS),
        "Tier 2 (technical appendix)": _first_index(sections, TIER2_MARKERS),
    }
    # `solution-design` Step 6 mandates a three-FILE split, so the main cut legitimately
    # carries no Tier 2 when it links out to the appendix document instead.
    links_out = any("-appendix" in s.body for s in sections)
    for name, index in found.items():
        if fragment:
            break  # a fragment carries one tier by design; only ordering is checkable
        if index is None and links_out and name.startswith("Tier 2"):
            continue
        if index is None:
            out.append(
                Finding(
                    "SD1",
                    "tier missing",
                    ERROR,
                    0,
                    name,
                    f"the three-tier read needs {name} — an exec, the customer overview, "
                    "then the appendix the customer copy drops",
                )
            )
    present = [(name, i) for name, i in found.items() if i is not None]
    for (before, i), (after, j) in zip(present, present[1:], strict=False):
        if i > j:
            out.append(
                Finding(
                    "SD1",
                    "tier order",
                    ERROR,
                    j,
                    f"{after} precedes {before}",
                    "order is exec summary → Tier 1 → Tier 2; the appendix comes last so "
                    "the customer copy can drop it whole",
                )
            )
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# SD2 / SD3 / SD4 — coverage
# ══════════════════════════════════════════════════════════════════════════════════════
#
# Matching is on HEADINGS, never on body prose. That is the point: this asks whether the
# document has a place where the question is answered, not whether the words appear
# somewhere. A constraint mentioned in passing inside the assumptions list is exactly the
# conflation COV-02 exists to surface.


def satisfied_by(sections: list[Section], dimension: Dimension) -> Section | None:
    for section in sections:
        if not section.heading:
            continue
        if any(pattern in section.slug for pattern in dimension.match):
            return section
    return None


def _missing(sections: list[Section]) -> list[Dimension]:
    return [d for d in dimensions() if satisfied_by(sections, d) is None]


def _severity_of(guid: str) -> str:
    """The severity coverage.toml declares for a dimension.

    Read, never hardcoded — SD3 previously carried `ERROR` in its own body, so downgrading
    COV-10 in the taxonomy changed nothing. A rule that ignores the file it is derived from
    is a second source of truth.
    """
    for dimension in dimensions():
        if dimension.guid == guid:
            return ERROR if dimension.severity == "error" else WARN
    return ERROR


def _links_out(sections: list[Section]) -> bool:
    """True when this document defers its appendix to the sibling `-appendix` file."""
    return any("-appendix" in s.body for s in sections)


def _appendix_resident(dimension: Dimension) -> bool:
    """A dimension our structure answers inside the Tier 2 appendix (A1…An)."""
    return bool(re.match(r"^A\d", dimension.satisfied_by))


def _sd2(sections: list[Section]) -> list[Finding]:
    out: list[Finding] = []
    links_out = _links_out(sections)
    for dimension in _missing(sections):
        if dimension.guid in _OWN_RULE:
            continue
        # The mandated three-file split means the main cut does not carry its own
        # appendix. A dimension answered in A1…An IS answered — in the sibling document.
        if links_out and _appendix_resident(dimension):
            continue
        severity = ERROR if dimension.severity == "error" else WARN
        out.append(
            Finding(
                "SD2",
                f"coverage gap {dimension.id}",
                severity,
                0,
                f"{dimension.name} — {dimension.question}",
                f"add a section that answers it; ours normally does so as: "
                f"{dimension.satisfied_by or 'a section of its own'}",
            )
        )
    return out


def _sd3(sections: list[Section]) -> list[Finding]:
    missing = {d.guid for d in _missing(sections)}
    if _QUALITY_REQUIREMENTS not in missing:
        return []
    return [
        Finding(
            "SD3",
            "quality requirements absent",
            _severity_of(_QUALITY_REQUIREMENTS),
            0,
            "no availability, latency, throughput or recovery figures anywhere",
            "add a quality-requirements section with a figure per attribute — this is the "
            "first thing the customer's architect interrogates, and stating service levels "
            "only in the commercial proposal puts them in the wrong document",
        )
    ]


def _sd4(sections: list[Section]) -> list[Finding]:
    missing = {d.guid for d in _missing(sections)}
    if _GLOSSARY not in missing:
        return []
    return [
        Finding(
            "SD4",
            "glossary absent",
            _severity_of(_GLOSSARY),
            0,
            "no glossary",
            "add one for the terms that mean something specific here — a reader who guesses "
            "a definition disagrees with the design without knowing it",
        )
    ]
