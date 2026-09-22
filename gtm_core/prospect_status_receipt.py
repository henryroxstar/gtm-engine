"""Where each ACCOUNT stands — the account-level half of ``prospects status``. No I/O.

The status block prints two things about one pipeline: a table of CONTACTS (people, one per
routed record — :mod:`gtm_core.prospect_status`) and this receipt of ACCOUNTS (companies, one
per ledger row). On 2026-09-21 they were derived from two unrelated sources and contradicted
each other in front of the operator — "ACTION REQUIRED: 705 accounts" above "Waiting on you
102", ``[Ready] 14`` above "Ready to send 275" — because every ledger account at
``status: new`` was counted as *held* and any account with an email as *ready*.

So held and ready are now derived from the SAME routed state as the contact table, joined to
the ledger:

* an account whose LEDGER row fails fit (a retired status, a refusal, a competitor or
  regulator) is **not a fit / excluded** whatever was routed for it — and a contact still on
  the list for one is a discrepancy :func:`cross_check` reports, never a "Ready";
* else it is **ready** iff at least one of its routed contacts is ready to send (or already
  in the sending tool);
* else **held** iff at least one is waiting on the operator or being fixed;
* an account no routed contact reaches is **not yet routed** — never "held". A ledger
  ``status: new`` alone says nothing about whose move it is.

Account identity is the ledger's own (``prospects_state._identity_key``: domain, else id,
else the exact company name, else the stamped account id) — reused, not re-spelled. The
company-name slug this module used to key on merged distinct ledger accounts that happen to
share a name.

This module must not import :mod:`gtm_core.prospect_status` (its importers are an
allowlist — ``tests/contracts/test_prospect_status_boundary.py``); callers hand it contacts
whose six-way ``status`` they have already derived.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass

from .prospects_state import _identity_key, _identity_keys

#: Contact statuses that make their account ready / held (``prospect_status.STATUSES`` ids).
READY_CONTACT: frozenset[str] = frozenset({"ready_to_send", "in_sending_tool"})
HELD_CONTACT: frozenset[str] = frozenset({"waiting_on_you", "being_fixed"})
CLOSED_CONTACT: frozenset[str] = frozenset({"not_emailing"})

#: Receipt buckets in the order they print, with the operator's words for each. The keys are
#: a stable API (the dashboard reads them); only the LABELS were renamed on 2026-09-21 —
#: "Failed Intent" read as a rejection when the skill defines that verdict as work QUEUED.
BUCKETS: tuple[str, ...] = (
    "failed_fit",
    "failed_intent",
    "failed_enrichment",
    "not_routed",
    "held",
    "ready",
)
BUCKET_LABELS: dict[str, str] = {
    "total_intake": "All accounts",
    "failed_fit": "Not a fit / excluded",
    "failed_intent": "Needs a new angle (queued)",
    "failed_enrichment": "No usable contact yet",
    "not_routed": "Not yet routed",
    "held": "Held",
    "ready": "Ready",
}
BUCKET_NOTES: dict[str, str] = {
    "total_intake": "every company in the ledger",
    "failed_fit": "closed — no email goes out",
    "failed_intent": "the machine's — it looks for a better reason to write",
    "failed_enrichment": "the machine's — it is still finding someone to write to",
    "not_routed": "nobody here has been sorted yet",
    "held": "a contact is waiting on you, or being fixed",
    "ready": "at least one contact is ready to send",
}
ACCOUNTS_HEADING = "Accounts — where each stands"

#: Placeholder tokens dirty exports put where an email should be. Pinned equal to
#: ``prospect_status._PSEUDO_VALUES`` by ``tests/unit/test_prospect_status_receipt.py``.
_PSEUDO_VALUES: frozenset[str] = frozenset(
    {"", "none", "null", "n/a", "undefined", "unknown", "unverified"}
)

_FIT_FAIL_STATUSES = frozenset(
    {"disqualified", "off-icp", "failed_fit", "closed", "closed-lost", "do-not-contact"}
)
_INTENT_FAIL_STATUSES = frozenset({"no-intent", "failed_intent"})
_ENRICHMENT_FAIL_STATUSES = frozenset(
    {"contact-defective", "no-contact", "unverified", "failed_enrichment", "enrichment_miss"}
)
#: Ledger words that say held/ready on their own. Read ONLY when the caller supplied no routed
#: state at all. ``new`` is deliberately absent: it is every account's first status.
_HELD_HINT_STATUSES = frozenset({"held", "waiting_on_you", "review", "being_fixed"})
_READY_HINT_STATUSES = frozenset(
    {"ready", "ready_to_send", "contact-resolved", "active", "in_sending_tool"}
)
_STAGES = frozenset({*BUCKETS, "enrichment_miss"})


@dataclass
class AttritionReceipt:
    """How many accounts stand where. ``total_intake`` is the sum of the six buckets."""

    total_intake: int
    failed_fit: int
    failed_intent: int
    failed_enrichment: int
    held: int
    ready: int
    not_routed: int = 0
    #: Routed mode only. ``routed_accounts`` — distinct accounts a contact reached; the two
    #: below are the reached, in-fit accounts that did not land in held/ready.
    #: ``unmatched_contacts`` reached no ledger account at all. ``excluded_listed_contacts``
    #: — contacts ready or waiting on the list whose ACCOUNT the ledger marks not a fit.
    routed_accounts: int = 0
    routed_closed: int = 0
    routed_unrecognised: int = 0
    unmatched_contacts: int = 0
    excluded_listed_contacts: int = 0
    #: False when the caller supplied no routed state (held/ready came from ledger hints).
    from_routed_state: bool = False

    @property
    def rejected(self) -> int:
        return self.failed_fit + self.failed_intent + self.failed_enrichment

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def verify_funnel_conservation(receipt: AttritionReceipt | dict[str, int]) -> bool:
    """Every account is in exactly one bucket: ``total_intake`` == the sum of the six.

    A sanity assert on the arithmetic, raised as ``ValueError``. It cannot see a wrong
    classification — :func:`cross_check` is the check that reads a second source.
    """
    data = receipt if isinstance(receipt, dict) else receipt.to_dict()
    total = data.get("total_intake", 0)
    parts = {b: data.get(b, 0) for b in BUCKETS}
    if total != sum(parts.values()):
        detail = " + ".join(f"{BUCKET_LABELS[b]} ({n})" for b, n in parts.items())
        raise ValueError(
            f"Funnel conservation violated: {BUCKET_LABELS['total_intake']} ({total}) != "
            f"{detail} [Sum = {sum(parts.values())}]"
        )
    return True


def _usable_email(item: dict) -> bool:
    raw = str(item.get("contact_email") or item.get("email") or "").strip().lower()
    return bool(raw) and raw not in _PSEUDO_VALUES and "@" in raw


def _word(item: dict, key: str) -> str:
    return str(item.get(key) or "").strip().lower()


def _fails_fit(item: dict) -> bool:
    lane_reason = _word(item, "reason") or _word(item, "lane_reason")
    return (
        _word(item, "verdict") == "drop"
        or _word(item, "category_relation") in ("competitor", "regulator")
        or _word(item, "status") in _FIT_FAIL_STATUSES
        # "UNSCORED" joined this list on 2026-09-22 with gtm_core.scorecard. It is NOT a weak
        # tier — it means an input was missing, so no rubric ever ran. Either way the row is not
        # ready to move: before this it passed the fit gate outright and kept going toward
        # enrolment, which is a row reaching a recipient on research nobody did.
        or _word(item, "tier").upper() in ("C", "DROP", "UNSCORED")
        or item.get("fit") is False
        or (_word(item, "lane") == "excluded" and lane_reason != "already-enrolled")
    )


def _fails_intent(item: dict) -> bool:
    return (
        _word(item, "verdict") == "re-angle"
        or _word(item, "status") in _INTENT_FAIL_STATUSES
        or item.get("intent") is False
    )


def _fails_enrichment(item: dict) -> bool:
    reason = _word(item, "reason") or _word(item, "lane_reason") or _word(item, "verdict_reason")
    return (
        not _usable_email(item)
        or _word(item, "email_status") == "unverified"
        or _word(item, "status") in _ENRICHMENT_FAIL_STATUSES
        or reason in ("needs-verification", "unverified")
    )


def _ledger_bucket(item: dict, *, hints: bool) -> str:
    """The bucket an account earns from its LEDGER row alone (no routed contact reached it).

    ``hints`` — read the row's own ``stage``/``lane``/``status`` as held/ready. Only when the
    caller had no routed state to give; with routed state on hand, an account nothing was
    routed for is "not yet routed" however its ledger row is worded.
    """
    stage = _word(item, "stage")
    if hints and stage in _STAGES:
        return "failed_enrichment" if stage == "enrichment_miss" else stage
    if _fails_fit(item):
        return "failed_fit"
    if _fails_intent(item):
        return "failed_intent"
    lane, status = _word(item, "lane"), _word(item, "status")
    if hints and (lane in ("hold", "repair") or status in _HELD_HINT_STATUSES):
        return "held"
    if _fails_enrichment(item):
        return "failed_enrichment"
    if hints and (lane in ("personalised", "generic") or status in _READY_HINT_STATUSES):
        return "ready"
    return "not_routed"


def _routed_bucket(statuses: set[str]) -> tuple[str, str]:
    """``(bucket, kind)`` for an account at least one routed contact reached."""
    if statuses & READY_CONTACT:
        return "ready", "ready"
    if statuses & HELD_CONTACT:
        return "held", "held"
    if statuses <= CLOSED_CONTACT:
        return "failed_fit", "closed"
    return "not_routed", "unrecognised"


def dedupe_accounts(accounts: Iterable[dict]) -> list[dict]:
    """One row per ledger identity (a later row's fields win). A row with no identity at all
    is its own account — never merged with another identity-less row.

    A retired ``status`` is STICKY: if any duplicate row carries one, the account keeps it. A
    later duplicate at ``status: new`` used to overwrite ``do-not-contact`` and the exclusion
    vanished from the block.
    """
    by_key: dict[str, dict] = {}
    for n, item in enumerate(accounts):
        if not isinstance(item, dict):
            continue
        key = _identity_key(item) or f"anon:{n}"
        prior = by_key.get(key, {})
        merged = {**prior, **item}
        if _word(prior, "status") in _FIT_FAIL_STATUSES:
            if _word(item, "status") not in _FIT_FAIL_STATUSES:
                merged["status"] = prior["status"]
        by_key[key] = merged
    return list(by_key.values())


def _domain(item: dict) -> str:
    return str(item.get("domain") or item.get("company_domain") or "").strip().lower()


class _AccountIndex:
    """Ledger accounts, reachable by contact email and by every identity key they carry."""

    def __init__(self, accounts: Sequence[dict]) -> None:
        self.accounts = accounts
        self.by_email: dict[str, int] = {}
        self.by_key: dict[str, list[int]] = {}
        for pos, item in enumerate(accounts):
            for field in ("contact_email", "email"):
                email = str(item.get(field) or "").strip().lower()
                if email:
                    self.by_email.setdefault(email, pos)
            for key in _identity_keys(item):
                self.by_key.setdefault(key, []).append(pos)

    def find(self, contact: dict) -> int | None:
        """The account a routed contact belongs to: its address as the ledger holds it, else
        the ledger's identity keys in the ledger's own order. A company-NAME match is refused
        when both sides name a domain and the domains differ, and when it is ambiguous — the
        same never-guess rule ``prospects_merge.AccountMatcher.find`` applies."""
        email = str(contact.get("email") or "").strip().lower()
        if email in self.by_email:
            return self.by_email[email]
        probe = {
            "domain": _domain(contact),
            "company": contact.get("company"),
            "account_id": contact.get("account_id"),
        }
        for key in _identity_keys(probe):
            hits = self.by_key.get(key, [])
            if key.startswith("c:"):
                hits = [
                    p
                    for p in hits
                    if not (
                        probe["domain"]
                        and _domain(self.accounts[p])
                        and _domain(self.accounts[p]) != probe["domain"]
                    )
                ]
                if len(hits) != 1:
                    continue
            if hits:
                return hits[0]
        return None


def compute_attrition_receipt(
    accounts: Iterable[dict], routed: Iterable[dict] | None = None
) -> AttritionReceipt:
    """The account receipt for ``accounts`` (ledger rows), given the ``routed`` contacts.

    ``routed`` — one dict per routed record: ``email``, ``company``, ``company_domain``,
    ``account_id`` (whichever it has) plus ``status``, the six-way contact status the caller
    derived (anything outside the six counts as unrecognised). ``None`` means the caller has
    no routed state; held/ready then fall back to the ledger row's own wording.
    """
    rows = dedupe_accounts(accounts)
    reached: dict[int, list[str]] = {}
    unmatched = 0
    if routed is not None:
        index = _AccountIndex(rows)
        for contact in routed:
            pos = index.find(contact) if isinstance(contact, dict) else None
            if pos is None:
                unmatched += 1
            else:
                reached.setdefault(pos, []).append(str(contact.get("status") or ""))

    buckets: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    live = READY_CONTACT | HELD_CONTACT
    for pos, item in enumerate(rows):
        if pos in reached and _fails_fit(item):
            # The ledger's exclusion outranks the routed state; what is still listed is said.
            bucket = "failed_fit"
            kinds["excluded_listed"] += sum(1 for s in reached[pos] if s in live)
        elif pos in reached:
            bucket, kind = _routed_bucket(set(reached[pos]))
            kinds[kind] += 1
        else:
            bucket = _ledger_bucket(item, hints=routed is None)
        buckets[bucket] += 1

    receipt = AttritionReceipt(
        total_intake=len(rows),
        **{b: buckets[b] for b in BUCKETS},
        routed_accounts=len(reached),
        routed_closed=kinds["closed"],
        routed_unrecognised=kinds["unrecognised"],
        unmatched_contacts=unmatched,
        excluded_listed_contacts=kinds["excluded_listed"],
        from_routed_state=routed is not None,
    )
    verify_funnel_conservation(receipt)
    return receipt


def cross_check(
    receipt: AttritionReceipt, contact_counts: dict[str, int], routed_records: int
) -> list[str]:
    """Lines to print when the two halves of the block cannot be reconciled. Empty = clean.

    Unlike :func:`verify_funnel_conservation` each line compares two things that can really
    disagree: the contact table against the number of routed records; routed contacts against
    the ledger accounts they should join; and the ledger's exclusions against what is still on
    the list. An "accounts placed" comparison used to sit here too — ready + held + closed +
    unrecognised against the accounts reached — and was deleted: both sides were counted in
    one loop, so it could not fail (§R18).
    """
    out: list[str] = []
    table_total = sum(contact_counts.values())
    if table_total != routed_records:
        out.append(
            f"Check: the contact table adds up to {table_total}, but {routed_records} "
            f"contact(s) were routed."
        )
    if not receipt.from_routed_state:
        return out
    if receipt.unmatched_contacts:
        n = receipt.unmatched_contacts
        out.append(
            f"Check: {n} routed contact{'s' if n != 1 else ''} match{'es' if n == 1 else ''} "
            f"no ledger account — the account lines above leave "
            f"{'it' if n == 1 else 'them'} out."
        )
    if receipt.excluded_listed_contacts:
        n = receipt.excluded_listed_contacts
        out.append(
            f"Check: {n} contact{' is' if n == 1 else 's are'} on the list for an account marked "
            f"{BUCKET_LABELS['failed_fit'].lower()} — nothing should go to "
            f"{'that person' if n == 1 else 'those people'}; take "
            f"{'that person' if n == 1 else 'them'} off the list, or correct the account."
        )
    return out


def format_attrition_receipt(receipt: AttritionReceipt) -> str:
    """The accounts block: one labelled line per bucket, then the total they add up to."""
    width = max(len(label) for label in BUCKET_LABELS.values())
    digits = len(str(receipt.total_intake))
    data = receipt.to_dict()
    lines = [f"{ACCOUNTS_HEADING} (companies, not people):"]
    lines += [
        f"  {BUCKET_LABELS[b]:<{width}}  {data[b]:>{digits}}   {BUCKET_NOTES[b]}" for b in BUCKETS
    ]
    lines.append(f"  {'':<{width}}  {'─' * digits}")
    lines.append(
        f"  {BUCKET_LABELS['total_intake']:<{width}}  {receipt.total_intake:>{digits}}   "
        f"{BUCKET_NOTES['total_intake']}"
    )
    return "\n".join(lines)
