from __future__ import annotations

import re
from datetime import UTC, datetime

from ..prospect_status import LABELS, NEXT_STEP, STATUSES
from .aggregate import _ceiling_sub, _planned_sub, _scope_figures
from .config import BENCHMARKS, PRIMARY_BENCHMARK
from .filters import sub_counts
from .forecast import _forecast_block, drafted_to
from .format import (
    _e,
    _i,
    _pct,
    _pool_scope_note,
    _scoped_out,
    _stat,
    roster_gap,
    scope_label,
)
from .views_funnel import _attrition_funnel_block, _safe_downloads_block

#: The five statuses rendered as tiles here. `needs_address` is the sixth `STATUSES` id but
#: gets its own card below, textually marked as a different population — see
#: `_prospect_status_block`.
_LANE_STATUSES: tuple[str, ...] = tuple(s for s in STATUSES if s != "needs_address")


def _prospect_status_block(m: dict) -> str:
    """PS14: the six-word operator status (`gtm_core.prospect_status`) as five summing
    tiles, plus the ledger's own — the same six words `prospects status` prints, so the
    page and the CLI cannot disagree about what one of them means.

    Renders only what ``model.prospect_status_model`` already derived; nothing here reads
    ``lanes-state.jsonl`` or calls ``status_of`` a second time. When the router has never
    run on this profile (``available`` is False) each of the five tiles still renders —
    an em dash and a reason, the same refusal shape ``_status_view``'s other tiles already
    use (e.g. the sending-ceiling tile) — rather than a zero that reads as "nothing is
    waiting" when the truth is "nobody has looked yet".

    ``needs_address`` is never summed into the five: it counts the account LEDGER, a
    different and larger population than the current routed list, and folding the two
    together is the double-counting failure this page has already paid for once.
    """
    ps = m.get("prospect_status") or {}
    available = bool(ps.get("available"))
    counts = ps.get("counts") or {}
    total = ps.get("total") or 0
    unmapped = ps.get("unmapped") or 0
    needs = ps.get("needs_address") or 0
    scope_note = _pool_scope_note(m, "The prospect pool")

    if available:
        tiles = "".join(
            _stat(
                counts.get(s, 0),
                LABELS[s],
                NEXT_STEP[s],
                raw={"value": counts.get(s, 0)},
                src=f"status:{s}",
            )
            for s in _LANE_STATUSES
        )
        total_line = (
            f'<p class="note">These five sum to <strong>{total:,}</strong> — every person '
            "in the current list.</p>"
        )
    else:
        tiles = "".join(
            _stat(
                "—",
                LABELS[s],
                "nothing to show yet — run your prospecting first",
                raw={"value": "—"},
                src=f"status:{s}",
            )
            for s in _LANE_STATUSES
        )
        total_line = '<p class="note">Nothing to show yet — run your prospecting first.</p>'
    unmapped_line = (
        f'<p class="note">{unmapped:,} more row(s) carry a state this page does not '
        "recognise. Held out of the total above rather than guessed at — that is a gap in "
        "the status mapping, not a real status.</p>"
        if unmapped
        else ""
    )
    needs_tile = _stat(
        needs,
        LABELS["needs_address"],
        f"{NEXT_STEP['needs_address']} — not counted above",
        raw={"value": needs},
        src="status:needs_address",
    )
    diff_note = f"not part of the {total:,} above" if available else "not part of the routed list"
    return f"""
      <div class="card">
        <h2>Where the current list stands</h2>
        {scope_note}
        <div class="stats">{tiles}</div>
        {total_line}
        {unmapped_line}
      </div>
      <div class="card">
        <h2>Needs an address</h2>
        <div class="stats">{needs_tile}</div>
        <p class="note"><strong>A different population — {diff_note}.</strong>
        This counts named contacts in the account ledger with no reachable address at all,
        which includes accounts that have never reached the current routed list.</p>
      </div>"""


