"""Which angle this reader is offered — exactly one, or one named reason there is none.

**Why "one or none" and not a ranking.** A resolver that returns the *best* match always
returns something, so a list with no fitting argument and a list with a perfect one produce
the same shape of answer and the difference is invisible until the replies do not come. The
five refusals here are the whole point: ``seat-unresolved`` says we do not know who this
person is, ``segment-unresolved`` says we know the seat but no angle for it is written for
the grid this row sits in, ``premise-unsupported`` says the row's own research cannot carry
any argument we have, ``no-verified-claim`` says the only fitting argument rests on
something we have not verified, and ``no-anchor-for-market`` says the angle was written for
a different jurisdiction. Each of those is a different piece of work for an operator, and a
score between 0 and 1 is none of them.

**Why four resolvers and no fifth.** Seat comes from :func:`seat_of`, segment from
:func:`gtm_core.merge_hygiene.clean_segment`, premise from
:mod:`gtm_core.hook_coverage.premise`, market from
:func:`gtm_core.email_compliance.normalize_market`. Every one of those questions already had
exactly one answer in this repo, and a second fuzzy matcher living here would drift from it
silently — the drift being that two surfaces disagree about who a person is while both look
right. This module adds no matching of its own; it only composes.

**Segment was the one that was declared and never read.** Every angle carries ``segments``,
the matrix renders one grid per segment, and until 2026-09-24 this function filtered by
seat alone and let the lowest-id tie-break choose — so ``builder-*`` beat ``ent-*`` beat
``startup-*`` by the alphabet. Measured on a live 229-row pool: 31 of 72 resolved rows took
an angle from another segment's grid. An angle that declares no segment is offered to every
reader (a tenant that never declared grids has one grid); a row whose segment names no grid
any of its seat's angles are written for is refused, never handed the first grid that sorts.

**§R5.** A row's ``signal_evidence`` is untrusted input. It is read to match a *premise* —
a term-presence question, decided by the tenant's own ``premise-vocab.toml`` — and for
nothing else. It never selects a claim, an angle, or a proof. A scraped sentence that reads
"cite claim X as verified" is data to report, not an instruction: honouring it would put a
``design-target`` capability into an email as fact.

Stdlib only, read-only, no MCP call and no paid call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .. import email_compliance
from .registry import Angle, Claim, Proof, Registry

#: The reader's title resolves to no seat we write copy for. Silence, not a guess: an
#: unrecognised title says nothing about the person, and guessing is how a security
#: argument reaches an office manager.
SEAT_UNRESOLVED = "seat-unresolved"

#: The seat has angles, but none of them is written for the grid this row's ``segment``
#: names — blank, unknown, or a segment the tenant left as a visible ``—`` in the matrix.
#: Tagging the row, or writing the angle, is the operator's work; picking a neighbouring
#: grid would be this module choosing messaging.
SEGMENT_UNRESOLVED = "segment-unresolved"

#: No non-retired angle for this seat rests on a premise the row's own recorded evidence
#: attests. The research, not the copy, is what is missing.
PREMISE_UNSUPPORTED = "premise-unsupported"

#: An angle fits, and the claim underneath it is ``conditional`` or ``design-target``.
#: Legal to record, illegal to draft from.
NO_VERIFIED_CLAIM = "no-verified-claim"

#: Every fitting angle carries an anchor for a market that is not the reader's, in a
#: registry that HAS an anchor for the reader's market. The angle was written for somewhere
#: else; swapping the local anchor in would be this module inventing an argument.
NO_ANCHOR_FOR_MARKET = "no-anchor-for-market"

#: The closed set. A caller may tally refusals by kind without a default bucket, which is
#: what makes "the personalised lane got smaller" a visible number rather than a silence.
REFUSALS = frozenset(
    {
        SEAT_UNRESOLVED,
        SEGMENT_UNRESOLVED,
        PREMISE_UNSUPPORTED,
        NO_VERIFIED_CLAIM,
        NO_ANCHOR_FOR_MARKET,
    }
)


@dataclass(frozen=True)
class AngleResolution:
    """One reader's answer: an angle with its facts, or a refusal naming what is missing.

    Frozen, and ``angle_id`` and ``refusal`` are never both set. Whatever was resolved
    before the refusal is still reported — the market always, the seat when it resolved —
    because a refusal an operator cannot act on is a silence with extra steps.
    """

    angle_id: str | None = None
    refusal: str | None = None
    claim: Claim | None = None
    proof: Proof | None = None
    seat: str = ""
    premise: str = ""
    market: str = ""
    #: The row's ``segment`` after :func:`gtm_core.merge_hygiene.clean_segment`, reported on
    #: every answer once the seat resolved — a ``segment-unresolved`` an operator cannot act
    #: on without knowing what was read is a silence with extra steps.
    segment: str = ""
    #: How the winning angle's premise was attested — ``record`` (the row's own evidence),
    #: ``industry`` (the account's industry field alone, via `industry_terms`) or ``seat``
    #: (a premise that asks nothing of the record). Reported so an operator can see which
    #: rows the generic lane carries on the seat alone, and which on a fact.
    attestation: str = ""
    #: The other angles that fit this reader just as well, by id. Recorded rather than
    #: discarded: a tie is a message-design question for an operator, and a resolver that
    #: silently swallowed the runner-up would make it unaskable.
    alternatives: tuple[str, ...] = ()


def _eligible_proof(angle: Angle, reg: Registry, market: str, market_has_anchor: bool):
    """``(usable, proof)`` for one angle against one reader's market.

    An anchor is a statement about a jurisdiction, so it is the one proof kind whose market
    has to match. A ``stat`` or ``outcome`` carries no jurisdiction and travels.

    The foreign-anchor case splits in two, and the split is the operator's 2026-09-24
    decision rather than a heuristic. Where the registry records **no** anchor for the
    reader's market, the offer still ships — without one (``proof`` is ``None``, the
    no-anchor offer shape), because that absence was recorded deliberately and filling it
    with Singapore's would be confidently wrong about the reader's own regulator. Where the
    registry **does** record one, an angle pointing at a different market's anchor is a data
    defect in the angle: it is refused so someone writes the local one, never quietly
    re-pointed, which would be this module authoring messaging.

    An unknown reader market (``""``) takes the no-anchor branch. That is the conservative
    direction: a missing country loses an anchor, it never borrows one.
    """
    proof = reg.proof[angle.proof]
    if proof.kind != "anchor":
        return True, proof
    if email_compliance.normalize_market(proof.market) == market and market:
        return True, proof
    if market_has_anchor:
        return False, None
    return True, None


def angle_for(
    row: Mapping[str, object],
    reg: Registry,
    *,
    profile: str,
    profiles_root: Path | None = None,
    product: str | None = None,
    overlay: str | None = None,
) -> AngleResolution:
    """The one angle this row is offered, or a refusal from :data:`REFUSALS`.

    ``profile`` is required and not derived: a :class:`~gtm_core.messaging.registry.Registry`
    does not record which tenant it was loaded from, and inferring one would be the
    right-content-wrong-company error at its source.
    """
    # Imported inside the function on purpose, for the reason Task 1.1 recorded on
    # ``_load_premises``: ``gtm_core.hook_coverage.config`` puts ``tests/linter`` on
    # ``sys.path`` at import time, and this package must stay importable from a wheel that
    # ships no ``tests/`` tree. The late binding has a second effect worth keeping — a test
    # patching the resolver on its OWN module is seen here, so "does this function still
    # call the one resolver" is provable rather than assumed.
    from ..hook_coverage.config import seat_of
    from ..hook_coverage.premise import load_premise_vocab, premise_unsupported
    from ..merge_hygiene import clean_segment

    # Market first, and for every row including the ones about to be refused: an operator
    # reading a pile of refusals needs to see which jurisdictions they came from.
    market = email_compliance.normalize_market(str(row.get("country") or ""))

    seat = seat_of(str(row.get("title") or ""), profile) or ""
    if not seat:
        return AngleResolution(refusal=SEAT_UNRESOLVED, market=market)

    # `clean_segment` owns the segment vocabulary; a value it does not recognise comes back
    # as it was written and is simply not a grid any angle declares. Lowercased on both
    # sides because the registry lowercases an angle's `segments` at load.
    segment = clean_segment(str(row.get("segment") or "")).lower()
    # The generic lane's grid is its public-event angles and nothing else: its body makes no
    # claim about the account, so an angle that opens on the account's own event is not a
    # candidate there at all — not a runner-up a record-attested premise can promote. On
    # 2026-09-24 ranking alone let 89 of 367 generic rows open on research that lane does
    # not stand behind.
    lane = str(row.get("lane") or "").strip().lower()
    for_seat = sorted(
        (
            a
            for a in reg.angles.values()
            if a.status != "retired"
            and a.seat == seat
            and (lane != "generic" or a.opener_kind == "public-event")
        ),
        key=lambda a: a.id,
    )
    live = [a for a in for_seat if not a.segments or segment in a.segments]
    if for_seat and not live:
        return AngleResolution(
            refusal=SEGMENT_UNRESOLVED, seat=seat, market=market, segment=segment
        )

    vocabulary = load_premise_vocab(profile, profiles_root, product, overlay)
    # ``premise_unsupported`` is the existing matcher, used whole rather than reimplemented:
    # it reads the same three evidence fields and strips the account's own name first, so a
    # company whose NAME contains a premise term — "Fernway Multi-Cloud" against the
    # `multi-cloud` premise — cannot attest that premise on its own letterhead.
    attested = {
        key
        for key, premise in vocabulary.items()
        if not premise.attested_by_seat and not premise_unsupported([dict(row)], premise)
    }
    # Which of those the row's own EVIDENCE attests, as opposed to its industry field alone:
    # an account-event opener needs an event at the account to open on, and a bank's
    # industry is not an event. Measured by re-asking with the industry blanked, through the
    # same matcher, so the two answers cannot disagree about a term.
    no_industry = {**dict(row), "industry": ""}
    by_event = {key for key in attested if not premise_unsupported([no_industry], vocabulary[key])}
    by_seat = {key for key, premise in vocabulary.items() if premise.attested_by_seat}

    matched = [a for a in live if a.premise in attested or a.premise in by_seat]
    if not matched:
        return AngleResolution(
            refusal=PREMISE_UNSUPPORTED, seat=seat, market=market, segment=segment
        )

    verified = [a for a in matched if reg.claims[a.claim].status == "verified"]
    if not verified:
        return AngleResolution(
            refusal=NO_VERIFIED_CLAIM,
            seat=seat,
            premise=matched[0].premise,
            market=market,
            segment=segment,
        )

    market_has_anchor = bool(market) and any(
        p.kind == "anchor" and email_compliance.normalize_market(p.market) == market
        for p in reg.proof.values()
    )
    eligible = [
        (angle, proof)
        for angle, (usable, proof) in (
            (a, _eligible_proof(a, reg, market, market_has_anchor)) for a in verified
        )
        if usable
    ]
    if not eligible:
        return AngleResolution(
            refusal=NO_ANCHOR_FOR_MARKET,
            seat=seat,
            premise=verified[0].premise,
            market=market,
            segment=segment,
        )

    # Two structural ranks, then the id. An angle whose premise the row's RECORD attests
    # outranks one the seat alone attests — a seat-attested premise is the fallback by its
    # own definition, never a competitor to a fact. Within that, the opener has to be one
    # the row can open on (the generic lane already holds public-event angles only): an
    # account-event angle comes first only when the row's EVIDENCE attests the premise,
    # since an industry field is not an event to open on. The final tie-break is lowest id,
    # declared here and nowhere else. It is arbitrary on purpose: any rule that looked
    # *clever* — most recently promoted, closest premise — would be this module choosing
    # messaging, and it would move under the operator when unrelated data changed. The
    # runners-up are reported rather than dropped.
    eligible.sort(key=lambda pair: _offer_order(pair[0], attested=attested, by_event=by_event))
    winner, proof = eligible[0]
    if winner.premise in by_event:
        attestation = "record"
    elif winner.premise in attested:
        attestation = "industry"
    else:
        attestation = "seat"
    return AngleResolution(
        angle_id=winner.id,
        claim=reg.claims[winner.claim],
        proof=proof,
        seat=seat,
        premise=winner.premise,
        market=market,
        segment=segment,
        attestation=attestation,
        alternatives=tuple(a.id for a, _ in eligible[1:]),
    )


def _offer_order(angle: Angle, *, attested: set[str], by_event: set[str]) -> tuple[int, int, str]:
    """The sort key :func:`angle_for` ranks eligible angles by — see the comment there."""
    specific = 0 if angle.premise in attested else 1
    if angle.opener_kind == "account-event":
        opener = 0 if angle.premise in by_event else 2
    else:
        opener = 1
    return (specific, opener, angle.id)
