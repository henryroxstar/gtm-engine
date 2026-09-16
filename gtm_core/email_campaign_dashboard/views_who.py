from __future__ import annotations

from .config import FUNNEL_GLOSS, SEAT_COVERAGE
from .filters import sub_counts
from .format import (
    _barlist,
    _e,
    _pct,
    _pool_scope_note,
    _row_status,
    _seat_label,
    _stat,
    roster_gap,
    scope_label,
)


def _trim(text: str, n: int = 190) -> str:
    """One line of the reason, not the whole research note."""
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def _next_step(j: dict | None) -> str:
    """The judge's read of this row's DRAFTED email, and the one thing it routes to.

    A verdict word with no follow-up is the defect this column exists to fix. ``re-angle``
    beside an account tells a reader something was rejected and nothing about who picks it
    up — and on this campaign the answer splits almost evenly between two different teams:
    *find another seat* (the copy is fine, the person does not own the problem) and *rewrite
    the argument* (the person is fine, the copy is what failed). Both were rendered as the
    same word, so the page could not distinguish a prospecting backlog from a writing one.
    """
    if not j:
        return "<td class='muted'>—<div class='muted' style='opacity:.75;margin-top:3px'>no drafted email, so nothing to judge</div></td>"
    tone = {"send": "good", "re-angle": "warn"}.get(j["verdict"], "bad")
    also = (
        " <span class='muted'>· a second row for this address scored differently; the "
        "stricter one is shown</span>"
        if j.get("also")
        else ""
    )
    return (
        "<td class='muted'>"
        f'<span class="pill {tone}">{_e(j["verdict"] or "—")}</span> '
        f"<strong>{_e(j['action'] or 'unrouted')}</strong>"
        f"<div class='muted' style='opacity:.75;margin-top:3px'>{_e(_trim(j['note'], 150))}"
        f"{also}</div></td>"
    )


#: Why a block does not follow the filter. Server-rendered and ``hidden``; the page's JS
#: only unhides it. Kept beside the blocks they describe rather than in the template,
#: because §R14's prose lint reads ``*.py`` and nothing else.
_STALE_SEAT = (
    '<p class="why" hidden>Not filtered — this measures the merge LANES against their own '
    "specs, so its denominator is the recipients those lanes render, not the accounts "
    "selected above.</p>"
)
_STALE_JUDGE = (
    '<p class="why" hidden>Not filtered — the judge scored a queue of drafted emails, so '
    "these are rows in that queue rather than accounts in this roster.</p>"
)


