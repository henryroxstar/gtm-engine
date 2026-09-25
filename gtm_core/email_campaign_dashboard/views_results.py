"""The Results tab: what did we learn? (PS20 PRD Phase 2, TP T2.13.)

Before a campaign's first contact it says what the run will learn and when we will know;
after, the figures. "First contact" is COUNTED (the campaign's current people contacted is
above 0), never read off the sending tool's status word: a paused campaign that has contacted
people is past it. One whose snapshot is unreadable is known to be neither: it shows no
figures line and no "if sending starts" date (sends may already be under way), but keeps the
manifest's hypotheses, which are the campaign's own words and make no before/after claim.
"""

from __future__ import annotations

from ..campaigns_dashboard import _hypotheses_html, _int
from ..power import detectable_lift
from .aggregate import (
    _scope_figures,
    bounce_rate,
    campaign_contacted,
    sending_tiles,
    sent_heading,
)
from .config import BOUNCE_RISK_PCT, TAB_LABELS
from .forecast import _lanes, when_done
from .format import _e, _stat, figure_span, section
from .views_learn import _can_answer_block


def _headline_learnings(m: dict) -> str:
    """The learning questions, opened rather than folded into a `<details>`.

    The panel this came from opened on two hand-written hypothesis cards that had outlived
    the run they were written for, while the questions the run actually answered sat in the
    manifest. Rendered from ``[[experiment.will_learn]]`` so there is one home for the fact.
    """
    blocks = ""
    for c in m["campaigns"]["campaigns"]:
        for i, w in enumerate((c.get("experiment") or {}).get("will_learn") or [], 1):
            blocks += (
                '<div class="hyp"><div class="hyphead"><span class="claim">'
                f"<b>{i}. {_e(str(w.get('question', '')))}</b></span></div>"
                f'<div class="hypbody">{_e(str(w.get("how", "")))}</div></div>'
            )
    if not blocks:
        return ""
    return (
        '<div class="card"><h2>What this run is actually for</h2>'
        "<p class='note'>The campaign's own questions, in the order its manifest lists them."
        "</p>" + blocks + "</div>"
    )


def _people(n: int) -> str:
    return "person" if n == 1 else "people"


def _goal_row(slug: str, k: str, v: dict, readable: bool) -> str:
    """One goal row. A percentage only where the units match: the snapshot counts PEOPLE
    contacted, so an ``emails`` goal is shown in its own unit beside that figure, with no
    percentage and no "N of M" (PS20 P1.4). The mark carries the campaign's slug: one card
    per campaign, so one name never holds two values."""
    actual = f"{v['actual']:,}" if readable else "—"
    if k == "emails":
        shown = f"{actual} {_people(v['actual'])} contacted"
        return (
            f"<tr><td>{_e(k)}</td><td class='num-cell'>{v['target']:,} emails</td>"
            f'<td class="num-cell" colspan="2">{figure_span(f"goal-emails-{slug}", shown)}'
            "</td></tr>"
        )
    pct = f"{v['pct']}%" if readable else "—"
    return (
        f"<tr><td>{_e(k)}</td><td class='num-cell'>{v['target']:,}</td>"
        f"<td class='num-cell'>{actual}</td><td class='num-cell muted'>{pct}</td></tr>"
    )


def _outcome_tiles(m: dict, fig: dict) -> str:
    """The scope's three outcome tiles, read off ``fig`` (PS20 P1.2), never re-summed. A
    ``None`` is a refusal and renders as an em dash."""
    t = sending_tiles(m, fig)
    sent, replied = t["contacted"], t["replied"]
    target_rate, rate_why = fig["target_rate"]
    rate_txt = f"{replied / sent:.1%}" if sent else "—"  # POOLED: Σreplied / Σsent, correct
    target_txt = "—" if target_rate is None else f"{target_rate:.1%}"
    # "nothing sent yet" only on a READ zero: an unreadable snapshot is a refusal, not a zero.
    idle = "sending figures unavailable" if sent is None else "" if sent else "nothing sent yet"
    if rate_why:
        rate_sub = f"no single target — {rate_why}"
    elif target_rate is None:
        rate_sub = idle or "no target declared"
    else:
        weighted = " (weighted by prospects)" if fig["rates_differ"] else ""
        rate_sub = f"target {target_txt}{weighted}" + (f" · {idle}" if idle else "")
    return (
        '<div class="stats">'
        + _stat(
            sent,
            "people contacted",
            sub_html=t["goal"],
            raw={"contacted": sent, "planned": fig["planned"][0]},
            src={
                "contacted": "sum:campaigns.actuals.sent",
                "planned": "sum-complete:campaigns.targets.emails",
            },
            figure="contacted-current",
        )
        + _stat(
            rate_txt,
            "reply rate",
            rate_sub,
            raw={"replied": replied, "sent": sent},
            src="pooled:campaigns.actuals.replied/campaigns.actuals.sent",
        )
        + _stat(
            replied, "replies so far", raw={"value": replied}, src="sum:campaigns.actuals.replied"
        )
        + "</div>"
    )


