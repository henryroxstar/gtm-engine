from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from .aggregate import _cadence_split, _ceiling_sub, _planned_sub, _scope_figures
from .config import BENCHMARKS, PRIMARY_BENCHMARK
from .format import _e, _i, _pct, _stat, roster_gap, scope_label

# --- views ----------------------------------------------------------------------


def _forecast_block(m: dict) -> str:
    """How long the run takes once it starts, and what sets the ceiling.

    Every duration here is arithmetic over a number printed beside it. A schedule quoted
    without its divisor is exactly the figure that outlives the list it was computed from —
    "about five weeks" was true of 1,359 emails and is still sitting in the campaign file
    now that the checked list is smaller.
    """
    fig = _scope_figures(m)
    camps = m["campaigns"]["campaigns"]
    window: dict = next((c["window"] for c in camps if c.get("window")), {})
    tgt: dict = next((c["targets"] for c in camps if c.get("targets")), {})
    cap, cap_why = fig["cap"]
    boxes = fig["boxes"][0]
    touches, touches_why = fig["touches"]
    touches = touches or max(
        (_i((msg.get("lint") or {}).get("touches")) for msg in m["messages"]), default=0
    )
    # REFUSE THE WHOLE BLOCK when the scope has no single cadence or ceiling. Every row
    # below divides by one of them: `people_day = cap // touches`, the send-day count, the
    # finish date, "each person receives N emails over D days". At two cadences none of
    # those sentences is true of any sequence on the page — and a duration is exactly the
    # figure that outlives the list it was computed from. A card cannot carry that much
    # "why" in a stat sub-line, so it is refused at card level with the numbers named.
    if cap_why or touches_why:
        return _cadence_split(m, camps, cap_why or touches_why)
    if not (cap and touches):
        return ""

    # The sequence's own span: the last person enrolled still has to live through it after
    # the final new enrolment, so it is added to the sending days rather than hidden in them.
    days_seen = [c["day"] for msg in m["messages"] for c in msg["copy"]]
    span = (max(days_seen) - min(days_seen)) if days_seen else 0
    tail = -(-span * 5 // 7)

    reviewed = sum(_i((msg.get("lint") or {}).get("rows")) for msg in m["messages"])
    bases: list[tuple[str, int]] = []
    if reviewed:
        bases.append(("the list that was checked, once it is loaded", reviewed))
    if _i(tgt.get("prospects")) and _i(tgt.get("prospects")) != reviewed:
        bases.append(("the campaign's own qualified-and-sendable target", _i(tgt["prospects"])))

    start = date.today()
    while start.weekday() > 4:
        start += timedelta(days=1)

    rows = ""
    for label, people in bases:
        emails = people * touches
        send_days = -(-emails // cap)
        total = send_days + tail
        finish, added = start, 0
        while added < total:
            finish += timedelta(days=1)
            if finish.weekday() < 5:
                added += 1
        rows += (
            f"<tr><td>{_e(label)}</td><td class='num-cell'>{people:,}</td>"
            f"<td class='num-cell'>{emails:,}</td>"
            f"<td class='num-cell'>{send_days} + {tail}</td>"
            f"<td class='num-cell'>{total} working days</td>"
            f"<td class='muted'>about {total / 5:.0f} weeks · {finish:%-d %b}</td></tr>"
        )

    per_box = (cap // boxes) if boxes else 0
    people_day = cap // touches
    month = people_day * 21
    why = (
        f"<strong>{cap} emails a day is the ceiling, and it is {boxes} mailboxes at "
        f"{per_box} a day each.</strong> "
        if boxes and per_box
        else f"<strong>{cap} emails a day is the ceiling.</strong> "
    )
    cap_note = window.get("capacity_note") or ""
    caveat = window.get("caveat") or ""

    return f"""
      <div class="card">
        <h2>How long it runs, once it starts</h2>
        <table><thead><tr><th>Counted on</th><th>People</th><th>Emails</th>
        <th>Sending + tail</th><th>Total</th><th>Done by</th></tr></thead>
        <tbody>{rows}</tbody></table>
        <p class="note">Each person receives {touches} emails over {span} days, so the run is
        not finished when the last email is <em>started</em> — the last person enrolled still
        has their own {span}-day sequence to live through. That is the "+ {tail}" column.
        Dates assume it starts on the next working day, which it cannot yet.
        {_e(caveat) and "<strong>Caveat:</strong> " + _e(caveat) + "."}</p>
      </div>

      <div class="card">
        <h2>The ceiling, and why it is where it is</h2>
        <p>{why}The list is not the constraint — the mailboxes are. At {touches} emails a
        person that ceiling absorbs about <strong>{people_day} new people a working day</strong>,
        or <strong>{month:,} a month</strong>. Going faster means more mailboxes, not a setting:
        the per-mailbox rate is the number deliberately held down, because volume out of a
        single mailbox is what costs a sending domain its reputation.</p>
        {f'<p class="note">{_e(cap_note)}.</p>' if cap_note else ""}
      </div>"""


def _roster_stats(m: dict) -> str:
    """The account funnel, ahead of the send figures.

    Sends were the first thing this page showed, which reads as the headline when a campaign
    has sent nothing and its real state is 26 accounts researched, 23 with an address and 10
    emails drafted. Rendered only when the campaign declares a roster: a profile-wide page has
    no single account list to count, and inventing one would be the pool mislabelled again.
    """
    r = m.get("roster") or {}
    if not r.get("accounts"):
        return ""
    # PARTIAL COVERAGE IS THE FAILURE HERE, not disagreement — see `format.roster_gap`.
    camps = m["campaigns"]["campaigns"]
    if gap := roster_gap(m):
        return (
            '<div class="card"><h2>Accounts in scope</h2><p class="note">Not shown for '
            f"{_e(scope_label(m))} — {_e(gap)}</p></div>"
        )
    packs = len((m.get("packs") or {}).get("packs") or [])
    enrolled = sum(int(s.get("loaded") or 0) for s in m["status"].get("sequences", []))
    unreachable = r["accounts"] - r["contact_verified"]
    # A pack is claimed by a campaign's DATE SUFFIX (`model.scope_to_campaign`), so a slug
    # without one claims none. That makes the drafted figure a floor, not a count — said
    # here rather than letting a lane silently go missing from a total.
    dateless = [c.get("slug", "?") for c in camps if not re.search(r"\d{8}$", c.get("slug", ""))]
    floor = (
        " · at least: " + ", ".join(dateless) + " claim no packs (no date in the slug)"
        if dateless
        else ""
    )
    return (
        '<div class="stats">'
        + _stat(
            r["accounts"],
            "accounts researched",
            "the whole segment, not a slice",
            raw={"value": r["accounts"]},
            src="roster:accounts",
        )
        + _stat(
            r["contact_verified"],
            "contacts verified",
            f"{r['named_seat']} to a named seat · {unreachable} with no address at all",
            raw={"value": r["contact_verified"]},
            src="roster:contact_verified",
        )
        + _stat(
            r["signal"],
            "carry a dated why-now",
            f"{r['signal_sourced']} of them cite a source — evidence is the binding constraint",
            raw={"value": r["signal"]},
            src="roster:signal",
        )
        + _stat(
            packs + enrolled,
            "emails drafted",
            f"{packs} hand-written 1:1 · {enrolled} in the sequence{floor}",
            raw={"packs": packs, "enrolled": enrolled},
            src={"packs": "count:packs", "enrolled": "sum:sequences.loaded"},
        )
        + "</div>"
    )


def _status_view(m: dict) -> str:
    roster_stats = _roster_stats(m)
    fig = _scope_figures(m)
    seqs = [x for x in m["status"].get("sequences", []) if x.get("id")]
    live_ids = set()
    window: dict = {}
    for c in m["campaigns"]["campaigns"]:
        live_ids |= {x["sequence_id"] for x in c.get("sequences", [])}
        window = window or (c.get("window") or {})
    current = [x for x in seqs if x["id"] in live_ids]

    # SUMS, and honestly so — every one is a count of events on the sequences in scope.
    # The caveat at N>1 is not the arithmetic but the unit: the pool is shared, so these
    # count ENROLMENTS, and one person enrolled in two campaigns counts twice.
    sent = sum(_i(x.get("sent")) for x in current)
    loaded = sum(_i(x.get("loaded")) for x in current)
    replied = sum(_i(x.get("replied")) for x in current)
    per_enrolment = (
        " · counted per enrolment — one person in two campaigns counts twice"
        if fig["n"] > 1
        else ""
    )
    cap, cap_why = fig["cap"]
    planned, planned_why = fig["planned"]

    seq_rows = "".join(
        f"<tr><td>{_e(x.get('name', '') or x['id'])}</td>"
        f"<td><span class='pill {'good' if str(x.get('status', '')).lower() in ('active', 'running') else 'warn'}'>"
        f"{_e(x.get('status') or '—')}</span></td>"
        f"<td class='num-cell'>{_i(x.get('loaded')):,}</td>"
        f"<td class='num-cell'>{_i(x.get('sent')):,}</td>"
        f"<td class='num-cell'>{_i(x.get('replied')):,}</td>"
        f"<td class='num-cell muted'>{_pct(_i(x.get('sent')), _i(x.get('loaded')) * 3)}</td></tr>"
        for x in sorted(current, key=lambda y: -_i(y.get("loaded")))
    )

    # Derived from the campaigns' own goals, never hardcoded here: a second copy of this
    # number is a second thing to forget when the target moves. One resolver feeds BOTH
    # this tile and the forecast card — they used to disagree, one taking the last
    # campaign's targets and the other the first, on the same page.
    target_rate, rate_why = fig["target_rate"]
    rate_txt = f"{replied / sent:.1%}" if sent else "—"  # POOLED: Σreplied / Σsent, correct
    target_txt = "—" if target_rate is None else f"{target_rate:.1%}"
    if rate_why:
        rate_sub = f"no single target — {rate_why}"
    elif target_rate is None:
        rate_sub = "no target declared" if sent else "nothing sent yet"
    else:
        weighted = " (weighted by prospects)" if fig["rates_differ"] else ""
        rate_sub = (
            f"target {target_txt}{weighted} · nothing sent yet"
            if not sent
            else f"target {target_txt}{weighted}"
        )
    prim = PRIMARY_BENCHMARK
    ratio = (target_rate / prim["high"]) if (target_rate and prim["high"]) else 0
    verdict = (
        f"about {ratio:.1f}x the closest published comparator"
        if ratio >= 1.15
        else "roughly in line with the closest published comparator"
        if ratio >= 0.85
        else f"about {ratio:.0%} of the closest published comparator"
    )
    stance = (
        "That is a stretch target: beating it means outperforming the published comparator."
        if ratio >= 1.15
        else "That is neither a stretch nor a floor — it is a bet that we perform like the "
        "published comparator, so missing it is a real signal rather than a rounding error."
        if ratio >= 0.85
        else "That is a deliberate floor, set below the comparator so that clearing it proves "
        "very little and missing it is unambiguous."
    )
    scale = 0.11  # bar full-width; the top band shown is 10%

    def _brow(label: str, low: float, high: float, tone: str, right: str) -> str:
        return (
            f'<div class="brow"><div class="blabel">{_e(label)}</div>'
            f'<div class="btrack"><div class="bfill {tone}" '
            f'style="width:{min(100, high * 100 / scale):.0f}%"></div></div>'
            f'<div class="bval">{right}</div></div>'
        )

    rows = _brow(
        f"{scope_label(m)}, so far",
        0,
        (replied / sent if sent else 0),
        "ta",
        rate_txt,
    )
    # One "our target" bar only when one target is what the scope has. Where the goals
    # differ, each campaign gets its own bar: a single blended bar next to a comparator
    # invites a verdict about a number nobody set.
    if fig["rates_differ"]:
        for slug, r, _w in fig["rates"]:
            rows += _brow(f"target · {slug}", 0, r, "tb", f"{r:.1%}")
    elif target_rate:
        rows += _brow("our target", 0, target_rate, "tb", f"{target_rate:.1%}")
    for bm in BENCHMARKS:
        val = (
            f"{bm['low']:.2%}".rstrip("0").rstrip(".")
            if bm["low"] == bm["high"]
            else f"{bm['low']:.1%}–{bm['high']:.1%}"
        )
        rows += _brow(bm["label"], bm["low"], bm["high"], "tc", val)

    src_rows = "".join(
        f"<tr><td>{_e(bm['label'])}</td><td class='muted'>{_e(bm['basis'])}</td>"
        f"<td><a href='{_e(bm['url'])}'>{_e(bm['source'])}</a>"
        + (f"<br><span class='muted'>{_e(bm['note'])}</span>" if bm["note"] else "")
        + "</td></tr>"
        for bm in BENCHMARKS
    )

    bench_html = f"""
      <div class="card">
        <h2>Reply rate</h2>
        <div class="bars">{rows}</div>
        {
        f'''<p class="note">Our {target_txt} target is <strong>{verdict}</strong> —
        {prim["low"]:.1%} for {_e(prim["label"])}, the published figure closest to who this
        campaign actually writes to. {stance}</p>'''
        if target_rate and not fig["rates_differ"]
        else f'''<p class="note"><strong>No single verdict for {_e(scope_label(m))}.</strong>
        The campaigns in scope set different goals ({_e(", ".join(f"{s} {r:.1%}" for s, r, _ in fig["rates"]))}),
        each argued against its own comparator in its own manifest. The blended
        {target_txt} above is weighted by prospects and is a summary, not a target anyone
        committed to.</p>'''
        if fig["rates_differ"]
        else '<p class="note">No reply-rate target is declared for this scope, so there is '
        "nothing to compare against the published figures below.</p>"
    }
        <details>
          <summary>Where these numbers come from, and why to distrust them</summary>
          <p class="note"><strong>The denominator is not standardised.</strong> Belkins divides
          replies by <em>emails sent</em>; most others divide by <em>people contacted</em>. At
          three emails per person that is a ~3x difference, which accounts for most of the gap
          between 0.45% and 3.4% — methodology, not performance. Our own target is stated per
          person contacted.</p>
          <p class="note"><strong>Every source below is a cold-email vendor</strong> reporting on
          its own platform's traffic: self-selected populations, and an interest in the answer. No
          independent academic or analyst dataset was found.</p>
          <p class="note"><strong>One number is deliberately excluded.</strong> The widely-quoted
          8.5% (Backlinko/Pitchbox, 12M emails) is link-building and blogger outreach, not B2B
          sales — a different population answering a different request. Quoting it as a sales
          benchmark is a category error, and inflated ranges usually trace back to it.</p>
          <table><thead><tr><th>Figure</th><th>Counted as</th><th>Source</th></tr></thead>
          <tbody>{src_rows}</tbody></table>
        </details>
      </div>"""

    blocker = window.get("capacity_blocker") or ""
    # The blocker itself is sending-tool mechanics, so it lives in the Operator notes panel;
    # what stays here is only the pointer, so a reader of this panel is never left wondering
    # why four ready sequences are sending nothing.
    blocker_html = (
        "<p class='note'>Why it has not started yet is in <strong>Operator notes</strong>.</p>"
        if blocker
        else ""
    )

    runs = m["runs"]
    if runs:
        last = runs[0]["ts"]
        try:
            age = (datetime.now(UTC).date() - datetime.strptime(last, "%Y-%m-%d").date()).days
        except ValueError:
            age = None
        age_txt = f"{age} days ago" if age is not None else last
        tone = "bad" if (age or 0) > 14 else "warn" if (age or 0) > 7 else "good"
        run_rows = "".join(
            f"<tr><td>{_e(r['ts'])}</td><td>{_e(r['kind'])}</td>"
            f"<td>{_e(r['market'] or '—')}</td>"
            f"<td class='num-cell'>{_i(r['found']):,}</td></tr>"
            for r in runs
        )
        runs_html = f"""
        <p><span class="pill {tone}">last run {_e(age_txt)}</span>
        Finding new people is a separate activity from emailing them. Nothing here is
        running automatically — each run is started by hand.</p>
        <table><thead><tr><th>Date</th><th>Kind</th><th>Market</th><th>People found</th>
        </tr></thead><tbody>{run_rows}</tbody></table>"""
    else:
        runs_html = "<p class='note'>No discovery or enrichment runs recorded.</p>"

    return f"""
      {roster_stats}
      <div class="stats">
        {
        _stat(
            f"{sent:,} of {planned:,}" if planned is not None else f"{sent:,} of —",
            "emails sent",
            "nothing goes out until a person starts it" + _planned_sub(fig, planned_why),
            raw={"sent": sent, "planned": planned},
            src={"sent": "sum:live.sent", "planned": "sum-complete:campaigns.targets.emails"},
        )
    }
        {
        _stat(
            rate_txt,
            "reply rate",
            rate_sub,
            raw={"replied": replied, "sent": sent},
            src="pooled:live.replied/live.sent",
        )
    }
        {
        _stat(
            loaded,
            "people loaded and waiting",
            per_enrolment.lstrip(" ·").strip(),
            raw={"value": loaded},
            src="sum:live.loaded",
        )
    }
        {_stat(replied, "replies so far", raw={"value": replied}, src="sum:live.replied")}
        {
        _stat(
            len(current),
            "sequences set up",
            f"{len([x for x in current if str(x.get('status', '')).lower() in ('active', 'running')])} currently sending",
            raw={"value": len(current)},
            src="count:live",
        )
    }
        {
        _stat(
            f"{cap:,}/day" if cap else "—",
            "sending ceiling",
            _ceiling_sub(fig, cap, cap_why),
            raw={"value": cap},
            src="agree:campaigns.window.daily_cap",
        )
    }
      </div>

      <div class="card">
        <h2>Email sequences</h2>
        <table><thead><tr><th>Sequence</th><th>State</th><th>People</th><th>Emails sent</th>
        <th>Replies</th><th>Progress</th></tr></thead><tbody>{seq_rows}</tbody></table>
        <p class="note">Progress is emails sent against the three each person is due.
        How long the whole run takes is worked out below.</p>
        {blocker_html}
      </div>

      {_forecast_block(m)}

      {bench_html}

      <div class="card">
        <h2>Finding new people</h2>
        {runs_html}
      </div>"""
