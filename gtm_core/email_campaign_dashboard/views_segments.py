"""The roster's CUSTOMER-TYPE split — how big each segment is, and how ready each one is.

Split out of :mod:`~gtm_core.email_campaign_dashboard.views_who` rather than added to it:
that module is already two blocks with two different denominators (the campaign roster and
the shared prospect pool) and the §R10 ratchet caps it at 500 lines. This is one coherent
question with one denominator — distinct accounts on the roster — so it gets its own file
instead of a dated ceiling raise.

The page could already SELECT one customer type; ``filters.FACETS`` has offered the dropdown
since 2026-09-10. Selecting is not comparing. An operator asking "what are the enterprise
accounts" wants the figure *beside the others*, and getting it meant choosing each type in
turn and holding three numbers in your head.
"""

from __future__ import annotations

from .filters import count
from .format import _barlist, _e, scope_label

#: Per-segment readiness columns, as ``(predicate, heading)``. Every one is a
#: ``roster_model`` company-level predicate, counted by :func:`filters.count` — the same
#: implementation the reactive tiles use, so a row here cannot drift from the tile above it.
MIX_COLUMNS: tuple[tuple[str, str], ...] = (
    ("all", "Accounts"),
    ("co_has_email", "Verified address"),
    ("co_named", "Named seat"),
    ("co_signal", "Dated why-now"),
)


def segment_mix(m: dict) -> str:
    """How the roster splits by customer type, and how ready each type is.

    Readiness is reported PER SEGMENT rather than page-wide because that is where the types
    differ and the page-wide tile hides it: on a live tenant rollup every enterprise account
    carries an address, a name and a dated signal, while the builder rows are short of all
    three. One "carry a dated why-now" tile averages those into a number true of neither.

    Counted with :func:`filters.count`, so each cell is DISTINCT ACCOUNTS — the unit every
    other figure on this page is in — and an account two campaigns both work stays one
    account. The block does not follow the filter: it IS the whole roster's shape, which is
    the fact being asked for, and rescaling it to a selected customer type would leave a
    customer-type breakdown with one bar.
    """
    r = m.get("roster") or {}
    segments, rows = r.get("segments") or [], r.get("rows") or []
    # One customer type is not a mix. Same §R18 rule the facet dropdown follows: a breakdown
    # that cannot distinguish anything is a claim that a split exists.
    if len(segments) < 2 or not rows:
        return ""

    head = "".join(f"<th>{_e(h)}</th>" for _p, h in MIX_COLUMNS)
    body = []
    for name, _n in segments:
        sub = [x for x in rows if (x.get("segment_key") or "—") == name]
        cells = "".join(f"<td>{count(sub, pred):,}</td>" for pred, _h in MIX_COLUMNS)
        # Which campaigns contribute this customer type. On this profile that is the
        # cross-campaign finding — every enterprise account comes from one campaign — and it
        # is derived here rather than written, because it stops being true the day a second
        # campaign loads one.
        where = sorted({x.get("campaign") or "—" for x in sub})
        body.append(
            f"<tr><td><strong>{_e(name)}</strong>"
            f'<div class="note">{_e(", ".join(where))}</div></td>{cells}</tr>'
        )
    return f"""
      <div class="card">
        <h2>Customer type across {_e(scope_label(m))}</h2>
        {_barlist(segments, r.get("accounts") or 0)}
        <p class="note">Every figure is distinct accounts, so an account two campaigns both
        work counts once. The second line under each type names the campaigns it comes from
        &mdash; a customer type carried by a single campaign is a concentration, not a
        segment the programme has tested twice.</p>
        <table><thead><tr><th>Customer type</th>{head}</tr></thead>
        <tbody>{"".join(body)}</tbody></table>
      </div>

"""
