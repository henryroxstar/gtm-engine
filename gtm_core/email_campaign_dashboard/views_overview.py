"""The Overview tab: where do we stand, and what needs me? (PS20 PRD Phase 2.)

The terminal's own lede (PS15, verbatim), one line per campaign, and the two blocks the
terminal also prints (accounts by where each stands, contacts by status). Nothing else.
"""

from __future__ import annotations

from ..prospect_status import (
    CHECKED_LABEL,
    CHECKED_NEXT_STEP,
    CHECKED_NOTES,
    LABELS,
    NEXT_STEP,
    STATUSES,
    UNRECOGNISED_LABEL,
    UNRECOGNISED_NEXT_STEP,
)
from .aggregate import _scope_figures, campaign_contacted
from .format import _e, _i, _pool_scope_note, _rate_of, _stat, figure_span, scope_label, section
from .frontier import render_ready_to_send_section
from .health import figures_date
from .views_funnel import _attrition_funnel_block
from .views_lede import _actions_required_card, _lede_block

#: The five statuses rendered as tiles here. `needs_address` is the sixth `STATUSES` id but
#: gets its own card below, textually marked as a different population — see
#: `_needs_address_block`.
_LANE_STATUSES: tuple[str, ...] = tuple(s for s in STATUSES if s != "needs_address")


def _contacts_block(m: dict) -> str:
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
    scope_note = _pool_scope_note(m, "The prospect pool")

    if available:
        # The terminal prints a record it cannot map as its own "Unrecognised" row, counted in
        # its total; the same label and count get a tile here, outside the five that sum.
        shown = [
            (
                s,
                LABELS[s],
                "Review drafted email and approve or skip"
                if s == "waiting_on_you"
                else NEXT_STEP[s],
                counts.get(s, 0),
            )
            for s in _LANE_STATUSES
        ]
        if unmapped:
            shown.append(("unrecognised", UNRECOGNISED_LABEL, UNRECOGNISED_NEXT_STEP, unmapped))
        tiles = "".join(
            _stat(n, label, step, raw={"value": n}, src=f"status:{s}")
            for s, label, step, n in shown
        )
        everyone = (
            "every person in the active pool"
            if not unmapped
            else f"with {unmapped:,} unmapped, <strong>{total + unmapped:,}</strong> "
            "people in the active pool"
        )
        total_line = f'<p class="note">Total active pool: <strong>{total:,}</strong> prospects across these {len(_LANE_STATUSES)} workflow stages ({everyone}).</p>'
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
    # PS15: beside the five, never inside their total — the same people, measured by a later
    # step. The number is the check report's; "—" plus the reason when it cannot be trusted.
    readiness = m.get("readiness")
    admitted = readiness.admitted if readiness is not None else None
    checked_tile = _stat(
        admitted if admitted is not None else "—",
        CHECKED_LABEL,
        CHECKED_NEXT_STEP
        if admitted is not None
        else CHECKED_NOTES.get(getattr(readiness, "state", "none"), CHECKED_NOTES["none"]),
        raw={"value": admitted if admitted is not None else "—"},
        src="readiness:admitted",
    )
    return f"""
      <div class="card glass-panel animate-on-load anim-up-lg" style="--anim-delay: 50ms;">
        <h2>Contacts by Status</h2>
        {scope_note}
        <div class="stats">{tiles}</div>
        {total_line}
        <div class="stats">{checked_tile}</div>
        <p class="note"><strong>{_e(CHECKED_LABEL)}</strong> reflects prospects approved after passing deliverability, compliance, and domain verification checks.</p>
      </div>"""


