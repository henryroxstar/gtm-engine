"""Which rows the page can slice by, and which tiles are allowed to move when it does.

The operator asked to filter the rollup by **country, campaign, customer type and product**,
and chose the harder option for the headline figures: recompute them from the filtered rows
rather than leave them at their page-wide value.

The hard part is not the filtering. This page is built around a documented refusal to show a
correct number under a label that answers a different question — ``format._agree``,
``sum-complete:``, ``roster_gap`` and two contract-test files exist for that alone. A filter
is a machine for producing exactly that failure: "sequences set up" is a true number, and it
is not a fact about the 25 Singapore accounts the reader just selected. So **which tiles may
react is a derived property, not a judgement**:

    A tile is reactive if and only if every component of its ``src`` is a ``rows:`` token.

There is deliberately no registry of reactive tiles to keep in step with the views. You
cannot mark a tile reactive without declaring a provenance that
``tests/contracts/test_dashboard_tile_provenance.py`` re-executes against the model, and the
grey-out reason is derived from the tile's first non-``rows:`` op rather than typed per tile.
Adding a tile with no ``src`` gets the honest default: it does not move, and it says why.

**Two things this module deliberately does not ship.**

*Numbers.* The payload carries facet keys, booleans and an opaque company index — never a
tile's value. The page's JS reads the server-rendered figure out of the DOM to check itself
(the zero-filter tripwire), so shipping the same number a second time would only create a
second place for it to be wrong. It also keeps assertion 2 of the provenance contract able
to discriminate: that check asks whether a recorded number appears in the document, and a
payload full of numbers satisfies it no matter what the tiles rendered.

*Names.* No company, person, address or URL. Distinct-account counting needs identity, not
readability, so each row carries ``ci`` — an index into the sorted company list. The readable
row is already in the DOM, where §R9 allows it; the payload is a second copy that would not
be (and is the copy a "just paste the page somewhere" would carry).
"""

from __future__ import annotations

from html import escape as _e
from pathlib import Path

#: The page's filter template and interpreter, read ONCE, at import (PS20 P1.6):
#: ``render_html`` opens no file. Both are package data, versioned with this module.
_TEMPLATE = (Path(__file__).parent / "template_filter.html").read_text(encoding="utf-8")
_FILTER_JS = (Path(__file__).parent / "filter.js").read_text(encoding="utf-8")

#: ``(row key, row label field, heading)``. The key is what the filter matches on and the
#: label is what the dropdown shows — they differ because ``GTM_Segment`` is spelled
#: ``Builder`` in two of this campaign's exports and ``builder`` in the other two, so a facet
#: built on the raw value would offer one customer type twice.
FACETS: tuple[tuple[str, str, str], ...] = (
    ("campaign", "campaign", "Campaign"),
    ("product", "product", "Product"),
    ("country_key", "country", "Country"),
    ("segment_key", "segment", "Customer type"),
)

#: Every predicate a reactive tile may count, as ``roster_model`` computes it. All are
#: COMPANY-level and all count distinct accounts, which is the unit every tile on this page
#: is in. ``all`` is the population itself.
#:
#: They come in complementary pairs — ``co_has_email``/``co_no_email`` and
#: ``co_named``/``co_role_inbox`` — because two sub-lines are written as differences
#: ("3 do not", "6 are role inboxes"). A difference computed in the browser would be two
#: independently-derived numbers subtracted, which is how a page ends up rendering "-3".
#: A partition cannot go negative.
PREDICATES: tuple[str, ...] = (
    "all",
    "co_has_email",
    "co_no_email",
    "co_named",
    "co_role_inbox",
    "co_signal",
    "co_signal_sourced",
)

#: Why a tile does not move, keyed by the provenance op that makes it unfilterable. The empty
#: key is a tile that declares no provenance at all — on this page that is always a pool-wide
#: figure, and saying so is more useful than "unknown".
_GREY: dict[str, str] = {
    "sum": (
        "adds up whole sequences. A sequence has no per-account key, so there is no honest "
        "way to say which part of its total belongs to the accounts you selected"
    ),
    "sum-complete": (
        "adds up the campaign plans, and refuses unless every campaign in scope declares "
        "one. Neither half is per-account"
    ),
    "distinct": (
        "counts addresses across drafted packs and rendered merges — those are files on "
        "disk, not roster rows"
    ),
    "count": "counts sequences, not accounts",
    "agree": (
        "is one sending ceiling shared by the same mailboxes. Selecting fewer accounts does "
        "not create a second one"
    ),
    "pooled": "divides one sequence total by another; neither is per-account",
    "status": (
        "counts the shared prospect pool, which is a different set from this campaign "
        "roster — filtering the roster cannot move it"
    ),
    "": (
        "counts the shared prospect pool, which is a different set from this campaign "
        "roster — filtering the roster cannot move it"
    ),
}


