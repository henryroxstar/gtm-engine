"""The three structural checks behind ``icp check``.

Each one reuses the implementation that already owns its question. Nothing here re-derives an
answer another module already computes — the drift that would cause is exactly the failure
``gtm_core/cells.py`` documents for ``seat_of``: two implementations of one rule diverge, and
the divergence is invisible because both sides keep answering.

===========================  =======================================================  ============
finding code                 reuses                                                   fails when
===========================  =======================================================  ============
``criterion-unqueryable``    :func:`prospects_backlog.load_backlog_accounts` +         hit count is
                             :func:`prospects_backlog._cohort_pattern` (via            0, or above
                             :mod:`.keyword`)                                          ``max_hits``
``persona-unmapped``         :attr:`role_vocabulary.RoleVocabulary.persona_to_seat` +  a persona
                             :meth:`hook_coverage.matrix.Matrix.unmapped_personas`     maps to no
                                                                                       seat
``rubric-undiscriminating``  :func:`prospects_backlog.score_account`                   the rubric
                                                                                       orders
                                                                                       nothing
===========================  =======================================================  ============

**What this cannot tell you, stated so it is not over-read.** These are three *structural*
properties — is a criterion queryable, is a persona mappable, does a rubric discriminate. None of
them answers *"is this the right ICP?"*, because that needs replies. A clean run is not
validation of a targeting strategy, and :mod:`.cli` says so in its own output.

**Scale.** ``rubric-undiscriminating`` reads :func:`prospects_backlog.score_account`, which is an
additive **enrichment/spend ranking** (cohort weight + segment + intent + geo). It is NOT the
qualification rubric in ``icp-personas.md`` — those are two different scales, and treating one as
the other is a defect this repo has already paid for once. The finding names its own scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import prospects_backlog as pb
from .. import role_vocabulary as rv
from ..hook_coverage.matrix import parse_matrix
from ..paths import resolve_content_root, resolve_knowledge_file, resolve_profiles_root
from .keyword import DEFAULT_MAX_HITS, keyword_hits

#: The closed finding-code vocabulary. Closed on purpose (§4.2): an unanticipated code is a
#: programming error here, never a silently-unclassified finding.
FINDING_CODES: tuple[str, ...] = (
    "criterion-unqueryable",
    "persona-unmapped",
    "rubric-undiscriminating",
)

#: At most this many exemplars are named inline in one finding — the 2026-08-19 rule is
#: binding: "Acknowledging 388 findings is not an action a human performs."
EXEMPLARS = 5

#: A rubric is undiscriminating when at least this share of the backlog scores zero on the
#: cohort axis. Not a tuning knob so much as a statement of what "orders nothing" means.
_ZERO_SCORE_MAJORITY = 0.5

#: Which rubric key is matched against which account field. Mirrors `score_account` exactly
#: (`keywords` -> industry, `description_keywords` -> description) — see keyword.py.
_RUBRIC_KEY_TO_FIELD = {
    "keywords": "industry",
    "description_keywords": "description",
}


@dataclass
class IcpCheck:
    """One run's findings, plus the counts three surfaces must all render identically."""

    profile: str
    product: str | None = None
    overlay: str | None = None
    backlog_size: int = 0
    findings: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        """Finding counts by code — the ONE derivation the terminal summary, the ``--out``
        JSON and the ``history.jsonl`` event all render (§4.5)."""
        out = {}
        for code in FINDING_CODES:
            n = sum(1 for f in self.findings if f.startswith(f"{code}:"))
            if n:
                out[code] = n
        return out

    @property
    def failed(self) -> bool:
        return bool(self.findings)


# --- the three checks -------------------------------------------------------------------------


