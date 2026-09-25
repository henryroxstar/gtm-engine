"""Operator notes: the maintainer's tab (PS20 PRD Phase 2).

Collapsed groups in `config.OPS_GROUPS` order. Raw ids are fine here and nowhere else.

Task 2.6a writes the three blocks that had no home of their own: the reply-rate benchmarks and
the prospecting runs (both copied out of `views_status`, which 2.7b deletes) and the roster notes
(composed from `views_who`'s carved blocks). They live here, not in `views_who` or `views_learn`,
because either would cross the §R10 500-line cap. Task 2.6b adds the group composition: one
builder per group, each returning ``{block id: html}``, and ``_ops_view`` joining them. This
module may import any view module; none of them may import it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .aggregate import _ceiling_sub, _scope_figures, sending_tiles
from .config import BENCHMARKS, OPS_GROUPS
from .forecast import _forecast_block
from .format import _e, _i, _pct, _scoped_out, _stat, figure_span, roster_gap, scope_label, section
from .health import disagree_names, reconciliation_detail, repeated_rows, shared_sequences
from .views_accounts import _list_vs_provider
from .views_funnel import _safe_downloads_block
from .views_inbound import _capability_card, _inbound_card
from .views_learn import _experiment_notes, _grid_block, _lift_block, _varies_block
from .views_lede import _maintainer_block
from .views_overview import _needs_address_block
from .views_ready import _maintenance_lines, _readiness_blocks
from .views_segments import segment_mix
from .views_what import (
    _capability_spread,
    _judge_notes,
    _opening_block,
    _pack_notes,
    _qa_detail,
    _subjects_block,
)
from .views_who import _judge_split, _pool_block, _seat_fit_note, _sources_note, _verdicts_note


def _brow(label: str, high: float, tone: str, right: str, scale: float = 0.11) -> str:
    """One bar; full width is ``scale`` (the top band shown is 10%)."""
    return (
        f'<div class="brow"><div class="blabel">{_e(label)}</div>'
        f'<div class="btrack"><div class="bfill {tone}" '
        f'style="width:{min(100, high * 100 / scale):.0f}%"></div></div>'
        f'<div class="bval">{right}</div></div>'
    )


def _bench_verdict(m: dict, fig: dict, target_rate: float | None, target_txt: str) -> str:
    if target_rate and not fig["rates_differ"]:
        return (
            f'<p class="note">Our {target_txt} target is drawn beside every published figure '
            f"above. Which of them fits the people {_e(scope_label(m))} writes to is not something "
            "this page can know, so it names none; how each one is counted is below.</p>"
        )
    if fig["rates_differ"]:
        goals = ", ".join(f"{s} {r:.1%}" for s, r, _ in fig["rates"])
        return (
            f'<p class="note"><strong>No single verdict for {_e(scope_label(m))}.</strong> The '
            f"campaigns in scope set different goals ({_e(goals)}). The blended {target_txt} above "
            "is weighted by prospects and is a summary, not a target anyone committed to.</p>"
        )
    return (
        '<p class="note">No reply-rate target is declared for this scope, so there is '
        "nothing to compare against the published figures below.</p>"
    )


def _benchmarks_block(m: dict) -> str:
    """The Reply-rate card, copied from `views_status._status_view`.

    Reads this module's ``BENCHMARKS`` (so a test can patch it) and recomputes the scope's own
    figures, as `_status_view` did. The gap's two ends come from the figures the table cites;
    a renamed one refuses rather than crashing the page (PS20 P1.7).
    """
    fig = _scope_figures(m)
    t = sending_tiles(m, fig)
    sent, replied = t["contacted"], t["replied"]
    target_rate, _rate_why = fig["target_rate"]
    rate_txt = f"{replied / sent:.1%}" if sent else "—"  # POOLED: Σreplied / Σsent, correct
    target_txt = "—" if target_rate is None else f"{target_rate:.1%}"

    rows = _brow(f"{scope_label(m)}, so far", (replied / sent if sent else 0), "ta", rate_txt)
    # One "our target" bar only when one target is what the scope has; where the goals differ,
    # each campaign gets its own bar — a blended bar invites a verdict about a number nobody set.
    if fig["rates_differ"]:
        for slug, r, _w in fig["rates"]:
            rows += _brow(f"target · {slug}", r, "tb", f"{r:.1%}")
    elif target_rate:
        rows += _brow("our target", target_rate, "tb", f"{target_rate:.1%}")
    for bm in BENCHMARKS:
        val = (
            f"{bm['low']:.2%}".rstrip("0").rstrip(".")
            if bm["low"] == bm["high"]
            else f"{bm['low']:.1%}–{bm['high']:.1%}"
        )
        rows += _brow(bm["label"], bm["high"], "tc", val)

    strict = next((b["low"] for b in BENCHMARKS if b["basis"] == "per email sent"), None)
    usual = next((b["high"] for b in BENCHMARKS if b["label"] == "all industries, average"), None)
    ends = "— and — (a figure it compares is no longer in the table below)"
    if strict is not None and usual is not None:
        ends = f"{strict:.2%} and {usual:.1%}"
    src_rows = "".join(
        f"<tr><td>{_e(bm['label'])}</td><td class='muted'>{_e(bm['basis'])}</td>"
        f"<td><a href='{_e(bm['url'])}'>{_e(bm['source'])}</a>"
        + (f"<br><span class='muted'>{_e(bm['note'])}</span>" if bm["note"] else "")
        + "</td></tr>"
        for bm in BENCHMARKS
    )
    return f"""
      <div class="card">
        <h2>Reply rate</h2>
        <div class="bars">{rows}</div>
        {_bench_verdict(m, fig, target_rate, target_txt)}
        <details>
          <summary>Where these numbers come from, and why to distrust them</summary>
          <p class="note"><strong>The denominator is not standardised.</strong> Belkins divides
          replies by <em>emails sent</em>; most others divide by <em>people contacted</em>. At
          a three-email sequence that is a ~3x difference, which accounts for most of the gap
          between {ends} — methodology, not performance. Our own target
          is stated per person contacted.</p>
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


