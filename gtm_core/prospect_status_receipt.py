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
from dataclasses import field as dc_field

from .account_exclusion_keys import account_hold_reason
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
    "failed_intent": "Being researched",
    "failed_enrichment": "Finding a contact",
    "not_routed": "Not yet sorted",
    "held": "Held",
    "ready": "Sorted",
}
BUCKET_NOTES: dict[str, str] = {
    "total_intake": "every company in the ledger",
    "failed_fit": "closed — no email goes out",
    "failed_intent": "the machine's — it looks for a better reason to write",
    "failed_enrichment": "the machine's — it is still finding someone to write to",
    "not_routed": "nobody here has been sorted yet",
    "held": "a contact is waiting on you, or being fixed",
    "ready": "a contact is on the list — the checks decide if it sends",
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
    #: PS15: of those, the ones already LOADED in the sending tool, by why their account is
    #: closed. The only ones a person must act on — nothing here can unload a sequence — and
    #: the list build removes the rest by itself.
    excluded_loaded: dict[str, int] = dc_field(default_factory=dict)
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


#: Why an account is closed to sending, as a closed set of ids an operator surface can put
#: plain words to (``prospect_lede.RISK_WORDS``). Order is precedence: the first that applies.
FIT_FAILURE_REASONS: tuple[str, ...] = (
    "do-not-contact",
    "disqualified",
    "outside-market",
    "competitor",
    "regulator",
    "ruled-out",
)


def fit_failure_reason(item: dict) -> str | None:
    """Why this ledger account is closed to sending, or ``None`` if it is a fit.

    PS15 (operator decision 2026-09-24): tier C is a fit — it is 50-64 on the 0-100 card, above
    the bottom tier D, and the router sends it the general email. An UNSCORED account is closed
    only when it is outside the target markets; unscored for missing research it is work in
    progress (:func:`_fails_intent`), not a rejection. Both come from
    :func:`~gtm_core.account_exclusion_keys.account_hold_reason`, the rule the send-list build
    and the enrollment gate also use.
    """
    status = _word(item, "status")
    lane_reason = _word(item, "reason") or _word(item, "lane_reason")
    hold = account_hold_reason(item)
    if status == "do-not-contact":
        return "do-not-contact"
    if status in _FIT_FAIL_STATUSES:
        return "disqualified"
    if hold == "outside-market":
        return "outside-market"
    if _word(item, "category_relation") in ("competitor", "regulator"):
        return _word(item, "category_relation")
    if (
        hold == "drop"
        or _word(item, "tier").upper() == "DROP"
        or item.get("fit") is False
        or (_word(item, "lane") == "excluded" and lane_reason != "already-enrolled")
    ):
        return "ruled-out"
    return None


def _fails_fit(item: dict) -> bool:
    return fit_failure_reason(item) is not None


def _fails_intent(item: dict) -> bool:
    return (
        account_hold_reason(item) == "needs-research"
        or _word(item, "verdict") == "re-angle"
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


#: How far a contact has got, lowest first (PS15). An account's word is the FURTHEST stage any
#: of its contacts reached — the rule the three set tests this replaced encoded implicitly,
#: now stated once and built from the same three sets. A status outside them (an unrecognised
#: reason, or anything a later build adds) ranks above "closed" and below "held": it cannot
#: make an account look finished, and it cannot hide a contact that is waiting on someone.
_CONTACT_STAGE: dict[str, int] = {
    **dict.fromkeys(CLOSED_CONTACT, 0),
    **dict.fromkeys(HELD_CONTACT, 2),
    **dict.fromkeys(READY_CONTACT, 3),
}
_UNKNOWN_STAGE = 1
_STAGE_BUCKET: dict[int, tuple[str, str]] = {
    0: ("failed_fit", "closed"),
    1: ("not_routed", "unrecognised"),
    2: ("held", "held"),
    3: ("ready", "ready"),
}


def _routed_bucket(statuses: set[str]) -> tuple[str, str]:
    """``(bucket, kind)`` for an account at least one routed contact reached: the bucket of
    the furthest stage among its contacts."""
    furthest = max((_CONTACT_STAGE.get(s, _UNKNOWN_STAGE) for s in statuses), default=0)
    return _STAGE_BUCKET[furthest]


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
    loaded: Counter[str] = Counter()
    for pos, item in enumerate(rows):
        reason = fit_failure_reason(item)
        if pos in reached and reason:
            # The ledger's exclusion outranks the routed state; what is still listed is said.
            bucket = "failed_fit"
            kinds["excluded_listed"] += sum(1 for s in reached[pos] if s in live)
            loaded[reason] += sum(1 for s in reached[pos] if s == "in_sending_tool")
        elif pos in reached and account_hold_reason(item) == "needs-research":
            # Leaves the list at the next build and rejoins once researched: the machine's.
            bucket = "failed_intent"
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
        excluded_loaded={k: v for k, v in loaded.items() if v},
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

    Each line ends with what the discrepancy does to SENDING, and that sentence must be true
    of the gate as written (PS15), never a reassurance. An unmatched contact is not refused (the
    account-status join finds no objection). A contact on a "not a fit" account is NOT
    necessarily refused either: this module's fit test (``_fails_fit``) is broader than the
    gate's (``enrollment_gate.BLOCKED_ACCOUNT_STATUSES``) — tier C and unscored accounts fail
    fit here and pass the gate. On the first live run all 99 such contacts were tier C or
    unscored and the gate would have admitted every one; the line said the opposite until it
    was measured. So it now states the gate's actual rule rather than a promise.
    """
    out: list[str] = []
    table_total = sum(contact_counts.values())
    if table_total != routed_records:
        out.append(
            f"Check: the contact table adds up to {table_total}, but {routed_records} "
            f"contact(s) were routed. Only these counts are affected, not what the checks "
            f"let through."
        )
    if not receipt.from_routed_state:
        return out
    if receipt.unmatched_contacts:
        n = receipt.unmatched_contacts
        out.append(
            f"Check: {n} routed contact{'s' if n != 1 else ''} match{'es' if n == 1 else ''} "
            f"no ledger account — the account lines above leave "
            f"{'it' if n == 1 else 'them'} out. {'It' if n == 1 else 'They'} can still be "
            f"sent; only the account count misses {'it' if n == 1 else 'them'}."
        )
    if receipt.excluded_listed_contacts:
        n = receipt.excluded_listed_contacts
        out.append(
            f"Check: {n} contact{' is' if n == 1 else 's are'} on the list for an account marked "
            f"{BUCKET_LABELS['failed_fit'].lower()} — nothing should go to "
            f"{'that person' if n == 1 else 'those people'}; take "
            f"{'that person' if n == 1 else 'them'} off the list, or correct the account. "
            f"The next list build removes those at do-not-contact, disqualified, out-of-market "
            f"or not-yet-researched companies; anyone already loaded in the sending tool is "
            f"named at the top, because only a person can take them out there."
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