def criterion_unqueryable(
    rubric: dict, accounts: list[dict], *, max_hits: int = DEFAULT_MAX_HITS
) -> list[str]:
    """Cohort phrases that select nothing, or select far too much to be a signal.

    **The ceiling applies to ``description_keywords`` only, and that asymmetry is the whole
    point of this check.** The two rubric keys are matched against different fields by
    :func:`prospects_backlog.score_account`, and breadth means opposite things in each:

    * ``keywords`` -> ``industry``, a NAICS-derived category label. A cohort keyword matching
      hundreds of accounts is the category working as intended — measured on a live tenant
      backlog, ``bank`` hits 115 and ``insur`` 321, and both are correct. Only **0 hits** is a
      defect there: a category that selects nothing cannot identify the cohort.
    * ``description_keywords`` -> ``description``, scraped free text. Here breadth IS the
      failure mode — the phrase has become a category label the whole market uses rather than
      a cohort signal, which is why a tenant rubric's own comment records rejecting phrases at
      88 and 38 hits while keeping ones at 1.

    Applying the description ceiling to industry keywords would flag the healthy state of
    every industry cohort — the same class of scale error as reading an enrichment score as a
    qualification score, and a check that fires on correct design is one an operator learns to
    ignore.

    Findings are aggregated **per cohort per key**, not one line per phrase: a real rubric has
    dozens of phrases, and "Acknowledging 388 findings is not an action a human performs".
    """
    findings = []
    for cohort in rubric.get("cohort", []):
        name = cohort.get("name", "<unnamed>")
        for rubric_key, field_name in _RUBRIC_KEY_TO_FIELD.items():
            phrases = cohort.get(rubric_key, []) or []
            if not phrases:
                continue
            counted = [(p, keyword_hits(p, accounts, field=field_name).count) for p in phrases]

            empty = [p for p, n in counted if n == 0]
            if empty:
                shown = ", ".join(repr(p) for p in empty[:EXEMPLARS])
                more = f" (+{len(empty) - EXEMPLARS} more)" if len(empty) > EXEMPLARS else ""
                findings.append(
                    f"criterion-unqueryable: icp-scoring.toml · cohort {name!r} · {rubric_key} "
                    f"— {len(empty)} of {len(phrases)} phrase(s) select nothing on a "
                    f"{len(accounts)}-account backlog: {shown}{more}; they cannot identify "
                    f"this cohort"
                )

            # The ceiling is a description-field question only (see the docstring).
            if rubric_key == "description_keywords":
                broad = [(p, n) for p, n in counted if n > max_hits]
                if broad:
                    shown = ", ".join(f"{p!r} ({n})" for p, n in broad[:EXEMPLARS])
                    more = f" (+{len(broad) - EXEMPLARS} more)" if len(broad) > EXEMPLARS else ""
                    findings.append(
                        f"criterion-unqueryable: icp-scoring.toml · cohort {name!r} · "
                        f"{rubric_key} — {len(broad)} phrase(s) above the ceiling of "
                        f"{max_hits} on a {len(accounts)}-account backlog: {shown}{more}; read "
                        f"the sample before keeping them (`icp keyword --phrase ... "
                        f"--field description`)"
                    )
    return findings


def persona_unmapped(vocab, matrix) -> list[str]:
    """Personas the tenant has written an argument *for* that nothing can be attributed *to*.

    This reduces to ONE question — the hook matrix's — and the two it deliberately does not
    ask are worth recording, because both look like obvious checks and neither is one:

    * **A seatless persona is not a finding.** ``role_vocabulary``'s defaults document
      resolver-only personas — recognised so they are never invisible, mapped to no seat so no
      copy is owed to them, because they hold 0 and 1 recipients across the measured pool and
      "a seat with no recipients is a copy obligation with no reader". Flagging those would
      fire on correct, deliberate design for every profile shipping no vocabulary of its own,
      and a warning that is always wrong is a warning nobody reads.
    * **A seat naming an unproducible persona is not reachable.**
      :func:`role_vocabulary.load` already refuses that vocabulary outright with
      ``VocabularyError`` ("a seat owed copy for a persona nothing resolves is a copy
      obligation with no reader"), before this module can ever see it. Re-checking it here
      would be a control that can never fire (§R18).

    So the genuinely unguarded question is the matrix's: a persona label that does not
    normalise onto the role vocabulary. That is computed by
    :meth:`Matrix.unmapped_personas` — which resolves labels through the role vocabulary
    itself, so the reuse is real and there is still exactly one normaliser.

    **Never a default seat.** Inventing a fallback would make the gap unobservable, which is
    the whole defect. ``vocab`` is accepted (and a malformed one is allowed to raise on the
    way in) precisely so that a tenant vocabulary which fails to load stops the run loudly.

    Note what this does NOT cover, so it is not over-read: persona headings in
    ``icp-personas.md`` are prose with no machine-readable list, and this adds no schema. A
    persona that exists only as a prose heading is invisible here.
    """
    findings: list[str] = []

    if matrix is None:
        findings.append(
            "persona-unmapped: hook-matrix.md — no hook matrix resolves for this profile, so "
            "whether its personas map to a seat cannot be answered; absence is not a pass"
        )
    elif not matrix.ok:
        findings.append(f"persona-unmapped: hook-matrix.md — {matrix.reason}")
    elif matrix.unmapped_personas():
        unmapped = matrix.unmapped_personas()
        findings.append(
            f"persona-unmapped: hook-matrix.md — {len(unmapped)} matrix persona(s) do not "
            f"normalise onto the role vocabulary ({', '.join(unmapped[:EXEMPLARS])}); "
            f"recipients can never be attributed to them"
        )
    return findings


