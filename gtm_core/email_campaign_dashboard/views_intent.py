"""Buying-intent and ICP fit — who the list is, as far as the scored pool can say.

Split out of :mod:`~gtm_core.email_campaign_dashboard.views_who` on 2026-09-25 under §R10,
when gating that module's tenant prose on its own data (PS20 P1.7 Rule B) took it over the
cap. The cut follows the question: this half answers "what do we know about their intent",
from the pool's scored records; the rest of `views_who` answers who the people are.
"""

from __future__ import annotations

from .format import _barlist, _e, _pct


def _intent_block(m: dict) -> str:
    """ICP score and buying-intent evidence, with coverage stated first.

    Coverage leads because it governs everything below it: a score distribution drawn
    from half the list is not a description of the list.
    """
    it = m["intent"]
    total, matched = it["total"], it["matched"]
    cov = _pct(matched, total)

    feed_rows = "".join(
        f"<tr><td>{_e(f['label'])}</td>"
        f"<td class='muted'>{_e(f['level'])}-level · {_e(f['means'])}</td>"
        + (
            f"<td class='num-cell'>{f['n']:,}</td><td><span class='pill'>present</span></td>"
            if f["present"]
            # f['n'] is `feeds.get(k, 0)` upstream (gtm_core/cells.py intent_profile) — it IS 0
            # here by construction whenever `present` is False, so this derives rather than types.
            else f"<td class='num-cell muted'>{f['n']:,}</td>"
            "<td><span class='pill'>not on this list</span></td>"
        )
        + "</tr>"
        for f in it["feeds"]
    )
    topic_rows = "".join(
        f"<tr><td>{_e(t['topic'])}</td><td class='num-cell'>{t['n']:,}</td>"
        f"<td class='num-cell muted'>{t['avg_score'] if t['avg_score'] is not None else '—'}</td></tr>"
        for t in it["topics"]
    )
    path_rows = "".join(
        f"<tr><td>{_e(p['name'])}</td><td class='num-cell'>{p['n']:,}</td>"
        f"<td class='num-cell muted'>{_pct(p['n'], matched)}</td></tr>"
        for p in it["paths"]
    )
    nir = it["new_in_role"]
    present = [f["label"] for f in it["feeds"] if f["present"]]
    # "Is empty" only when it is: the sentence was typed over a count it never read (P1.7).
    empty = "" if nir["true"] else '<strong>"New in role" is empty.</strong> '
    never = "" if nir["true"] else " A marker that is never true cannot be tested."

    score_line = (
        f"Scores run {it['score_min']:.0f} to {it['score_max']:.0f} "
        f"(average {it['score_avg']}) across the {it['score_n']:,} we can trace."
        if it["score_n"]
        else "No ICP score could be recovered for anyone on this list."
    )

    return f"""
      <div class="card">
        <h2>How well they fit the ideal customer</h2>
        <p class="note"><strong>We can only answer this for {matched:,} of {total:,} people
        ({cov}).</strong> The other {it["unmatched"]:,} were loaded into the sending list without a
        traceable link back to the scored prospect pool, so they carry no known score and no known
        buying-intent signal at all. That is not a low score — it is no score.</p>
        <p class="note">{score_line} The scores are attached to the <em>company</em>, not the
        person, and the link back is made on company domain, so read every number below as a
        statement about the employer.</p>
        {_barlist([("traceable to a scored record", matched), ("no score on file", it["unmatched"])], total)}
      </div>

      <div class="card">
        <h2>What buying-intent signals we actually hold</h2>
        <p class="note">The full roster of signals this pipeline can source, and which of them
        reached this list. Showing the absent ones matters: otherwise there is no way to tell
        "we have no hiring signal here" from "hiring signal is not a thing we collect".</p>
        <table><thead><tr><th>Buying signal</th><th>What it means</th><th>People</th><th></th></tr>
        </thead><tbody>{feed_rows}</tbody></table>
        <p class="note"><strong>{len(present)} of the {len(it["feeds"])} feeds reached this
        list</strong>{": " + _e(", ".join(present)) if present else ""}.</p>
        <p class="note">{empty}{nir["true"]:,} people are marked as recently changed job,
        {nir["false"]:,} are marked as not, {nir["unknown"]:,} are unrecorded.{never}</p>
      </div>

      <div class="card">
        <h2>Which topics they are researching</h2>
        <p class="note">Topic surge across the traceable group. The score is the third party's own
        intensity reading, not ours.</p>
        <table><thead><tr><th>Topic</th><th>Companies</th><th>Avg intensity</th></tr></thead>
        <tbody>{topic_rows or "<tr><td colspan=3 class='muted'>none recorded</td></tr>"}</tbody>
        </table>
        <h3>How each one qualified</h3>
        <table><thead><tr><th>Qualification path</th><th>People</th><th>Share</th></tr></thead>
        <tbody>{path_rows}</tbody></table>
      </div>"""
