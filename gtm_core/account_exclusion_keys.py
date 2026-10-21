"""The keys an account-level EXCLUSION joins on — one implementation, two callers.

``prospects_consolidate`` (which builds ``ready-to-load.csv``) and ``enrollment_gate`` (which
refuses a list at enrollment) both ask the same question of a contact row: *does this row belong
to an account the ledger has retired?* They used to answer it with two hand-rolled key lists, and
both joined only on the exact ``d:``/``i:``/``c:``/``a:`` keys of
:func:`gtm_core.prospects_state._identity_keys`. A company-NAME VARIANT walked through both: the
ledger held a do-not-contact account under its registered name with a legal suffix and its
domain, a later export named the same account without the suffix and with no domain column, and
neither ``c:`` key nor ``d:`` key met. The contact was listed as ready to load, and the gate's
account-status check raised no objection to it.

Two keys close that, and both stay EXACT — a whole normalised value, never a substring:

* ``d:<domain of the contact's own email>``. The address being mailed is at the retired account's
  domain whatever the company column says. Free-mail domains are skipped: a shared mailbox
  provider identifies nobody's employer.
* ``n:<company name minus trailing legal-form tokens>``, offered by BOTH sides. Only legal forms
  are stripped (:data:`LEGAL_FORM_TOKENS`). Words such as ``group``, ``holdings``, ``company`` and
  ``co`` are NOT: two firms sharing a first word and differing in one of those are different
  companies, and retiring one must never exclude the other. (``prospects_state._norm`` strips
  those words too, which is why it is not reused here.)

This module widens the EXCLUSION join only. Account identity for merge and for ``account_id``
stamping stays with ``_identity_keys``: excluding one row too many is the cheap way to be wrong,
merging two companies is not.

Stdlib + two sibling imports, no I/O.
"""

from __future__ import annotations

import re

from .merge_hygiene import _FREEMAIL
from .prospects_state import ACCOUNT_ID_FIELD, _identity_keys
from .slugify import slug as _slug

#: Trailing tokens that name a legal FORM rather than the company. A closed list on purpose:
#: every word added here makes two differently-named ledger accounts exclude each other.
LEGAL_FORM_TOKENS: frozenset[str] = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "plc",
        "pte",
        "gmbh",
        "ag",
        "sa",
        "bv",
        "pty",
    }
)

_NON_WORD_RE = re.compile(r"[^\w]+")


def legal_form_key(company: object) -> str:
    """``company`` lowercased, punctuation-free, with trailing legal-form tokens removed.

    Dots are deleted rather than turned into spaces so a dotted abbreviation collapses to the
    token it spells before the comparison. A name that is nothing BUT legal-form tokens keeps its
    first token: a key is never stripped down to empty.
    """
    text = str(company or "").casefold().replace(".", "")
    tokens = _NON_WORD_RE.sub(" ", text).split()
    while len(tokens) > 1 and tokens[-1] in LEGAL_FORM_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def email_domain_key(email: object) -> str:
    """``d:<domain>`` for a corporate address; ``""`` for a free-mail or malformed one."""
    _local, at, domain = str(email or "").strip().lower().rpartition("@")
    domain = domain.strip().rstrip(".")
    if not at or "." not in domain or domain in _FREEMAIL:
        return ""
    return f"d:{domain}"


def _dedup(keys: list[str]) -> list[str]:
    return list(dict.fromkeys(k for k in keys if k))


def account_is_dropped(item: dict) -> bool:
    """True when the ledger's own row for this account carries ``verdict: drop``.

    An exclusion axis of its own, beside lifecycle ``status``. A row that CAN be tied to its
    account by an exact key inherits that ``drop`` and is refused by every lane's verdict filter
    — but a row that cannot carries a BLANK verdict, and the generic lane admits blank
    (:data:`gtm_core.lane_verdicts.LANE_VERDICTS`). Reproduced 2026-09-21: a ledger account
    dropped as a direct competitor, a pooled row naming it without its legal suffix and with no
    domain, tier B — routed to generic and the gate printed PASS.
    """
    return str(item.get("verdict") or "").strip().lower() == "drop"


#: Scorecard inputs whose absence CLOSES an account to sending (a hard block), as opposed to
#: every other missing input, which only means the research is not done yet. The tenant's
#: scorecard names this category "Blocked — outside target markets"; the id is matched, never
#: the display text, because the text is tenant-authored.
MARKET_BLOCK_INPUTS: frozenset[str] = frozenset({"in_target_market"})

#: The reasons :func:`account_hold_reason` can return — a closed set, so a reader that maps a
#: reason to words cannot meet one it has no words for.
HOLD_REASONS: frozenset[str] = frozenset({"drop", "outside-market", "needs-research"})


def _missing_inputs(item: dict) -> set[str]:
    raw = item.get("score_missing_inputs")
    found = {str(x).strip() for x in raw} if isinstance(raw, list) else set()
    one = str(item.get("score_missing_input") or "").strip()
    return found | ({one} if one else set())


def account_hold_reason(item: dict) -> str | None:
    """Why the ledger keeps this account OFF the send list today, or ``None`` (PS15).

    One rule, read by the send-list build (``prospects_consolidate``) and the enrollment gate
    alike, so the list and the gate cannot disagree about who may be emailed:

    * ``drop`` — the research verdict dropped the account (:func:`account_is_dropped`);
    * ``outside-market`` — the scorecard could not place it in a target market. A hard block;
    * ``needs-research`` — the scorecard could not score it for any other missing input. Not a
      rejection: the account rejoins the list by itself once it is researched and rescored.
      An unscored row with NO recorded missing input is held here too — fail closed.

    Tier C is deliberately absent: on the 0-100 card it is a middling fit (50-64), above the
    bottom tier D, and it gets the general email like any other fit (operator decision
    2026-09-24). The status page used to call C "not a fit" while the router and gate sent it.
    """
    if account_is_dropped(item):
        return "drop"
    if str(item.get("tier") or "").strip().lower() != "unscored":
        return None
    return "outside-market" if _missing_inputs(item) & MARKET_BLOCK_INPUTS else "needs-research"


def ledger_account_keys(item: dict) -> list[str]:
    """Every key a ``latest.json`` account is excluded under: its identity keys + ``n:``.

    Deliberately NOT the domain of the item's ``contact_email``: a provider's recommended address
    can belong to a contact's former employer, and retiring one account must not retire another
    company's whole domain.
    """
    name = legal_form_key(item.get("company"))
    return _dedup([*_identity_keys(item), f"n:{name}" if name else ""])


def row_account_keys(row: dict) -> list[str]:
    """Every key a contact ROW offers to the exclusion join (pooled CSV row or enrollment row).

    The row's own account fields in ``latest.json``'s key space, the account slug the ledger
    stores as ``id`` (``prospects_import`` stamps ``id = slug(company)``; a row carries none of
    its own), the legal-form-normalised name, and the domain of the contact's own email.
    """
    company = row.get("company") or ""
    item = {
        "domain": row.get("company_domain") or row.get("domain") or "",
        "id": row.get("id") or "",
        "company": company,
        ACCOUNT_ID_FIELD: row.get(ACCOUNT_ID_FIELD) or "",
    }
    slug_id = _slug(str(company)) if str(company).strip() else ""
    name = legal_form_key(company)
    return _dedup(
        [
            *_identity_keys(item),
            f"i:{slug_id.lower()}" if slug_id else "",
            f"n:{name}" if name else "",
            email_domain_key(row.get("email") or row.get("contact_email")),
        ]
    )
