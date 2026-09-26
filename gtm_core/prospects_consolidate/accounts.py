from __future__ import annotations

import datetime
from pathlib import Path

from ..account_exclusion_keys import (
    account_hold_reason,
    ledger_account_keys,
    row_account_keys,
)
from ..prospects_state import (
    ACCOUNT_ID_FIELD,
    RETIRED_STATUSES,
    _identity_key,
    _identity_keys,
    latest_path,
    load_latest,
)
from ..signal_record import RECORD_COLUMNS, SIGNAL_COLUMN, SIGNAL_RECORD_COLUMNS


class LedgerUnreadableError(SystemExit):
    """``latest.json`` is PRESENT but cannot be read — the send-list build must not run.

    A :class:`SystemExit` on purpose. ``python -m gtm_core.prospects_consolidate`` then prints
    exactly this one line on stderr and exits 1 with no traceback, and no unattended wrapper's
    broad ``except Exception`` can turn "the do-not-contact ledger could not be read" back into a
    successful-looking build — which is the failure this exists to end.
    """


def _ledger_items(profile: str, content_root: Path | None) -> list[dict]:
    """The ledger's account items, or an ABORT — never a silent "no accounts".

    Every reader below used to answer an unreadable ledger with an empty index. For the id and
    record joins that costs a column; for the retired set it re-admits every do-not-contact
    account: a truncated ``latest.json`` (a write cut short) produced exit 0 and a
    ``ready-to-load.csv`` that listed the retired account's contact. "Could not read it" is not
    "nobody is retired".

    ABSENT stays an empty ledger: that is ``load_latest``'s own contract (absent = empty skeleton,
    present-but-unreadable = an error), and a first run has no ledger yet. What is caught is what
    ``load_latest`` can raise — ``OSError`` (a directory, permissions, I/O) and ``ValueError``
    (``JSONDecodeError`` and ``UnicodeDecodeError`` are both subclasses, plus its own wrong-shape
    errors) — and an item that is not an object is refused the same way rather than left to raise
    an ``AttributeError`` three calls later. Consolidate reads the ledger before it writes
    anything, so an abort leaves every output exactly as it was.
    """
    try:
        items = load_latest(profile, content_root).get("items", [])
        malformed = sum(1 for item in items if not isinstance(item, dict))
        if malformed:
            raise ValueError(f"{malformed} ledger entr(ies) are not JSON objects")
    except (OSError, ValueError) as exc:
        raise LedgerUnreadableError(
            f"ABORTED: account ledger unreadable ({latest_path(profile, content_root)}: "
            f"{type(exc).__name__}: {exc}) — cannot tell which accounts are do-not-contact, "
            "disqualified or closed-lost, so no send list was built and nothing was written; "
            "repair the file or restore it from prospects/.snapshots/, then run consolidate again"
        ) from exc
    return items


def _account_item_of(row: dict) -> dict:
    """The row's account, shaped as a ``latest.json`` item for key derivation."""
    return {
        "domain": (row.get("company_domain") or "").strip(),
        "company": row.get("company") or "",
        ACCOUNT_ID_FIELD: row.get(ACCOUNT_ID_FIELD) or "",
    }


def _account_key_of(row: dict) -> str:
    """The row's account identity, in ``latest.json``'s own key space."""
    return _identity_key(_account_item_of(row))


def _account_keys_of(row: dict) -> list[str]:
    """EVERY ``latest.json`` key the row's account is reachable under — the exclusion join.

    :func:`_account_key_of` answers "what is this account called" with one key, domain-first.
    That is the wrong question for "has this account been retired": the retired set holds every
    key of the ledger item, and the two only meet if the ONE key the row picked is still among
    them. A ledger item whose ``domain`` was blanked by a minimal re-discovery kept its
    ``do-not-contact`` status and stopped matching, because the pooled row still carried the
    domain and so never offered its company name — the contact came back as ready to load.

    So the row offers all of them, including the account slug the ledger stores as ``id``
    (``prospects_import`` stamps ``id = slug(company)``, and a row carries no ``id`` of its own).
    Every comparison stays EXACT on a normalised value — a whole domain, a whole slug, a
    whitespace-collapsed lowercased name — never a substring, so retiring one company cannot
    take out a neighbour whose name merely contains it.

    Since 2026-09-21 the list is :func:`gtm_core.account_exclusion_keys.row_account_keys`, shared
    with the enrollment gate, and carries two more exact keys: the domain of the contact's own
    email and the company name minus its trailing legal form. A name VARIANT of a retired account
    ("... , Inc." in the ledger, the bare name and no domain on the row) met none of the keys
    above and was listed as ready to load.
    """
    return row_account_keys(row)


