from __future__ import annotations

from .config import FUNNEL_GLOSS, SEAT_COVERAGE
from .format import (
    _barlist,
    _e,
    _pct,
    _pool_scope_note,
    _seat_label,
    _stat,
    roster_gap,
    scope_label,
)


def _trim(text: str, n: int = 190) -> str:
    """One line of the reason, not the whole research note."""
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def _roster_who(m: dict) -> str:
    """Who THIS campaign is emailing — its own 26 accounts, not the shared pool.

    The pool view answers "who could we email next", which is a profile-level question. Asked
    of a campaign it produced "731 people we will actually email of 859 loaded" on a page whose
    entire roster is 26 accounts. When a campaign declares where its accounts live, this
    replaces that view outright rather than labelling it — a number that is not about this
    campaign does not belong on this campaign's page at all.
    """
    r = m.get("roster") or {}
    rows = r.get("rows") or []
    if not rows:
        return ""
    pill = lambda ok, yes, no: (  # noqa: E731
        f'<span class="pill good">{yes}</span>' if ok else f'<span class="pill warn">{no}</span>'
    )
    body = "".join(
        f"<tr><td><strong>{_e(x['company'])}</strong></td>"
        f"<td class='muted'>{_e(x['seat'] or '—')}</td>"
        f"<td>{_e(x['tier'] or '—')}</td>"
        f"<td>{pill(bool(x['email']), 'verified', 'no address')}</td>"
        f"<td>{pill(x['named'], 'named seat', 'role inbox')}</td>"
        + (
            f"<td class='muted'>{_e(_trim(x['why_now'], 150))}</td>"
            if x.get("why_now")
            else "<td class='muted'>—</td>"
        )
        + f"<td class='muted'>{_e(x['verdict'] or '—')}"
        + (
            f"<div class='muted' style='opacity:.75;margin-top:3px'>{_e(_trim(x['verdict_reason']))}</div>"
            if x.get("verdict_reason")
            else ""
        )
        + "</td></tr>"
        for x in rows
    )
    one = len(m["campaigns"]["campaigns"]) == 1
    # A hand-written finding about ONE campaign's generic lane. True of sg-builders and
    # of nothing else, so it renders only when the page is about a single campaign — under
    # a multi-campaign heading it is a specific claim about a set it was never measured on,
    # which is a mis-scoped tile wearing prose.
    lane_finding = (
        """<p class="note"><strong>7 of the 18 generic-lane recipients hold a seat the hook matrix
        has no row for</strong> — an Administrative Assistant, an Operations Manager, a Managing
        Partner, plus five role inboxes with no title at all. That is a targeting finding, not a
        metadata one: <code>persona-lead-mismatch</code> cannot even apply to a title it does not
        recognise, so nothing checks that an agent-governance argument suits the person receiving
        it. The other 11 record no signal, which is <em>correct</em> for a generic lane — it makes
        no per-row research claim, and filling that column would mean inventing the signals these
        rows were put in the generic lane for lacking.</p>"""
        if one
        else ""
    )
    tiers = ", ".join(f"{n} {_e(k)}" for k, n in r.get("tiers", []))
    verdicts = ", ".join(f"{n} {_e(k)}" for k, n in r.get("verdicts", []))
    srcs = ", ".join(f"<code>{_e(s)}</code>" for s in r.get("sources", []))
    return f"""
      <div class="stats">
        {_stat(r["accounts"], f"accounts in {scope_label(m)}", "every one, not a sample", src="roster:accounts")}
        {_stat(r["contact_verified"], "have a verified address", f"{r['accounts'] - r['contact_verified']} do not")}
        {_stat(r["named_seat"], "resolve to a named seat", f"{r['contact_verified'] - r['named_seat']} are role inboxes")}
        {_stat(r["signal"], "carry a dated why-now", f"{r['signal_sourced']} cite a source")}
      </div>

      <div class="card">
        <h2>Every account in {_e(scope_label(m))}</h2>
        <p class="note">Tiers: {tiers or "—"}. Research verdicts: {verdicts or "—"}.
        A verdict is the researcher's call on the ACCOUNT, made before any copy existed; the
        email judge never overwrites it.</p>
        <table><thead><tr><th>Company</th><th>Seat</th><th>Tier</th><th>Address</th>
        <th>Seat kind</th><th>Why-now (the signal)</th><th>Verdict</th></tr></thead><tbody>{body}</tbody></table>
        {lane_finding}
        <p class="note">Counted from {_e(scope_label(m))}'s own run exports ({srcs}), folded by
        company keeping the richest row &mdash; so an enrichment pass wins over the discovery
        snapshot that preceded it. The shared prospect pool is deliberately not shown here: it
        answers "who could we email next", which is a question about the profile.</p>
      </div>"""


