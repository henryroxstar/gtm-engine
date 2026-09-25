"""Reading `lanes-state.jsonl` for the page — the one file every lane-derived figure on it
comes from. Split out of `model.py` (§R10 ceiling), not rewritten.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..prospect_paths import evals_dir
from ..prospect_status import (
    STATUSES,
    UnmappedStatus,
    needs_address,
    status_of,
)
from ..prospects_state import _identity_keys, load_latest

#: The five statuses `status_of` derives from a routed row's (lane, reason).
_LANE_STATUSES: tuple[str, ...] = tuple(s for s in STATUSES if s != "needs_address")


class LaneStateUnreadable(ValueError):
    """`lanes-state.jsonl` exists but cannot be read. Every lane-derived number on the page
    comes from it, so the page is refused rather than rendered short."""


def _read_lane_state(profile: str, content_root: Path | None) -> list[dict]:
    """The router's last routing per email — `lanes route`'s own `lanes-state.jsonl`.

    Read directly rather than through `gtm_core.lanes.decisions.read_state`: that helper
    lives in the `lanes` package, whose `__init__` pulls in the router, the hold-decisions
    ledger and the hold sheet — dragging that in here to read one JSONL is the opposite of
    what this leaf-ish read needs. `evals_dir` is the same path resolver
    `prospect_status_cli` and `account_integrity` already use for this file — reused rather
    than re-spelled.

    A missing file returns no rows rather than raising: the page already has a "no data
    yet" contract for this (`views_status`'s status tiles), and a profile that has never
    run `lanes route` is not a broken page, it is a page with nothing routed yet.

    A MALFORMED line raises. It used to be skipped, on the grounds that `prospect_status_cli`
    skipped one too — that stopped being true on 2026-09-21, when the terminal block started
    refusing rather than printing a confident undercount. Leaving the page lenient would make
    the two surfaces disagree about the same file in exactly the way this whole change set
    exists to stop: every number the page derives from this file would be short by the
    unreadable rows, with nothing on the page saying so.
    """
    path = evals_dir(profile, content_root) / "lanes-state.jsonl"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        raise LaneStateUnreadable(f"{path} is not UTF-8 text (byte {err.start})") from err
    out: list[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            val = json.loads(line)
        except json.JSONDecodeError as err:
            raise LaneStateUnreadable(f"{path} line {number} is not JSON: {err}") from err
        if not isinstance(val, dict):
            raise LaneStateUnreadable(f"{path} line {number} is not a JSON object")
        out.append(val)
    return out


def prospect_status_model(profile: str, content_root: Path | None = None) -> dict:
    """PS14's population: the six-way operator status, derived the one place it is derived
    (`gtm_core.prospect_status`) — never re-implemented here, so the page and `prospects
    status` cannot disagree about what one of these words means.

    A `(lane, reason)` pair `status_of` has never seen is a real gap in the mapping (see
    that module's docstring), and `prospects status` answers for nothing else on a run, so
    it can afford to raise loudly on one. A dashboard render answers for the WHOLE page —
    one such row must not blank every tile beside it, so it is counted separately
    (`unmapped`) rather than raising. Both surfaces read the same file through the same
    function; only the failure mode differs, and this is where that divergence is decided
    and recorded.

    `needs_address` is a different, larger population (the account ledger, not the current
    routed list) and is never summed into `total` — see `gtm_core.prospect_status.
    needs_address`'s own docstring.
    """
    records = _read_lane_state(profile, content_root)
    counts: dict[str, int] = dict.fromkeys(_LANE_STATUSES, 0)
    by_email: dict[str, str] = {}
    notes_by_email: dict[str, str] = {}
    unmapped = 0
    items = load_latest(profile, content_root).get("items", [])
    account_status: dict[str, str] = {}
    for item in items:
        st = str(item.get("status") or "").strip().lower()
        if st:
            for k in _identity_keys(item):
                account_status.setdefault(k, st)
    for rec in records:
        reason = rec.get("reason") or rec.get("trigger") or ""
        email = (rec.get("email") or "").strip().lower()
        try:
            status = status_of(rec.get("lane") or "", reason)
        except UnmappedStatus:
            unmapped += 1
            if email:
                by_email[email] = "unmapped"
            continue
        counts[status] += 1
        if email:
            by_email[email] = status
            trigger = reason.rsplit(":", 1)[-1]
            if status == "waiting_on_you" and trigger == "engaged-account":
                ident_row = {
                    "domain": (rec.get("company_domain") or "").strip().lower(),
                    "id": rec.get("id") or "",
                    "company": rec.get("company") or "",
                    "account_id": rec.get("account_id") or "",
                }
                for k in _identity_keys(ident_row):
                    if account_status.get(k) == "replied":
                        notes_by_email[email] = "they replied"
                        break
    return {
        "available": bool(records),
        "counts": counts,
        "total": sum(counts.values()),
        "unmapped": unmapped,
        "needs_address": sum(1 for item in items if needs_address(item)),
        "by_email": by_email,
        "notes_by_email": notes_by_email,
    }
