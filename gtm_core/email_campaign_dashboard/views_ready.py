"""Before sending starts, and maintenance: has anything gone out, what still has to be
pushed, what is holding the start up, and the lines a maintainer acts on. Moved out of
``views_learn`` (PS20 Task 2.1b) so that module has §R10 room for its own carve."""

from __future__ import annotations

from .aggregate import _scope_figures, sent_heading
from .format import _e, figure_span


def _people(n: int) -> str:
    return "person" if n == 1 else "people"


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


def _readiness_blocks(m: dict) -> dict[str, str]:
    """The "Before sending starts" cards, keyed, in the order ``_ops_view`` joins them;
    ``""`` where that branch does not run (PS20 Task 2.1b)."""
    staged = {
        sq["sequence_id"]: sq.get("staged_ts", "")
        for c in m["campaigns"]["campaigns"]
        for sq in c.get("sequences", [])
    }
    drifted = [msg for msg in m["messages"] if (msg.get("lint") or {}).get("drift")]
    # "Written, checked and loaded" is the sentence a reader acts on. It must never appear
    # while the reviewed copy and the loaded copy are different things — the whole point of
    # the drift check is that those two can silently diverge after a revision.
    blocks = dict.fromkeys(("sent", "re-push", "holding-up", "nothing-outstanding"), "")
    blocks["sent"] = _sent_card(m, _scope_figures(m), drifted)

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
        blocks["re-push"] = f"""
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
        blocks["holding-up"] = (
            '<div class="card"><h2>What is holding it up</h2>'
            f'<p class="note">{_e(blocker)}</p></div>'
        )

    # Keyed on whether anything is *pending*, not on whether the panel happens to be empty:
    # "no outstanding steps" is a finding a reader needs stated, and it must not be inferable
    # only from the absence of a card — never-checked and checked-and-clear look identical
    # when both render as silence.
    if not drifted and not blocker:
        blocks["nothing-outstanding"] = (
            '<div class="card"><h2>Nothing outstanding</h2>'
            "<p class='note'>Every sequence in the sending tool matches the copy and the "
            "recipient list that were checked. Whether to start one is a decision, not a "
            "missing step.</p></div>"
        )
    return blocks


def _maintenance_lines(m: dict) -> str:
    """The maintenance ``<p>`` lines, or ``""``: an eval round waiting to be labelled — the
    retired page banner, now one plain line — and sending-figure rows the loader could not
    read. Both read with ``.get``: hand-built models (``test_m17``) carry neither key. The
    Operator notes group names the section itself (PS20 Task 2.6b)."""
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
        row_w = "row" if skipped == 1 else "rows"
        lines.append(
            f"{skipped:,} {row_w} in the sending tool's figures file could not be read and are "
            "left out of every sending figure."
        )
    return "".join(f"<p>{line}</p>" for line in lines)