def _runs_scoped_why(m: dict) -> str:
    """Why a scoped page shows no runs, and where its accounts' origin is named instead."""
    r = m.get("roster") or {}
    n, k = r.get("accounts", 0), len(r.get("sources") or [])
    why = (
        "Discovery and enrichment runs are recorded per profile, not per campaign, and a run "
        "export carries no campaign tag — so there is no way to say which of them fed this roster"
    )
    # The pointer below names the account notes, which render only where the roster does.
    if roster_gap(m) or not r.get("rows"):
        return f"{why}."
    if not k:
        return f"{why}, and {scope_label(m)} declares no run export, so there is nothing to trace."
    return (
        f"{why}. Where {scope_label(m)}'s {n} account{'' if n == 1 else 's'} came from is named "
        f"in the account notes above ({k} run export{'' if k == 1 else 's'} its manifest "
        "declares)."
    )


def _runs_block(m: dict) -> str:
    """Finding new people, copied from `views_status._status_view`.

    Prospecting runs are a PROFILE activity: on 2026-09-05 a campaign's page listed eight of
    them, six predating the campaign and none scoped to it, under a heading that read as "this
    campaign's supply". A run export is not tagged with the campaign that later drew from it,
    so a scoped page says so rather than showing the profile's runs under the campaign's name.
    """
    scoped = _scoped_out(m, "Finding new people", _runs_scoped_why(m))
    if scoped:
        return scoped
    runs = m["runs"]
    if runs:
        last = runs[0]["ts"]
        try:
            age = (datetime.now(UTC).date() - datetime.strptime(last, "%Y-%m-%d").date()).days
        except ValueError:
            age = None
        age_txt = f"{age} days ago" if age is not None else last
        run_rows = "".join(
            f"<tr><td>{_e(r['ts'])}</td><td>{_e(r['kind'])}</td>"
            f"<td>{_e(r['market'] or '—')}</td>"
            f"<td class='num-cell'>{_i(r['found']):,}</td></tr>"
            for r in runs
        )
        runs_html = f"""
        <p><span class="pill">last run {_e(age_txt)}</span>
        Finding new people is a separate activity from emailing them. Nothing here is
        running automatically — each run is started by hand.</p>
        <table><thead><tr><th>Date</th><th>Kind</th><th>Market</th><th>People found</th>
        </tr></thead><tbody>{run_rows}</tbody></table>"""
    else:
        runs_html = "<p class='note'>No discovery or enrichment runs recorded.</p>"
    return f"""      <div class="card">
        <h2>Finding new people</h2>
        {runs_html}
      </div>"""


def _roster_notes(m: dict) -> str:
    """About these accounts: the notes the old who table carried, without the table.

    Composed from `views_who`'s blocks, which `_roster_who` still calls. Renders only where
    the roster does: a roster only some in-scope campaigns contributed to (``roster_gap``) is
    not this scope's accounts. Seat fit only on a single campaign's page: a multi-campaign set
    was never measured.
    """
    r = m.get("roster") or {}
    rows = r.get("rows") or []
    if not rows or roster_gap(m):
        return ""
    tiers = ", ".join(f"{n} {_e(k)}" for k, n in r.get("tiers", []))
    verdicts = ", ".join(f"{n} {_e(k)}" for k, n in r.get("verdicts", []))
    seat_fit = _seat_fit_note(m) if len(m["campaigns"]["campaigns"]) == 1 else ""
    return (
        '<div class="card"><h2>About these accounts</h2>'
        f'<p class="note">Tiers: {tiers or "—"}. Research verdicts: {verdicts or "—"}.</p>'
        + _verdicts_note(m, rows)
        + _judge_split(m, rows)
        + seat_fit
        + _sources_note(m, r)
        + "</div>"
    )