def rubric_undiscriminating(rubric: dict, accounts: list[dict]) -> list[str]:
    """Does this rubric actually separate the backlog, or rank everything the same?

    A rubric that orders nothing still spends enrichment credits — in arbitrary order, which
    is precisely what a ranked queue exists to prevent. Two ways it happens: nothing matches a
    cohort at all, or every account lands on the same score.

    Strictly on the **enrichment scale** (:func:`prospects_backlog.score_account`: cohort +
    segment + intent + geo). This is not the qualification rubric in ``icp-personas.md``.
    """
    if not accounts:
        return [
            "rubric-undiscriminating: icp-scoring.toml — the backlog is empty, so whether this "
            "rubric separates anything cannot be answered; absence is not a pass"
        ]

    scored = [pb.score_account(rec, rubric) for rec in accounts]
    no_cohort = sum(1 for _, cohort in scored if not cohort)
    share = no_cohort / len(accounts)
    distinct = {score for score, _ in scored}

    findings = []
    if share >= _ZERO_SCORE_MAJORITY:
        findings.append(
            f"rubric-undiscriminating: icp-scoring.toml — {no_cohort} of {len(accounts)} "
            f"accounts ({share:.0%}) match no cohort at all, so this rubric orders almost "
            f"nothing on the enrichment scale; the enrichment queue would spend in "
            f"near-arbitrary order"
        )
    elif len(distinct) == 1:
        findings.append(
            f"rubric-undiscriminating: icp-scoring.toml — every one of {len(accounts)} accounts "
            f"scores {distinct.pop()} on the enrichment scale; the rubric has no spread, so the "
            f"queue's order carries no information"
        )
    return findings


# --- the run ------------------------------------------------------------------------------------


def _load_matrix(profiles_root: Path, profile: str, product: str | None, overlay: str | None):
    path = resolve_knowledge_file(profiles_root, profile, "hook-matrix.md", product, overlay)
    if not path.is_file():
        return None
    return parse_matrix(path, profile=profile)


def _load_vocabulary(profiles_root: Path, profile: str, product: str | None, overlay: str | None):
    """The tenant's role vocabulary, or ``None`` when it cannot be read.

    A missing file yields the shipped default (``role_vocabulary.load``'s own documented
    behaviour), which is a real answer. A malformed one raises ``VocabularyError`` and is
    allowed to propagate: a file that exists but is wrong must fail loudly.
    """
    return rv.load(profile, profiles_root, product, overlay)


def run_checks(
    profile: str,
    *,
    profiles_root: Path | None = None,
    content_root: Path | None = None,
    product: str | None = None,
    overlay: str | None = None,
    max_hits: int = DEFAULT_MAX_HITS,
) -> IcpCheck:
    """Run all three checks over the RESOLVED ICP (overlay -> product -> profile).

    Reads only. The backlog is loaded ``strict=True``: an unreadable row refuses the run
    rather than lowering a hit count, because a lower count reads as "safe to add".
    """
    profiles_root = profiles_root or resolve_profiles_root()
    content_root = content_root or resolve_content_root()

    rubric = pb.load_rubric(profile, profiles_root, product, overlay)
    accounts = pb.load_backlog_accounts(profile, content_root=content_root, strict=True)
    vocab = _load_vocabulary(profiles_root, profile, product, overlay)
    matrix = _load_matrix(profiles_root, profile, product, overlay)

    result = IcpCheck(profile=profile, product=product, overlay=overlay, backlog_size=len(accounts))
    result.findings.extend(criterion_unqueryable(rubric, accounts, max_hits=max_hits))
    result.findings.extend(persona_unmapped(vocab, matrix))
    result.findings.extend(rubric_undiscriminating(rubric, accounts))
    return result
