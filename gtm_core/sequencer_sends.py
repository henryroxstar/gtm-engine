"""Sequencer send counts → the outcomes ledger: the denominator every reply rate was missing.

Until 2026-09-03 no ``sent`` row had ever been appended, so every reply rate the learnings note
and the cell table computed had a zero denominator and read ``—``. Replies were being recorded
(:mod:`gtm_core.sequencer_outcomes`); attempts were not.

**Provider reality (verified against a real ``get_sequence_stats`` payload, fetched 2026-08-31).**
The per-sequence stats carry TOTALS only — ``emails.status.delivered``, ``prospects[0].contacted``,
``replied``, ``unsubscribed``, the bounce buckets — and **no recipient addresses**. So:

* the default row is a per-SEQUENCE **delta**: ``value = delivered_now − delivered_already_recorded``.
  Totals are cumulative and the ledger sums ``value``, so recording the total twice would double
  the denominator. A re-run on the same fetch date writes nothing (``meta.sent_key``).
* a payload that DOES carry addresses (a future per-recipient stats tool) writes one ``sent`` row
  per delivered recipient, joined to its cell by email, idempotent on ``<seq>:<email>``.
* a payload in neither shape is **refused**, never written as zero — a missing field must not
  look like a zero-send wave.

Every row carries ``seq:<id>`` and ``lane:<lane>`` (from ``cells.toml``), plus ``cell:<id>`` when
the sequence's lists resolve to exactly one cell; otherwise the row counts toward the lane and
the sequence but not a cell, and the run says so.

The same reading feeds the wave gate (``gtm_core.wave_gate``), which reads a DIFFERENT file
(``prospects/outcomes.jsonl``) and would otherwise stay starved.

MCP-free like the reply producer: the agent fetches the payloads over MCP and pipes them in.
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

from gtm_core.cells import cells_by_sequence, email_index, lane_by_sequence, lane_index
from gtm_core.outcomes import append_outcome, read_outcomes
from gtm_core.paths import resolve_content_root
from gtm_core.wave_gate import WaveReport, append_report

#: Statuses that mean "an email reached this person" on a per-recipient payload.
CONTACTED_STATUSES = frozenset({"contacted", "sent", "delivered", "replied", "completed"})
SOURCE = "sequencer_sends"


def sequences_in(payload: dict | list) -> list[dict]:
    """Unwrap ``{sequences:[…]}`` / ``{payload:{…}}`` / a bare sequence dict / a list."""
    node = payload
    if isinstance(node, dict) and "sequences" in node:
        node = node["sequences"]
    if isinstance(node, dict) and "payload" in node:
        node = node["payload"]
    if isinstance(node, dict):
        node = [node]
    return [x for x in (node or []) if isinstance(x, dict) and x.get("sequenceId")]


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def sequence_totals(seq: dict) -> dict | None:
    """``{delivered, replied, bounced, unsubscribed}`` or ``None`` when the shape is unknown."""
    emails = seq.get("emails") if isinstance(seq.get("emails"), dict) else {}
    status = emails.get("status") if isinstance(emails.get("status"), dict) else {}
    prospects = seq.get("prospects")
    p0 = (
        prospects[0]
        if isinstance(prospects, list) and prospects and isinstance(prospects[0], dict)
        else {}
    )
    delivered = _int(status.get("delivered"))
    if delivered is None:
        delivered = _int(p0.get("contacted"))
    if delivered is None:
        return None
    bounced = sum(
        _int(status.get(k)) or 0 for k in ("bounced", "hardBounced", "softBounced", "blockBounced")
    )
    return {
        "delivered": delivered,
        "replied": _int(status.get("replied")) or _int(p0.get("replied")) or 0,
        "bounced": bounced or (_int(p0.get("bounced")) or 0),
        "unsubscribed": _int(p0.get("unsubscribed")) or 0,
    }


def recipients_in(seq: dict) -> list[dict]:
    """Per-recipient entries, when the payload carries addresses (none do today)."""
    out = []
    for key in ("prospects", "contacts", "items", "recipients"):
        items = seq.get(key)
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict) or not it.get("email"):
                continue
            status = str(it.get("status") or "").strip().lower()
            contacted = status in CONTACTED_STATUSES or bool(it.get("contacted"))
            bounced = bool(it.get("bounced")) or status.endswith("bounced")
            if contacted and not bounced:
                out.append(it)
    return out


def _recorded(existing: list[dict], seq_id: str) -> tuple[int, set[str]]:
    """``(aggregate delivered already on file, sent_keys already on file)`` for one sequence."""
    total = 0
    keys: set[str] = set()
    for r in existing:
        if r.get("outcome") != "sent":
            continue
        meta = r.get("meta") or {}
        if meta.get("sequence_id") != seq_id and f"seq:{seq_id}" not in (r.get("tags") or []):
            continue
        if meta.get("sent_key"):
            keys.add(str(meta["sent_key"]))
        if meta.get("aggregate"):
            try:
                total += int(float(r.get("value") or 0))
            except (TypeError, ValueError):
                pass
    return total, keys


def plan_sends(
    payloads: list[dict | list],
    existing: list[dict],
    *,
    lane_by_seq: dict[str, str],
    cell_by_seq: dict[str, list[str]],
    email_to_cell: dict[str, str],
    email_to_lane: dict[str, str],
    fetched: str,
) -> tuple[list[dict], list[str]]:
    """Outcome rows for every sequence in ``payloads``, plus the notes a reader needs.
    Pure — no I/O."""
    rows: list[dict] = []
    notes: list[str] = []
    for payload in payloads:
        seqs = sequences_in(payload)
        if not seqs:
            notes.append(
                "REFUSED: a payload carried no sequence with a sequenceId — nothing written for it"
            )
            continue
        for seq in seqs:
            sid = str(seq["sequenceId"])
            lane = lane_by_seq.get(sid) or "unregistered"
            cells = cell_by_seq.get(sid) or []
            recorded_total, keys = _recorded(existing, sid)
            recipients = recipients_in(seq)
            if recipients:
                for it in recipients:
                    email = str(it["email"]).strip().lower()
                    key = f"{sid}:{email}"
                    if key in keys:
                        continue
                    cell = email_to_cell.get(email)
                    tags = [f"seq:{sid}", f"lane:{email_to_lane.get(email) or lane}"]
                    if cell:
                        tags.append(f"cell:{cell}")
                    rows.append(
                        {
                            "channel": "email",
                            "outcome": "sent",
                            "ref": sid,
                            "value": 1,
                            "ts": fetched,
                            "tags": tags,
                            "meta": {
                                "sequence_id": sid,
                                "sent_key": key,
                                "prospect_email": email,
                                "source": SOURCE,
                            },
                        }
                    )
                continue
            totals = sequence_totals(seq)
            if totals is None:
                notes.append(
                    f"REFUSED: sequence {sid} has neither emails.status.delivered nor prospects[].contacted — not written as zero"
                )
                continue
            key = f"{sid}:{fetched}"
            if key in keys:
                notes.append(f"{sid}: already recorded for {fetched} (sent_key) — skipped")
                continue
            delta = totals["delivered"] - recorded_total
            if delta <= 0:
                notes.append(
                    f"{sid}: delivered {totals['delivered']} already on file ({recorded_total}) — no new sends"
                )
                continue
            tags = [f"seq:{sid}", f"lane:{lane}"]
            if len(cells) == 1:
                tags.append(f"cell:{cells[0]}")
            else:
                notes.append(
                    f"{sid}: {len(cells)} cell(s) share this sequence, so its {delta} send(s) count toward the "
                    f"lane and sequence, not a cell (per-recipient stats would fix this)"
                )
            rows.append(
                {
                    "channel": "email",
                    "outcome": "sent",
                    "ref": sid,
                    "value": delta,
                    "ts": fetched,
                    "tags": tags,
                    "meta": {
                        "sequence_id": sid,
                        "sent_key": key,
                        "aggregate": True,
                        "cumulative_delivered": totals["delivered"],
                        "replied": totals["replied"],
                        "bounced": totals["bounced"],
                        "unsubscribed": totals["unsubscribed"],
                        "source": SOURCE,
                    },
                }
            )
    return rows, notes


def wave_reports(payloads: list[dict | list], fetched: str) -> list[WaveReport]:
    """One :class:`WaveReport` per sequence — the wave gate's own file. ``positive_replies``
    is not in the stats payload and is recorded as 0, which the gate reads as unmeasured
    rather than as a bad wave only if ``sends`` clears its floor; the reply ingest is where
    positives come from."""
    out = []
    for payload in payloads:
        for seq in sequences_in(payload):
            totals = sequence_totals(seq)
            if totals is None:
                continue
            out.append(
                WaveReport(
                    wave=str(seq["sequenceId"]),
                    date=fetched,
                    sends=totals["delivered"],
                    replies=totals["replied"],
                    positive_replies=0,
                    opt_outs=totals["unsubscribed"],
                    source=SOURCE,
                )
            )
    return out


def _load(path: str) -> dict | list:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _fetched_date(payloads: list[dict | list], override: str) -> str:
    if override:
        return override
    for p in payloads:
        if isinstance(p, dict) and isinstance(p.get("fetched"), str) and len(p["fetched"]) >= 10:
            return p["fetched"][:10]
    return datetime.date.today().isoformat()


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record sequencer send counts as `sent` outcome rows.")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--content-root", default=None)
    ap.add_argument(
        "--stats", required=True, nargs="+", help="get_sequence_stats payload(s) (JSON files)"
    )
    ap.add_argument(
        "--fetched",
        default="",
        help="YYYY-MM-DD the stats were fetched (default: payload `fetched`, else today)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="append to outcomes.jsonl + the wave-gate file (default: dry run)",
    )
    args = ap.parse_args(argv)

    root = Path(args.content_root) if args.content_root else resolve_content_root()
    payloads = [_load(p) for p in args.stats]
    fetched = _fetched_date(payloads, args.fetched)
    rows, notes = plan_sends(
        payloads,
        read_outcomes(root, args.profile),
        lane_by_seq=lane_by_sequence(args.profile, root),
        cell_by_seq=cells_by_sequence(args.profile, root),
        email_to_cell=email_index(args.profile, root),
        email_to_lane=lane_index(args.profile, root),
        fetched=fetched,
    )
    for r in rows:
        print(("APPEND " if args.apply else "DRY    ") + json.dumps(r, ensure_ascii=False))
    for n in notes:
        print(f"  ! {n}")
    if args.apply:
        for r in rows:
            append_outcome(root, args.profile, r)
        written = sum(
            1
            for rep in wave_reports(payloads, fetched)
            if append_report(rep, args.profile, root)[0]
        )
        print(
            f"\n{len(rows)} sent row(s) appended; {written} wave reading(s) recorded for the wave gate"
        )
    else:
        print(f"\n{len(rows)} sent row(s) planned (dry run)")
    refused = [n for n in notes if n.startswith("REFUSED")]
    return 1 if refused else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
