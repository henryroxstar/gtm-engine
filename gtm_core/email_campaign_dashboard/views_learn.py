from __future__ import annotations

from ..campaigns_dashboard import _experiment_block
from ..power import detectable_lift
from .aggregate import _scope_figures, sent_heading
from .config import TAB_LABELS
from .forecast import _lanes
from .format import _e, _scoped_out, _seat_label


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


def _varies_block(m: dict) -> str:
    varies_card = _scoped_out(
        m,
        "What varies, and by how much",
        "The combinations-per-person ratio is computed over the whole prospect pool, so it "
        "describes the profile's experiment design, not this campaign's.",
    )
    cells = m["cells"]["cells"]
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
    return varies_card or varies_full


def _grid_block(m: dict) -> str:
    cells = m["cells"]["cells"]
    own = {msg["sequence_id"] for msg in m["messages"]}
    beyond = (
        " from outside this campaign" if any(c["sequence_id"] not in own for c in cells) else ""
    )
    seats = sorted({c["seat"] for c in cells})
    variants = sorted({c["variant"] for c in cells})
    # Seat × message grid — the shape of the experiment at a glance.
    grid_card = _scoped_out(
        m,
        "The experiment, drawn",
        "The cell grid is built from cells.toml across the whole profile"
        + (f", so on a campaign page it draws message groups{beyond}" if beyond else "")
        + f". This campaign's own sequences are on the {_e(TAB_LABELS['emails'])} tab.",
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
    return grid_card or grid_full


def _can_answer_block(m: dict) -> str:
    cells = m["cells"]["cells"]
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
    readable = [c for c in cells if c["comparable_on"]]
    seat_readable = [c for c in cells if any(e.startswith("seat") for e in c["comparable_on"])]
    var_readable = [c for c in cells if any(e.startswith("variant") for e in c["comparable_on"])]

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
    return readable_card or readable_full


def _lift_block(m: dict) -> str:
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
            f" At {scheduled} scheduled recipient{'' if scheduled == 1 else 's'} across {lanes_n} "
            f"lane{'' if lanes_n == 1 else 's'} this campaign cannot "
            "power a comparison at all, which is the honest answer rather than a forecast."
            if unpowered
            else ""
        ),
    )
    cells = m["cells"]["cells"]
    lift_rows = "".join(
        f'<div class="brow"><div class="blabel">{_e(_seat_label(c["seat"]))} · {_e(c["variant"])}</div>'
        f'<div class="btrack"><div class="bfill ta" '
        f'style="width:{min(100, 100 * (c["detectable_lift"] or 20) / 14):.0f}%"></div></div>'
        f'<div class="bval">{c["detectable_lift"]}×</div></div>'
        for c in sorted(cells, key=lambda x: x["detectable_lift"] or 99)
        if c["detectable_lift"]
    )
    fig = _scope_figures(m)

    lift_full = f"""      <div class="card">
        <h2>How big a difference each group could even show</h2>
        <p class="note">At the number of people planned per group, a difference smaller than this
        would be indistinguishable from chance. Shorter is better.</p>
        {lift_rows or "<p class='note'>No groups sized yet.</p>"}
        <p class="note">{_e(_lift_note(fig["contacted"][0]))}</p>
      </div>"""
    return lift_card or lift_full


def _experiment_notes(m: dict) -> str:
    return "".join(
        f'<details class="card"><summary>Full experiment notes · {_e(c["title"])}</summary>'
        + _experiment_block(c.get("experiment") or {})
        + "</details>"
        for c in m["campaigns"]["campaigns"]
        if c.get("experiment")
    )