def _tokens(src) -> list[str]:
    """Every provenance token on a tile, whether it declared one or several."""
    if not src:
        return []
    return list(src.values()) if isinstance(src, dict) else [str(src)]


def reactive(src) -> bool:
    """May this tile be recomputed from the filtered rows? See the module docstring.

    Exactly ONE ``rows:`` token, not merely all-of-them. A tile with two components renders
    a composed string ("0 of 998"), so there is no single number for the filter to write —
    calling it reactive would mark it filterable and then leave it at its page-wide value,
    which is the silent-wrong-number failure this whole module is arranged to prevent.
    """
    toks = _tokens(src)
    return len(toks) == 1 and toks[0].startswith("rows:")


def predicate_of(src) -> str:
    """The predicate a reactive tile counts, or ``""`` for one that is not reactive."""
    return _tokens(src)[0].partition(":")[2] if reactive(src) else ""


def grey_reason(src) -> str:
    """The sentence shown when a filter is on and this tile did not move.

    Derived from the first non-``rows:`` op rather than typed per tile, so a new tile gets a
    true reason without anyone remembering to write one.
    """
    for tok in _tokens(src):
        op = tok.partition(":")[0]
        if op != "rows":
            return _GREY.get(op, f"is derived by `{op}`, which has no per-account form")
    return _GREY[""]


def count(rows, pred: str) -> int:
    """Distinct ACCOUNTS among ``rows`` satisfying ``pred``.

    Distinct rather than a row count because the roster folds on ``(campaign, company)``: an
    account two campaigns are both working is two rows of work and still one account.
    """
    if pred not in PREDICATES:
        raise KeyError(
            f"unknown filter predicate {pred!r}. Add it to PREDICATES and to roster_model's "
            "per-row payload, or the tile declares a count nothing can compute."
        )
    return len({r["ci"] for r in rows if pred == "all" or r[pred]})


def sub_counts(rows, parts: list[tuple[str, str]]) -> str:
    """A tile's sub-line with each figure in a slot the filter can rewrite.

    The only place this page assembles markup around a count. ``parts`` is
    ``[(predicate, the words after the number)]``; the words are escaped, the number is
    derived. Returns ready-to-embed HTML, so ``_stat`` takes it as ``sub_html`` rather than
    through ``sub``, which escapes.
    """
    return " · ".join(
        f'<span data-count-pred="{_e(pred)}">{count(rows, pred):,}</span>{_e(text)}'
        for pred, text in parts
    )


def row_groups(m: dict) -> dict[int, str]:
    """``row index -> worklist group``, from the worklist's own rule. One implementation.

    Imported inside the function on purpose: ``views_worklist`` reads this module's public
    helpers, so a module-level import here would be a cycle. The alternative — a second copy
    of ``_group_of`` — is the shape that lets the filter count a row into one group while the
    table below prints it in another.

    Group membership depends on no facet, so filtering only changes the counts, never who is
    in which group.
    """
    from .views_accounts import _group_of, _staged_candidates

    rows = (m.get("roster") or {}).get("rows") or []
    if not rows:
        return {}
    packs = {r["account"] for r in (m.get("packs") or {}).get("packs") or []}
    candidates = _staged_candidates(m)
    return {r["i"]: _group_of(r, packs, candidates) for r in rows}


def facets(rows) -> list[dict]:
    """The dropdowns to offer, in :data:`FACETS` order.

    **A facet with one value is SHOWN, but not as a control** (operator decision,
    2026-09-10: "keep product filter on, but just show agent gateway if that is all that is
    there"). It renders disabled, with the single value displayed.

    That distinction is the §R18 one. A dropdown offering "All" and exactly one option cannot
    discriminate — every selection returns the same rows — so presenting it as a live control
    would be a claim that a choice exists. Disabled, it stops being a control and becomes what
    the operator actually wants from it: a statement of what this page covers — "Product: X"
    for whichever single product the campaign is scoped to, which is true and worth saying.

    A facet with NO values is omitted entirely; there is nothing to state.
    """
    out = []
    for key, label_field, heading in FACETS:
        vals: dict[str, str] = {}
        for r in rows:
            k = str(r.get(key) or "").strip()
            if k:
                vals.setdefault(k, str(r.get(label_field) or k).strip() or k)
        if not vals:
            continue
        out.append(
            {
                "key": key,
                "heading": heading,
                "values": [{"k": k, "label": vals[k]} for k in sorted(vals)],
                # False when there is nothing to choose between — see the docstring.
                "selectable": len(vals) > 1,
            }
        )
    return out


