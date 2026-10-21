"""The vocabulary, containers and suppression map behind the outreach log's person rows.

Split from :mod:`gtm_core.outreach_people` (the builder) to keep each file small. Nothing here
reads a file; it is the closed lists, the shapes the builder fills, and the one place that
decides whether a ledger row means "do not contact".

Two things the log keeps apart on purpose, because mixing them was the misleading part of the
first design: what is known about the SEQUENCE (``sequence_state`` — sending, paused, not
started …) and what is known about the PERSON (``emailed`` — yes, no, don't know). Saleshandy
gives only counts per sequence, never names, so for most people in a part-sent sequence
``emailed`` is ``unknown`` by construction: ``yes`` only for people in a closed-out list or
marked SENT, ``no`` only when the provider's own counter, from a row that is not stale, is
present and zero.

Every uncertain input falls toward the cautious value: a missing flag, a missing counter or an
old row reads *Status unknown*; a suppression reason outside the closed three-reason
bookkeeping set reads ``suppressed``. The page package is imported only inside functions —
``prospects_dashboard`` imports :mod:`gtm_core.outreach_log`, so a top-level import is a cycle.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

from .outreach_findings import Finding
from .outreach_touches import COLUMNS as TOUCH_COLUMNS

# ── the closed vocabularies ───────────────────────────────────────────────────────────────

KIND_PACK, KIND_SEQUENCE, KIND_CONTACTED = "pack", "sequence", "contacted"

DRAFTED = "drafted"
IN_SEQUENCE = "in-sequence"
CLOSED_OUT = "closed-out"
SUPPRESSED = "suppressed"
DELETED_NO_SENDS = "sequence-deleted-no-sends"
DELETED_SENDS = "sequence-deleted-sends-unrecorded"
UNKNOWN = "unknown"
PERSON_STATUSES = (
    DRAFTED,
    IN_SEQUENCE,
    CLOSED_OUT,
    SUPPRESSED,
    DELETED_NO_SENDS,
    DELETED_SENDS,
    UNKNOWN,
)

YES, NO, DONT_KNOW = "yes", "no", "unknown"
EMAILED_VALUES = (YES, NO, DONT_KNOW)

S_ACTIVE, S_PAUSED, S_STAGED = "active", "paused", "staged"
S_UNKNOWN, S_DELETED, S_ABSENT = "unknown", "deleted", "not-in-snapshot"
SEQUENCE_STATES = (S_ACTIVE, S_PAUSED, S_STAGED, S_UNKNOWN, S_DELETED, S_ABSENT)

#: The ONLY suppression reasons that leave a person's status alone: they are the ledger's own
#: bookkeeping ("this person is already loaded in another sequence", "already emailed"), not a
#: decision not to contact. Every other reason — including free text and any word nobody
#: anticipated — reads ``suppressed``. The set fails toward ``suppressed`` on purpose.
BOOKKEEPING_REASONS = frozenset(
    {"cross-sequence-overlap", "contacted-no-reply", "contacted-bounced"}
)

COLUMNS = (
    "kind",
    "account",
    "persona_name",
    "email_address",
    "person_status",
    "emailed",
    "sequence_state",
    "sequence_name",
    "progress",
    "outcome",
    "suppression_reason",
    "draft_date",
    "tier",
    "score",
    "persona_title",
    "email_verified",
    "channels",
    "email_subject",
    "followup_touches_in_ladder",
    "file",
    "sequence_id",
    "lane",
    *TOUCH_COLUMNS,
)

#: A sequence in one of these states is one the operator is relying on: missing email text for
#: its people is a defect worth a non-zero exit. A deleted or unmeasured sequence's is not.
_ATTENTION_STATES = frozenset({S_ACTIVE, S_PAUSED, S_STAGED})


@lru_cache(maxsize=1)
def state_words() -> dict[str, str]:
    """The page's own words for a sequence's state, minus the emoji — read from the page's
    vocabulary, not retyped, so the two cannot drift."""
    from .prospect_lede import GO_LIVE_WORDS

    def plain(key: str) -> str:
        return re.sub(r"^[^A-Za-z0-9]+", "", GO_LIVE_WORDS[key])

    return {
        S_ACTIVE: plain("active"),
        S_PAUSED: plain("paused"),
        S_STAGED: plain("staged"),
        S_UNKNOWN: plain("unknown"),
        S_DELETED: "Deleted",
        S_ABSENT: "Not in the latest Saleshandy figures",
    }


@lru_cache(maxsize=1)
def _age_tools():
    from .email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS
    from .email_campaign_dashboard.health import _figures_age_exact_days

    return _figures_age_exact_days, FIGURES_MAX_AGE_DAYS


def is_stale(stamp: str | None, now: datetime) -> bool:
    """Older than the page's own figures-age limit, or a date nobody can read."""
    age_fn, limit = _age_tools()
    age = age_fn(stamp, now)
    return age is None or age > limit


