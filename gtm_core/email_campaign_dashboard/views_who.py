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
    figure_span,
    roster_gap,
    scope_label,
)
from .views_intent import _intent_block
from .views_segments import segment_mix


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
    also = (
        " <span class='muted'>· a second row for this address scored differently; the "
        "stricter one is shown</span>"
        if j.get("also")
        else ""
    )
    return (
        "<td class='muted'>"
        f'<span class="pill">{_e(j["verdict"] or "—")}</span> '
        f"<strong>{_e(j['action'] or 'unrouted')}</strong>"
        f"<div class='muted' style='opacity:.75;margin-top:3px'>{_e(_trim(j['note'], 150))}"
        f"{also}</div></td>"
    )


#: Why a block does not follow the filter. Server-rendered and ``hidden``; the page's JS
#: only unhides it. Kept beside the blocks they describe rather than in the template,
#: because §R14's prose lint reads ``*.py`` and nothing else.
#: The visible text is a two-word mark; the reason is its tooltip. The one full explanation
#: sits beside the filter control (``filters.bar_html``) rather than under every block.
_STALE_SEAT = (
    '<p class="why" hidden title="Not filtered — this measures the merge LANES against their '
    "own specs, so its denominator is the recipients those lanes render, not the accounts "
    'selected above.">not filtered</p>'
)
_STALE_JUDGE = (
    '<p class="why" hidden title="Not filtered — the judge scored a queue of drafted emails, '
    'so these are rows in that queue rather than accounts in this roster.">not filtered</p>'
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
        f"{_generic_lane_finding(m, f)}{_STALE_SEAT}</div>"
    )