def _started(own: dict | None) -> bool:
    """Past first contact: this campaign's own current people contacted is above 0."""
    return bool(own and own["current"] > 0)


def _before_block(c: dict) -> str:
    """What the run will learn, from the manifest's own words (PS20 P1.7)."""
    x = c.get("experiment") or {}
    out = (
        _hypotheses_html(x) or "<p class='note'>This campaign's manifest states no hypotheses.</p>"
    )
    if x.get("what_it_tells_us"):
        out += f"<p><strong>What it can tell us:</strong> {_e(x['what_it_tells_us'])}</p>"
    if x.get("what_it_cant_tell_us"):
        out += f"<p><strong>What it will not tell us:</strong> {_e(x['what_it_cant_tell_us'])}</p>"
    return out


def _seq_bounce_rate(live: dict) -> str:
    """Bounce rate for one sequence: ``bounced / (delivered + bounced)`` using only the
    per-email status block (``bounce_source == "emails"``).

    Without the per-email status block (the prospect-level fallback, or the flat-dict path),
    the rate is "not available". If ``delivered + bounced == 0``, renders as em dash.
    A risk pill is added when the rate is strictly greater than ``BOUNCE_RISK_PCT`` (3%).
    """
    if live.get("bounce_source") != "emails":
        return '<span class="muted">not available</span>'
    bounced = _int(live.get("bounced", 0))
    delivered = _int(live.get("delivered", 0))
    rate = bounce_rate(bounced, delivered)
    if rate is None:
        return '<span class="muted">—</span>'
    text = f"{rate:.1f}%"
    if rate > BOUNCE_RISK_PCT:
        return f"<span class='pill risk' data-risk='bounce-rate'>{text}</span>"
    return text


def _optout_line(m: dict) -> str:
    """The Results tab's opt-out figure (PRD P1.5): N people asked not to be contacted;
    M of N on the do-not-contact list."""
    inbound = m.get("inbound") or {}
    detected = int(inbound.get("optout_detected") or 0)
    unattributable = int(inbound.get("optout_unattributable") or 0)
    if not detected and not unattributable:
        return ""
    added = int(inbound.get("optout_dnc_added") or 0)
    person_word = "person" if detected == 1 else "people"
    unattr_clause = f" ({unattributable} unattributable)" if unattributable else ""
    return (
        f"<p><span class='pill risk' data-risk='opted-out'>{detected}</span> {person_word} "
        f"asked not to be contacted; {added} of {detected} on the do-not-contact list{unattr_clause}.</p>"
    )


