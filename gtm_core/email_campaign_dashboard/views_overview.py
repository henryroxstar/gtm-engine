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
from .format import _e, _pool_scope_note, _stat, figure_span, scope_label, section
from .health import figures_date
from .views_funnel import _attrition_funnel_block
from .views_lede import _lede_block

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
        shown = [(s, LABELS[s], NEXT_STEP[s], counts.get(s, 0)) for s in _LANE_STATUSES]
        if unmapped:
            shown.append(("unrecognised", UNRECOGNISED_LABEL, UNRECOGNISED_NEXT_STEP, unmapped))
        tiles = "".join(
            _stat(n, label, step, raw={"value": n}, src=f"status:{s}")
            for s, label, step, n in shown
        )
        everyone = (
            "every person in the current list"
            if not unmapped
            else f"with the {unmapped:,} unrecognised, <strong>{total + unmapped:,}</strong> "
            "people in the current list"
        )
        total_line = (
            f'<p class="note">These {len(_LANE_STATUSES)} sum to <strong>{total:,}</strong> — '
            f"{everyone}.</p>"
        )
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
      <div class="card">
        <h2>Contacts — by status (people, not companies)</h2>
        {scope_note}
        <div class="stats">{tiles}</div>
        {total_line}
        <div class="stats">{checked_tile}</div>
        <p class="note"><strong>{_e(CHECKED_LABEL)}</strong> counts the same people after the
        checks, so it is never added into the {len(_LANE_STATUSES)} above.</p>
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
      <div class="card">
        <h2>Needs an address</h2>
        <div class="stats">{needs_tile}</div>
        <p class="note"><strong>A different population — {diff_note}.</strong>
        This counts named contacts in the account ledger with no reachable address at all,
        which includes accounts that have never reached the current routed list.</p>
      </div>"""


def _plural(n, one: str, many: str) -> str:
    return one if n == 1 else many


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
        items.append(
            f"<li><strong>{_e(c.get('title') or slug)}</strong> "
            f'<span class="pill" data-figure="{_e(f"campaign-word-{slug}")}">'
            f"{_e(c.get('state', ''))}</span>"
            f" — {figure_span(f'campaign-contacted-{slug}', contacted)} "
            f"{_plural(contacted, 'person', 'people')} contacted"
            f" · {figure_span(f'campaign-replied-{slug}', replied)} "
            f"{_plural(replied, 'reply', 'replies')}"
            f' · <span data-figure="{_e(f"campaign-date-{slug}")}">{_e(as_of)}</span></li>'
        )
    split = fig["contacted"][0]
    if split is not None and split["not_linked"] > 0:
        n = split["not_linked"]
        items.append(
            "<li><strong>Not in any campaign</strong> — "
            f"{figure_span('campaign-contacted-unlinked', n)} {_plural(n, 'person', 'people')} "
            "contacted on sequences no campaign lists</li>"
        )
    if not items:
        return ""
    return (
        '<div class="card"><h2>Campaigns</h2><p class="note">One line per campaign in '
        f"{_e(scope_label(m))}: its go-live word, the people it has contacted and their "
        f'replies.</p><ul class="steps">{"".join(items)}</ul></div>'
    )


def _overview_view(m: dict) -> str:
    return "".join(
        (
            section("lede", _lede_block(m)),
            section("campaign-lines", _campaign_lines(m)),
            section(
                "accounts-funnel",
                _pool_scope_note(m, "The account ledger") + _attrition_funnel_block(m),
            ),
            section("contacts-by-status", _contacts_block(m)),
        )
    )
