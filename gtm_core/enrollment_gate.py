"""Enrollment-time safety gates for prospect lists.

Addresses review findings PS-R I1 and I2:
  - I1: Under `--require-verdict` (enrollment), each row's lane is verified against
    `evals/lanes-state.jsonl`. Refuses when state is missing or empty, when any row
    is unrouted, when a row's lane is blank, hold, or excluded, or when it disagrees
    with `--lane` or the CSV's `lane` column.
  - I2: Under `--require-verdict`, joins rows to `latest.json` and refuses any row
    belonging to an account with a retired (disqualified, opt-out, closed-lost) or
    engaged (replied, meeting, customer, etc.) status.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .account_exclusion_keys import (
    account_hold_reason,
    ledger_account_keys,
    row_account_keys,
)
from .prospect_paths import evals_dir
from .prospects_state import RETIRED_STATUSES, latest_path, load_latest

#: Accounts in latest.json that mean "already in conversation" (PS6)
DEFAULT_ENGAGED_STATUSES = frozenset(
    {"engaged", "customer", "partner", "in-conversation", "replied", "meeting"}
)

#: Statuses in latest.json that must never be enrolled
BLOCKED_ACCOUNT_STATUSES = (
    RETIRED_STATUSES | DEFAULT_ENGAGED_STATUSES | {"opt-out", "optout", "closed_lost"}
)


def _refuse_parked_lanes(rows: list[dict], want: str) -> str | None:
    """Refuse a CSV whose own lane column carries hold or excluded."""
    parked = [r for r in rows if (r.get("lane") or "").strip().lower() in {"hold", "excluded"}]
    if not parked:
        return None
    counts: dict[tuple[str, str], int] = {}
    at_want_by_lane: Counter[str] = Counter()
    for r in parked:
        lane_ = (r.get("lane") or "").strip().lower()
        key = (
            lane_,
            (r.get("lane_reason") or r.get("reason") or "").strip(),
        )
        counts[key] = counts.get(key, 0) + 1
        if (r.get("verdict") or "").strip().lower() == want:
            at_want_by_lane[lane_] += 1
    parts = ", ".join(
        f"{lane_}/{reason}: {n}" if reason else f"{lane_}: {n}"
        for (lane_, reason), n in sorted(counts.items())
    )
    total_at_want = sum(at_want_by_lane.values())
    at_want_str = (
        " + ".join(f"{n} {lane_name}" for lane_name, n in sorted(at_want_by_lane.items()))
        if len(at_want_by_lane) > 1
        else str(total_at_want)
    )
    return (
        f"REFUSED: {len(parked)} row(s) carry lane 'hold' or 'excluded' — {parts} — "
        f"{at_want_str} of them are at verdict {want!r} — a row parked out of the send "
        f"path enrols nowhere; naming --lane does not rescue it, only re-routing does "
        f"(these rows are on hold or excluded and cannot be sent)"
    )


def _refuse_ambiguous_lane(rows: list[dict]) -> str | None:
    """Refuse a CSV naming more than one enrollable lane with no --lane specified."""
    enrollable = sorted(
        {(r.get("lane") or "").strip().lower() for r in rows} - {"hold", "excluded", ""}
    )
    if len(enrollable) <= 1:
        return None
    return (
        f"REFUSED: the CSV's own lane column carries more than one enrollable "
        f"lane {enrollable} with no --lane to say which this run is enrolling — "
        f"name --lane (multiple email tracks are mixed in this CSV without a "
        f"--lane choice to specify which one to enroll)"
    )


def _refuse_lane_state_mismatch(
    rows: list[dict], profile: str, content_root: Path | None = None
) -> str | None:
    """Refuse a CSV whose lane column disagrees with evals/lanes-state.jsonl."""
    state_file = evals_dir(profile, content_root) / "lanes-state.jsonl"
    if not state_file.is_file():
        return None
    state_lane_by_email: dict[str, str] = {}
    with state_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                return f"REFUSED: lanes-state.jsonl line is not a JSON object: {line!r}"
            email = (rec.get("email") or "").strip().lower()
            if email:
                state_lane_by_email[email] = (rec.get("lane") or "").strip().lower()
    disagreeing = sum(
        1
        for r in rows
        if (email := (r.get("email") or "").strip().lower()) in state_lane_by_email
        and (c_lane := (r.get("lane") or "").strip().lower())
        and state_lane_by_email[email] != c_lane
    )
    if not disagreeing:
        return None
    return (
        f"REFUSED: {disagreeing} row(s)' lane column disagrees with "
        f"evals/lanes-state.jsonl's last recorded routing for that email — "
        f"re-run `lanes route` before enrolling this list (the list has stale "
        f"routing from a prior run; refresh each row's lane before enrolling)"
    )


def _load_lanes_state(
    profile: str, content_root: Path | None = None
) -> tuple[dict[str, dict] | None, str | None]:
    state_file = evals_dir(profile, content_root) / "lanes-state.jsonl"
    if not state_file.is_file():
        return None, (
            "REFUSED: evals/lanes-state.jsonl is missing — "
            "run `lanes route` before enrolling this list"
        )
    state_by_email: dict[str, dict] = {}
    with state_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                return None, f"REFUSED: lanes-state.jsonl line is not a JSON object: {line!r}"
            email = (rec.get("email") or "").strip().lower()
            if email:
                state_by_email[email] = rec
    if not state_by_email:
        return None, (
            "REFUSED: evals/lanes-state.jsonl is empty — "
            "run `lanes route` before enrolling this list"
        )
    return state_by_email, None


def _format_parked_refusal(
    parked: list[tuple[dict, str, str]],
    want: str,
) -> str:
    counts: dict[tuple[str, str], int] = {}
    at_want = 0
    for r, pl, reason in parked:
        key = (pl, reason)
        counts[key] = counts.get(key, 0) + 1
        if (r.get("verdict") or "").strip().lower() == want:
            at_want += 1
    parts = ", ".join(
        f"{lane_}/{reason}: {n}" if reason else f"{lane_}: {n}"
        for (lane_, reason), n in sorted(counts.items())
    )
    return (
        f"REFUSED: {len(parked)} row(s) carry lane 'hold' or 'excluded' — {parts} — "
        f"{at_want} of them are at verdict {want!r} — a row parked out of the send "
        f"path enrols nowhere; naming --lane does not rescue it, only re-routing does "
        f"(these rows are on hold or excluded and cannot be sent)"
    )


def _validate_parked_and_disagreements(
    rows: list[dict],
    fieldnames: list[str],
    state_by_email: dict[str, dict],
    want: str,
) -> str | None:
    unrouted: list[str] = []
    blank_lanes: list[str] = []
    parked: list[tuple[dict, str, str]] = []
    disagreements: list[str] = []

    for r in rows:
        email = (r.get("email") or "").strip().lower()
        if not email or email not in state_by_email:
            unrouted.append(email or r.get("company", "unknown"))
            continue
        s_rec = state_by_email[email]
        s_lane = (s_rec.get("lane") or "").strip().lower()
        if "lane" in fieldnames:
            c_lane = (r.get("lane") or "").strip().lower()
            if c_lane and c_lane != s_lane:
                disagreements.append(email)
        r["lane"] = s_lane  # stamp authoritative lane onto row

        if not s_lane:
            blank_lanes.append(email)
        elif s_lane in {"hold", "excluded"}:
            reason = (
                s_rec.get("reason") or s_rec.get("trigger") or r.get("lane_reason") or ""
            ).strip()
            parked.append((r, s_lane, reason))

    if unrouted:
        return (
            f"REFUSED: {len(unrouted)} row(s) missing from evals/lanes-state.jsonl — "
            f"run `lanes route` before enrolling this list"
        )
    if disagreements:
        return (
            f"REFUSED: {len(disagreements)} row(s)' lane column disagrees with "
            f"evals/lanes-state.jsonl's last recorded routing for that email — "
            f"re-run `lanes route` before enrolling this list (the list has stale "
            f"routing from a prior run; refresh each row's lane before enrolling)"
        )
    if blank_lanes:
        return (
            f"REFUSED: {len(blank_lanes)} row(s) have blank lane in evals/lanes-state.jsonl — "
            f"only routed rows in enrollable lanes may be enrolled"
        )
    if parked:
        return _format_parked_refusal(parked, want)
    return None


def _resolve_effective_lane(
    rows: list[dict],
    state_by_email: dict[str, dict],
    lane: str,
) -> tuple[str | None, str]:
    effective_lanes = {
        (state_by_email[(r.get("email") or "").strip().lower()].get("lane") or "").strip().lower()
        for r in rows
    }
    if lane:
        foreign = sorted(effective_lanes - {lane})
        if foreign:
            return (
                f"REFUSED: --lane {lane!r} but rows are routed to {foreign} — "
                f"a list routed into one lane must not be enrolled into another",
                lane,
            )
        return None, lane
    if len(effective_lanes) > 1:
        return (
            f"REFUSED: rows are routed to more than one enrollable lane {sorted(effective_lanes)} "
            f"with no --lane to say which this run is enrolling — name --lane "
            f"(multiple email tracks are mixed in this CSV without a --lane choice to specify which one to enroll)",
            lane,
        )
    return None, next(iter(effective_lanes)) if effective_lanes else ""


def check_enrollment_lanes(
    rows: list[dict],
    profile: str,
    lane: str,
    fieldnames: list[str],
    want: str = "send",
    content_root: Path | None = None,
) -> tuple[str | None, str]:
    """PS-R I1: Under --require-verdict, take each row's lane from lanes-state.jsonl.

    Refuse when:
      - lanes-state.jsonl is missing or empty;
      - any row is absent from state;
      - any row's lane in state is blank, hold, or excluded;
      - the CSV's own lane column disagrees with state;
      - rows route to a lane other than --lane (if specified);
      - rows route to multiple enrollable lanes when --lane is omitted.

    Stamps the authoritative lane onto each row (r["lane"]), and returns
    (refusal_message, resolved_lane).
    """
    state_by_email, err = _load_lanes_state(profile, content_root)
    if err or state_by_email is None:
        return err, lane

    err = _validate_parked_and_disagreements(rows, fieldnames, state_by_email, want)
    if err:
        return err, lane

    err, resolved_lane = _resolve_effective_lane(rows, state_by_email, lane)
    if err:
        return err, lane

    return None, resolved_lane


def _extract_blocked_lookups(
    items: list[dict],
) -> tuple[dict[str, str], dict[str, str]]:
    blocked_keys: dict[str, str] = {}
    blocked_emails: dict[str, str] = {}
    for it in items:
        st = str(it.get("status") or "").strip().lower()
        hold = account_hold_reason(it)
        if st.replace("_", "-") not in BLOCKED_ACCOUNT_STATUSES and hold:
            # Closed to sending on a second axis — the SAME rule the send-list build removes
            # them by (`account_hold_reason`), so a list built before a rescore is refused here
            # rather than sent. A row that cannot be tied to a dropped account by an exact key
            # carries a blank verdict, which the generic lane admits.
            st = _HOLD_LABELS[hold]
        if (
            st in BLOCKED_ACCOUNT_STATUSES
            or st.replace("_", "-") in BLOCKED_ACCOUNT_STATUSES
            or st in _HOLD_LABELS.values()
        ):
            for k in ledger_account_keys(it):
                blocked_keys[k] = st
            for f in ("contact_email", "email"):
                em = str(it.get(f) or "").strip().lower()
                if em:
                    blocked_emails[em] = st
    return blocked_keys, blocked_emails


#: How each ``account_hold_reason`` reads in a refusal line. "verdict drop" is kept verbatim —
#: it is the word this gate has always printed for a dropped account.
_HOLD_LABELS: dict[str, str] = {
    "drop": "verdict drop",
    "outside-market": "outside target markets",
    "needs-research": "not yet researched",
}


def _row_identity_keys(r: dict) -> list[str]:
    """Every key the row offers to the account-status join.

    The SAME list ``prospects_consolidate`` excludes on
    (:func:`gtm_core.account_exclusion_keys.row_account_keys`), so the gate and the send-list
    build cannot disagree about which account a row belongs to. This used to be a second,
    narrower hand-rolled list — exact domain / id / company / account_id only — and a company-name
    VARIANT of a do-not-contact account (no domain and no ``account_id`` on the row, a legal
    suffix on the ledger's name) drew no objection here. The row now also offers the domain of
    the contact's own email and its legal-form-normalised company name; every comparison is
    still exact, never a substring.
    """
    return row_account_keys(r)


def check_account_status(
    rows: list[dict], profile: str, content_root: Path | None = None
) -> str | None:
    """PS-R I2: Join rows to latest.json and refuse retired or engaged accounts.

    Fail-CLOSED. "The ledger could not be read" is not "no account objects": a truncated or
    wrong-shaped ``latest.json`` refuses the list, because the do-not-contact status this
    gate exists to honour is exactly what it could not see. An ABSENT ledger is different and
    is no objection — that is ``load_latest``'s own contract (absent = an empty ledger,
    present-but-unreadable = an error), and a tenant with no ledger has no retired accounts.
    """
    try:
        items = load_latest(profile, content_root).get("items", [])
        malformed = sum(1 for it in items if not isinstance(it, dict))
        if malformed:
            raise ValueError(f"{malformed} ledger entr(ies) are not JSON objects")
    except (OSError, ValueError) as exc:  # ValueError covers JSON + unicode decode errors
        return (
            f"REFUSED: account ledger unreadable ({latest_path(profile, content_root).name}: "
            f"{type(exc).__name__}) — cannot confirm no account on this list is "
            f"do-not-contact, disqualified, or already in conversation; repair or restore the "
            f"ledger before enrolling"
        )

    blocked_keys, blocked_emails = _extract_blocked_lookups(items)

    blocked_rows: list[tuple[str, str]] = []
    for r in rows:
        em = (r.get("email") or r.get("contact_email") or "").strip().lower()
        st = blocked_emails.get(em)
        if not st:
            for k in _row_identity_keys(r):
                if k in blocked_keys:
                    st = blocked_keys[k]
                    break
        if st:
            blocked_rows.append((em or r.get("company", ""), st))

    if blocked_rows:
        by_status: dict[str, int] = {}
        for _, s in blocked_rows:
            by_status[s] = by_status.get(s, 0) + 1
        summary = ", ".join(f"{s}: {count}" for s, count in sorted(by_status.items()))
        return (
            f"REFUSED: {len(blocked_rows)} row(s) belong to accounts with retired or engaged "
            f"status in latest.json ({summary}) — "
            f"re-route or re-consolidate before enrolling this list"
        )
    return None
