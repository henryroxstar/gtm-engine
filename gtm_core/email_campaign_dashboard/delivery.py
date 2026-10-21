"""Delivery health — unsubscribes and bounces — and the sequences added up by seat and argument.

The sending tool reports one row of totals per sequence. Until 2026-10-06 the page showed those
rows as bare counts, unsorted, with no rate beside them, and nothing anywhere said that 49 of
244 people contacted had unsubscribed. This module turns the same rows into:

* a page strip when unsubscribes or bounces run above the level that harms sending domains
  (:func:`delivery_strip`);
* a rate beside each count in the Results tab's sequence table, worst first, with the sequences
  that have contacted nobody folded into one line (:func:`table_order`, :func:`unsent_line`);
* the same totals added up by seat and by argument (:func:`rollup_html`), which is also what the
  Insights tab's "Replies by Seat / by Hook" cards read (:func:`groups`) — so the two tabs cannot
  disagree about how many people were contacted.

**Units.** ``contacted``/``replied``/``unsubscribed`` count PEOPLE; ``bounced``/``delivered`` count
EMAILS and are only trustworthy where the per-email status block was present
(``bounce_source == "emails"``, see ``campaigns_dashboard.build_campaigns``). The two never mix.

**What is not added up: segment.** Sequences are split by seat and argument, not by segment —
one sequence routinely reaches startups, enterprises and builders at once — so dividing its
single total between segments would be a guess. That needs a result per person, which the
sending tool's per-sequence totals do not carry. The roll-up says so instead of guessing.

**Where a sequence's seat and argument come from.** What the page already knows: the seats of
the people registered to it (``m["messages"][…]["audience"]``, the same cells the Emails tab
reads) and its spec's declared ``premise`` (``portfolio.enrich_portfolio_metadata``). More than
one value is *Mixed*, none is *Not recorded* — never the first one alphabetically, which is how
the Emails tab picks a lead seat and is fine for a label but wrong for a sum.
"""

from __future__ import annotations

from .aggregate import _scope_figures, bounce_rate
from .config import BOUNCE_RISK_PCT, UNSUB_RISK_PCT
from .format import _e, _i, _seat_label
from .frontier import extract_angle_title

NOT_RECORDED = "Not recorded"
#: Counters whose refusal by the snapshot loader makes a sequence's people figures unusable.
_PEOPLE = frozenset({"sent", "replied", "unsubscribed"})


def rate(part: int, whole: int) -> float | None:
    """``part / whole`` as a percentage, or ``None`` with nothing to divide by."""
    return 100 * part / whole if whole else None


def rate_cell(part: int, whole: int, risk_pct: float | None = None, risk: str = "") -> str:
    """A rate to one decimal; a risk pill when it is strictly above ``risk_pct``; a dash with no
    denominator. Same shape as ``views_results._seq_bounce_rate``'s bounce pill."""
    r = rate(part, whole)
    if r is None:
        return '<span class="muted">—</span>'
    text = f"{r:.1f}%"
    if risk_pct is not None and r > risk_pct:
        return f"<span class='pill risk' data-risk='{risk}'>{text}</span>"
    return text


def live_rate(live: dict, field: str, risk_pct: float | None = None, risk: str = "") -> str:
    """One sequence's ``field`` over its people contacted. A counter the snapshot loader refused
    makes the rate a dash, never a rate computed from a stand-in zero."""
    if {"sent", field} & set(live.get("refused") or []):
        return '<span class="muted">—</span>'
    return rate_cell(_i(live.get(field)), _i(live.get("sent")), risk_pct, risk)


def bounce_cell(bounced: int | None, delivered: int | None) -> str:
    """A bounce rate over emails, or *not available* where the sequence sent no per-email
    status block."""
    if bounced is None or delivered is None:
        return '<span class="muted">not available</span>'
    return rate_cell(bounced, bounced + delivered, BOUNCE_RISK_PCT, "bounce-rate")


