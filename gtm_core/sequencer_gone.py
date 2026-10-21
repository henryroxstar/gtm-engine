"""A registered sequence that has vanished from the sending tool — found, and (on request) written down.

``cells.toml`` keeps a registration for every sequence ever staged, because the row is the join
that attributes a reply to a cell. When the operator deletes a sequence in the Saleshandy screen
nothing here fires, so the registration keeps counting as "loaded" until someone records the
deletion in ``history.jsonl``. :func:`gone_findings` notices: a registered, provider-shaped id
that the live ``list_sequences`` reply does not show, and that history does not record deleted.

**Recording a deletion is a one-way door.** ``lanes.context`` reads a recorded deletion and stops
counting that sequence's people as enrolled, so a FALSE "deleted" lets routing load them again.
The classic way to be wrong is a short listing: a page the reply never contained. So the finding
fires only when the listing is demonstrably whole:

* the caller states the page size it asked for (``--page-size``), and the reply has FEWER rows
  than that — one page, and not a full one;
* no saved stats file is for a sequence the listing omits (that case is already the
  ``stats-without-listing`` warning: "a missing page of the list"); and
* the listing shares at least one sequence with the registrations — a positive control. A reply
  from the wrong account, or saved before these sequences existed, shares none, and without this
  check every registered id would look deleted at once.

The page size is a number the caller supplies, so it is a check on honest use, not proof about
the provider; the explicit confirmation below is what actually stands between a mistake and the
ledger.

Otherwise it says it CANNOT TELL, naming the ids, and never calls them gone. ``--record``
records only the ids the caller names with ``--confirm-deleted`` (the operator has confirmed in
the Saleshandy screen that they deleted them): reporting a sequence as gone and writing that down
are two different acts. It re-checks everything immediately before it writes, writes one
``sequence_cleanup`` event (the shape :func:`gtm_core.lanes.context._deleted_sequences` already
reads), and a second run finds nothing left to record. The ledger offers no check-then-append
under its own lock, so two runs at the same instant could both append; the reader takes the union
of ids, so a duplicate is harmless. It cannot go through ``sequencer_reconcile.record``, which
looks each finding up among the LIVE rows — a gone sequence has none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from .ledgers import Ledgers
from .sequencer_history import read_history
from .sequencer_stats_read import is_provider_id, shown

CODE_GONE = "registered-sequence-gone"
CODE_UNVERIFIED = "registered-sequence-gone-unverified"
SKILL = "sequencer-reconcile"
PROVIDER = "saleshandy"


@dataclass(frozen=True)
class GoneFinding:
    severity: str
    code: str
    sequence_id: str
    title: str
    message: str


def deleted_ids(history_rows: list[dict]) -> set[str]:
    """Ids history records as deleted — the two shapes ``lanes.context`` reads, stripped."""
    gone: set[str] = set()
    for row in history_rows:
        if row.get("event") == "sequence_deleted" and row.get("sequence_id"):
            gone.add(str(row["sequence_id"]).strip())
        elif row.get("event") == "sequence_cleanup":
            for item in row.get("sequences_deleted") or []:
                if isinstance(item, dict) and item.get("id"):
                    gone.add(str(item["id"]).strip())
    return gone


def listing_is_whole(
    page_size: int | None,
    live_count: int,
    live_ids: set[str],
    stats_ids: set[str],
    raw_ids: set[str],
) -> tuple[bool, str]:
    """``(whole?, why not)``. Whole only if the reply is one non-full page, no stats file is for
    a sequence the listing leaves out, and the listing shares a sequence with the registrations."""
    if not ({r.strip() for r in raw_ids} & live_ids):
        return False, (
            "the listing shares no sequence with the registered ones, so it may be from another "
            "account or from before they existed"
        )
    if page_size is None:
        return False, "no --page-size was given, so a short listing cannot be ruled out"
    if live_count >= page_size:
        return False, (
            f"the listing has {live_count} rows for a page size of {page_size}, so there may be "
            "more pages"
        )
    if stats_ids - live_ids:
        return (
            False,
            "a stats file is for a sequence the listing omits, so the listing may be short",
        )
    return True, ""


def candidates(raw_ids: set[str], live_ids: set[str], history_rows: list[dict]) -> list[str]:
    """Registered, provider-shaped ids that are neither listed nor recorded deleted (sorted)."""
    gone = deleted_ids(history_rows)
    out = set()
    for rid in raw_ids:
        sid = rid.strip()
        if sid.upper().startswith("DRAFT") or not is_provider_id(sid):
            continue
        if sid in live_ids or sid in gone:
            continue
        out.add(sid)
    return sorted(out)


def gone_findings(
    *,
    raw_ids: set[str],
    live_ids: set[str],
    stats_ids: set[str],
    live_count: int,
    history_rows: list[dict],
    page_size: int | None,
    titles: dict[str, str] | None = None,
) -> list[GoneFinding]:
    """Nothing when ``page_size`` was not given (the check was not asked for) or nothing is
    missing; WARN findings otherwise."""
    if page_size is None:
        return []
    missing = candidates(raw_ids, live_ids, history_rows)
    if not missing:
        return []
    titles = titles or {}
    whole, why = listing_is_whole(page_size, live_count, live_ids, stats_ids, raw_ids)
    if not whole:
        names = ", ".join(shown(s) for s in missing[:8]) + (" …" if len(missing) > 8 else "")
        msg = (
            f"{len(missing)} registered sequence(s) are not in the listing ({names}), but this "
            f"cannot tell whether they were deleted: {why}."
        )
        return [GoneFinding("WARN", CODE_UNVERIFIED, "", "", msg)]
    out = []
    for sid in missing:
        title = titles.get(sid, "")
        who = f"'{shown(title)}' ({shown(sid)})" if title else shown(sid)
        out.append(
            GoneFinding(
                "WARN",
                CODE_GONE,
                sid,
                title,
                f"Sequence {who} is registered here but no longer in Saleshandy, and nothing "
                "records that it was deleted. If you deleted it, run this command again with "
                "--record to write that down.",
            )
        )
    return out


def record_gone(
    findings: list[GoneFinding],
    *,
    profile: str,
    content_root: Path,
    raw_ids: set[str],
    live_ids: set[str],
    stats_ids: set[str],
    live_count: int,
    page_size: int | None,
    evidence_source: str,
    confirmed: set[str] | None = None,
    titles: dict[str, str] | None = None,
    now: datetime | None = None,
) -> tuple[list[str], list[str]]:
    """Append ONE ``sequence_cleanup`` event for the findings that still hold. ``(ids, notes)``.

    The ledger and the listing are checked again as they stand now; an id already recorded, or
    one the listing no longer proves gone, is skipped and said so. Nothing is ever written for
    an unverified finding."""
    reported = {f.sequence_id for f in findings if f.code == CODE_GONE}
    confirmed = {c.strip() for c in (confirmed or set())}
    asked = sorted(reported & confirmed)
    notes = [
        f"{shown(s)} was not recorded: pass --confirm-deleted {shown(s)} once the operator has "
        "confirmed in the Saleshandy screen that they deleted it"
        for s in sorted(reported - confirmed)
    ] + [
        f"--confirm-deleted {shown(s)} ignored: it is not reported as gone"
        for s in sorted(confirmed - reported)
    ]
    if not asked:
        return [], notes
    history_path = content_root / profile / "history.jsonl"
    rows = read_history(history_path)
    whole, why = listing_is_whole(page_size, live_count, live_ids, stats_ids, raw_ids)
    if not whole:
        return [], [*notes, f"nothing recorded: {why}"]
    still = set(candidates(raw_ids, live_ids, rows))
    todo = [s for s in asked if s in still]
    notes += [
        f"{shown(s)} is already recorded, or is in the listing now" for s in asked if s not in still
    ]
    if not todo:
        return [], notes
    titles = titles or {}
    today = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
    event = {
        "event": "sequence_cleanup",
        "skill": SKILL,
        "provider": PROVIDER,
        "recorded_retroactively": True,
        "recorded_on": today,
        "evidence": evidence_source,
        "note": (
            "registered in cells.toml but absent from a complete live list_sequences reply, with "
            "no deletion on record; recorded retroactively by sequencer_reconcile --record"
        ),
        "sequences_deleted": [{"id": s, "title": titles.get(s, "")} for s in todo],
    }
    Ledgers(SimpleNamespace(content_root=content_root), profile).append_history(event)
    return todo, notes
