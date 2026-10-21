from __future__ import annotations

import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from ..email_compliance import normalize_market, read_target_markets
from .confidence import _person_key, _row_to_record
from .paths import _sequences_dir, dnc_cache_path

# How old a suppression cache may be before ``require_dnc`` refuses it. Suppression
# data going stale is silent under-suppression — a compliance failure, not a warning —
# so the scheduled path re-fetches every run and this is only the backstop.
DNC_MAX_AGE_HOURS = 24


class DncSuppression:
    """The suppression set: opted-out emails plus whole blocked domains.

    Saleshandy DNC entries are ``email`` **or** ``domain`` typed, and a domain entry
    suppresses every address at that domain. An email-only check silently under-blocks
    the moment someone adds a domain entry in the provider UI, so both are carried here
    and every call site asks :meth:`blocks` rather than testing membership itself.
    """

    __slots__ = ("emails", "domains", "fetched_at")

    def __init__(
        self,
        emails: set[str] | None = None,
        domains: set[str] | None = None,
        fetched_at: datetime | None = None,
    ) -> None:
        self.emails = emails or set()
        self.domains = domains or set()
        self.fetched_at = fetched_at

    def __len__(self) -> int:
        return len(self.emails) + len(self.domains)

    def blocks(self, email: str, company_domain: str = "") -> bool:
        """True if this address is suppressed — by exact address, by the domain of the
        address itself, or by the row's company domain."""
        email = email.strip().lower()
        if email in self.emails:
            return True
        if not self.domains:
            return False
        candidates = {company_domain.strip().lower().removeprefix("www.")}
        if "@" in email:
            candidates.add(email.rsplit("@", 1)[1])
        return any(c and c in self.domains for c in candidates)


class MarketGate:
    """The allowed-jurisdiction filter: a row's country must be inside ``target_markets``.

    Email law is not the same in every market (US CAN-SPAM needs no prior consent; Canada and
    Australia are consent-based; the EU/UK need a documented lawful basis), so a lead in a market
    the sequence was not built for is a compliance problem *before* it is a targeting one. Gating
    here — at consolidation — means such a lead never reaches ``ready-to-load.csv``, and so is never
    the thing an operator has to remember to strip at enrollment time.

    A **blank** country is *unresolved*, not *allowed*: it is counted separately and, under
    ``strict``, dropped. Non-strict is the default because a partly-enriched pool would otherwise
    empty itself; the count is always reported so the gap stays visible.

    A literal ``"global"`` entry (case-insensitive, after quote-stripping) is a wildcard: it is a
    profile's explicit declaration that it has no jurisdiction restriction, so it disables the
    filter entirely rather than being compared against a row's country like any other market — a
    row's country is never literally the word "global", so without this special case a profile
    could list "global" and still have every row excluded.
    """

    __slots__ = ("allowed", "markets", "strict", "wildcard")

    def __init__(self, markets: list[str] | None = None, *, strict: bool = False) -> None:
        self.markets = markets or []
        normalized = {normalize_market(m) for m in self.markets}
        self.wildcard = "global" in normalized
        self.allowed = normalized
        self.strict = strict

    def __bool__(self) -> bool:
        """False when no markets resolved — an unconfigured gate filters nothing."""
        return bool(self.allowed)

    def blocks(self, country: str) -> bool:
        """True if this row must not be loaded. Blank country blocks only under ``strict``."""
        if self.wildcard:
            return False
        c = (country or "").strip()
        if not c:
            return self.strict
        return normalize_market(c) not in self.allowed


def _resolve_market_gate(profile: str, strict: bool) -> MarketGate:
    """Read ``target_markets`` from the profile; an unreadable profile leaves the gate off.

    Off is loud, not silent — ``consolidate`` reports ``market_gate`` in its result and warns on
    stderr — because the hard stop for a real send is the ``gtm_core.email_compliance`` preflight,
    which fails closed. This gate is the earlier, cheaper net.
    """
    try:
        return MarketGate(read_target_markets(profile), strict=strict)
    except (FileNotFoundError, ValueError) as exc:
        print(f"market gate off ({exc}) — pool not filtered by jurisdiction", file=sys.stderr)
        return MarketGate()


def _parse_fetched_at(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _load_dnc(
    profile: str,
    content_root: Path | None,
    dnc_file: Path | None,
    *,
    require: bool = False,
    max_age_hours: int = DNC_MAX_AGE_HOURS,
) -> DncSuppression:
    """Load the suppression cache.

    Default (``require=False``) preserves the permissive interactive behaviour: a
    missing cache yields an empty suppression set. ``require=True`` — which every
    scheduled/unattended run passes — makes a missing, empty, undatable, or stale
    cache a **hard error**. Consolidating with an empty suppression set is
    indistinguishable in its output from consolidating with a correct one, which is
    exactly why it must not be reachable by accident.
    """
    path = dnc_file or dnc_cache_path(profile, content_root)
    if not path.exists():
        if require:
            raise ValueError(
                f"DNC suppression cache missing: {path}. Refusing to consolidate — an "
                "absent cache silently suppresses nothing. Refresh it from the live "
                "provider list (saleshandy list_dnc_lists -> get_dnc_items) first."
            )
        return DncSuppression()

    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):  # legacy bare-list cache
        payload: dict = {"emails": raw, "domains": [], "fetched_at": ""}
    else:
        payload = raw

    dnc = DncSuppression(
        emails={e.strip().lower() for e in payload.get("emails") or [] if str(e).strip()},
        domains={
            d.strip().lower().removeprefix("www.")
            for d in payload.get("domains") or []
            if str(d).strip()
        },
        fetched_at=_parse_fetched_at(payload.get("fetched_at") or ""),
    )

    if not require:
        return dnc
    if not dnc:
        raise ValueError(
            f"DNC suppression cache is empty: {path}. Refusing to consolidate — refresh "
            "it from the live provider list, or confirm the list really is empty."
        )
    if dnc.fetched_at is None:
        raise ValueError(
            f"DNC suppression cache has no usable `fetched_at`: {path}. Refusing to "
            "consolidate — an undatable cache cannot be shown to be current."
        )
    age_h = (datetime.now(UTC) - dnc.fetched_at).total_seconds() / 3600
    if age_h > max_age_hours:
        raise ValueError(
            f"DNC suppression cache is stale: {path} fetched {age_h:.1f}h ago "
            f"(max {max_age_hours}h). Refusing to consolidate against stale suppression "
            "data — refresh it from the live provider list first."
        )
    return dnc


def _load_sent(profile: str, content_root: Path | None) -> tuple[set[str], set[str]]:
    """Everyone already contacted, by ``sequences/*.csv`` rows marked ``SENT`` —
    covers manual sends that predate a formal Saleshandy sequence/DNC entry.
    Returns ``(emails, person_keys)``: the person keys catch the same human
    re-resolved under a second address (the email set alone can't)."""
    emails: set[str] = set()
    people: set[str] = set()
    seq_dir = _sequences_dir(profile, content_root)
    for path in sorted(seq_dir.glob("*.csv")):
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or "status" not in reader.fieldnames:
                continue
            for row in reader:
                if (row.get("status") or "").strip().upper() != "SENT":
                    continue
                email = (row.get("email") or "").strip().lower()
                if email:
                    emails.add(email)
                pk = _person_key(_row_to_record(row, path.name))
                if pk:
                    people.add(pk)
    return emails, people
