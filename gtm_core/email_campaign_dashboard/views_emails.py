"""The Emails tab (PS20 Phase 2): what each registered sequence says, and the emails themselves.

One row per ``m["messages"]`` entry — a ``cells.toml`` registration — opening to its touches and
its own rendered samples. Samples are claimed by the campaign's date, not by a registration
(``sources.samples_model``), so a sample whose spec no row names renders under ``hand-sent``
rather than vanishing. The 1:1 packs are a short list with their bodies behind a toggle.

The QA state stays beside the copy it qualifies (PRD P1.5): a row's Checks cell carries the
verdict and, when the checked copy is not what would go out today, "not cleared to start".
"""

from __future__ import annotations

from pathlib import Path

from .aggregate import sequence_word
from .config import TAB_LABELS
from .format import _e, _i, _seat_label, figure_span, section


def _later_touches(m: dict) -> str:
    """When the follow-ups land, read from the specs rather than restated — or nothing.

    This sentence said "Touch 2 follows on day 5" — true of one spec on one day, and a
    sentence nobody re-reads when a cadence changes. Then it said "both are in the templates
    below" over any number of later touches, and "later ones are in the templates below"
    over none (PS20 P1.7).
    """
    days = sorted(
        {(t["n"], t["day"]) for t in ((m.get("samples") or {}).get("touches") or []) if t["n"] > 1}
    )
    if not days:
        return ""
    phrase = ", ".join(f"touch {n} on day {d}" for n, d in days[:4])
    return (
        f"{phrase[0].upper()}{phrase[1:]} — the same for everyone; they are in the templates below."
    )


def _has_samples(m: dict) -> bool:
    """Whether this page shows any email at all — the one test for "The emails themselves"."""
    s = m.get("samples") or {}
    return bool(s.get("packs") or s.get("touches") or s.get("rendered"))


def _mail(subject, body, meta):
    return (
        '<div style="border:1px solid var(--line);border-radius:8px;'
        'padding:12px 14px;margin:10px 0">'
        f'<div style="font-size:12px;opacity:.7;margin-bottom:6px">{meta}</div>'
        f"<div><strong>Subject:</strong> <code>{_e(subject)}</code></div>"
        '<pre style="white-space:pre-wrap;margin:8px 0 0;font:inherit">'
        f"{_e(body)}</pre></div>"
    )


def _pack_mails(packs: list) -> str:
    return "".join(
        _mail(
            x["subject"],
            x["body"],
            f"<strong>{_e(x['company'] or x['account'])}</strong> · "
            f"{_e(x['to'])} · <code>{_e(x['capability'])}</code> · {_e(x['words'])} words · "
            "1:1, sent by hand",
        )
        for x in packs
    )


def _rendered_mails(rendered: list) -> str:
    t1 = [x for x in rendered if x["n"] == 1]
    return "".join(
        _mail(
            x["subject"],
            x["body"],
            f"<strong>{_e(x['company'])}</strong> · {_e(x['to'])} · "
            f"<code>{_e(x['spec'])}</code> · touch 1",
        )
        for x in sorted(t1, key=lambda r: (r["company"] or "").lower())
    )


def _template_mails(touches: list) -> str:
    return "".join(
        _mail(
            x["subject"],
            x["body"],
            f"<code>{_e(x['spec'])}</code> · touch {_e(x['n'])}, day {_e(x['day'])} · {_e(x['words'])} words",
        )
        for x in touches
    )


def _touches_rows(msg: dict) -> str:
    return "".join(
        f"<tr><td>Email {c['step']}<br><span class='muted'>day {c['day']}</span></td>"
        + (
            f"<td><code>{_e(c['subject'])}</code></td>"
            if c["subject"]
            else "<td class='muted'>reply inside the first email's thread — no new subject</td>"
        )
        + f"<td class='muted'>{_e(c['opener'][:200])}</td>"
        + f"<td class='num-cell muted'>{c['words']}w</td></tr>"
        for c in msg["copy"]
    )


#: The table's columns. "Go-live", never "State": a default-visible "State" header is banned
#: (`tests/test_email_campaign_dashboard.py`), and the cell holds the one go-live word.
_HEAD = (
    "For whom",
    "First subject",
    "The argument it opens on",
    "People",
    "Emails",
    "Checks",
    "Go-live",
)


def _for_whom(msg: dict) -> str:
    """Seats, then segments, each sorted and de-duplicated. An empty side is left out with its
    separator, so a row never reads "· enterprise" or "cto · "; both empty is an em dash."""
    audience = msg.get("audience") or []
    seats = sorted({_seat_label(c["seat"]) for c in audience if c.get("seat")})
    segments = sorted({c["segment"] for c in audience if c.get("segment")})
    return " · ".join(p for p in (", ".join(seats), ", ".join(segments)) if p) or "—"


def _checks(lint: dict) -> str:
    """The row's QA state, compact. Never-checked reads "unchecked", not a blank that looks
    like checked-and-clean."""
    if not lint:
        return '<span class="muted">unchecked</span>'
    verdict = _e(lint.get("verdict", "?"))
    errors = _i(lint.get("errors"))
    if errors > 0 or lint.get("verdict") == "FAIL":
        what = (
            f" {errors} blocking problem{'s' if errors != 1 else ''}"
            if errors > 0
            else " a blocking check failed"
        )
        cell = f'<span class="pill risk" data-risk="blocking-check">{verdict}</span>{what}'
    else:
        cell = f'<span class="pill">{verdict}</span> no blocking problems'
    if lint.get("drift"):
        cell = '<span class="pill risk" data-risk="re-push">not cleared to start</span> ' + cell
    return cell