def _account_id_index(profile: str, content_root: Path | None) -> dict[str, str]:
    """Every ``latest.json`` identity key -> that account's stamped ``account_id``.

    Built from the ledger of record rather than re-derived per row, so a pooled row
    joins to the same account the dashboard shows. ABSENT is an empty index: a first run
    has no accounts to join to, which is not an error. Present-but-unreadable aborts —
    see :func:`_ledger_items`.
    """
    index: dict[str, str] = {}
    for item in _ledger_items(profile, content_root):
        account_id = str(item.get(ACCOUNT_ID_FIELD) or "").strip()
        if not account_id:
            continue
        for key in _identity_keys(item):
            index.setdefault(key, account_id)
    return index


#: The account-level fields a row inherits from its account. The six provenance fields
#: plus the researcher's verdict, plus which matrix signal column the fact belongs to.
#: Deliberately EXCLUDES ``JUDGE_COLUMNS``: `verdict` is the researcher's, written once
#: during research; `judge_*` is the judge's, written by `write-verdicts` against the
#: rendered row. Two different authors at two different grains — blurring them here is
#: exactly the confusion the split was introduced to end.
#: Account-level facts a row inherits from its account's ``latest.json`` record.
#:
#: ``segment``/``tier``/``score`` joined 2026-09-04: they are ICP judgments about the
#: *account* (`prospects_import.finalize`'s docstring calls them "the skill's judgment"),
#: not the contact row, so a re-run that re-scores an account already in the pool has the
#: same "correction never lands" shape `why_now` had before it joined this tuple. Before
#: this, `consolidate`'s per-email join derived them once from whichever hubspot export
#: first introduced the email (`_row_to_record`) and never revisited them: `finalize`
#: correctly overwrote the account's record in `latest.json`, but an already-present row
#: only gets its `conf_tier` reclassified, so the stale segment/tier/score sat in
#: `ready-to-load.csv` indefinitely with no documented path to fix it.
#: ``industry`` joined 2026-09-24 for the same reason: it is the ledger's classification of
#: the ACCOUNT, and `premise-vocab.toml` may attest a premise from it (`industry_terms`), so
#: the pool has to carry it to where the resolver and the render gate read rows.
_INHERITED_RECORD_COLUMNS = (
    *RECORD_COLUMNS,
    SIGNAL_COLUMN,
    "hook_cell",
    "why_now",
    "segment",
    "tier",
    "score",
    "industry",
    "verdict_on",
)

#: The subset the ACCOUNT record wins outright on, rather than only filling a blank.
#:
#: Everything else here is fill-only, because a value the row already carries came from its
#: own source export and is the more specific artifact. ``why_now`` is the exception, and it
#: is not a close call: this module's own account-record docstring says a why-now "is about
#: the company, not the person", so there is no row-specific version of it to protect.
#:
#: Without this, re-research could not reach the pool. `consolidate` skips an already-present
#: email except to re-rank its confidence tier, and no flag re-reads ``why_now`` — so a row's
#: opener was written once, at first sight, and was unimprovable forever. Found 2026-08-29:
#: a boundary-fact re-research pass produced five verified, better clauses, wrote them to the
#: account ledger, and the pool could not see any of them. Measured on the same pool, 88 of
#: 1,171 rows disagreed with their own account record and the account was better in every
#: sampled case — two rows still held the literal placeholder "to confirm".
#:
#: ``segment``/``tier``/``score`` are authoritative for the identical reason as ``why_now``:
#: a re-score is a correction to the account's OWN judgment, not a more-specific row-level
#: fact a row's own export could out-rank, so fill-only would leave a corrected account
#: re-scoring stuck behind whatever the row happened to see first.
#:
#: This can never blank a row: ``_account_record_index`` indexes only non-empty values. The
#: one way a record blanks anything is the explicit ``signal_state: cleared`` below.
_AUTHORITATIVE_RECORD_COLUMNS = frozenset(
    {
        "why_now",
        "signal_source_url",
        "signal_observed",
        "signal_evidence",
        "segment",
        "tier",
        "score",
        "industry",
        "hook_cell",
    }
)
#: The three provenance columns travel WITH ``why_now`` and are not separable from it. A
#: clause and the source that evidences it are one record: taking the clause from the account
#: and leaving the row's old source in place produces a row whose cited page does not support
#: its own claim — the `signal-evidence-unsupported` defect these columns exist to catch. Seen
#: 2026-08-29: one account's row took a new clause about a named customer selecting them while
#: still
#: citing the funding article the previous clause came from.
#:
#: Everything else stays fill-only on purpose. `signal_subject`, `signal_agent_kind` and
#: `category_relation` are judgements a row's own export may hold more specifically than the
#: account does ("Northwind Holdings (parent)"), and clobbering those is a real regression
#: with a test on it. Conditioning the takeover on "the clause changed" was also tried and is
#: WRONG: it latches, firing only on the single pass that copies the new clause down, so on
#: every later pass the row matches and stale provenance beside a fresh clause is permanent.
#:
#: Those three describe the CLAUSE, though, so they are fill-only only while the row keeps
#: its own clause. When the account supplies ``why_now`` it has already replaced the row's,
#: and the row's reading describes a sentence the row no longer holds — see
#: ``_CLAUSE_BOUND_RECORD_COLUMNS``. Keyed on the account carrying a clause, never on the
#: clause having changed, so it does not latch.