def _seat_fit_note(m: dict) -> str:
    """Whether the merge lanes reach the seat their own spec declares.

    Replaces a hand-written sentence that read "7 of the 18 generic-lane recipients hold a seat
    the hook matrix has no row for". Measured with the coverage gate's own classifier it is 15
    of 18 — a hand count of unfamiliar titles is exactly what a classifier is for, and the
    understatement made the campaign's biggest targeting finding look like a rounding issue.
    """
    f = m.get("seat_fit") or {}
    total = f.get("total")
    if not total:
        return ""
    off = f.get("elsewhere", 0) + f.get("unresolved", 0)
    if not off:
        return (
            '<div data-stale-when-filtered><p class="note">All '
            f"{total} merge-lane recipients hold the seat their spec declares.</p>"
            f"{_STALE_SEAT}</div>"
        )
    eg = ", ".join(
        f"{'an' if x[:1].upper() in 'AEIOU' else 'a'} {_e(x)}"
        for x in (f.get("off_seat_titles") or [])[:3]
    )
    return (
        "<div data-stale-when-filtered>"
        f'<p class="note"><strong>{off} of the {total} merge-lane recipients do not hold the '
        f"seat their own spec declares</strong> — {f.get('unresolved', 0)} whose title the hook "
        f"matrix has no row for at all ({eg}, plus {f.get('no_title', 0)} role inboxes with no "
        f"title), and {f.get('elsewhere', 0)} who resolve to a different seat. Only "
        f"<strong>{f.get('matched', 0)}</strong> are the declared one.</p>"
        '<p class="note"><strong>Being on the generic lane does not excuse that.</strong> '
        "Generic means <em>signal-free</em>, not <em>seat-free</em>: the body makes no claim "
        "about the recipient's company — that is what the lane buys — but the spec still "
        "declares a <code>hook_cell</code> whose left half is a persona, and the body still "
        "argues that persona's problem and that persona's stakes. A recipient who is not that "
        "seat gets an argument written for someone else. That is a targeting finding, not a "
        "metadata one: <code>persona-lead-mismatch</code> cannot fire on a title it does not "
        "recognise, so nothing was checking it.</p>"
        '<p class="note">The empty why-now column on these rows is <em>correct</em>, by '
        "contrast — a generic lane makes no per-row research claim, and filling it would mean "
        "inventing the signals these rows were put in the lane for lacking.</p>"
        f"{_STALE_SEAT}</div>"
    )


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
        # Same canonical roster index the worklist stamps — see `_row_html` there. This
        # table sorts by tier and that one regroups into buckets, so a display-position
        # index would make one filter selection hide two different sets of accounts.
        f'<tr data-row="{x["i"]}"><td><strong>{_e(x["company"])}</strong></td>'
        f"<td>{_row_status(m, x['email'])}</td>"
        f"<td class='muted tech'>{_e(x['seat'] or '—')}</td>"
        f"<td>{_e(x['tier'] or '—')}</td>"
        f"<td>{pill(bool(x['email']), 'verified', 'no address')}</td>"
        f"<td class='tech'>{pill(x['named'], 'named seat', 'role inbox')}</td>"
        + (
            f"<td class='muted'>{_e(_trim(x['why_now'], 150))}</td>"
            if x.get("why_now")
            else "<td class='muted'>—</td>"
        )
        + f"<td class='muted tech'>{_e(x['verdict'] or '—')}"
        + (
            f"<div class='muted' style='opacity:.75;margin-top:3px'>{_e(_trim(x['verdict_reason']))}</div>"
            if x.get("verdict_reason")
            else ""
        )
        + "</td>"
        + _next_step(x.get("judge"))
        + "</tr>"
        for x in rows
    )
    one = len(m["campaigns"]["campaigns"]) == 1
    # A hand-written finding about ONE campaign's generic lane. True of sg-builders and
    # of nothing else, so it renders only when the page is about a single campaign — under
    # a multi-campaign heading it is a specific claim about a set it was never measured on,
    # which is a mis-scoped tile wearing prose.
    lane_finding = _seat_fit_note(m) if one else ""
    tiers = ", ".join(f"{n} {_e(k)}" for k, n in r.get("tiers", []))
    verdicts = ", ".join(f"{n} {_e(k)}" for k, n in r.get("verdicts", []))

    # The split between the two follow-ups, stated once above the table. A reader counting
    # `re-angle` down the column learns how many rows were rejected; what they need is how
    # many of those are a PROSPECTING job and how many are a WRITING one, because the answer
    # decides which of the two gets worked next and they are not the same size.
    judged = [x["judge"] for x in rows if x.get("judge")]
    src = next((j["source"] for j in judged if j.get("source")), "")
    filed = next((j["filed"] for j in judged if j.get("filed")), "")
    judge_src = (
        f" Scored {_e(filed)}, filed in <code>{_e(src)}</code>."
        if src
        else " No judge run on file for this roster."
    )
    by_action: dict[str, int] = {}
    for j in judged:
        by_action[j["action"] or "unrouted"] = by_action.get(j["action"] or "unrouted", 0) + 1
    uncal = any(not j["calibrated"] for j in judged)
    judge_split = ""
    if by_action:
        parts = ", ".join(
            f"<strong>{n} → {_e(a)}</strong>"
            for a, n in sorted(by_action.items(), key=lambda kv: -kv[1])
        )
        judge_split = (
            "<div data-stale-when-filtered>"
            f'<p class="note"><strong>Where the {len(judged)} judged rows go:</strong> {parts}. '
            "Those are two different queues, not two shades of the same one — a re-target is a "
            "prospecting run to find a seat that owns the problem, a re-argue is a rewrite of "
            "the spec or the pack. <strong>Neither has been worked yet:</strong> every drafted "
            "email on this page is the version the judge scored, so a row reading "
            "<em>rewrite the argument</em> has not been rewritten."
            + (
                " And the judge is UNCALIBRATED for this profile — no sealed holdout has "
                "passed — so read the ordering, never the count, and expect it to reject "
                "well-formed category copy on principle."
                if uncal
                else ""
            )
            + "</p>"
            + _STALE_JUDGE
            + "</div>"
        )
    srcs = ", ".join(f"<code>{_e(s)}</code>" for s in r.get("sources", []))
    return f"""
      <div class="stats">
        {_stat(r["accounts"], f"accounts in {scope_label(m)}", "every one, not a sample", src="rows:all")}
        {_stat(r["contact_verified"], "have a verified address", src="rows:co_has_email", sub_html=sub_counts(r["rows"], [("co_no_email", " do not")]))}
        {_stat(r["named_seat"], "resolve to a named seat", src="rows:co_named", sub_html=sub_counts(r["rows"], [("co_role_inbox", " are role inboxes")]))}
        {_stat(r["signal"], "carry a dated why-now", src="rows:co_signal", sub_html=sub_counts(r["rows"], [("co_signal_sourced", " cite a source")]))}
      </div>

      <div class="card">
        <h2>Every account in {_e(scope_label(m))}</h2>
        <p class="note">Tiers: {tiers or "—"}. Research verdicts: {verdicts or "—"}.</p>
        <p class="note"><strong>Two different verdicts sit in this table and they answer
        different questions.</strong> <em>Research verdict</em> is the researcher's call on the
        ACCOUNT, made before any copy existed — <code>re-angle</code> there means the evidence
        was too thin for a hand-written 1:1, and its follow-up already happened: that account
        was routed to the generic seat lane, which is why it has a drafted email at all.
        <em>What happens next</em> is the email judge's read of that drafted email, and it is a
        RANKING, not a gate — no verdict here stops a send, and the deterministic
        <code>account_integrity</code> check is what does.{judge_src}</p>
        {judge_split}
        <table><thead><tr><th>Company</th><th>Status</th><th class="tech">Seat</th><th>Tier</th>
        <th>Address</th><th class="tech">Seat kind</th><th>Why-now (the signal)</th>
        <th class="tech">Research verdict</th>
        <th>What happens next</th></tr></thead><tbody>{body}</tbody></table>
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
    # A SCOPED page replaces the pool panel outright: a number that is not about this
    # campaign does not belong on this campaign's page. That reasoning INVERTS on the
    # profile rollup, where the shared pool is the subject — so there the roster is an
    # extra block, appended below. Returning early on the rollup deletes the funnel, the
    # market gate, supply, intent and topic surge and leaves a 26-row table in their place;
    # `test_dashboard_roster_identity.py::test_the_profile_rollup_keeps_the_pool_panel`
    # is the guard, and it convicts if this branch is removed.
    roster = _roster_who(m) if not roster_gap(m) else ""
    if roster and m.get("campaign_scope"):
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

      <div class="card" data-no-filter data-stale-when-filtered>
        <h2>Where they are</h2>
        {_barlist([(c["name"], c["n"]) for c in sup["countries"]], sup["total"])}
        <p class="why" hidden>Not filtered — this counts the shared prospect pool, a
        different and much larger set than the campaign roster the filter selects from.
        Filtering the roster cannot move it, and rescaling it to the selection would answer
        a question nobody asked.</p>
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

      {_intent_block(m)}
      {roster}"""


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
            # f['n'] is `feeds.get(k, 0)` upstream (gtm_core/cells.py intent_profile) — it IS 0
            # here by construction whenever `present` is False, so this derives rather than types.
            else f"<td class='num-cell muted'>{f['n']:,}</td>"
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
        <table><thead><tr><th>Buying signal</th><th>What it means</th><th>People</th><th></th></tr>
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
        <table><thead><tr><th>Qualification path</th><th>People</th><th>Share</th></tr></thead>
        <tbody>{path_rows}</tbody></table>
        <p class="note">Worth reading closely: the dominant route is a <em>relaxed</em> one. Very
        few cleared the full qualification gate, which means most of this list is here on topic
        surge alone rather than on surge plus a firmographic fit check.</p>
      </div>"""