def sequence_name(s: dict) -> str:
    live = s.get("live") or {}
    return (
        s.get("title")
        or s.get("name")
        or live.get("name")
        or s.get("sequence_id")
        or s.get("id")
        or "Sequence"
    )


def _one(values: set, label, mixed: str) -> str:
    vals = {v for v in values if v}
    if not vals:
        return NOT_RECORDED
    return label(next(iter(vals))) if len(vals) == 1 else mixed


def _argument_label(premise: str) -> str:
    return premise.replace("-", " ").replace("_", " ").capitalize()


def sequence_rows(m: dict) -> list[dict]:
    """One row per current sequence in scope, in campaign order, each sequence ONCE even when
    two campaigns list it (both would otherwise add its people into a group total)."""
    seats: dict[str, set] = {}
    premises: dict[str, set] = {}
    for msg in m.get("messages") or []:
        sid = msg.get("sequence_id")
        seats.setdefault(sid, set()).update(c.get("seat") for c in msg.get("audience") or [])
        premises.setdefault(sid, set()).add((msg.get("portfolio") or {}).get("premise"))
    rows: list[dict] = []
    seen: set[str] = set()
    for c in (m.get("campaigns") or {}).get("campaigns") or []:
        for s in c.get("sequences") or []:
            sid = s.get("sequence_id") or s.get("id") or ""
            if sid in seen:
                continue
            seen.add(sid)
            live = s.get("live") or {}
            refused = set(live.get("refused") or [])
            emails = live.get("bounce_source") == "emails" and "bounced" not in refused
            name = sequence_name(s)
            rows.append(
                {
                    "id": sid,
                    "name": name,
                    "angle": extract_angle_title(name),
                    "seat": _one(seats.get(sid, set()), _seat_label, "Mixed seats"),
                    "argument": _one(premises.get(sid, set()), _argument_label, "Mixed arguments"),
                    "readable": not (_PEOPLE & refused),
                    "sent_refused": "sent" in refused,
                    "contacted": _i(live.get("sent")),
                    "replied": _i(live.get("replied")),
                    "unsubscribed": _i(live.get("unsubscribed")),
                    "loaded": _i(live.get("loaded")),
                    "bounced": _i(live.get("bounced")) if emails else None,
                    "delivered": _i(live.get("delivered")) if emails else None,
                }
            )
    return rows


def rollup(rows: list[dict], key: str) -> list[dict]:
    """``rows`` that have contacted anyone, added up by ``key``, largest group first. A group's
    bounce figures stay ``None`` unless EVERY sequence in it reported bounces: a rate over some
    of a group's emails would wear the whole group's label."""
    groups: dict[str, dict] = {}
    for r in rows:
        if not r["readable"] or not r["contacted"]:
            continue
        g = groups.setdefault(
            r[key],
            {"label": r[key], "sequences": 0, "contacted": 0, "replied": 0, "unsubscribed": 0}
            | {"bounced": 0, "delivered": 0, "bounce_rows": 0},
        )
        g["sequences"] += 1
        for f in ("contacted", "replied", "unsubscribed"):
            g[f] += r[f]
        if r["bounced"] is not None:
            g["bounced"] += r["bounced"]
            g["delivered"] += r["delivered"]
            g["bounce_rows"] += 1
    out = sorted(groups.values(), key=lambda g: (-g["contacted"], g["label"]))
    for g in out:
        if g["bounce_rows"] != g["sequences"]:
            g["bounced"] = g["delivered"] = None
    return out


def groups(m: dict, key: str) -> list[tuple[str, int, int]]:
    """``[(label, contacted, replied), …]`` — the Insights tab's group cards read this, so the
    count they show is the one the Results tab shows."""
    return [(g["label"], g["contacted"], g["replied"]) for g in rollup(sequence_rows(m), key)]