def _generic_lane_finding(m: dict, f: dict) -> str:
    """Why an off-seat recipient on a GENERIC lane is still a targeting defect — only where the
    data holds each clause (P1.7 Rule B): every sequence here is registered on the generic lane
    in cells.toml, its spec declares a persona; "nothing was checking it" needs a title no
    persona resolves, and "the empty why-now column" needs the lane's rows to carry none."""
    msgs = m.get("messages") or []
    if not (msgs and all(x.get("lane") == "generic" for x in msgs) and f.get("declared")):
        return ""
    to = {(r.get("to") or "").lower() for r in (m.get("samples") or {}).get("rendered") or []}
    rows = (m.get("roster") or {}).get("rows") or []
    lane = [x for x in rows if (x.get("email") or "").lower() in to]
    unchecked = (
        " That is a targeting finding, not a metadata one: <code>persona-lead-mismatch</code> "
        "cannot fire on a title it does not recognise, so nothing was checking it."
        if f.get("unresolved")
        else ""
    )
    return (
        '<p class="note"><strong>Being on the generic lane does not excuse that.</strong> '
        "Generic means <em>signal-free</em>, not <em>seat-free</em>: the body makes no claim "
        "about the recipient's company — that is what the lane buys — but the spec still "
        "declares a <code>hook_cell</code> whose left half is a persona, and the body still "
        "argues that persona's problem and that persona's stakes. A recipient who is not that "
        f"seat gets an argument written for someone else.{unchecked}</p>"
        + (
            '<p class="note">The empty why-now column on these rows is <em>correct</em>, by '
            "contrast — a generic lane makes no per-row research claim, and filling it would "
            "mean inventing the signals these rows were put in the lane for lacking.</p>"
            if lane and not any(x.get("why_now") for x in lane)
            else ""
        )
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
        f'<span class="pill ok">{yes}</span>' if ok else f'<span class="pill">{no}</span>'
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
        f"<td class='tech'><span class='pill'>{'named seat' if x['named'] else 'role inbox'}"
        "</span></td>"
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
    # Seat fit only on a single campaign's page: a multi-campaign set was never measured.
    # Its hand-written explanation is further gated on the lane's data (_generic_lane_finding).
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
        if src and filed
        else f" Filed in <code>{_e(src)}</code>."
        if src
        else " No judge run on file for this roster."
    )
    by_action: dict[str, int] = {}
    for j in judged:
        by_action[j["action"] or "unrouted"] = by_action.get(j["action"] or "unrouted", 0) + 1
    uncal = all(not j["calibrated"] for j in judged)
    judge_split = ""
    if by_action:
        parts = ", ".join(
            f"<strong>{n} → {_e(a)}</strong>"
            for a, n in sorted(by_action.items(), key=lambda kv: -kv[1])
        )
        judge_split = (
            "<div data-stale-when-filtered>"
            f'<p class="note"><strong>Where the {len(judged)} judged rows go:</strong> {parts}.'
            + (
                " Those are two different queues, not two shades of the same one — a re-target "
                "is a prospecting run to find a seat that owns the problem, a re-argue is a "
                "rewrite of the spec or the pack."
                if len(set(by_action) - {"unrouted"}) == 2
                else ""
            )
            # Only the copy half of "has either queue been worked" is on record (P1.7 Rule B).
            + (
                " No judged row records a copy revision since it was scored, so a row reading "
                "<em>rewrite the argument</em> has not been rewritten."
                if not any(j.get("revised") for j in judged)
                else ""
            )
            + (
                " And the judge is UNCALIBRATED for this profile — no sealed holdout has "
                "passed — so read the ordering, never the count."
                if uncal
                else ""
            )
            + "</p>"
            + _STALE_JUDGE
            + "</div>"
        )
    srcs = ", ".join(f"<code>{_e(s)}</code>" for s in r.get("sources", []))
    # The re-angle gloss only beside a re-angle verdict (PS20 P1.7 Rule B). It also said the
    # account's follow-up had happened — routed to the generic lane — which nothing records.
    reangle = (
        " — <code>re-angle</code> there means the evidence was too thin for a hand-written 1:1"
        if any((x.get("verdict") or "").strip().lower() == "re-angle" for x in rows)
        else ""
    )
    return f"""
      <div class="stats">
        {_stat(r["accounts"], f"accounts in {scope_label(m)}", "every one, not a sample", src="rows:all")}
        {_stat(r["contact_verified"], "have a verified address", src="rows:co_has_email", sub_html=sub_counts(r["rows"], [("co_no_email", " do not")]))}
        {_stat(r["named_seat"], "resolve to a named seat", src="rows:co_named", sub_html=sub_counts(r["rows"], [("co_role_inbox", " are role inboxes")]))}
        {_stat(r["signal"], "carry a dated why-now", src="rows:co_signal", sub_html=sub_counts(r["rows"], [("co_signal_sourced", " cite a source")]))}
      </div>

      {segment_mix(m)}

      <div class="card">
        <h2>Every account in {_e(scope_label(m))}</h2>
        <p class="note">Tiers: {tiers or "—"}. Research verdicts: {verdicts or "—"}.</p>
        <p class="note"><strong>Two different verdicts sit in this table and they answer
        different questions.</strong> <em>Research verdict</em> is the researcher's call on the
        ACCOUNT, made before any copy existed{reangle}. <em>What happens next</em> is the email
        judge's read of the account's drafted email, and it is a RANKING, not a gate — no
        verdict here stops a send, and the deterministic <code>account_integrity</code> check
        is what does.{judge_src}</p>
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


def _markets_note(sup: dict) -> str:
    """The markets the pool spans and the largest one's share, from the market data — it said
    "Three markets … overwhelmingly a US motion", true of one tenant's pool (PS20 P1.7)."""
    countries = sup.get("countries") or []
    if not countries:
        return "No market is recorded for anyone in the pool."
    top = max(countries, key=lambda c: c["n"])
    return (
        f"{len(countries)} market{'' if len(countries) == 1 else 's'}. The largest, "
        f"{_e(top['name'])}, holds {_pct(top['n'], sup['total'])} of the pool."
    )


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
    # The pool figures below are described, never tied to goals: nothing here compares a
    # manifest's targets to them (PS20 P1.7 Rule B).
    top = (sup["unplaced_titles"] or [None])[0]
    largest = f" The largest group below is {_e(top['name'])} ({top['n']:,})." if top else ""

    return f"""
      {scope_note}
      <div class="stats">
        {
        _stat(
            sup["qualified_sendable"],
            "people we will actually email",
            f"of {sup['total']:,} loaded" + (" — pool-wide" if m.get("campaign_scope") else ""),
        )
    }
        {_stat(f["ready"], "more we could email", "verified address, not yet loaded")}
        {_stat(f["needs_verification"], "address not confirmed", "person is right, inbox unproven")}
        {_stat(f["excluded_dnc_or_sent"], "off limits", "already contacted or opted out")}
      </div>

      <div class="card">
        <h2>How many actually count</h2>
        <p class="note">Between "loaded" and "will receive an email" sit the opening-line check
        and the job-title check.
        <strong>{sup["suppressed"]:,} of {sup["total"]:,} are held
        back</strong> because the one researched sentence their email opens on does not survive
        inspection — {supp_top}. Separately, <strong>{sup["unclear"]:,} rows are
        "unread"</strong>: nobody has checked whether that job title could own this, and the
        qualification gate treats unread as not-proven rather than a soft pass.
        {sup["not_qualified"]:,} are ruled out outright. What is left is
        <strong>{sup["qualified_sendable"]:,} people</strong>.</p>
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
        <p class="note">These are the {len(FUNNEL_GLOSS)} states a person can be in. The only one
        we can email today is the first.</p>
        <table><tbody>{gloss_rows}</tbody></table>
      </div>

      <div class="card" data-no-filter data-stale-when-filtered>
        <h2>Where they are</h2>
        {_barlist([(c["name"], c["n"]) for c in sup["countries"]], sup["total"])}
        <p class="why" hidden title="Not filtered — this counts the shared prospect pool, a different and much larger set than the campaign roster the filter selects from. Filtering the roster cannot move it, and rescaling it to the selection would answer a question nobody asked.">not filtered</p>
        <p class="note">{_markets_note(sup)}</p>
      </div>

      <div class="card">
        <h2>What job they do</h2>
        {_barlist([(_seat_label(s["name"]), s["n"]) for s in sup["seats"]], sup["total"], tone="b")}
        <h3>Why {unplaced:,} say "other"</h3>
        <p class="note"><strong>This is a gap in our own classifier, not missing data.</strong>
        Every one of these people has a job title on file. The automated resolver recognises
        <strong>{figure_span("seats-recognised", len(SEAT_COVERAGE))}</strong> buyer seats —
        {covered} — so any title outside them is grouped as "other".{largest}</p>
        <p class="note">This matters beyond tidiness: the check that stops us leading on the
        wrong seat's problem stays <em>silent</em> on an unrecognised title, by design.</p>
        <table><thead><tr><th>Title we could not place</th><th>People</th></tr></thead>
        <tbody>{unplaced_rows}</tbody></table>
        <p class="note">{sup["unplaced_distinct"]} distinct titles in total.</p>
      </div>

      {_intent_block(m)}
      {roster}"""
