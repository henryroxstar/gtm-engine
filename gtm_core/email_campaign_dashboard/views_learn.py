from __future__ import annotations

from ..campaigns_dashboard import _experiment_block
from .forecast import _lanes
from .format import _e, _pct, _scoped_out, _seat_label


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
        '<div class="card banner"><h2>What this run is actually for</h2>'
        '<p class="note">In order. Every one of these is answerable at this size; a reply '
        "RATE is not, and is not on the list.</p>" + blocks + "</div>"
    )


def _learn_view(m: dict) -> str:
    varies_card = _scoped_out(
        m,
        "What varies, and by how much",
        "The combinations-per-person ratio is computed over the whole prospect pool, so it "
        "describes the profile's experiment design, not this campaign's.",
    )
    scheduled = sum(ln["people"] for ln in _lanes(m))
    lanes_n = len(_lanes(m))
    lift_card = _scoped_out(
        m,
        "How big a difference each group could even show",
        "Minimum detectable effect is sized from the pool's per-group counts. At "
        f"{scheduled} scheduled recipients across {lanes_n} lanes this campaign cannot power a "
        "comparison at all, which is the honest answer rather than a forecast.",
    )
    # Two hand-written hypothesis cards used to open this panel and both had gone stale. The
    # first tested "a verified fact about the company plus the seat's own problem beats a
    # generic pitch" — but 12 of the 17 scheduled recipients are on a lane that asserts
    # nothing about them BY DESIGN, so the contrast it names is not the one this run set up.
    # The second asked whether a paid topic-surge feed predicts replies, on a list where only
    # one signal type appears at all and the untraceable half cannot sit on either side. What
    # replaces them is the manifest's own `will_learn`, rendered at the top of the panel.
    hypothesis_card = _scoped_out(
        m,
        "The hypothesis",
        "Superseded. This campaign's questions are the numbered list above, read from its own "
        "manifest, and they are not the profile-wide fact-plus-seat hypothesis this card "
        "carried: most of this run's recipients are on a lane that makes no claim about them.",
    )
    trigger_card = _scoped_out(
        m,
        "Second hypothesis: does the trigger predict the reply?",
        "Not askable of this run and so not asked. Only one trigger type reaches the list "
        "(topic surge) and the rest of the roster carries no recorded signal, so the "
        "comparison has no second arm — it would contrast a traceable group against an "
        "untraceable one. The question is a profile-level one and belongs on the rollup page.",
    )
    readable_card = _scoped_out(
        m,
        "What this run can and cannot answer",
        "The readable-group counts are computed over cells.toml across the whole profile, so "
        "on a campaign page they count another campaign's message groups. What THIS run can "
        "and cannot answer is the numbered list above, and the 'what it will not tell us' "
        "line in the full experiment notes below.",
    )
    cm = m["cells"]
    cells = cm["cells"]
    sup = m["supply"]

    _days = sorted({c["day"] for msg in m.get("messages") or [] for c in msg.get("copy") or []})
    seats = sorted({c["seat"] for c in cells})
    variants = sorted({c["variant"] for c in cells})
    params = [
        ("Market", len(sup["countries"]), ", ".join(c["name"] for c in sup["countries"])),
        ("Company type", len({c["segment"] for c in cells}), "startup, enterprise"),
        ("Buyer seat", len(seats), ", ".join(_seat_label(x) for x in seats)),
        ("Message", len(variants), f"{len(variants)} different bodies"),
        # Read from the specs, not restated: this row said "3 · day 1, day 4, day 9" while the
        # live campaign ran two touches on day 0 and day 5.
        ("Email in sequence", len(_days), ", ".join(f"day {d}" for d in _days) or "—"),
        (
            "Trigger type",
            1,
            "topic surge only — the other four signal types reached none of this list",
        ),
    ]
    combos = 1
    for _, n, _ in params:
        combos *= max(n, 1)
    param_rows = "".join(
        f"<tr><td><strong>{_e(name)}</strong></td><td class='num-cell'>{n}</td>"
        f"<td class='muted'>{_e(detail)}</td></tr>"
        for name, n, detail in params
    )

    # Seat × message grid — the shape of the experiment at a glance.
    grid_card = _scoped_out(
        m,
        "The experiment, drawn",
        "The cell grid is built from cells.toml across the whole profile, so on a campaign page "
        "it draws another campaign's message groups. This campaign's two lanes are on Where "
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
                    f'<td class="gcell" style="background:rgba(124,92,255,{heat:.2f})">'
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

    goal_cards = ""
    for c in m["campaigns"]["campaigns"]:
        pva = "".join(
            f"<tr><td>{_e(k)}</td><td class='num-cell'>{v['target']:,}</td>"
            f"<td class='num-cell'>{v['actual']:,}</td>"
            f"<td class='num-cell muted'>{v['pct']}%</td></tr>"
            for k, v in c["promised_vs_actual"].items()
        )
        goal_cards += (
            f'<div class="card"><h2>{_e(c["title"])}</h2>'
            + (
                "<p class='note'>What this campaign promised, and where it actually is. "
                "Everything reads 0% because nothing has been sent yet.</p>"
                "<table><thead><tr><th>Measure</th><th>Goal</th><th>So far</th><th></th>"
                f"</tr></thead><tbody>{pva}</tbody></table>"
                if pva
                else "<p class='note'>No goals recorded for this campaign.</p>"
            )
            + "</div>"
        )

    varies_full = f"""      <div class="card">
        <h2>What varies, and by how much</h2>
        <p class="note">Five things change from person to person. Multiplied out that is
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
        <p class="note">Nothing has been sent, so these are forecasts at the planned size. Once
        sending starts they are recalculated against real numbers.</p>
      </div>"""

    grid_full = f"""<div class="card">
        <h2>The experiment, drawn</h2>
        <p class="note">Rows are buyer seats, columns are the four different message bodies.
        A filled square is a real group of people; darker means larger.</p>
        <div class="gridwrap"><table class="grid">
          <thead><tr><th></th>{grid_head}</tr></thead><tbody>{grid_rows}</tbody></table></div>
        <p class="note"><strong>Read the shape, not the numbers.</strong> Where a column has
        several filled squares, one message went to several seats — so seat can be compared
        cleanly. Where a row has several filled squares, one seat got several messages — so the
        message can be compared cleanly. A square that is alone in both its row and column can
        be compared with nothing.</p>
      </div>"""

    # The profile-wide page keeps all three: on the rollup they ARE the right question, and
    # their denominators (the pool, cells.toml) are the pool the rollup is about. Only a
    # campaign-scoped page swaps them for the manifest's own list above.
    hypothesis_full = """      <div class="card banner">
        <h2>The hypothesis</h2>
        <p style="font-size:17px;margin:0 0 10px">If we open with a <strong>verified fact about
        the company</strong> and lead on <strong>the problem that buyer's seat actually owns</strong>,
        more of them reply than to a generic pitch.</p>
        <p class="note">The last attempt sent 24 emails, got one reply, and it was an unsubscribe.
        An audit found half the list could not have acted on the offer. This run fixes the aim
        before spending more sends — so the thing being tested is <em>targeting plus opening
        line together</em>, not either one alone.</p>
      </div>"""

    trigger_full = f"""      <div class="card">
        <h2>Second hypothesis: does the trigger predict the reply?</h2>
        <p style="font-size:16px;margin:0 0 10px">Not every reason to write is worth the same.
        A company <strong>researching the exact topic we sell into</strong> may be a better
        timing signal than one that merely <em>ships something adjacent</em> — and if so, the
        topic itself may rank: agent identity surging is a different buying moment from
        multi-agent systems surging.</p>
        <p class="note">This is worth testing because it changes what we buy. Topic-surge data
        is a paid feed; a durable capability statement is free research. If surge predicts
        replies, the feed earns its cost and should gate the list. If it does not, we are paying
        for a sort order that does nothing.</p>
        <p class="note"><strong>Right now this question is half-answerable at best.</strong>
        {_pct(m["intent"]["matched"], m["intent"]["total"])} of the people
        being emailed can be traced to an intent record; the rest carry no signal at all, so they
        cannot sit on either side of the comparison. And only one signal type is present
        (topic surge), so "which kind of trigger works best" cannot be asked of this run — only
        "does topic surge beat no recorded surge", and even that compares a traceable group
        against an untraceable one, which is not a clean contrast.</p>
        <p class="note"><strong>To make it answerable next run:</strong> carry the intent fields
        onto the sending list instead of leaving them in the prospect pool, so a reply can be
        attributed to the trigger that earned the send; and record the trigger <em>type</em>
        per person, not just the sentence it produced.</p>
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
          <div class="v bad"><div class="vn">{len(cells) - len(readable)}</div>
            <div class="vl">groups readable against <strong>nothing</strong></div>
            <div class="vd muted">Both the audience and the message differ, so a gap means neither.</div></div>
        </div>
        <p class="note">The message comparison is an accident worth keeping: the recipients whose
        job title our classifier could not place are spread across all three enterprise messages,
        which holds the audience roughly constant and varies only the copy. It is the one place
        this run can compare messages at all.</p>
      </div>"""

    return f"""
      {goal_cards}
      {_headline_learnings(m)}

      {hypothesis_card or hypothesis_full}

      {trigger_card or trigger_full}

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
    cards = ""

    # "Written, checked and loaded" is the sentence a reader acts on. It must never appear
    # while the reviewed copy and the loaded copy are different things — the whole point of
    # the drift check is that those two can silently diverge after a revision.
    if any(c.get("state") == "not_sending" for c in m["campaigns"]["campaigns"]):
        planned = sum(int(c["targets"].get("emails", 0) or 0) for c in m["campaigns"]["campaigns"])
        loaded_note = (
            "The emails are written and checked, but the campaign is <strong>not cleared to "
            "start</strong> yet — the re-push below is what is left."
            if drifted
            else "Everything is written, checked and loaded; a person still has to press send."
        )
        # The sent count is READ, not asserted as 0 because the branch above implies it. The
        # guard is `state == "not_sending"`, which is a campaign-state fact; the headline is a
        # send fact, and a literal that is only true because of a sibling condition is one
        # widened guard away from being a lie in bold type.
        sent = sum(int(s.get("sent") or 0) for s in m["status"].get("sequences", []))
        cards += (
            f'<div class="card banner"><h2>Nothing has been sent</h2><p><strong>{sent:,} of '
            f"{planned:,}</strong> planned emails have gone out. {loaded_note}</p></div>"
        )

    if drifted:
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
      <div class="card banner">
        <h2>Re-push before anyone starts a sequence</h2>
        <p><strong>{len(drifted)} of {len(m["messages"])} sequences</strong> were revised
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
    return cards