# ── containers ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SeqFacts:
    id: str
    name: str
    lane: str
    campaign: str
    spec: str
    state: str
    registered: bool
    deleted: bool
    has_row: bool
    provider_total: int | None
    emailed_shown: int | None  # the counter as the figures hold it (may be old)
    emailed_trusted: int | None  # the counter ONLY when its row is present and not stale
    stamp: str | None
    people: int
    closed_out: int
    missing_lists: tuple[str, ...] = ()


@dataclass
class Member:
    sid: str
    email: str
    row: dict
    reg_csv: str | None  # the registered list the row came from
    spec: str | None
    reg_index: int | None
    via: set = field(default_factory=set)


@dataclass
class View:
    rows: list[dict]
    sequences: list[SeqFacts]
    findings: list[Finding]
    not_checkable: int
    notes: list[str]
    problems: list[str]  # email-text defects in sequences the operator relies on (exit 1)
    snapshot_present: bool
    snapshot_fetched: str | None
    people: int
    suppressed_by_reason: Counter
    first_sid: dict[str, str]  # email -> the sequence load_enrolled would credit them to
    state_by_sid: dict[str, str]

    def sending_split(self, emails) -> dict[str, int]:
        """The page's three buckets for these people, from THIS view: sending / paused (both
        *Paused* and *Loaded, not started*) / unknown. Same shape as ``delivery.sending_split``."""
        out: Counter = Counter()
        for email in emails:
            st = self.state_by_sid.get(self.first_sid.get(email, ""), S_UNKNOWN)
            out[
                "sending"
                if st == S_ACTIVE
                else "paused"
                if st in (S_PAUSED, S_STAGED)
                else "unknown"
            ] += 1
        return dict(out)


# ── suppression: every row, fail toward suppressed ────────────────────────────────────────


class SuppressionMap:
    def __init__(self, entries):
        from .suppression import COMPANY_REASONS, _norm_domain, email_key

        self._norm = _norm_domain
        self._by_email: dict[str, list] = defaultdict(list)
        self._by_person: dict[str, list] = defaultdict(list)
        self._by_domain: dict[str, list] = defaultdict(list)
        for e in entries:
            if k := email_key(e.email):
                self._by_email[k].append(e)
            if k := e.person:
                self._by_person[k].append(e)
            d = _norm_domain(e.company_domain)
            if d and (e.reason in COMPANY_REASONS or (not e.email and not e.name)):
                self._by_domain[d].append(e)
        self._email_key = email_key

    def verdict(self, email: str, name: str = "", domain: str = "") -> tuple[bool, str]:
        """``(suppressed?, the reason to show)`` across EVERY ledger row that covers the person
        by person key, company domain (and parents) or address."""
        from .suppression import row_person_key

        domain = domain or (email.split("@", 1)[1] if "@" in email else "")
        hits: list = []
        if pk := row_person_key({"name": name, "company_domain": domain}):
            hits += self._by_person.get(pk, [])
        if d := self._norm(domain):
            hits += self._by_domain.get(d, [])
            parts = d.split(".")
            for i in range(1, len(parts) - 1):
                hits += self._by_domain.get(".".join(parts[i:]), [])
        hits += self._by_email.get(self._email_key(email), [])
        if not hits:
            return False, ""
        for h in hits:
            if h.reason not in BOOKKEEPING_REASONS:
                return True, h.reason
        return False, hits[0].reason