def table_order(seqs: list[dict]) -> tuple[list[dict], list[dict]]:
    """The Results table's rows, worst unsubscribe rate first, and the sequences that have
    contacted nobody, which the table folds into one line. A sequence whose ``sent`` the loader
    refused stays in the table: nobody knows it is unsent."""
    sent, unsent = [], []
    for s in seqs:
        live = s.get("live") or {}
        if not _i(live.get("sent")) and "sent" not in (live.get("refused") or []):
            unsent.append(s)
        else:
            sent.append(s)

    def worst(s: dict) -> tuple:
        live = s.get("live") or {}
        u = rate(_i(live.get("unsubscribed")), _i(live.get("sent")))
        return (-(u if u is not None else -1), -_i(live.get("sent")))

    return sorted(sent, key=worst), unsent


def unsent_line(unsent: list[dict]) -> str:
    if not unsent:
        return ""
    n = len(unsent)
    loaded = sum(_i((s.get("live") or {}).get("loaded")) for s in unsent)
    names = "".join(f"<li>{_e(sequence_name(s))}</li>" for s in unsent)
    word = "sequence" if n == 1 else "sequences"
    return (
        f'<details class="note" data-unsent><summary>{n:,} {word} not sent yet '
        f"({loaded:,} people loaded)</summary><ul>{names}</ul></details>"
    )