def _sequence_results_table(c: dict) -> str:
    """Per-sequence live outcomes: contacted, replied, interested, not now, not interested,
    unsubscribed, out of office, bounces, bounce rate, meetings (tagged in sending tool), pilots."""
    seqs = c.get("sequences") or []
    if not seqs:
        return ""
    rows = []
    for s in seqs:
        live = s.get("live") or {}
        sid = s.get("sequence_id") or s.get("id") or ""
        name = s.get("title") or s.get("name") or live.get("name") or sid or "Sequence"
        contacted = _int(live.get("sent", 0))
        replied = _int(live.get("replied", 0))
        interested = _int(live.get("interested", 0))
        not_now = _int(live.get("not_now", 0))
        not_interested = _int(live.get("not_interested", 0))
        unsubscribed = _int(live.get("unsubscribed", 0))
        ooo = _int(live.get("out_of_office", 0))
        if live.get("bounce_source") == "emails":
            bounces = f"{_int(live.get('bounced', 0)):,}"
        else:
            bounces = '<span class="muted">—</span>'
        br = _seq_bounce_rate(live)
        meetings = _int(live.get("meetings", 0))
        rows.append(
            f"<tr>"
            f"<td><strong>{_e(name)}</strong></td>"
            f"<td class='num-cell'>{contacted:,}</td>"
            f"<td class='num-cell'>{replied:,}</td>"
            f"<td class='num-cell'>{interested:,}</td>"
            f"<td class='num-cell'>{not_now:,}</td>"
            f"<td class='num-cell'>{not_interested:,}</td>"
            f"<td class='num-cell'>{unsubscribed:,}</td>"
            f"<td class='num-cell'>{ooo:,}</td>"
            f"<td class='num-cell'>{bounces}</td>"
            f"<td class='num-cell'>{br}</td>"
            f"<td class='num-cell'>{meetings:,}</td>"
            f"<td class='num-cell muted'>not tracked</td>"
            f"</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Sequence</th>"
        "<th class='num-cell'>Contacted</th>"
        "<th class='num-cell'>Replied</th>"
        "<th class='num-cell'>Interested</th>"
        "<th class='num-cell'>Not now</th>"
        "<th class='num-cell'>Not interested</th>"
        "<th class='num-cell'>Unsubscribed</th>"
        "<th class='num-cell'>Out of office</th>"
        "<th class='num-cell'>Bounces</th>"
        "<th class='num-cell'>Bounce rate</th>"
        "<th class='num-cell'>Meetings <span class='note'>(tagged in the sending tool)</span></th>"
        "<th class='num-cell'>Pilots</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _campaign_card(c: dict, own: dict | None, m: dict | None = None) -> str:
    """One campaign: what it promised and where it is, then the figures once it has
    contacted anyone, or what it will learn before that. The sentence and the earlier-run
    line sit before the goal table."""
    slug, readable = c.get("slug", "?"), own is not None
    rows = "".join(_goal_row(slug, k, v, readable) for k, v in c["promised_vs_actual"].items())
    earlier = (
        f"<p class='note'><strong>Earlier run.</strong> An earlier run contacted "
        f"{own['earlier']:,} more {_people(own['earlier'])}, not counted here.</p>"
        if own and own["earlier"]
        else ""
    )
    goals = (
        "<table><thead><tr><th>Measure</th><th>Goal</th><th>So far</th><th></th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
        if rows
        else "<p class='note'>No goals recorded for this campaign.</p>"
    )
    if _started(own):
        n, r = own["current"], own["replied"]
        tail = (
            f"<p>{figure_span(f'results-contacted-{slug}', n)} {_people(n)} contacted · "
            f"{figure_span(f'results-replied-{slug}', r)} {'reply' if r == 1 else 'replies'}</p>"
            + _optout_line(m or {})
            + _sequence_results_table(c)
        )
    else:
        tail = _before_block(c)
    return (
        f'<div class="card" data-campaign="{_e(slug)}"><h2>{_e(c["title"])}</h2>'
        "<p class='note'>What this campaign promised, and where it actually is: "
        f"{_e(sent_heading(own).lower())}.</p>{earlier}{goals}{tail}</div>"
    )


def _when_we_know(m: dict, before: bool, unknown: str | None) -> str:
    """The forecast's own finish date (``forecast.when_done``), shown while some campaign is
    before first contact; its refusal reason when it cannot date one. ``unknown`` is the
    scope's refusal of the contacted figure: then nobody knows whether sending has started,
    so the "if sending starts" date is not offered, and the refusal says why in the page's
    own words for it."""
    if unknown:
        body = f"<p class='note'>Can't tell whether sending has started: {_e(unknown)}.</p>"
        return f'<div class="card"><h2>When we will know</h2>{body}</div>'
    if not before:
        return ""
    sentence, why = when_done(m)
    body = (
        f"<p>{_e(sentence)}</p>"
        if sentence
        else f"<p class='note'>No finish date: {_e(why)}. The detail is under "
        f"{_e(TAB_LABELS['ops'])}.</p>"
    )
    return f'<div class="card"><h2>When we will know</h2>{body}</div>'


def _small_numbers(m: dict) -> str:
    """The smallest difference the largest group could show (``power.detectable_lift``), or
    that none could: said only when the arithmetic agrees (PS20 P1.7 Rule B)."""
    lanes = _lanes(m)
    if not lanes:
        return ""
    base = m["cells"]["baseline"]
    lifts = [x for ln in lanes if (x := detectable_lift(ln["people"], base))]
    line = (
        "<strong>Small numbers.</strong> At the numbers planned, the largest group here could "
        f"only show a difference of {figure_span('smallest-lift', f'{min(lifts):g}×')} or more; "
        "a smaller gap between groups reads as chance."
        if lifts
        else "<strong>Small numbers.</strong> No group here is big enough for any difference in "
        "replies to show, so read replies one by one, not as rates."
    )
    return f'<div class="card"><h2>Reading small numbers</h2><p class="note">{line}</p></div>'


def _results_view(m: dict) -> str:
    fig = _scope_figures(m)
    camps = m["campaigns"]["campaigns"]
    owns = [(c, campaign_contacted(fig, c)) for c in camps]
    cards = "".join(_campaign_card(c, own, m) for c, own in owns)
    before = any(not _started(own) for _c, own in owns)
    contacted, unknown = fig["contacted"]
    return (
        section("results-figures", _outcome_tiles(m, fig))
        + section("campaign-results", cards)
        + section("when-we-know", _when_we_know(m, before, unknown if contacted is None else None))
        + section("small-numbers", _small_numbers(m))
        + section("learnings", _headline_learnings(m))
        + section("can-answer", _can_answer_block(m))
    )
