from __future__ import annotations

import html

from ..prospect_status import LABELS
from . import filters

# --- render helpers -------------------------------------------------------------


def _e(x) -> str:
    return html.escape(str(x))


def _row_status(m: dict, email: str) -> str:
    """The Status column cell for one roster row, joined to the router's last route
    (``model.prospect_status_model``, via ``m["prospect_status"]["by_email"]``) by address.

    Shared by the worklist and the who-tab table so the two cannot derive this word two
    different ways. A roster row and a router row are different populations — one a CSV
    export, the other ``lanes route``'s own output — so a miss here is ordinary, not a
    defect: the address may not have reached the router yet, or the router has never run
    on this profile at all. Both read as a plain sentence rather than a blank cell.
    """
    ps = m.get("prospect_status") or {}
    if not ps.get("available"):
        return '<span class="muted">—</span>'
    status = (ps.get("by_email") or {}).get((email or "").strip().lower())
    if not status:
        return '<span class="muted">not yet routed</span>'
    if status == "unmapped":
        return '<span class="pill warn" data-warn="unmapped">status unmapped</span>'
    return _e(LABELS[status])


def _rate_of(targets: dict) -> float:
    """The campaign's reply-rate goal. Prefers the explicitly declared ``reply_rate`` and
    falls back to replies/prospects — deriving it from two rounded integers reports 3.1%
    for a goal set at 3.0%, which is the kind of small lie a reader has no way to catch."""
    try:
        rate = float(targets.get("reply_rate") or 0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate:
        return rate
    try:
        return float(targets["replies"]) / float(targets["prospects"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _i(v) -> int:
    try:
        return int(float(str(v).strip() or 0))
    except (TypeError, ValueError):
        return 0


def _seat_label(seat: str) -> str:
    """Display name for a seat. ``unknown`` stays the internal key — it is written into
    the cell id and onto outcome tags, so renaming the value would orphan any row already
    tagged with it. Only the label a reader sees changes."""
    return "other" if seat == "unknown" else seat


def _pct(n: float, d: float) -> str:
    return f"{round(100 * n / d)}%" if d else "—"


#: Every tile the last ``render_html`` emitted, as ``{label, raw, src}``. Read by the
#: model->HTML contract test; carries no rendering behaviour.
#:
#: Recording the VALUE alone would be theatre — it proves ``_stat`` renders what it was
#: handed, which was never in doubt. What goes wrong is PROVENANCE: a tile labelled as a
#: total over the scope whose implementation is ``window = window or c.get("window")``,
#: i.e. one campaign's figure wearing the set's label. So each tile also declares HOW its
#: value is derived (``src``), and the test re-executes that claim against the model.
_TILES: list[dict] = []


def _tiles_reset() -> None:
    _TILES.clear()


def _tiles_recorded() -> list[dict]:
    return list(_TILES)


def figure_span(name: str, value) -> str:
    """One headline figure, marked (PS20). ``data-figure`` names WHICH figure an element
    shows, so a test reads that element rather than a page-wide substring — benchmark prose
    and tag-split phrases make a page-wide check pass or fail by accident. ``None`` is a
    refusal and renders as an em dash, never as a zero."""
    shown = "—" if value is None else f"{value:,}" if isinstance(value, int) else _e(value)
    return f'<span data-figure="{_e(name)}">{shown}</span>'


def _stat(
    value,
    label: str,
    sub: str = "",
    *,
    raw: dict | None = None,
    src=None,
    sub_html: str = "",
    figure: str = "",
) -> str:
    """One headline tile. ``raw``/``src`` declare its components and their provenance.

    ``value`` is often pre-formatted ("0 of 998", "90/day"), so the components are passed
    separately in ``raw`` rather than parsed back out of the string. ``src`` is a token
    per component — ``sum:``, ``count:``, ``agree:``, ``pooled:``, ``rows:`` — resolved
    independently by the contract test.

    ``src`` also decides whether the client-side filter may touch this tile
    (:func:`gtm_core.email_campaign_dashboard.filters.reactive`), which is why there is no
    ``filterable=`` argument: a tile cannot opt into being recomputed without declaring a
    derivation something re-executes. A tile that does not react carries the reason, server-
    rendered and ``hidden`` — the filter's only job is to unhide it, so no sentence on this
    page lives in JavaScript where §R14's prose lint cannot see it. What is unhidden is a
    two-word mark; the exact reason is its tooltip. The sentence itself used to be the
    visible text, so selecting one facet printed ~30 words under every frozen tile — the
    explanation now appears once, beside the filter (``filters.bar_html``).

    ``sub_html`` is pre-escaped markup and the ONLY way to get a live count into a sub-line;
    build it with ``filters.sub_counts`` (a live count) or :func:`figure_span` (a marked
    figure) and nothing else. ``sub`` stays escaped.

    ``figure`` marks the value with ``data-figure`` (see :func:`figure_span`). A ``None``
    value is a refusal and renders as an em dash, never as the word "None".
    """
    _TILES.append({"label": label, "value": value, "raw": raw or {"value": value}, "src": src})
    idx = len(_TILES) - 1
    pred = filters.predicate_of(src)
    v = "—" if value is None else f"{value:,}" if isinstance(value, int) else _e(value)
    if pred:
        v = f'<span data-count-pred="{_e(pred)}">{v}</span>'
    if figure:
        v = f'<span data-figure="{_e(figure)}">{v}</span>'
    body = sub_html or (_e(sub) if sub else "")
    sub_block = f'<div class="stat-sub">{body}</div>' if body else ""
    why = (
        ""
        if pred
        else f'<div class="stat-why" hidden title="Not filtered — this figure '
        f'{_e(filters.grey_reason(src))}.">not filtered</div>'
    )
    return (
        f'<div class="stat" data-tile="t{idx}" data-filter="{"on" if pred else "off"}">'
        f'<div class="stat-value">{v}</div>'
        f'<div class="stat-label">{_e(label)}</div>{sub_block}{why}</div>'
    )


def _agree(pairs: list[tuple[str, object]], what: str) -> tuple[object | None, str | None]:
    """One value if every campaign that declares it agrees; otherwise a refusal reason.

    ``pairs`` is ``[(slug, value)]`` over the in-scope campaigns **that declare the
    field** — a campaign that declares nothing cannot disagree, and must not be read as
    a zero. Returns ``(value, None)`` on agreement, ``(None, reason)`` on disagreement,
    ``(None, None)`` when nobody declares it.

    This is the honest-or-absent rule in one place. Summing a rate, or silently taking
    the first campaign's value, is how "0 of 990" happened one level up: a real number,
    correctly computed, belonging to a different question than the one the label asks.
    """
    if not pairs:
        return None, None
    values = {v for _, v in pairs}
    if len(values) == 1:
        return next(iter(values)), None
    shown = ", ".join(f"{s}: {v}" for s, v in pairs)
    return None, f"the {len(pairs)} campaigns in scope declare different {what} ({shown})"


def _barlist(items: list[tuple[str, int]], total: int, *, tone: str = "a") -> str:
    """A labelled proportional bar per row — the cheapest honest chart."""
    if not total:
        return "<p class='note'>Nothing to show yet.</p>"
    out = []
    top = max((n for _, n in items), default=1) or 1
    for label, n in items:
        out.append(
            f'<div class="brow"><div class="blabel">{_e(label)}</div>'
            f'<div class="btrack"><div class="bfill t{tone}" style="width:{100 * n / top:.1f}%">'
            f"</div></div>"
            f'<div class="bval">{n:,}<span class="muted"> · {_pct(n, total)}</span></div></div>'
        )
    return f'<div class="bars">{"".join(out)}</div>'


def _scoped_out(m: dict, what: str, why: str) -> str:
    """Replace a pool-wide block with one line saying it was removed, and why.

    A campaign page must answer only about that campaign. Where a block's denominator is the
    shared pool there is no scoped version to render, so it is dropped rather than labelled:
    "0 different subject lines across 859 people" is two wrong numbers wearing a caveat.
    Returns "" on the profile-wide page, where the block is correct and stays.
    """
    if not m.get("campaign_scope"):
        return ""
    return (
        f'<div class="card"><h2>{_e(what)}</h2><p class="note">Not shown on a '
        f"scoped page. {_e(why)}</p></div>"
    )


def _pool_scope_note(m: dict, what: str) -> str:
    """On a campaign-scoped page, say plainly that this block is NOT scoped.

    A prospect pool is shared across campaigns, so scoping it would trade one wrong number
    for another — but rendering it unlabelled under a campaign's title is how "731 people we
    will actually email, goals are set on this number" appeared on a page whose goals are 4
    people and 8 emails. The docstring of ``scope_to_campaign`` promised this note from the
    start and nothing rendered it: a declared contract nobody runs.
    """
    if not m.get("campaign_scope"):
        return ""
    label = scope_label(m)
    return (
        f'<p class="note"><strong>Profile-wide, not {_e(label)}.</strong> '
        f"{what} is shared across every campaign on this profile, so the figures below are "
        f"not scoped to <code>{_e(str(m['campaign_scope']))}</code>. {_e(label.capitalize())}'s "
        "own numbers are on <em>Where things stand</em>.</p>"
    )


def roster_gap(m: dict) -> str | None:
    """Why this scope's roster must not be shown, or None if it is complete.

    ``roster_model`` unions the ``roster_globs`` of every in-scope campaign, so a scope
    where only SOME campaigns declare a roster renders those accounts under labels that
    assert completeness — "the whole segment, not a slice", "every one, not a sample".
    Silent partial coverage reads worse than a missing block, because the sub-line is a
    claim about a set the figure does not cover.

    Both the status tiles and the who-tab table go through here: two copies of this guard
    would be two chances to fix one and forget the other, which is the shape of every bug
    this page has had. (The worklist deliberately does NOT — see :func:`roster_partial`.)

    **The profile rollup is exempt, and the distinction is principled rather than
    convenient.** A scoped page's whole promise is "this page is about exactly these
    campaigns", so a roster covering N-1 of them is a set the page claims and is not — and
    there is a right answer to point at, which the message below gives. The rollup makes no
    such promise: its label is "the whole profile", it already renders partial-coverage
    blocks that say so (``_pool_scope_note``, ``Scope.excluded``), and the advice "render a
    campaign on its own" is meaningless there. So the rollup renders what exists and NAMES
    what is missing, via :func:`roster_partial`.
    """
    if not m.get("campaign_scope"):
        return None
    camps = m["campaigns"]["campaigns"]
    silent = [c.get("slug", "?") for c in camps if not c.get("roster_globs")]
    if not silent or len(camps) < 2:
        return None
    return (
        f"{len(camps) - len(silent)} of {len(camps)} campaigns declare where their accounts "
        f"live ({', '.join(silent)} {'does' if len(silent) == 1 else 'do'} not). A roster "
        "covering some of them is not this scope's roster. Render a campaign on its own "
        "(--scope campaign) for its accounts."
    )


def roster_coverage(m: dict) -> dict:
    """Which in-scope campaigns the roster covers, and which are silent. One derivation.

    ``roster_gap`` and every partial-coverage sentence read this, so "does this campaign
    declare a roster" is answered in one place rather than re-derived per caller.
    """
    camps = m["campaigns"]["campaigns"]
    covered = [c.get("slug", "?") for c in camps if c.get("roster_globs")]
    silent = [c.get("slug", "?") for c in camps if not c.get("roster_globs")]
    return {"covered": covered, "silent": silent, "total": len(camps)}


def roster_partial(m: dict) -> str:
    """The sentence naming the campaigns this roster does not cover, or "" when it covers all.

    The rollup's honest alternative to :func:`roster_gap`'s refusal: show the accounts that
    exist and say whose are missing, rather than showing nothing or — worse — showing them
    under a label that claims completeness.
    """
    cov = roster_coverage(m)
    if not cov["silent"] or not cov["covered"]:
        return ""
    silent = ", ".join(cov["silent"])
    return (
        f"Covers {len(cov['covered'])} of {cov['total']} campaigns. "
        f"{silent} {'declares' if len(cov['silent']) == 1 else 'declare'} no roster, so "
        f"{'its' if len(cov['silent']) == 1 else 'their'} accounts are not counted here."
    )


def scope_label(m: dict) -> str:
    """How the page refers to its own scope: "this campaign" / "the 2 open campaigns".

    Written by ``scope_to_campaign`` from ``scope.Scope.label``. Both fallbacks matter,
    and they are different: ``scope_to_campaign`` is public and called directly by tests
    and by callers that never went through the CLI, so a scoped model with no label must
    degrade to the old singular wording — but an UNSCOPED model is the profile rollup, and
    calling that "this campaign" is the mislabelling this whole module exists to stop.
    """
    if m.get("scope_label"):
        return str(m["scope_label"])
    return "this campaign" if m.get("campaign_scope") else "the whole profile"
