"""R2.5: when a source list, or the account's own recorded announcement, may stand in for a premise's terms (Amendment A).

A list is evidence only when the list is itself about AI agents (``attestation = "agentic"``),
so the list route is closed to every other source. Both routes are closed to every premise that
does not opt in (``attested_by_source``). The list route is also closed to every observation that
no longer holds: another product's, an expired or untrusted source's, a vendor's, one whose
capture is gone, one a registry edit has retracted, one older than a year. Anything missing
refuses. ``source_attests`` reads no file and no clock: its answer is a function of its arguments.

``record_attests`` is the second route (cohort rule D12). It reads the account's stored capture of
the cited page to show the quote exists, and the run's date (or, if none is given, the wall clock),
and refuses when it has nowhere to look (verification audit 2026-10-02, Critical 2 and 3).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from .premise import Premise

#: A membership first seen longer ago than this is a stale fact, not current evidence.
MAX_AGE = datetime.timedelta(days=365)

_MEMBERSHIP = frozenset({"source_member", "source_join"})
_ROLES = frozenset({"buyer", "mixed"})


@dataclass(frozen=True)
class AttestContext:
    """What one run knows about its source lists: loaded once, then only read."""

    product: str
    registry: object
    observations: tuple[dict, ...]
    capture_shas: frozenset[str]
    today: datetime.date
    #: ``{shard, line, why}`` for each observation shard that could not be read. While any is, no
    #: observation is trusted (``observations`` is empty): a shard that cannot be read may hold a
    #: retraction, and the reader would otherwise drop it along with the shard.
    refused: tuple[dict, ...] = ()


def row_domain(row: dict) -> str:
    """The row's account domain: the ledger writes ``domain``, every lane CSV ``company_domain``."""
    raw = row.get("domain") or row.get("company_domain") or ""
    return str(raw).strip().lower().removeprefix("www.")


def _holds(obs: dict, premise: Premise, ctx: AttestContext) -> bool:
    from ..signal_obs.registry import inert_reason

    source = ctx.registry.by_id.get(obs.get("source_id"))
    if source is None or source.attestation != "agentic":
        return False
    if inert_reason(source, ctx.today) is not None:
        return False
    # The CURRENT registry premise must still be the one written with the observation, so an
    # edit to the registry retracts attestation at once without rewriting history.
    if not (source.premise == premise.key == obs.get("premise_at_write")):
        return False
    if obs.get("role") not in _ROLES or obs.get("capture_sha256") not in ctx.capture_shas:
        return False
    try:
        seen = datetime.date.fromisoformat(str(obs.get("observed")))
    except ValueError:
        return False
    return datetime.timedelta(0) <= ctx.today - seen <= MAX_AGE


def attesting_observation(row: dict, premise: Premise, ctx: AttestContext | None) -> dict | None:
    """The observation that lets an agentic source list stand in for this premise, or ``None``."""
    if ctx is None or not premise.attested_by_source:
        return None
    key = row_domain(row)
    if not key:
        return None
    return next(
        (
            o
            for o in ctx.observations
            if o.get("kind") in _MEMBERSHIP
            and o.get("product") == ctx.product
            and str(o.get("account_key") or "").strip().lower().removeprefix("www.") == key
            and _holds(o, premise, ctx)
        ),
        None,
    )


def source_attests(row: dict, premise: Premise, ctx: AttestContext | None) -> bool:
    """True only when an agentic source list the run trusts names this row's account."""
    return attesting_observation(row, premise, ctx) is not None


def record_attests(
    row: dict,
    premise: Premise,
    today: datetime.date | None = None,
    *,
    profile: str | None = None,
    sources_dir=None,
) -> bool:
    """True when the row carries its OWN recorded, dated announcement about AI agents (cohort rule D12).

    An opted-in premise, an agent kind of ``ai``, a quoted span that itself says agent or agentic,
    a source and a date inside a year, an account that is a prospect, **and** a stored capture of
    the cited page that holds that quote verbatim. The last condition is checked here and not left
    to the integrity gate: that gate skips a row with no clause, and only warns about a missing
    capture in the generic lane, so a route that trusted it would vouch for a quote nothing had
    shown to exist (verification audit 2026-10-02, Critical 3). With no profile and no sources
    folder to look in, the route refuses.

    ``today`` is the run's date; omitted it is the wall clock, which a replay cannot pin.
    """
    if not getattr(premise, "attested_by_source", False):
        return False
    if str(row.get("signal_agent_kind") or "").strip().lower() != "ai":
        return False
    if str(row.get("category_relation") or "").strip().lower() != "prospect":
        return False
    if not all(str(row.get(k) or "").strip() for k in ("signal_evidence", "signal_source_url")):
        return False
    # The `ai` kind is a model-written label; the quote itself must name an agent, by the gate's
    # own rule. A product the source calls a "companion" or "assistant" does not attest.
    from ..signal_record import _AGENT_WORD_RE

    if not _AGENT_WORD_RE.search(str(row.get("signal_evidence") or "")):
        return False
    try:
        seen = datetime.date.fromisoformat(str(row.get("signal_observed") or "").strip())
    except ValueError:
        return False
    if not datetime.timedelta(0) <= (today or datetime.date.today()) - seen <= MAX_AGE:
        return False
    from ..capture_quote import quote_in_latest_capture

    return quote_in_latest_capture(
        str(row.get("signal_evidence") or ""),
        str(row.get("signal_source_url") or ""),
        sources_dir=sources_dir,
        profile=profile or str(row.get("profile") or "").strip() or None,
    )