#: What an empty group says, so an empty group is a finding rather than a blank.
_EMPTY = {
    "numbers": "Nothing to look at: none of this section's checks found a disagreement.",
    "maintenance": "Nothing to maintain.",
}
_TITLES = {gid: title for gid, title, _b in OPS_GROUPS}


def _group(gid: str, title: str, blocks: dict[str, str], *, open_: bool) -> str:
    """One collapsed group: its declared blocks in `OPS_GROUPS` order, each a section."""
    declared = next(b for g, _t, b in OPS_GROUPS if g == gid)
    body = "".join(section(sid, blocks.get(sid, "")) for sid in declared)
    if not body:
        body = f'<p class="note">{_e(_EMPTY.get(gid, "Nothing here."))}</p>'
    return (
        f'<details class="ops-group" data-section="{_e(gid)}"{" open" if open_ else ""}>'
        f"<summary>{_e(title)}</summary>{body}</details>"
    )


def _card(title: str, body: str) -> str:
    return f'<div class="card"><h2>{_e(title)}</h2>{body}</div>' if body else ""


def _names(names: list[str]) -> str:
    esc = [_e(n) for n in names]
    if not esc:
        return ""
    return esc[0] if len(esc) == 1 else f"{', '.join(esc[:-1])} and {esc[-1]}"


def _numbers(m: dict) -> dict[str, str]:
    """Numbers that need a look. Raw ids are fine here: this is the maintainer's tab."""
    rec = m.get("reconciliation") or {}
    reconciliation = ""
    if not rec.get("ok", True):
        affected, detail = disagree_names(m), reconciliation_detail(rec)
        reconciliation = _card(
            "Our records and the sending figures disagree",
            f"<p>{_e(detail[:1].upper() + detail[1:])}.</p>"  # ids keep their case
            + (f'<p class="note">Campaigns affected: {_names(affected)}.</p>' if affected else ""),
        )
    shared = [
        f"<li><code>{_e(sid)}</code> is listed by {_names(owners)}, so its people are counted "
        f"in {'both' if len(owners) == 2 else 'each'}.</li>"
        for sid, owners in shared_sequences(m).items()
    ] + [
        f"<li><code>{_e(sid)}</code> appears more than once in the sending figures, so its "
        "people are summed once per row.</li>"
        for sid in repeated_rows(m)
    ]
    snap_names = {x.get("id"): x.get("name") for x in m["status"].get("sequences", [])}
    unlinked = [
        f"<li><code>{_e(s['sequence_id'])}</code>"
        + (f" ({_e(snap_names[s['sequence_id']])})" if snap_names.get(s["sequence_id"]) else "")
        + ": staged in the sending tool, but no campaign lists it.</li>"
        for s in m["campaigns"].get("unlinked_sequences") or []
    ]
    return {
        "cross-check": _maintainer_block(m),
        "reconciliation": reconciliation,
        "shared-sequences": _card(
            "Sequences counted more than once", f"<ul>{''.join(shared)}</ul>" if shared else ""
        ),
        "unlinked": _card(
            "Sequences no campaign lists", f"<ul>{''.join(unlinked)}</ul>" if unlinked else ""
        ),
        "list-vs-provider": _list_vs_provider(m),
    }


def _before_sending(m: dict) -> dict[str, str]:
    fig = _scope_figures(m)
    cap, cap_why = fig["cap"]
    ceiling = _stat(
        f"{cap:,}/day" if cap else "—",
        "sending ceiling",
        _ceiling_sub(fig, cap, cap_why),
        raw={"value": cap},
        src="agree:campaigns.window.daily_cap",
    )
    return _readiness_blocks(m) | {
        "load-files": _safe_downloads_block(m),
        "ceiling-tile": f'<div class="stats">{ceiling}</div>',
        "compliance": _capability_card(m),
    }


