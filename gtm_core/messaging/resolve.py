"""Which angle this reader is offered — exactly one, or one named reason there is none.

**Why "one or none" and not a ranking.** A resolver that returns the *best* match always
returns something, so a list with no fitting argument and a list with a perfect one produce
the same shape of answer and the difference is invisible until the replies do not come. The
four refusals here are the whole point: ``seat-unresolved`` says we do not know who this
person is, ``premise-unsupported`` says the row's own research cannot carry any argument we
have, ``no-verified-claim`` says the only fitting argument rests on something we have not
verified, and ``no-anchor-for-market`` says the angle was written for a different
jurisdiction. Each of those is a different piece of work for an operator, and a score
between 0 and 1 is none of them.

**Why three resolvers and no fourth.** Seat comes from :func:`seat_of`, premise from
:mod:`gtm_core.hook_coverage.premise`, market from
:func:`gtm_core.email_compliance.normalize_market`. Every one of those questions already had
exactly one answer in this repo, and a second fuzzy matcher living here would drift from it
silently — the drift being that two surfaces disagree about who a person is while both look
right. This module adds no matching of its own; it only composes.

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
    {SEAT_UNRESOLVED, PREMISE_UNSUPPORTED, NO_VERIFIED_CLAIM, NO_ANCHOR_FOR_MARKET}
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

    # Market first, and for every row including the ones about to be refused: an operator
    # reading a pile of refusals needs to see which jurisdictions they came from.
    market = email_compliance.normalize_market(str(row.get("country") or ""))

    seat = seat_of(str(row.get("title") or ""), profile) or ""
    if not seat:
        return AngleResolution(refusal=SEAT_UNRESOLVED, market=market)

    vocabulary = load_premise_vocab(profile, profiles_root, product, overlay)
    # ``premise_unsupported`` is the existing matcher, used whole rather than reimplemented:
    # it reads the same three evidence fields and strips the account's own name first, so a
    # company whose NAME contains a premise term — "Fernway Multi-Cloud" against the
    # `multi-cloud` premise — cannot attest that premise on its own letterhead.
    attested = {
        key for key, premise in vocabulary.items() if not premise_unsupported([dict(row)], premise)
    }

    live = sorted(
        (a for a in reg.angles.values() if a.status != "retired" and a.seat == seat),
        key=lambda a: a.id,
    )
    matched = [a for a in live if a.premise in attested]
    if not matched:
        return AngleResolution(refusal=PREMISE_UNSUPPORTED, seat=seat, market=market)

    verified = [a for a in matched if reg.claims[a.claim].status == "verified"]
    if not verified:
        return AngleResolution(
            refusal=NO_VERIFIED_CLAIM, seat=seat, premise=matched[0].premise, market=market
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
            refusal=NO_ANCHOR_FOR_MARKET, seat=seat, premise=verified[0].premise, market=market
        )

    # The tie-break is lowest id, declared here and nowhere else. It is arbitrary on
    # purpose: any rule that looked *clever* — most recently promoted, closest premise —
    # would be this module choosing messaging, and it would move under the operator when
    # unrelated data changed. Arbitrary and stable beats plausible and drifting, and the
    # runners-up are reported rather than dropped.
    winner, proof = eligible[0]
    return AngleResolution(
        angle_id=winner.id,
        claim=reg.claims[winner.claim],
        proof=proof,
        seat=seat,
        premise=winner.premise,
        market=market,
        alternatives=tuple(a.id for a, _ in eligible[1:]),
    )
