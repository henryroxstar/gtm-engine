from __future__ import annotations

from ..campaigns_dashboard import _experiment_block
from ..power import detectable_lift
from .aggregate import _scope_figures, campaign_contacted, sent_heading
from .forecast import _lanes
from .format import _e, _scoped_out, _seat_label, figure_span
from .views_lede import _maintainer_block


def _headline_learnings(m: dict) -> str:
    """The learning questions, at the top of the panel rather than folded into a `<details>`.

    This panel opened on two hand-written hypothesis cards that had outlived the run they were
    written for — "if we open with a verified fact … more of them reply than to a generic
    pitch", and a second about whether a paid topic-surge feed predicts replies. Neither is
    this campaign's question: 12 of its 17 scheduled recipients are on a lane that asserts
    nothing about them BY DESIGN, and only one signal type reaches the list at all, so the
    feed comparison has no second arm. Meanwhile the questions this run actually answers were
    in the manifest all along, collapsed behind a summary at the foot of the page.

    Rendered from ``[[experiment.will_learn]]`` so there is one home for the fact. The
    `<details>` at the foot still carries the full notes; this is the same list, opened.
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
    percentage and no "N of M" (PS20 P1.4). ``replies`` and ``sqls`` (meetings, the
    labelled proxy) keep theirs. The mark carries the campaign's slug, as ``progress-<id>``
    carries the sequence's: one card per campaign, so one name never holds two values."""
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