def _needs_address_block(m: dict) -> str:
    """The second card `_prospect_status_block` drew: named contacts in the account LEDGER with
    no reachable address. A different population from the routed list, so it never sits in
    the contacts card's total; PS20 Phase 2 places it under Operator notes → List quality."""
    ps = m.get("prospect_status") or {}
    available = bool(ps.get("available"))
    total = ps.get("total") or 0
    needs = ps.get("needs_address") or 0
    needs_tile = _stat(
        needs,
        LABELS["needs_address"],
        f"{NEXT_STEP['needs_address']} — not counted above",
        raw={"value": needs},
        src="status:needs_address",
    )
    diff_note = f"not part of the {total:,} above" if available else "not part of the routed list"
    return f"""
      <div class="card glass-panel animate-on-load anim-up-lg" style="--anim-delay: 50ms;">
        <h2>Needs an address</h2>
        <div class="stats">{needs_tile}</div>
        <p class="note">Identified contacts at target accounts where direct email addresses are currently being researched and verified before enrollment ({diff_note}).</p>
      </div>"""


def _plural(n, one: str, many: str) -> str:
    return one if n == 1 else many


def _campaign_snapshot(c: dict) -> str:
    """Scope/audience/hypothesis for one campaign, entirely from ITS OWN manifest —
    never a slug-keyed branch. Every campaign used to route through one of two
    hardcoded blocks of tenant prose (a third, generic branch existed for anything
    else), which put this tenant's account descriptions and hypotheses inside the
    de-branded engine and meant a new campaign's manifest fields were never read at
    all. A field the manifest does not declare says so, rather than guessing."""
    targets = c.get("targets") or {}
    num_seqs = len(c.get("sequences") or [])
    prospects = targets.get("prospects") or targets.get("accounts")
    scope = (
        f"{prospects:,} targets across {num_seqs} sequence{'s' if num_seqs != 1 else ''}"
        if prospects
        else f"{num_seqs} sequence{'s' if num_seqs != 1 else ''}"
    )
    # `audience` is an optional manifest field (`[targets] audience = "..."`) the operator
    # writes for the humans reading this card — this engine does not infer a target
    # audience description from targeting data.
    accounts = targets.get("audience") or c.get("product") or "not declared in the campaign file"
    exp = c.get("experiment") or {}
    tests = (
        exp.get("what_it_tells_us")
        or exp.get("why_we_run_it")
        or "not declared in the campaign file"
    )
    if len(tests) > 160:
        tests = tests[:157] + "..."

    return (
        '<div class="campaign-snapshot">'
        f'<div class="campaign-snapshot-line"><strong>Scope:</strong> <span>{_e(scope)}</span></div>'
        f'<div class="campaign-snapshot-line"><strong>Target Accounts:</strong> <span>{_e(accounts)}</span></div>'
        f'<div class="campaign-snapshot-line"><strong>What It Tests:</strong> <span>{_e(tests)}</span></div>'
        "</div>"
    )


def _campaign_outcome_tiles(fig: dict, c: dict) -> str:
    """This campaign's own three headline numbers — contacted, reply rate, replies — read
    off ITS OWN ``actuals``/``targets`` via ``campaign_contacted``, never pooled across the
    scope's other campaigns. Mirrors ``views_results._outcome_tiles``, one campaign at a
    time; a refused scope figure (an unreadable snapshot) renders every value as an em
    dash, never a zero. Labelled distinctly from that scope-wide block (``_tile_labelled``
    in the test suite looks up "people contacted"/"reply rate"/"replies so far" by exact
    string and expects exactly one match, on the Results tab)."""
    slug = str(c.get("slug", "?"))
    own = campaign_contacted(fig, c)
    sent = None if own is None else own["current"]
    replied = None if own is None else own["replied"]
    targets = c.get("targets") or {}
    planned = _i(targets["emails"]) if targets.get("emails") else None
    target_rate = _rate_of(targets) or None
    rate_txt = f"{replied / sent:.1%}" if sent else "—"
    target_txt = "—" if target_rate is None else f"{target_rate:.1%}"
    idle = "sending figures unavailable" if sent is None else "" if sent else "nothing sent yet"
    rate_sub = (
        (idle or "no target declared")
        if target_rate is None
        else f"target {target_txt}" + (f" · {idle}" if idle else "")
    )
    goal_sub = (
        "no email goal declared"
        if planned is None
        else f"goal: {figure_span(f'campaign-goal-{slug}', planned)} emails"
    )
    return (
        '<div class="stats">'
        + _stat(
            sent,
            "contacted",
            sub_html=goal_sub,
            raw={"contacted": sent, "planned": planned},
            src={
                "contacted": f"campaign:{slug}.actuals.sent",
                "planned": f"campaign:{slug}.targets.emails",
            },
            figure=f"campaign-tile-contacted-{slug}",
        )
        + _stat(
            rate_txt,
            "reply rate so far",
            rate_sub,
            raw={"replied": replied, "sent": sent},
            src=f"campaign-pooled:{slug}|actuals.replied/actuals.sent",
        )
        + _stat(
            replied,
            "replies",
            raw={"value": replied},
            src=f"campaign:{slug}.actuals.replied",
        )
        + "</div>"
    )


