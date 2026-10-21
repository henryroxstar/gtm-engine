"""``status`` — what the sending-figures file says about itself, in plain lines. Read-only.

Split out of :mod:`sequencer_snapshot` (which re-exports :func:`status`) so the writer stays under
the file-size ratchet. Every sequence id printed here is untrusted text and goes through
``printable``; an id is put inside a suggested command only when it needs no escaping, and then
through ``shlex.quote``.
"""

from __future__ import annotations

import shlex
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.page_inputs_io import printable
from gtm_core.sequence_snapshot_format import FILE_NAME, IDENTICAL, fall_sentence


def _forget_hint(sid: str) -> str:
    """The way to drop a sequence from the file — a command only when the id needs no escaping."""
    if printable(sid) == sid:
        return f"`forget --id {shlex.quote(sid)}` removes it from the file"
    return (
        "its id holds characters this message cannot show as a command, so re-run write with "
        "--replace to start the file again"
    )


def _ack_hint(sid: str) -> str:
    """The way to acknowledge a recorded fall — a command only when the id needs no escaping."""
    if printable(sid) == sid:
        return f"`ack --id {shlex.quote(sid)}` clears this note"
    return (
        "its id holds characters this message cannot show as a command, so run `ack` with no "
        "--id to clear all recorded falls"
    )


def _age_lines(ages: dict, current: set[str], now: datetime) -> list[str]:
    from gtm_core.email_campaign_dashboard import figure_ages, health
    from gtm_core.email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS

    lines = []
    for sid, a in sorted(ages["members"].items()):
        shown = printable(sid)
        if a["cause"]:
            lines.append(f"{shown}: {figure_ages.CAUSES[a['cause']]}")
        elif (days := health._figures_age_exact_days(a["stamp"], now)) is not None:
            if days > FIGURES_MAX_AGE_DAYS:
                line = f"{shown}: figures are {int(days)} days old"
                if sid not in current:
                    line += (
                        " and no current campaign lists it — if it was deleted in the sending "
                        f"tool, {_forget_hint(sid)}"
                    )
                lines.append(line)
    return lines


def _redated_lines(meta: dict) -> list[str]:
    """Rows the last refresh re-dated with figures identical to the old ones — information, not a
    to-do: a paused sequence's figures do not move, and a copy of an old file looks the same."""
    return [
        f"{printable(i)}: re-dated {printable(was)} -> {printable(meta['stamps'].get(i, '?'))}, "
        f"{IDENTICAL} (if nothing was fetched, that date is not true)"
        for i, was in sorted(meta["restamped"].items())
        if i in meta["ids"]
    ]


def status(profile: str, *, content_root: Path | None = None, now: datetime | None = None):
    """``(nothing_to_do, lines)`` — read-only: current sequences whose figures are missing,
    old or undated, and any recorded fall."""
    from gtm_core.campaigns_dashboard import build_campaigns
    from gtm_core.email_campaign_dashboard import figure_ages
    from gtm_core.prospects_dashboard import load_sequence_snapshot

    now = now or datetime.now(UTC)
    snap = load_sequence_snapshot(profile, content_root)
    campaigns = build_campaigns(profile, content_root)
    ages = figure_ages.per_id(snap, campaigns, now)
    if ages is None:
        return False, [
            f"No per-sequence figures to read: {FILE_NAME} is missing, unreadable or empty."
        ]
    current = {s["sequence_id"] for c in campaigns.get("campaigns", []) for s in c["sequences"]}
    lines = _age_lines(ages, current, now)
    if ages["edited"]:
        lines.append("the file was changed by hand since the refresh command wrote it")
    meta = snap["file_meta"]
    lines += [
        f"{printable(i)}: {fall_sentence(f)} — {_ack_hint(i)}"
        for i, f in sorted(meta["falls"].items())
    ]
    info = _redated_lines(meta)
    if lines:
        return False, lines + info
    if not current:
        return True, [
            "There are no current sequences: no campaign lists a sequence, so there is nothing to "
            f"check. The file holds {len(ages['members'])} sequence(s) no campaign lists.",
            *info,
        ]
    return True, ["Every current sequence has recent figures.", *info]