def _go_live(m: dict) -> tuple[dict, set, bool]:
    """What `sequence_word` reads: the snapshot rows by id, the ids a campaign lists as
    current (not archived), and whether the snapshot could be read at all."""
    status = m.get("status") or {}
    rows = {x["id"]: x for x in status.get("sequences") or [] if x.get("id")}
    camps = (m.get("campaigns") or {}).get("campaigns") or []
    current = {s["sequence_id"] for c in camps for s in c.get("sequences") or []}
    readable = not (status.get("snapshot") or {}).get("unreadable")
    return rows, current, readable


def _details(m: dict, msg: dict, sid: str) -> str:
    samples = m.get("samples") or {}
    stem = Path(msg.get("spec") or "").stem
    drift = (
        '<p class="note">The check describes the reviewed files, not what would go out today. '
        f"See <strong>{_e(TAB_LABELS['ops'])}</strong> for what is left to do.</p>"
        if (msg.get("lint") or {}).get("drift")
        else ""
    )
    mails = _rendered_mails(
        [x for x in samples.get("rendered") or [] if x["spec"] == stem]
    ) + _template_mails([x for x in samples.get("touches") or [] if x["spec"] == stem])
    return (
        f'<tr class="more"><td colspan="{len(_HEAD)}"><details data-more="{_e(sid)}">'
        "<summary>The emails, and a rendered sample</summary>"
        "<table><thead><tr><th>Email</th><th>Subject</th><th>The argument it opens on</th>"
        f"<th>Length</th></tr></thead><tbody>{_touches_rows(msg)}</tbody></table>"
        f"{drift}{mails}</details></td></tr>"
    )


def _row(m: dict, msg: dict, go: tuple[dict, set, bool]) -> str:
    rows, current, readable = go
    sid = msg["sequence_id"]
    first = next((c for c in msg.get("copy") or [] if c.get("subject")), None)
    subject = f"<code>{_e(first['subject'])}</code>" if first else '<span class="muted">—</span>'
    opener = _e(first["opener"][:200]) if first else ""
    people = sum(c["enrolled"] for c in msg.get("audience") or [])
    word = sequence_word(rows.get(sid), sid in current, readable)
    return (
        f'<tr data-sequence="{_e(sid)}">'
        f"<td>{_e(_for_whom(msg))}</td>"
        f"<td>{subject}</td>"
        f'<td class="muted">{opener}</td>'
        f'<td class="num-cell">{figure_span(f"email-people-{sid}", people)}</td>'
        f'<td class="num-cell">{figure_span(f"email-steps-{sid}", len(msg.get("copy") or []))}</td>'
        f"<td>{_checks(msg.get('lint') or {})}</td>"
        f'<td><span class="pill" data-figure="email-word-{_e(sid)}">{_e(word)}</span></td>'
        "</tr>" + _details(m, msg, sid)
    )


def _email_table(m: dict) -> str:
    messages = m.get("messages") or []
    if not messages:
        return ""
    go = _go_live(m)
    later = _later_touches(m)
    head = "".join(f"<th>{_e(h)}</th>" for h in _HEAD)
    return (
        '<div class="card"><h2>What each sequence says</h2>'
        '<p class="note">Each row is a sequence as it is registered. A row opens to its emails.'
        + (f" {_e(later)}" if later else "")
        + "</p>"
        f"<table><thead><tr>{head}</tr></thead><tbody>"
        + "".join(_row(m, msg, go) for msg in messages)
        + "</tbody></table></div>"
    )


def _hand_sent(m: dict) -> str:
    """Samples whose spec no registered row claims — the role-inbox lane's emails, say.
    Without this card they would vanish from the page."""
    if not _has_samples(m):
        return ""
    s = m.get("samples") or {}
    claimed = {Path(msg.get("spec") or "").stem for msg in m.get("messages") or []}
    rendered = [x for x in s.get("rendered") or [] if x["spec"] not in claimed]
    touches = [x for x in s.get("touches") or [] if x["spec"] not in claimed]
    if not (rendered or touches):
        return ""
    return (
        '<div class="card"><h2>Emails sent by hand from a template</h2>'
        '<p class="note">The templates are shown with their merge tags intact: filling them in '
        "here would be a second renderer beside the merge-render gate, and a second renderer "
        "is how a page starts showing copy nobody sends.</p>"
        f"{_rendered_mails(rendered)}{_template_mails(touches)}</div>"
    )


def _packs_list(m: dict) -> str:
    """The 1:1 lane as a short list. The campaign's own samples when it has any (they carry
    bodies); else the rollup, which lists only capability-era packs and carries no bodies."""
    packs = (m.get("samples") or {}).get("packs") or (m.get("packs") or {}).get("packs") or []
    if not packs:
        return ""
    items = []
    for p in packs:
        bits = [f"<strong>{_e(p.get('company') or p.get('account') or '')}</strong>"]
        if p.get("date"):
            bits.append(f'<span class="muted">{_e(p["date"])}</span>')
        if p.get("capability"):
            bits.append(f"<code>{_e(p['capability'])}</code>")
        body = (
            f"<details><summary>The email</summary>{_pack_mails([p])}</details>"
            if p.get("body")
            else ""
        )
        items.append(f"<li>{' · '.join(bits)}{body}</li>")
    return (
        f'<div class="card"><h2>1:1 emails, written by hand ({len(packs)})</h2>'
        f"<ul>{''.join(items)}</ul></div>"
    )


def _emails_view(m: dict) -> str:
    return (
        section("email-table", _email_table(m))
        + section("hand-sent", _hand_sent(m))
        + section("packs-list", _packs_list(m))
    )