def _goal_card(c: dict, own: dict | None) -> str:
    """What one campaign promised and where it is, from its OWN figures. The sentence is
    ``sent_heading`` over this campaign's split (PS20 P1.3), never a typed "nothing sent"."""
    slug, readable = c.get("slug", "?"), own is not None
    rows = "".join(_goal_row(slug, k, v, readable) for k, v in c["promised_vs_actual"].items())
    if not rows:
        return (
            f'<div class="card"><h2>{_e(c["title"])}</h2>'
            "<p class='note'>No goals recorded for this campaign.</p></div>"
        )
    earlier = (
        f" An earlier run contacted {own['earlier']:,} more {_people(own['earlier'])}, not "
        "counted here."
        if own and own["earlier"]
        else ""
    )
    return (
        f'<div class="card"><h2>{_e(c["title"])}</h2>'
        "<p class='note'>What this campaign promised, and where it actually is: "
        f"{_e(sent_heading(own).lower())}.{_e(earlier)}</p>"
        "<table><thead><tr><th>Measure</th><th>Goal</th><th>So far</th><th></th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def _lift_note(contacted: dict | None) -> str:
    """The detectable-difference card's caveat, from the figures (PS20 P1.3).

    The heading and the sizing sit side by side and neither is inferred from the other: a
    group is sized from the sends the OUTCOMES ledger records against it, or from its planned
    size while there are none (``cells.detectable_lift``), and the heading reads the sending
    tool's snapshot. "Nothing has been sent, so these are forecasts" joined two sources with
    a "so" that neither of them states."""
    return (
        f"{sent_heading(contacted)}. Each group is sized from the sends recorded against it; "
        "one with none recorded is shown at its planned size, a forecast rather than a "
        "measurement."
    )


def _learn_view(m: dict) -> str:
    varies_card = _scoped_out(
        m,
        "What varies, and by how much",
        "The combinations-per-person ratio is computed over the whole prospect pool, so it "
        "describes the profile's experiment design, not this campaign's.",
    )
    lanes = _lanes(m)
    scheduled = sum(ln["people"] for ln in lanes)
    lanes_n = len(lanes)
    # Said only when the power computation agrees (PS20 P1.7 Rule B): no lane is large enough
    # for ANY lift to show. It used to be said of this campaign whatever its lanes' size.
    unpowered = all(detectable_lift(ln["people"], m["cells"]["baseline"]) is None for ln in lanes)
    lift_card = _scoped_out(
        m,
        "How big a difference each group could even show",
        "Minimum detectable effect is sized from the pool's per-group counts."
        + (
            f" At {scheduled} scheduled recipients across {lanes_n} lanes this campaign cannot "
            "power a comparison at all, which is the honest answer rather than a forecast."
            if unpowered
            else ""
        ),
    )
    cm = m["cells"]
    cells = cm["cells"]
    # Each clause only where it holds (PS20 P1.7 Rule B). cells.toml is shared, but it holds
    # another campaign's groups only when another campaign registered some; and a pointer
    # needs its target: the numbered list, or a declared "what it will not tell us".
    own = {msg["sequence_id"] for msg in m["messages"]}
    beyond = (
        " from outside this campaign" if any(c["sequence_id"] not in own for c in cells) else ""
    )
    exps = [c.get("experiment") or {} for c in m["campaigns"]["campaigns"]]
    listed = any(x.get("will_learn") for x in exps)
    limited = any(x.get("what_it_cant_tell_us") for x in exps)
    pointers = (
        " What THIS run sets out to answer is the numbered list above." if listed else ""
    ) + (
        " What it cannot answer is the 'what it will not tell us' line in the full experiment "
        "notes below."
        if limited
        else ""
    )
    readable_card = _scoped_out(
        m,
        "What this run can and cannot answer",
        "The readable-group counts are computed over cells.toml across the whole profile"
        + (f", so on a campaign page they include message groups{beyond}" if beyond else "")
        + f".{pointers}",
    )
    sup = m["supply"]

    _days = sorted({c["day"] for msg in m.get("messages") or [] for c in msg.get("copy") or []})
    seats = sorted({c["seat"] for c in cells})
    variants = sorted({c["variant"] for c in cells})
    segments = sorted({c["segment"] for c in cells})
    # The trigger types that reached the list: the intent feeds present on it (PS20 P1.7).
    feeds = m["intent"]["feeds"]
    present = [f["label"] for f in feeds if f["present"]]
    absent = len(feeds) - len(present)
    params = [
        ("Market", len(sup["countries"]), ", ".join(c["name"] for c in sup["countries"])),
        ("Company type", len(segments), ", ".join(segments) or "—"),
        ("Buyer seat", len(seats), ", ".join(_seat_label(x) for x in seats)),
        ("Message", len(variants), ", ".join(variants) or "—"),
        # Read from the specs, not restated: this row said "3 · day 1, day 4, day 9" while the
        # live campaign ran two touches on day 0 and day 5.
        ("Email in sequence", len(_days), ", ".join(f"day {d}" for d in _days) or "—"),
        (
            "Trigger type",
            len(present),
            (", ".join(present) or "none recorded")
            + (
                f" — the other {absent} feed{'' if absent == 1 else 's'} reached none of this list"
                if present and absent
                else ""
            ),
        ),
    ]
    combos = 1
    for _, n, _ in params:
        combos *= max(n, 1)
    # Only a parameter with more than one level varies (PS20 P1.7): "Five things change" sat
    # over a six-row table in which several rows had one level.
    varying = sum(1 for _, n, _ in params if n > 1)
    param_rows = "".join(
        f"<tr><td><strong>{_e(name)}</strong></td><td class='num-cell'>{n}</td>"
        f"<td class='muted'>{_e(detail)}</td></tr>"
        for name, n, detail in params
    )

    # Seat × message grid — the shape of the experiment at a glance.
    grid_card = _scoped_out(
        m,
        "The experiment, drawn",
        "The cell grid is built from cells.toml across the whole profile"
        + (f", so on a campaign page it draws message groups{beyond}" if beyond else "")
        + f". This campaign's {lanes_n} lane{' is' if lanes_n == 1 else 's are'} on Where "
        "things stand and What we're saying.",
    )
    grid_head = "".join(f"<th>{_e(v)}</th>" for v in variants)
    grid_rows = ""
    biggest = max((c["enrolled"] for c in cells), default=1) or 1
    for seat in seats:
        tds = ""
        for var in variants:
            cell = next((c for c in cells if c["seat"] == seat and c["variant"] == var), None)
            if cell:
                heat = 0.12 + 0.6 * (cell["enrolled"] / biggest)
                tds += (
                    f'<td class="gcell" style="background:color-mix(in srgb, var(--accent) {heat:.0%}, transparent)">'
                    f"<strong>{cell['enrolled']:,}</strong></td>"
                )
            else:
                tds += '<td class="gcell empty">·</td>'
        grid_rows += f"<tr><th>{_e(_seat_label(seat))}</th>{tds}</tr>"

    readable = [c for c in cells if c["comparable_on"]]
    seat_readable = [c for c in cells if any(e.startswith("seat") for e in c["comparable_on"])]
    var_readable = [c for c in cells if any(e.startswith("variant") for e in c["comparable_on"])]

    lift_rows = "".join(
        f'<div class="brow"><div class="blabel">{_e(_seat_label(c["seat"]))} · {_e(c["variant"])}</div>'
        f'<div class="btrack"><div class="bfill ta" '
        f'style="width:{min(100, 100 * (c["detectable_lift"] or 20) / 14):.0f}%"></div></div>'
        f'<div class="bval">{c["detectable_lift"]}×</div></div>'
        for c in sorted(cells, key=lambda x: x["detectable_lift"] or 99)
        if c["detectable_lift"]
    )

    fig = _scope_figures(m)
    goal_cards = "".join(
        _goal_card(c, campaign_contacted(fig, c)) for c in m["campaigns"]["campaigns"]
    )

    varies_full = f"""      <div class="card">
        <h2>What varies, and by how much</h2>
        <p class="note">{varying} of the {len(params)} parameters below
        {"takes" if varying == 1 else "take"} more than one level. Multiplied out that is
        <strong>{combos:,} possible combinations</strong> across {sup["total"]:,} people — roughly
        {sup["total"] // max(combos, 1)} people per combination. That ratio, not the total, is what
        decides whether anything is measurable.</p>
        <table><thead><tr><th>Parameter</th><th>Levels</th><th></th></tr></thead>
        <tbody>{param_rows}</tbody></table>
      </div>"""

    lift_full = f"""      <div class="card">
        <h2>How big a difference each group could even show</h2>
        <p class="note">At the number of people planned per group, a difference smaller than this
        would be indistinguishable from chance. Shorter is better.</p>
        {lift_rows or "<p class='note'>No groups sized yet.</p>"}
        <p class="note">{_e(_lift_note(fig["contacted"][0]))}</p>
      </div>"""

    grid_full = f"""<div class="card">
        <h2>The experiment, drawn</h2>
        <p class="note">Rows are buyer seats, columns are message bodies ({len(variants)} of them).
        A filled square is a real group of people; darker means larger.</p>
        <div class="gridwrap"><table class="grid">
          <thead><tr><th></th>{grid_head}</tr></thead><tbody>{grid_rows}</tbody></table></div>
        <p class="note"><strong>Read the shape, not the numbers.</strong> Where a column has
        several filled squares, one message went to several seats — so seat can be compared
        cleanly. Where a row has several filled squares, one seat got several messages — so the
        message can be compared cleanly. A square that is alone in both its row and column can
        be compared with nothing.</p>
      </div>"""

    readable_full = f"""      <div class="card">
        <h2>What this run can and cannot answer</h2>
        <div class="verdicts">
          <div class="v ok"><div class="vn">{len(seat_readable)}</div>
            <div class="vl">groups where <strong>seat</strong> is readable</div>
            <div class="vd muted">Same message, different job — so a gap is about the person.</div></div>
          <div class="v ok"><div class="vn">{len(var_readable)}</div>
            <div class="vl">groups where <strong>message</strong> is readable</div>
            <div class="vd muted">Same job, different message — so a gap is about the copy.</div></div>
          <div class="v"><div class="vn">{len(cells) - len(readable)}</div>
            <div class="vl">groups readable against <strong>nothing</strong></div>
            <div class="vd muted">Both the audience and the message differ, so a gap means neither.</div></div>
        </div>
      </div>"""

    return f"""
      {goal_cards}
      {_headline_learnings(m)}

      {varies_card or varies_full}

      {grid_card or grid_full}

      {readable_card or readable_full}

      {lift_card or lift_full}

      {
        "".join(
            f'<details class="card"><summary>Full experiment notes · {_e(c["title"])}</summary>'
            + _experiment_block(c.get("experiment") or {})
            + "</details>"
            for c in m["campaigns"]["campaigns"]
            if c.get("experiment")
        )
    }"""


def _contacted_sentences(split: dict, planned: tuple) -> str:
    """ "N people contacted; the plan is M emails." — people with people, emails with emails
    (PS20 P1.4) — then everyone else the snapshot says was contacted, so the heading's own
    figure (current plus not linked) adds up inside the card."""
    n, earlier, unlinked = split["current"], split["earlier"], split["not_linked"]
    goal, goal_why = planned
    plan = (
        f"the plan is {figure_span('planned-emails', goal)} emails"
        if goal is not None
        else f"the email plan is not shown: {_e(goal_why)}"
        if goal_why
        else "no email plan is declared"
    )
    out = f"{figure_span('ops-contacted', n)} {_people(n)} contacted; {plan}."
    if earlier:
        out += (
            f" An earlier run contacted {figure_span('contacted-earlier', earlier)} more "
            f"{_people(earlier)}, not counted here."
        )
    if unlinked:
        out += (
            f" {figure_span('contacted-not-linked', unlinked)} more {_people(unlinked)} "
            f"{'was' if unlinked == 1 else 'were'} contacted on sequences no campaign lists."
        )
    return out


def _sent_card(m: dict, fig: dict, drifted: list) -> str:
    """Has anything gone out? Answered from the FIGURES (PS20 P1.3), never a campaign word.

    The heading is ``sent_heading``. The "press send" note appears only when all three hold:
    the snapshot was read, nobody in the current campaigns has been contacted, and a
    campaign's go-live word is ``staged`` or ``paused``. It replaces a guard on the campaign
    word alone, which headed "Nothing has been sent" over a count of 34 (every snapshot row,
    a retired run's included) while 10 people on the current campaign had been contacted.
    """
    camps = m["campaigns"]["campaigns"]
    if not camps:
        return ""
    split, why = fig["contacted"]
    if split is None:
        body = f"Not shown: {_e(why)}."
    else:
        body = _contacted_sentences(split, fig["planned"])
        if not split["current"] and any(c.get("state") in {"staged", "paused"} for c in camps):
            body += (
                " The emails are written and checked, but the campaign is <strong>not cleared "
                "to start</strong> yet — the re-push below is what is left."
                if drifted
                else " Everything is written, checked and loaded; a person still has to press send."
            )
    return (
        '<div class="card"><h2 data-figure="ops-heading">'
        f"{_e(sent_heading(split))}</h2><p>{body}</p></div>"
    )


def _ops_view(m: dict) -> str:
    """The mechanics behind the other four panels — what is actually loaded in the sending
    tool, what still has to be pushed, and what is blocking the start.

    This panel exists so that detail has somewhere to live other than the top of the page.
    The reader this page is written for opens it for the numbers; the re-push instruction is
    real and load-bearing, but it is an instruction to the one person who presses the
    buttons, not a headline. The safety property survives the move because the *state* stays
    where the copy is judged ("not cleared to start", next to the quality badge it qualifies)
    and only the *procedure* comes here.
    """
    staged = {
        sq["sequence_id"]: sq.get("staged_ts", "")
        for c in m["campaigns"]["campaigns"]
        for sq in c.get("sequences", [])
    }
    drifted = [msg for msg in m["messages"] if (msg.get("lint") or {}).get("drift")]
    # "Written, checked and loaded" is the sentence a reader acts on. It must never appear
    # while the reviewed copy and the loaded copy are different things — the whole point of
    # the drift check is that those two can silently diverge after a revision.
    cards = _sent_card(m, _scope_figures(m), drifted)

    if drifted:
        drifted_seqs = {
            msg["sequence_id"]
            for msg in m["messages"]
            if (msg.get("lint") or {}).get("drift") and msg.get("sequence_id")
        }
        distinct_seqs = {msg["sequence_id"] for msg in m["messages"] if msg.get("sequence_id")}
        rows = ""
        for msg in drifted:
            lint = msg["lint"]
            pushed = (staged.get(msg["sequence_id"]) or "")[:10] or "—"
            checked = (lint.get("ran_at") or "")[:10] or "—"
            what = "; ".join(lint["drift"]).capitalize()
            rows += (
                f"<tr><td>{_e(msg.get('title') or msg['sequence_id'])}</td>"
                f"<td class='muted'>{_e(pushed)}</td>"
                f"<td class='muted'>{_e(checked)}</td>"
                f"<td class='muted'>{_e(what)}</td></tr>"
            )
        cards += f"""
      <div class="card">
        <h2>Re-push before anyone starts a sequence
        <span class="pill risk" data-risk="re-push">not cleared to start</span></h2>
        <p><strong>{len(drifted_seqs)} of {len(distinct_seqs)} sequences</strong> were revised
        after they were last pushed to the sending tool. The tool still holds the older
        emails and the older recipient list, so every check on this page describes the
        revised files rather than what would actually go out today. Starting one now would
        send the version that was already replaced, to a list that still includes the people
        since held back.</p>
        <table><thead><tr><th>Sequence</th><th>Last pushed</th><th>Copy last checked</th>
        <th>What changed since it was pushed</th></tr></thead><tbody>{rows}</tbody></table>
        <p class="note">Re-pushing is a person's job in the sending tool's own interface.
        Nothing on this page and nothing in the pipeline can do it — that capability is
        withheld by design, so that no automated step can put mail in front of a human
        without one.</p>
      </div>"""

    blocker = ""
    for c in m["campaigns"]["campaigns"]:
        blocker = blocker or ((c.get("window") or {}).get("capacity_blocker") or "")
    if blocker:
        cards += (
            '<div class="card"><h2>What is holding it up</h2>'
            f'<p class="note">{_e(blocker)}</p></div>'
        )

    # Keyed on whether anything is *pending*, not on whether the panel happens to be empty:
    # "no outstanding steps" is a finding a reader needs stated, and it must not be inferable
    # only from the absence of a card — never-checked and checked-and-clear look identical
    # when both render as silence.
    if not drifted and not blocker:
        cards += (
            '<div class="card"><h2>Nothing outstanding</h2>'
            "<p class='note'>Every sequence in the sending tool matches the copy and the "
            "recipient list that were checked. Whether to start one is a decision, not a "
            "missing step.</p></div>"
        )
    return cards + _maintainer_block(m) + _maintenance_card(m)


def _maintenance_card(m: dict) -> str:
    """Maintenance (PS20 P1.6, P1.11): an eval round waiting to be labelled — the retired
    page banner, now one plain line — and sending-figure rows the loader could not read.
    Both read with ``.get``: hand-built models (``test_m17``) carry neither key."""
    lab, lines = m.get("eval_labeler") or {}, []
    pre, blank = lab.get("prefilled", 0), lab.get("blind", 0)
    if pre + blank:
        how = f"{pre:,} pre-filled for you to correct and" if pre else "none pre-filled,"
        lines.append(
            f'An email-check round is waiting to be labelled: <a href="{_e(lab["href"])}" '
            f'class="review-sheet-link">labeler-{_e(lab["stamp"])}</a> ({how} {blank:,} blank).'
            " Until someone labels it, the rules it tests read <code>delete-candidate</code>: no"
            " evidence yet, not no value."
        )
    skipped = (m["status"].get("snapshot") or {}).get("skipped") or 0
    if skipped > 0:
        lines.append(
            f"{skipped:,} row(s) in the sending tool's figures file could not be read and are "
            "left out of every sending figure."
        )
    body = "".join(f"<p>{line}</p>" for line in lines)
    return f'<div class="card"><h2>Maintenance</h2>{body}</div>' if lines else ""