def _campaign_lines(m: dict) -> str:
    """One line per campaign in scope: its go-live word, its current people contacted and
    their replies, read off ``_scope_figures`` via ``campaign_contacted`` — never re-summed
    from snapshot rows. A refused figure (unreadable snapshot) is an em dash, never a zero.
    The go-live pill is neutral: a state, not a warning (PRD P1.5)."""
    fig = _scope_figures(m)
    snap = (m.get("status") or {}).get("snapshot") or {}
    day = figures_date(snap.get("fetched"))
    if snap.get("unreadable"):
        as_of = "the sending tool's figures couldn't be read"
    elif day:
        as_of = f"figures from {day}"
    else:
        as_of = "figures carry no date"
    items = []
    for c in m["campaigns"]["campaigns"]:
        slug = str(c.get("slug", "?"))
        own = campaign_contacted(fig, c)
        contacted = None if own is None else own["current"]
        replied = None if own is None else own["replied"]
        snapshot_html = _campaign_snapshot(c)
        tiles_html = _campaign_outcome_tiles(fig, c)
        items.append(
            '<li class="campaign-card">'
            '<div class="campaign-card-header">'
            '<div class="campaign-card-title">'
            f"<strong>{_e(c.get('title') or slug)}</strong> "
            f'<span class="pill" data-figure="{_e(f"campaign-word-{slug}")}">'
            f"{_e(c.get('state', ''))}</span>"
            "</div>"
            '<div class="campaign-card-metrics">'
            f" — {figure_span(f'campaign-contacted-{slug}', contacted)} "
            f"{_plural(contacted, 'person', 'people')} contacted"
            f" · {figure_span(f'campaign-replied-{slug}', replied)} "
            f"{_plural(replied, 'reply', 'replies')}"
            f' · <span data-figure="{_e(f"campaign-date-{slug}")}">{_e(as_of)}</span>'
            "</div>"
            "</div>"
            f"{snapshot_html}{tiles_html}</li>"
        )
    split = fig["contacted"][0]
    if split is not None and split["not_linked"] > 0:
        n = split["not_linked"]
        items.append(
            '<li class="campaign-card"><div class="campaign-card-header"><div class="campaign-card-title">'
            "<strong>Not in any campaign</strong> — "
            f"{figure_span('campaign-contacted-unlinked', n)} {_plural(n, 'person', 'people')} "
            "contacted on sequences no campaign lists</div></div></li>"
        )
    if not items:
        return ""
    return (
        '<div class="card glass-panel animate-on-load anim-up-lg" style="--anim-delay: 50ms;"><h2>Campaigns</h2><p class="note">Targeted outbound initiatives testing specific value propositions across '
        f"{_e(scope_label(m))} — tracking launch status, prospects contacted, and replies.</p>"
        f'<ul class="steps">{"".join(items)}</ul></div>'
    )


def _overview_view(m: dict) -> str:
    return "".join(
        (
            section("lede", _lede_block(m)),
            section("campaign-lines", _campaign_lines(m)),
            render_ready_to_send_section(m),
            section(
                "accounts-funnel",
                _pool_scope_note(m, "The account ledger") + _attrition_funnel_block(m),
            ),
            section("contacts-by-status", _contacts_block(m)),
            section("actions-required", _actions_required_card(m)),
        )
    )
