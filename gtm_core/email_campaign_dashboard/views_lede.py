"""The status tab's first card: the lede ``prospects status`` opens with (PS15).

Rendered, never re-worded — the lines are ``prospect_lede.compose_lede``'s output, carried on
the model as ``m["lede"]``. Its own module so the view that renders the rest of the tab does
not also own the one card whose words must match the terminal byte for byte.
"""

from __future__ import annotations

from .format import _e, scope_label


def _lede_block(m: dict) -> str:
    """PS15: the lines `prospects status` opens with — `compose_lede`'s output, rendered, never
    re-worded. One element per line; an indented line is a detail of the one above it. The
    card takes the warning tone only when the answer cannot be trusted (stale, unreadable,
    or never checked): a decision waiting on the operator is theirs, not a warning.
    """
    lines = m.get("lede") or []
    if not lines:
        return ""
    readiness = m.get("readiness")
    # PS20 P1.5: warn is a number that cannot be trusted, and it says why.
    card = (
        'class="card lede"'
        if getattr(readiness, "state", "none") == "ok"
        else 'class="card lede warn" data-warn="checks-untrusted"'
    )
    # `.get`: a hand-built model (the lede's own tests) carries no `review_sheet`.
    sheet = m.get("review_sheet") or {}
    body = []
    for ln in lines:
        text = _e(ln.strip())
        cls = (
            "lede-reason"
            if ln.startswith("    ")
            else "lede-detail"
            if ln.startswith("  ")
            else "lede-line"
        )
        if ln.startswith("Yours ("):
            cls += " yours"  # the operator's move: the lede's one accent line
            if sheet.get("href"):
                text += f' <a href="{_e(sheet["href"])}" class="review-sheet-link">open it</a>'
        body.append(f'<p class="{cls}">{text}</p>')
    # Composed before `scope_to_campaign` narrows the model, so on a campaign page it is still
    # the whole profile's answer — its go-live word included — and says so (PS20 P1.10). Not
    # `_pool_scope_note`: that one points the reader at this very tab for the scoped figures.
    scope_note = (
        f'<p class="note"><strong>Profile-wide, not {_e(scope_label(m))}.</strong> Every line '
        "of this summary is about the whole profile.</p>"
        if m.get("campaign_scope")
        else ""
    )
    return f"""
      <div {card}>
        <h2>Where you stand</h2>
        {scope_note}{"".join(body)}
      </div>"""


def _maintainer_block(m: dict) -> str:
    """The counting discrepancies ``prospects status`` prints under "For the record" — the same
    ``cross_check`` lines, on the Operator notes tab rather than the status tab. They are for
    whoever maintains the setup; the status tab keeps to what a person decides or must know."""
    problems = m.get("cross_check") or []
    if not problems:
        return ""
    items = "".join(f"<li>{_e(p)}</li>" for p in problems)
    return (
        '<div class="card"><h2>Counts that do not add up</h2>'
        "<p class='note'>For whoever maintains your setup. None of these stops a send on its "
        f"own; each line says what it affects.</p><ul>{items}</ul></div>"
    )