#: The judgements that describe the clause, authoritative whenever the account record
#: carries the ``why_now`` they describe (``_account_record_index`` indexes only non-empty
#: values, so "carries" means a real clause). Seen 2026-09-25: re-research promoted corrected
#: records onto their accounts, and the pool took each new clause while keeping the old
#: subject beside it — the gate blocked those rows on `signal-subject-mismatch`, and an
#: adjacent vendor's row still read `prospect`, the direction that passes silently.
_CLAUSE_BOUND_RECORD_COLUMNS = frozenset(
    {"signal_subject", "signal_agent_kind", "category_relation"}
)


def _account_record_wins(col: str, record: dict[str, str]) -> bool:
    """Whether the account's value for ``col`` overwrites a value the row already carries."""
    if col in _AUTHORITATIVE_RECORD_COLUMNS:
        return True
    return col in _CLAUSE_BOUND_RECORD_COLUMNS and "why_now" in record


#: ``verdict`` restrictiveness. The account record may make a row's verdict STRICTER and
#: never looser — the monotone-stricter rule this repo already applies to tenant pack
#: overrides, for the same reason: a merge that can only tighten is safe without having to
#: establish which side is newer.
#:
#: Research must be able to change its mind. A brokerage account was re-angled on 2026-08-29
#: because its recorded clause carries no agent content, and the demotion could not reach
#: the row the enrollment gate actually filters. But of the 6 rows whose verdict disagreed
#: with their account, 4 pointed the other way (row ``re-angle`` vs account ``send``) with
#: no way to tell which was written later — and promoting a row into the sendable pool on a
#: possibly-stale record is the expensive direction to be wrong in. So: demote freely, and
#: promote only on proof of which side is newer (``_verdict_promotes``, 2026-09-25).
_VERDICT_STRICTNESS = {"send": 0, "re-angle": 1, "drop": 2}


def _verdict_at_least_as_strict(current: str, incoming: str) -> bool:
    """True when ``incoming`` is a demotion (or equal) relative to ``current``."""
    cur = _VERDICT_STRICTNESS.get((current or "").strip().lower())
    inc = _VERDICT_STRICTNESS.get((incoming or "").strip().lower())
    if cur is None or inc is None:
        return False
    return inc > cur


#: The one promotion there is, and what the account must carry to earn it. "Promote never"
#: above held because nothing said which side was newer; ``verdict_on`` says so. It is
#: written only where research sets a verdict (``signal_backfill --promote``, whose records
#: have all passed ``check_record``, and ``prospects_state mutate``). The judge never writes
#: ``verdict``. ``drop`` is never a starting point, and an account with a missing clause,
#: source, date or evidence has nothing to send on.
_PROMOTION = ("re-angle", "send")
_PROMOTION_REQUIRES = ("why_now", "signal_source_url", "signal_observed", "signal_evidence")