def _who_view(m: dict) -> str:
    # Same guard as the status tiles, same reason: a roster only some of the in-scope
    # campaigns contributed to must not render under "every one, not a sample". Falling
    # through to the pool-wide branch is right — it is at least LABELLED as profile-wide.
    if not roster_gap(m):
        roster = _roster_who(m)
        if roster:
            return roster
    scope_note = _pool_scope_note(m, "The prospect pool")
    f = m["status"]["funnel"]
    sup = m["supply"]

    mk = m["market"]
    gloss_rows = ""
    for key, title, desc in FUNNEL_GLOSS:
        extra = ""
        if key == "excluded_dnc_or_sent" and mk["gate_on"]:
            allowed = ", ".join(mk["markets"])
            extra = (
                f" <strong>{mk['out_of_market']:,} of them are out of market</strong> — this "
                f"profile may only email {_e(allowed)}. A further {mk['unknown_country']:,} rows "
                "carry no country at all; the gate only blocks a country it can read, so those "
                "are still treated as mailable."
            )
        gloss_rows += (
            f"<tr><td><strong>{_e(title)}</strong></td>"
            f"<td class='num-cell'>{f.get(key, 0):,}</td>"
            f"<td class='muted'>{_e(desc)}{extra}</td></tr>"
        )

    reasons = sup.get("suppression_reasons") or []
    supp_top = (
        ", ".join(f"{r['n']:,} {_e(r['reason'])}" for r in reasons[:3])
        if reasons
        else "no reasons recorded"
    )
    supp_rows = (
        "<table><thead><tr><th>Why held back</th><th>People</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td>{_e(r['reason'])}</td><td class='num-cell'>{r['n']:,}</td></tr>"
            for r in reasons
        )
        + "</tbody></table>"
        if reasons
        else ""
    )

    unplaced = sup["unplaced_total"]
    unplaced_rows = "".join(
        f"<tr><td>{_e(t['name'])}</td><td class='num-cell'>{t['n']:,}</td></tr>"
        for t in sup["unplaced_titles"][:12]
    )
    covered = ", ".join(f"{v} (<code>{k}</code>)" for k, v in SEAT_COVERAGE.items())

    return f"""
      {scope_note}
      <div class="stats">
        {
        _stat(
            sup["qualified_sendable"],
            "people we will actually email",
            (
                f"of {sup['total']:,} loaded — pool-wide; this campaign's goals are on "
                "Where things stand"
                if m.get("campaign_scope")
                else f"of {sup['total']:,} loaded — goals are set on this number"
            ),
        )
    }
        {_stat(f["ready"], "more we could email", "verified address, not yet loaded")}
        {_stat(f["needs_verification"], "address not confirmed", "person is right, inbox unproven")}
        {_stat(f["excluded_dnc_or_sent"], "off limits", "already contacted or opted out")}
      </div>

      <div class="card">
        <h2>How many actually count</h2>
        <p class="note">Two filters stand between "loaded" and "will receive an email", and the
        goals are set after both. <strong>{sup["suppressed"]:,} of {sup["total"]:,} are held
        back</strong> because the one researched sentence their email opens on does not survive
        inspection — {supp_top}. Separately, <strong>{sup["unclear"]:,} rows are
        "unread"</strong>: nobody has checked whether that job title could own this, and the
        qualification gate treats unread as not-proven rather than a soft pass.
        {sup["not_qualified"]:,} are ruled out outright. What is left —
        <strong>{sup["qualified_sendable"]:,} people</strong> — is what the goals are stated
        on.</p>
        {supp_rows}
        {
        _barlist(
            [
                ("will be emailed", sup["qualified_sendable"]),
                ("held back — weak opening line", sup["suppressed"]),
                ("job title unread", sup["unclear"]),
                ("ruled out", sup["not_qualified"]),
            ],
            sup["total"],
        )
    }
      </div>

      <div class="card">
        <h2>What each group means</h2>
        <p class="note">These are the four states a person can be in. The only one we can
        email today is the first.</p>
        <table><tbody>{gloss_rows}</tbody></table>
      </div>

      <div class="card">
        <h2>Where they are</h2>
        {_barlist([(c["name"], c["n"]) for c in sup["countries"]], sup["total"])}
        <p class="note">Three markets. The campaign is overwhelmingly a US motion — the two
        smaller markets are too small to read a result from on their own.</p>
      </div>

      <div class="card">
        <h2>What job they do</h2>
        {_barlist([(_seat_label(s["name"]), s["n"]) for s in sup["seats"]], sup["total"], tone="b")}
        <h3>Why {unplaced:,} say "other"</h3>
        <p class="note"><strong>This is a gap in our own classifier, not missing data.</strong>
        Every one of these people has a job title on file. The tenant's voice guide defines
        <strong>seven</strong> buyer seats, but the automated resolver only recognises
        <strong>three</strong> — {covered} — so anything outside those three is grouped as "other".
        The largest group below is Chief Information Officer, which is plainly a technology
        seat and simply is not in the resolver's word list.</p>
        <p class="note">This matters beyond tidiness: the check that stops us leading on the
        wrong seat's problem stays <em>silent</em> on an unrecognised title, by design. So
        these {unplaced:,} people received seat-targeted copy that nothing verified was aimed
        at them.</p>
        <table><thead><tr><th>Title we could not place</th><th>People</th></tr></thead>
        <tbody>{unplaced_rows}</tbody></table>
        <p class="note">{sup["unplaced_distinct"]} distinct titles in total.</p>
      </div>

      {_intent_block(m)}"""


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
            f"<td class='num-cell'>{f['n']:,}</td><td><span class='pill good'>present</span></td>"
            if f["present"]
            else "<td class='num-cell muted'>0</td>"
            "<td><span class='pill warn'>not on this list</span></td>"
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
        buying-intent signal at all. That is not a low score — it is no score, and it is the single
        biggest gap in what we know about who we are writing to.</p>
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
        <table><thead><tr><th>Signal</th><th>What it means</th><th>People</th><th></th></tr>
        </thead><tbody>{feed_rows}</tbody></table>
        <p class="note"><strong>Only one feed is actually present.</strong> Everything we know
        about intent on this list is topic surge — a third party observing that people at the
        company are reading unusually much about a subject. No hiring signal, no news signal, and
        no job-change timing reached these rows.</p>
        <p class="note"><strong>"New in role" is empty.</strong> {nir["true"]:,} people are marked
        as recently changed job, {nir["false"]:,} are marked as not, {nir["unknown"]:,} are
        unrecorded. A marker that is never true cannot be tested, and it is one of the questions
        this campaign originally set out to answer.</p>
      </div>

      <div class="card">
        <h2>Which topics they are researching</h2>
        <p class="note">Topic surge across the traceable group. The score is the third party's own
        intensity reading, not ours.</p>
        <table><thead><tr><th>Topic</th><th>Companies</th><th>Avg intensity</th></tr></thead>
        <tbody>{topic_rows or "<tr><td colspan=3 class='muted'>none recorded</td></tr>"}</tbody>
        </table>
        <h3>How each one qualified</h3>
        <table><thead><tr><th>Route</th><th>People</th><th>Share</th></tr></thead>
        <tbody>{path_rows}</tbody></table>
        <p class="note">Worth reading closely: the dominant route is a <em>relaxed</em> one. Very
        few cleared the full qualification gate, which means most of this list is here on topic
        surge alone rather than on surge plus a firmographic fit check.</p>
      </div>"""