# --- views ----------------------------------------------------------------------


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
    # The count is over composed BODIES, not over what the sequencer holds — see
    # `forecast.drafted_to`. Falls back to the old arithmetic where no rendered sample exists,
    # so a page with no per-recipient renders still says something rather than zero.
    merge = len({r["to"] for r in (m.get("samples") or {}).get("rendered") or []})
    drafted = len(drafted_to(m))
    rows = r["rows"]
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
            src="rows:all",
        )
        + _stat(
            r["contact_verified"],
            "contacts verified",
            raw={"value": r["contact_verified"]},
            src="rows:co_has_email",
            sub_html=sub_counts(
                rows,
                [
                    ("co_named", " to a named seat"),
                    ("co_no_email", " with no address at all"),
                ],
            ),
        )
        + _stat(
            r["signal"],
            "carry a dated why-now",
            raw={"value": r["signal"]},
            src="rows:co_signal",
            sub_html=sub_counts(
                rows,
                [
                    (
                        "co_signal_sourced",
                        " of them cite a source — evidence is the binding constraint",
                    )
                ],
            ),
        )
        + _stat(
            drafted or packs + enrolled,
            "have a drafted email",
            (
                f"{packs} hand-written 1:1 · {merge} rendered per recipient · "
                f"{enrolled} of them loaded into the sequence{floor}"
                if drafted
                else f"{packs} hand-written 1:1 · {enrolled} in the sequence{floor}"
            ),
            raw={"drafted": drafted, "packs": packs, "merge": merge, "enrolled": enrolled},
            src={
                "drafted": "distinct:samples.rendered.to|samples.packs.to",
                "enrolled": "sum:sequences.loaded",
            },
        )
        + "</div>"
    )


def _status_view(m: dict) -> str:
    funnel_block = _attrition_funnel_block(m)
    downloads_block = _safe_downloads_block(m)
    status_block = _prospect_status_block(m)
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
        f"<td class='tech'><span class='pill {'good' if str(x.get('status', '')).lower() in ('active', 'running') else 'warn'}'>"
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
          <table><thead><tr><th>Figure</th><th>How it is counted</th><th>Source</th></tr></thead>
          <tbody>{src_rows}</tbody></table>
        </details>
      </div>"""

    # "the three each person is due" was written when every sequence ran a 3-touch ladder and
    # stayed put when this campaign staged a 2-touch one — a restated number going stale in
    # the one sentence that tells a reader what the progress column is a fraction OF.
    due, due_why = fig["touches"]
    due_txt = f"{due} emails" if due and not due_why else "emails their own sequence declares"

    blocker = window.get("capacity_blocker") or ""
    # The blocker itself is sending-tool mechanics, so it lives in the Operator notes panel;
    # what stays here is only the pointer, so a reader of this panel is never left wondering
    # why four ready sequences are sending nothing.
    blocker_html = (
        "<p class='note'>Why it has not started yet is in <strong>Operator notes</strong>.</p>"
        if blocker
        else ""
    )

    # Prospecting runs are a PROFILE activity, not a campaign one. `prospecting_runs` reads
    # every discovery/enrichment run on the profile — on 2026-09-05 this campaign's page
    # listed eight of them, six predating the campaign and none scoped to it, under a heading
    # that reads as "this campaign's supply". There is no scoped version to render: a run
    # export is not tagged with the campaign that later drew from it, so the honest page says
    # so rather than showing the profile's runs with the campaign's name over them.
    runs_card = _scoped_out(
        m,
        "Finding new people",
        "Discovery and enrichment runs are recorded per profile, not per campaign, and a run "
        "export carries no campaign tag — so there is no way to say which of them fed this "
        f"roster. Where {scope_label(m)}'s {(m.get('roster') or {}).get('accounts', 0)} accounts "
        f"came from is on Who we're emailing, which names the "
        f"{len((m.get('roster') or {}).get('sources') or [])} run exports its manifest declares.",
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
    runs_full = f"""      <div class="card">
        <h2>Finding new people</h2>
        {runs_html}
      </div>"""

    return f"""
      {funnel_block}
      {status_block}
      {downloads_block}
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
        <table><thead><tr><th>Sequence</th><th class="tech">State</th><th>People</th><th>Emails sent</th>
        <th>Replies</th><th>Progress</th></tr></thead><tbody>{seq_rows}</tbody></table>
        <p class="note">Progress is emails sent against the {due_txt} each person is due.
        How long the whole run takes is worked out below.</p>
        {blocker_html}
      </div>

      {_forecast_block(m)}

      {bench_html}

      {runs_card or runs_full}"""