def _iso_date(value: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _verdict_promotes(row: dict, record: dict[str, str]) -> bool:
    """Whether the account's newer research verdict lifts this row from re-angle to send.

    The row's own ``verdict_on`` came down with its current verdict; a blank one predates
    stamping, so any stamp is newer. A row stamp that does not parse fails closed.
    """
    pair = (
        str(row.get("verdict") or "").strip().lower(),
        record.get("verdict", "").strip().lower(),
    )
    if pair != _PROMOTION or not all(record.get(c) for c in _PROMOTION_REQUIRES):
        return False
    stamp = _iso_date(record.get("verdict_on", ""))
    row_on = str(row.get("verdict_on") or "").strip()
    row_stamp = _iso_date(row_on)
    if stamp is None or (row_on and row_stamp is None):
        return False
    return row_stamp is None or stamp > row_stamp


#: The atomic signal group: the clause plus every column that describes or evidences it. They
#: move together or not at all — see ``_AUTHORITATIVE_RECORD_COLUMNS`` and
#: ``_CLAUSE_BOUND_RECORD_COLUMNS``.
SIGNAL_GROUP_COLUMNS = ("why_now", *SIGNAL_RECORD_COLUMNS, SIGNAL_COLUMN, "hook_cell")

#: ``signal_state`` on a ledger account: the one value that says research LOOKED and found no
#: qualifying signal, as opposed to a blank record, which says nothing. Closed on purpose —
#: any other value is ignored, so a negative result can never be free text (with a date in
#: it) that a gate then parses as a claim. Set with ``prospects_state mutate --set
#: signal_state=cleared``; remove it when research finds a signal again.
SIGNAL_CLEARED = "cleared"


def _is_signal_cleared(item: dict) -> bool:
    return str(item.get("signal_state") or "").strip().lower() == SIGNAL_CLEARED


def _signal_cleared_accounts(profile: str, content_root: Path | None) -> dict[str, bool]:
    """``account_id`` -> whether that CLEARED account's ledger entry still carries a clause.

    ``consolidate`` blanks :data:`SIGNAL_GROUP_COLUMNS` on every row of these accounts.
    Cleared wins over a clause still sitting on the ledger — the conservative direction, a
    row that makes no dated claim — and the ``True`` values are reported, because a clause
    beside ``cleared`` is either a stale leftover or new research whose author forgot to
    lift the state.
    """
    return {
        account_id: bool(str(item.get("why_now") or "").strip())
        for item in _ledger_items(profile, content_root)
        if _is_signal_cleared(item)
        and (account_id := str(item.get(ACCOUNT_ID_FIELD) or "").strip())
    }


def _account_record_index(profile: str, content_root: Path | None) -> dict[str, dict[str, str]]:
    """``account_id`` -> the research record fields that account carries.

    The record is a fact about the *account* — a why-now signal is about the company, not
    the person — so it is written once onto the account in ``latest.json``. But the
    enrollment gate (``account_integrity --require-verdict send``) filters *rows* in
    ``ready-to-load.csv``. Without this index the join stamped ``account_id`` and stopped,
    so a fully-researched batch still failed the gate with "kept 0/N": the judgement was
    made in one file and enforced from another, with nothing carrying it across.

    Only non-empty values are indexed, so an account with a partial record contributes
    exactly the fields it actually has and never blanks a column the row already filled. A
    ``signal_state: cleared`` account contributes none of its signal group at all.
    """
    index: dict[str, dict[str, str]] = {}
    for item in _ledger_items(profile, content_root):
        account_id = str(item.get(ACCOUNT_ID_FIELD) or "").strip()
        if not account_id:
            continue
        cleared = _is_signal_cleared(item)
        record = {
            col: str(item.get(col) or "").strip()
            for col in _INHERITED_RECORD_COLUMNS
            if str(item.get(col) or "").strip() and not (cleared and col in SIGNAL_GROUP_COLUMNS)
        }
        if record:
            index[account_id] = record
    return index


def _disqualified_account_keys(profile: str, content_root: Path | None) -> set[str]:
    """Account keys the ledger has closed to sending: a retiring lifecycle ``status``, or any
    :func:`~gtm_core.account_exclusion_keys.account_hold_reason` — a ``verdict: drop``, an
    account outside the target markets, or one not yet researched enough to score (PS15: the
    last two used to reach the list, and the gate, as if they were fits).

    ``latest.json`` is the ledger of record for account lifecycle; the pooled CSVs are
    derived views of it. Reading it here is what makes an operator's (or an eval
    writeback's) disqualification actually reach a build output — before this, nothing
    in the send-list build filtered on lifecycle status at all.

    ABSENT is an empty set: a profile with no latest.json is a first run, and the ledger +
    DNC gates still apply. Present-but-UNREADABLE is an abort (:func:`_ledger_items`) — until
    2026-09-21 it was an empty set too, so a truncated ledger retired nobody.

    Keys are :func:`~gtm_core.account_exclusion_keys.ledger_account_keys` — the item's identity
    keys plus its legal-form-normalised name — the other half of :func:`_account_keys_of`.
    """
    keys: set[str] = set()
    for item in _ledger_items(profile, content_root):
        retired = str(item.get("status") or "").strip().lower() in RETIRED_STATUSES
        if retired or account_hold_reason(item):
            keys.update(ledger_account_keys(item))
    return keys