def _sending_setup(m: dict) -> dict[str, str]:
    """The loaded and sequences-set-up tiles and the "Email sequences" table, copied from
    `views_status._status_view` without the table's technical State column (TP T2.12: the
    Emails tab carries each sequence's word). Progress is PEOPLE over PEOPLE (PS20 P1.4)."""
    fig = _scope_figures(m)
    t = sending_tiles(m, fig)
    per_enrolment = (
        "counted per enrolment — a person in more than one campaign counts once per campaign"
        if fig["n"] > 1
        else ""
    )
    tiles = _stat(
        t["loaded"],
        "people loaded and waiting",
        per_enrolment,
        raw={"value": t["loaded"]},
        src="sum:campaigns.actuals.loaded",
        figure="loaded",
    ) + _stat(
        t["sequences"],
        "sequences set up",
        sub_html=figure_span("seq-tally", t["tally"]) if t["tally"] else "",
        raw={"value": t["sequences"]},
        src="count:live",
    )
    seq_rows = "".join(
        f"<tr><td>{_e(x.get('name', '') or x['id'])}</td>"
        f"<td class='num-cell'>{_i(x.get('loaded')):,}</td>"
        f"<td class='num-cell'>{_i(x.get('sent')):,}</td>"
        f"<td class='num-cell'>{_i(x.get('replied')):,}</td>"
        f'<td class="num-cell muted" data-figure="progress-{_e(x["id"])}">'
        f"{_pct(_i(x.get('sent')), _i(x.get('loaded')))}</td></tr>"
        for x in sorted(t["current"], key=lambda y: -_i(y.get("loaded")))
    )
    blocker = (
        "<p class='note'>Why it has not started yet is under "
        f"<strong>{_e(_TITLES['before-sending'])}</strong>.</p>"
        if t["blocker"]
        else ""
    )
    table = f"""
      <div class="card">
        <h2>Email sequences</h2>
        <table><thead><tr><th>Sequence</th><th>People loaded</th>
        <th>People contacted</th><th>Replies</th><th>Progress</th></tr></thead>
        <tbody>{seq_rows}</tbody></table>
        <p class="note">Progress is people contacted against people loaded: the share of each
        sequence's people who have had their first email. How long the whole run takes is
        worked out below.</p>
        {blocker}
      </div>"""
    return {
        "setup-tiles": f'<div class="stats">{tiles}</div>',
        "sequence-table": table,
        "forecast": _forecast_block(m),
    }


def _shows_pool(m: dict) -> bool:
    """The shared pool shows except on a scoped page whose own roster is complete: there the
    roster is the subject, and a pool-wide number is not about this campaign."""
    rows = (m.get("roster") or {}).get("rows")
    return not (m.get("campaign_scope") and rows and not roster_gap(m))


def _list_quality(m: dict) -> dict[str, str]:
    return {
        "pool": _pool_block(m) if _shows_pool(m) else "",
        "roster-notes": _roster_notes(m),
        "segment-mix": segment_mix(m),
        "needs-address": _needs_address_block(m),
        "finding-new-people": _runs_block(m),
    }


def _email_quality(m: dict) -> dict[str, str]:
    """The quality-check detail, one card per sequence. The badge and the drift note stay on
    Emails, so a page carries exactly two re-push marks."""
    pm = m.get("packs") or {}
    spread = _pack_notes(pm, m) + _capability_spread(pm) if pm.get("packs") else ""
    seen: dict[str, dict] = {}
    for msg in m["messages"]:
        if msg.get("sequence_id") and msg["sequence_id"] not in seen:
            seen[msg["sequence_id"]] = msg
    detail = "".join(
        _card(
            msg.get("title") or sid,
            _qa_detail(msg["lint"])
            if msg.get("lint")
            else "<p class='note'>No quality record on file for this message. Never-checked and "
            "checked-and-clean look identical here — which is exactly why the record is kept.</p>",
        )
        for sid, msg in seen.items()
    )
    return {
        "subjects": _subjects_block(m),
        "opening-lines": _opening_block(m),
        "capability-spread": _card("1:1 emails by the capability they argue", spread),
        "judge-notes": _judge_notes(m),
        "checks-detail": detail,
    }


def _ops_view(m: dict) -> str:
    """Operator notes: every `OPS_GROUPS` group, collapsed, in order. Only "Numbers that need a
    look" opens itself, and only when one of its checks has something to show."""
    builders = {
        "numbers": _numbers,
        "before-sending": _before_sending,
        "sending-setup": _sending_setup,
        "list-quality": _list_quality,
        "email-quality": _email_quality,
        "experiment-design": lambda m: {
            "varies": _varies_block(m),
            "grid": _grid_block(m),
            "readable-difference": _lift_block(m),
            "benchmarks": _benchmarks_block(m),
            "experiment-notes": _experiment_notes(m),
        },
        "replies": lambda m: {"inbound-health": _inbound_card(m)},
        "maintenance": lambda m: {"maintenance-lines": _card_lines(_maintenance_lines(m))},
    }
    out = []
    for gid, title, _blocks in OPS_GROUPS:
        blocks = builders[gid](m)
        opens = gid == "numbers" and any(b.strip() for b in blocks.values())
        out.append(_group(gid, title, blocks, open_=opens))
    return "".join(out)


def _card_lines(lines: str) -> str:
    return f'<div class="card">{lines}</div>' if lines else ""