def payload_rows(m: dict) -> list[dict]:
    """The per-row payload the page's JS filters over. Facet keys, booleans, two integers."""
    rows = (m.get("roster") or {}).get("rows") or []
    groups = row_groups(m)
    return [
        {
            "i": r["i"],
            "ci": r["ci"],
            "group": groups.get(r["i"], ""),
            **{key: str(r.get(key) or "") for key, _label, _heading in FACETS},
            **{p: bool(r[p]) for p in PREDICATES if p != "all"},
        }
        for r in rows
    ]


def bar_html(m: dict) -> str:
    """The filter bar, plus the tripwire banner — both server-rendered, in Python.

    The dropdowns are markup, not data handed to a script: the page's JS reads
    ``select.value`` and never builds an ``<option>``. That is stricter than
    ``lanes/template_hold.html``'s ``el(tag, attrs, children)`` and for the same reason —
    nothing on this page assembles markup out of values that came from a CSV.

    Returns ``""`` only when the page has no roster at all. A facet carrying a single value
    still renders — disabled, stating that value rather than offering a choice it does not
    have (see :func:`facets`).
    """
    rows = (m.get("roster") or {}).get("rows") or []
    fs = facets(rows)
    if not fs or not any(f["selectable"] for f in fs):
        return ""

    def _one(f: dict) -> str:
        if not f["selectable"]:
            # A STATEMENT, and therefore not a <select> — not even a disabled one.
            #
            # It was a disabled select until 2026-09-10, on the reasoning that disabling it
            # stopped it acting as a control. That reasoning was wrong in a way no test
            # caught: `HTMLSelectElement.value` is populated whether or not the element is
            # disabled — `disabled` withholds an element from form SUBMISSION, nothing more.
            # So `chosen()` read the facet's one value off it on page load, decided a filter was
            # active before the reader had touched anything, and greyed out all thirteen
            # unfilterable tiles with their "this figure did not move" reasons showing. The
            # page opened looking like it was full of warnings.
            #
            # A <span> cannot have a value, so the bug is not available to be reintroduced.
            return (
                f'<span class="ffield fixed"><span>{_e(f["heading"])}</span>'
                f"<strong>{_e(f['values'][0]['label'])}</strong></span>"
            )
        opts = "".join(
            f'<option value="{_e(v["k"])}">{_e(v["label"])}</option>' for v in f["values"]
        )
        return (
            f'<label class="ffield"><span>{_e(f["heading"])}</span>'
            f'<select data-key="{_e(f["key"])}">'
            f'<option value="">All</option>{opts}</select></label>'
        )

    picks = "".join(_one(f) for f in fs)
    return (
        f'<div class="filterbar" id="filterbar"><span class="flabel">Filter</span>{picks}'
        '<button type="button" id="filter-clear" hidden>Clear</button></div>'
        # The ONE place the filter's limit is explained. Each figure that cannot follow a
        # selection carries a two-word mark (its exact reason is the mark's tooltip); the
        # sentence is here, once, instead of under every one of them.
        '<p class="why filter-note" id="filter-note" data-filter-note hidden>Some figures '
        "below are marked <strong>not filtered</strong>: they count something the filter does "
        "not select from, so they stay as they were. Hover a mark for the exact reason.</p>"
        '<div class="card warn" id="filter-tripwire" data-warn="tripwire" hidden>'
        "<h2>This page disagrees with its own filter data</h2>"
        "<p>A headline figure and the row data behind the filter do not match, so every "
        "number the filter writes is unreliable. Read the figures as first rendered and "
        "re-render the page before trusting anything you select. Disagreements: "
        "<code data-tripwire-detail></code></p></div>"
    )


def script_block(m: dict) -> str:
    """The page's filter payload and interpreter, from ``template_filter.html``.

    Returns ``""`` when :func:`bar_html` rendered nothing, so a page with no usable facet
    ships no payload at all rather than a script with nothing to drive.
    """
    from ..htmlpage import script_json

    rows = (m.get("roster") or {}).get("rows") or []
    fs = facets(rows)
    # Mirror bar_html exactly: no selectable facet means no bar, and a payload with no bar to
    # drive is dead weight in the page. Two conditions that must agree, written once each and
    # checked against each other by test_no_payload_ships_without_a_bar.
    if not fs or not any(f["selectable"] for f in fs):
        return ""
    # `__FILTER_JS__` last: the JS is a literal, so substituting it first would let a `$`
    # or a token-shaped comment inside it collide with the payload substitution.
    tpl = _TEMPLATE.replace("__ROWS_JSON__", script_json(payload_rows(m)))
    return tpl.replace("__FILTER_JS__", _FILTER_JS)