def _rollup_table(head: str, gs: list[dict]) -> str:
    rows = "".join(
        f"<tr><td>{_e(g['label'])}</td><td class='num-cell'>{g['sequences']:,}</td>"
        f"<td class='num-cell'>{g['contacted']:,}</td><td class='num-cell'>{g['replied']:,}</td>"
        f"<td class='num-cell'>{rate_cell(g['replied'], g['contacted'])}</td>"
        f"<td class='num-cell'>{g['unsubscribed']:,}</td>"
        f"<td class='num-cell'>{rate_cell(g['unsubscribed'], g['contacted'], UNSUB_RISK_PCT, 'unsub-rate')}</td>"
        f"<td class='num-cell'>{bounce_cell(g['bounced'], g['delivered'])}</td></tr>"
        for g in gs
    )
    return (
        f"<table><thead><tr><th>{_e(head)}</th><th class='num-cell'>Sequences</th>"
        "<th class='num-cell'>Contacted</th><th class='num-cell'>Replied</th>"
        "<th class='num-cell'>Reply rate</th><th class='num-cell'>Unsubscribed</th>"
        "<th class='num-cell'>Unsubscribe rate</th><th class='num-cell'>Bounce rate</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def rollup_html(m: dict) -> str:
    """The Results tab's roll-up card: every sequence in scope that has contacted anyone, added
    up by seat and by argument. Nothing before the first contact — there is nothing to add."""
    rows = sequence_rows(m)
    if not any(r["readable"] and r["contacted"] for r in rows):
        return ""
    return (
        '<div class="card" data-rollup="seat-argument"><h2>Added up by seat and by argument</h2>'
        "<p class='note'>The sequences in the campaigns above that have contacted anyone, added "
        "up. A sequence that reached more than one seat, or was written around more than one "
        "argument, counts under Mixed. Segment is not added up: a single sequence reaches "
        "startups, enterprises and builders alike, and the sending tool gives one total per "
        "sequence, so dividing it between segments would be a guess.</p>"
        + _rollup_table("Seat", rollup(rows, "seat"))
        + _rollup_table("Argument", rollup(rows, "argument"))
        + "</div>"
    )


def unsub_sub(unsubscribed: int | None, contacted: int | None, contacted_html: str) -> str:
    """The Overview tile's sub-line: the rate, flagged above ``UNSUB_RISK_PCT``, over the
    campaign's own contacted figure (``contacted_html``, a marked figure)."""
    if unsubscribed is None or contacted is None:
        return "sending figures unavailable"
    if not contacted:
        return "nothing sent yet"
    shown = rate_cell(unsubscribed, contacted, UNSUB_RISK_PCT, "unsub-rate")
    return f"{shown} of {contacted_html} contacted · normal is under {UNSUB_RISK_PCT}%"


def _worst(rows: list[dict], part: str, whole) -> dict | None:
    hit = [r for r in rows if r[part]]
    return max(hit, key=lambda r: (r[part], rate(r[part], whole(r)) or 0), default=None)


def delivery_strip(m: dict) -> str:
    """A page-wide RISK strip (not a warn: warn means a number cannot be trusted, and these
    numbers can) when the scope's unsubscribe or bounce rate is strictly above its threshold.
    Totals are the scope's own figures (``_scope_figures``), the same ones its tiles show; the
    sequence named is the one contributing most. An unreadable snapshot raises nothing — a
    refusal is not a zero, and not a risk either."""
    fig = _scope_figures(m)
    split, labels, bounces = fig["contacted"][0], fig["labels"][0], fig["bounces"][0]
    if split is None or labels is None:
        return ""
    rows = [r for r in sequence_rows(m) if r["readable"] and r["contacted"]]
    contacted, unsub = split["current"], labels.get("unsubscribed", 0)
    reasons, sentences = [], []
    u = rate(unsub, contacted)
    if u is not None and u > UNSUB_RISK_PCT:
        s = (
            f"Unsubscribes are high: {unsub:,} of {contacted:,} people contacted ({u:.1f}%) "
            f"unsubscribed. Cold email normally stays under {UNSUB_RISK_PCT}%, and a rate this "
            "high can hurt the sending domains whoever clicked: a mail scanner following the "
            "link counts the same as a person."
        )
        if w := _worst(rows, "unsubscribed", lambda r: r["contacted"]):
            s += (
                f" Most came from {_e(w['name'])}: {w['unsubscribed']:,} of {w['contacted']:,} "
                f"({rate(w['unsubscribed'], w['contacted']):.1f}%)."
            )
        reasons.append("unsub-rate")
        sentences.append(s)
    b, d = (bounces["bounced"], bounces["delivered"]) if bounces else (0, 0)
    br = bounce_rate(b, d)
    if br is not None and br > BOUNCE_RISK_PCT:
        s = (
            f"Bounces are high: {b:,} of {b + d:,} emails ({br:.1f}%) bounced. Above "
            f"{BOUNCE_RISK_PCT}%, mailbox providers start to distrust the sending domains."
        )
        known = [r for r in rows if r["bounced"] is not None]
        if w := _worst(known, "bounced", lambda r: r["bounced"] + r["delivered"]):
            total = w["bounced"] + w["delivered"]
            s += (
                f" Most came from {_e(w['name'])}: {w['bounced']:,} of {total:,} emails "
                f"({rate(w['bounced'], total):.1f}%)."
            )
        reasons.append("bounce-rate")
        sentences.append(s)
    if not sentences:
        return ""
    body = "".join(f"<p>{s}</p>" for s in sentences)
    return f'<div class="card risk" data-risk="{" ".join(reasons)}">{body}</div>'


def sending_split(emails, enrolled: dict, statuses: dict) -> dict[str, int]:
    """People in the sending tool by whether their sequence is sending now or loaded and
    paused, from the sequence's own live status. No status is ``unknown``, never paused."""
    from collections import Counter

    from ..prospect_lede import LIVE_STATUSES

    out: Counter = Counter()
    for email in emails:
        st = str(statuses.get(enrolled.get(email, ""), "") or "").strip().lower()
        out["sending" if st in LIVE_STATUSES else "paused" if st == "paused" else "unknown"] += 1
    return dict(out)


def sending_split_for(profile: str, content_root, routed, status: dict) -> dict[str, int] | None:
    """:func:`sending_split` for the routed contacts counted "in the sending tool", joined
    through the one enrolled-set reader. ``None`` when nobody is in the sending tool."""
    from ..lanes.context import load_enrolled
    from ..paths import resolve_content_root
    from ..prospect_paths import sequences_dir

    emails = [
        (c.get("email") or "").strip().lower()
        for c in routed or []
        if c.get("status") == "in_sending_tool"
    ]
    if not emails:
        return None
    root = content_root or resolve_content_root()
    enrolled, _ = load_enrolled(profile, root, sequences_dir(profile, root))
    statuses = {r.get("id"): r.get("status") for r in status.get("sequences") or []}
    return sending_split(emails, enrolled, statuses)
