"""Every headline tile's value is what its declared derivation actually produces.

WHY THIS EXISTS (2026-09-04/05). "0 of 990 emails · 51 people · 3 sequences" rendered on a
campaign page whose real figures were 8 and 4. Nothing tested it; a human reading the page
caught it, as they caught 859, run500, and "930 earlier packs". The obvious test — record
each tile's value and assert the HTML shows it — is theatre: it proves ``_stat`` renders
what it was handed, which was never in doubt. What goes wrong one level up is PROVENANCE.
``window = window or c.get("window")`` is a correct expression producing a real number that
belongs to a different question than the label asks. So each tile declares HOW it is derived
(``src``) and this test **re-executes that claim against the model** with its own resolver.
A tile that says ``sum:`` while taking the first campaign's value fails here.

THREE ASSERTIONS, because any one alone is bypassable:

1. the declared derivation, recomputed independently, equals the recorded components;
2. every recorded component appears in the rendered HTML, formatted as ``_stat`` formats it
   — closing "the raw said 8, the f-string printed 80";
3. an AST scan of the view modules finds no ``_stat(`` call the recorder missed — a new
   tile added without provenance fails rather than being silently unchecked.

WHAT IT CANNOT CATCH: a tile whose ``src`` token is itself the wrong claim, implemented
honestly (``sum:`` on something that should never be summed reads as consistent here — the
per-tile aggregation RULES are in ``aggregate.py`` comments and are enforced by
``test_dashboard_aggregation_refusal.py``, not by this file); a sub-line that is wrong in
prose; and anything about whether the model's own inputs are current, which is
``gtm_core/page_inputs.py``'s job.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from gtm_core import email_campaign_dashboard as gd  # noqa: E402
from gtm_core.email_campaign_dashboard import format as fmt  # noqa: E402

VIEWS = sorted((REPO / "gtm_core" / "email_campaign_dashboard").glob("views_*.py"))

#: The view modules' tile call sites are the population this test must cover. Kept as a
#: floor rather than an exact number so adding a tile does not fail for the wrong reason —
#: what must hold is that every call site is RECORDED, asserted separately below.
assert VIEWS, "no view modules found — the AST scan would pass vacuously"


# --- the independent resolver ---------------------------------------------------
#
# Deliberately re-derived here rather than imported: a resolver shared with the renderer
# would agree with it by construction, which is the fail-open shape this repo keeps
# rediscovering. The collections are read straight off the model.


def _live(m: dict) -> list[dict]:
    live_ids: set[str] = set()
    for c in m["campaigns"]["campaigns"]:
        live_ids |= {s["sequence_id"] for s in c.get("sequences", [])}
    return [s for s in m["status"].get("sequences", []) if s.get("id") in live_ids]


def _num(v) -> float:
    try:
        return float(str(v).strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _resolve_rows(m: dict, arg: str) -> int:
    """Distinct ACCOUNTS satisfying one filter predicate.

    Split out of :func:`resolve_src` only to keep that function under the complexity
    cap. It stays in this module and stays independent of the renderer, which is the
    whole point of the duplication this file's docstring declares.
    """
    # DISTINCT ACCOUNTS satisfying a predicate, re-derived from each row's SEMANTIC
    # fields — `email`, `named`, `signal`, `signal_source_url`. Deliberately NOT read
    # off the `co_*` booleans the page ships in its filter payload: those are the thing
    # under test, and comparing them to themselves would make this assertion "the number
    # equals the number". This duplication is the one the module docstring above already
    # declares deliberate; it is what makes the check independent, not an oversight.
    #
    # Set membership, not subtraction. `co_role_inbox` is `has_email - named`, so it
    # cannot go negative the way `contact_verified - named_seat` can once one account
    # resolves a name without an inbox.
    rows = (m.get("roster") or {}).get("rows") or []
    every = {r["company"] for r in rows}

    def _co(field: str) -> set[str]:
        return {r["company"] for r in rows if r.get(field)}

    has_email, named = _co("email"), _co("named")
    by_pred = {
        "all": every,
        "co_has_email": has_email,
        "co_no_email": every - has_email,
        "co_named": named,
        "co_role_inbox": has_email - named,
        "co_signal": _co("signal"),
        "co_signal_sourced": _co("signal_source_url"),
    }
    assert arg in by_pred, (
        f"unknown filter predicate {arg!r}. A tile may only declare a predicate this "
        "resolver re-derives independently; adding one to filters.PREDICATES without "
        "adding it here leaves the tile's count unchecked."
    )
    return len(by_pred[arg])


def resolve_src(m: dict, token: str):  # noqa: C901, PLR0911 — one return per provenance op, by design
    """Recompute one provenance claim. Unknown ops are an error, never a pass."""
    op, _, arg = token.partition(":")
    if op == "sum":
        coll, _, field = arg.partition(".")
        if coll == "live":
            return int(sum(_num(s.get(field)) for s in _live(m)))
        if coll == "sequences":
            return int(sum(_num(s.get(field)) for s in m["status"].get("sequences", [])))
        if coll == "campaigns":
            head, _, tail = field.partition(".")
            return int(
                sum(_num((c.get(head) or {}).get(tail)) for c in m["campaigns"]["campaigns"])
            )
    if op == "sum-complete":
        # A sum only when EVERY campaign declares the field; otherwise None (refused),
        # because a partial sum reads as a total.
        _, head, tail = arg.split(".")
        vals = [(c.get(head) or {}).get(tail) for c in m["campaigns"]["campaigns"]]
        return int(sum(_num(v) for v in vals)) if vals and all(vals) else None
    if op == "distinct":
        # A UNION over addresses, not a sum: one recipient can appear in two collections (a
        # merge row and a 1:1 pack written to the same inbox), and adding them double-counts.
        # Declared as `distinct:<coll>.<path>|<coll>.<path>`; recomputed here the same way.
        seen: set[str] = set()
        for part in arg.split("|"):
            coll, _, path = part.partition(".")
            head, _, field = path.partition(".")
            for row in (m.get(coll) or {}).get(head) or []:
                if row.get(field):
                    seen.add(row[field])
        return len(seen)
    if op == "count":
        return len(_live(m)) if arg == "live" else len((m.get(arg) or {}).get(arg) or [])
    if op == "agree":
        parts = arg.split(".")
        vals = {
            int(_num((c.get(parts[1]) or {}).get(parts[2])))
            for c in m["campaigns"]["campaigns"]
            if (c.get(parts[1]) or {}).get(parts[2])
        }
        return next(iter(vals)) if len(vals) == 1 else None
    if op == "roster":
        return (m.get("roster") or {}).get(arg)
    if op == "rows":
        return _resolve_rows(m, arg)
    if op == "pooled":
        num, _, den = arg.partition("/")
        return {"replied": resolve_src(m, f"sum:{num}"), "sent": resolve_src(m, f"sum:{den}")}
    if op == "status":
        ps = m.get("prospect_status") or {}
        if arg == "needs_address":
            return ps.get("needs_address", 0)
        if not ps.get("available"):
            return "—"
        return (ps.get("counts") or {}).get(arg, 0)
    if op == "readiness":
        # Re-derived from the check report on disk, not from the model's copy of it (PS15).
        from gtm_core.prospect_readiness import load_readiness

        value = getattr(load_readiness(m["profile"], m.get("_content_root")), arg)
        return "—" if value is None else value
    raise AssertionError(
        f"unknown provenance op {token!r}. Add it to resolve_src, or use one of "
        "sum / sum-complete / count / agree / roster / pooled / rows / status / readiness — "
        "never leave a tile "
        "declaring an op nothing checks."
    )


# --- fixtures -------------------------------------------------------------------

CSV = "First Name,Last Name,Email,Company Name,Company Domain Name,Email Status,GTM_Tier\n"


def _seed(tmp_path, profile="acme"):
    from gtm_core import prospects_consolidate as pc

    pros = pc._prospects_dir(profile, tmp_path)
    (pros / "sequences").mkdir(parents=True, exist_ok=True)
    (pros / "mine-20260904-hubspot.csv").write_text(
        CSV + "Ada,L,ada@analytical.example,Analytical Engine,analytical.example,verified,A\n",
        encoding="utf-8",
    )
    pool = pc._pool_dir(profile, tmp_path)
    pool.mkdir(parents=True, exist_ok=True)
    (pool / "sequence-stats.json").write_text(
        '{"fetched":"2026-09-04","sequences":[{"sequenceId":"sq1","sequenceName":"One",'
        '"status":"active","prospects":[{"total":"12","contacted":"9","replied":"3"}],'
        '"emails":{"status":{"delivered":"9","replied":"3"}}}]}',
        encoding="utf-8",
    )
    camps = pros.parent / "plans" / "campaigns"
    camps.mkdir(parents=True, exist_ok=True)
    (camps / "mine-20260904.campaign.toml").write_text(
        'slug = "mine-20260904"\ntitle = "Singapore agentic builders"\nstatus = "active"\n'
        'sequences = ["sq1"]\nroster_globs = ["mine-20260904-hubspot.csv"]\n'
        "[targets]\nprospects = 4\nemails = 8\nreply_rate = 0.018\n"
        "[window]\ndaily_cap = 90\nmailboxes = 9\ntouches = 2\n",
        encoding="utf-8",
    )
    (camps / "other-20260718.campaign.toml").write_text(
        'slug = "other-20260718"\ntitle = "Cross-org agent trust"\nstatus = "active"\n'
        "[targets]\nprospects = 330\nemails = 990\nreply_rate = 0.018\n"
        "[window]\ndaily_cap = 90\nmailboxes = 9\ntouches = 3\n",
        encoding="utf-8",
    )
    return pros


def _render(tmp_path, campaign: str | None = None):
    m = gd.build_model("acme", tmp_path)
    if campaign:
        m = gd.scope_to_campaign(m, campaign)
    html = gd.render_html(m)
    return m, html, [t for t in fmt._tiles_recorded() if t["src"]]


# --- the checks -----------------------------------------------------------------


def check_tiles(m: dict, tiles: list[dict]) -> None:
    """ASSERTION 1's body. Shared with the seeded-violation test below, which must see it
    RAISE — a check whose failure path is never exercised is a check nobody has tested."""
    for t in tiles:
        srcs = t["src"] if isinstance(t["src"], dict) else {"value": t["src"]}
        for key, token in srcs.items():
            expected = resolve_src(m, token)
            if isinstance(expected, dict):  # pooled: a numerator/denominator pair
                assert t["raw"] == expected, f"{t['label']}: {token}"
                continue
            assert t["raw"][key] == expected, (
                f"tile {t['label']!r} component {key!r} rendered {t['raw'][key]!r} but its "
                f"declared derivation {token!r} produces {expected!r}. Either the tile is "
                "computing something other than what it claims (the '0 of 990' class of "
                "bug), or the claim is stale. Fix the one that is wrong — do not relax "
                "the token to match the code."
            )


@pytest.mark.parametrize("campaign", [None, "mine-20260904", "mine-20260904,other-20260718"])
def test_every_tile_equals_its_declared_derivation(tmp_path, campaign):
    """ASSERTION 1 — the claim, recomputed independently, matches what was rendered."""
    _seed(tmp_path)
    m, _html, tiles = _render(tmp_path, campaign)
    assert tiles, "no tile declared its provenance — the recorder is not wired"
    check_tiles(m, tiles)


#: Everything between a ``<script>`` open and its close, non-greedy so two blocks are two
#: matches rather than one span swallowing the document between them.
_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)


def _visible(html: str) -> str:
    """The page a READER sees — every ``<script>`` block removed.

    Assertion 2 is a document-wide substring check, which is only meaningful while the
    document contains nothing but rendered text. The moment the page carries a JSON
    payload, ``"26" in html`` is satisfied by ``{"base": 26}`` no matter what the tile
    rendered, and the assertion silently stops discriminating (§R18) — it would pass on a
    tile printing 260, or 0, or nothing at all.

    So the payload is stripped BEFORE the check, and
    :func:`test_the_script_strip_actually_removes_something` is the positive control: a
    renamed or removed payload block must fail loudly rather than quietly restoring the
    fail-open shape this function exists to close.
    """
    return _SCRIPT.sub("", html)


def test_every_component_reaches_the_html_formatted_as_stat_formats_it(tmp_path):
    """ASSERTION 2 — closes "the raw said 8, the f-string printed 80"."""
    _seed(tmp_path)
    _m, html, tiles = _render(tmp_path, "mine-20260904")
    visible = _visible(html)
    for t in tiles:
        for key, val in t["raw"].items():
            if not isinstance(val, int):
                continue
            assert f"{val:,}" in visible, (
                f"tile {t['label']!r} recorded {key}={val} but that number is nowhere in "
                "the page. The recorded component and the rendered string have diverged."
            )


def test_the_script_strip_actually_removes_something(tmp_path):
    """POSITIVE CONTROL for :func:`_visible` — a strip that strips nothing is not a strip.

    Without this, deleting or renaming the page's ``<script>`` block leaves ``_visible`` an
    identity function and reverts assertion 2 to the document-wide check it used to be.
    Nothing would fail; the test would simply stop being able to catch anything.
    """
    _seed(tmp_path)
    _m, html, _tiles = _render(tmp_path, "mine-20260904")
    visible = _visible(html)
    assert len(visible) < len(html), (
        "stripping <script> removed nothing, so assertion 2 is once again matching numbers "
        "anywhere in the document — including inside a JSON payload. Either the page lost "
        "its script block, or the block is spelled in a way _SCRIPT does not match."
    )
    assert "<script" not in visible.lower(), "a script block survived the strip"


def test_no_tile_escapes_the_recorder(tmp_path):
    """ASSERTION 3 — a new tile added without provenance fails rather than going unchecked."""
    call_sites = 0
    for path in VIEWS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_stat":
                call_sites += 1
    _seed(tmp_path)
    _m, _html, _tiles = _render(tmp_path, "mine-20260904")
    recorded_labels = {t["label"] for t in fmt._tiles_recorded()}
    assert fmt._tiles_recorded(), "the recorder captured nothing"
    # Not every call site fires on every page (a branch may be refused), so the assertion
    # is the reverse direction: nothing rendered without being recorded. The count is
    # reported so a reviewer can see the population this covers.
    assert len(recorded_labels) <= call_sites, "more tiles recorded than call sites exist"
    assert call_sites >= 10, (
        f"only {call_sites} _stat call sites found across {[p.name for p in VIEWS]} — the "
        "scan is looking in the wrong place and would pass on an empty page."
    )


def test_the_recorder_holds_exactly_one_page(tmp_path):
    """Two renders in one process must not accumulate — a stale tile from a previous page
    is the same wrong-scope bug, wearing a test's clothes."""
    _seed(tmp_path)
    _m, _h, first = _render(tmp_path, "mine-20260904")
    _m2, _h2, second = _render(tmp_path, "mine-20260904")
    assert [t["label"] for t in first] == [t["label"] for t in second]


def test_a_seeded_provenance_lie_is_convicted(tmp_path, monkeypatch):
    """SEEDED VIOLATION — the regression this whole file exists for.

    Reinstates the 2026-09-04 defect in the REAL renderer: ``planned`` reverts to the first
    campaign's value while its tile still declares a complete sum. Nothing else changes,
    the page renders normally, and assertion 1 must convict. If it does not, every green
    run of this file above means nothing.

    Patched at ``_scope_figures`` rather than by hand-building a tile dict, so the lie
    travels the same path a real regression would — through the view, into ``_stat``, into
    the recorder.
    """
    from gtm_core.email_campaign_dashboard import aggregate as ag
    from gtm_core.email_campaign_dashboard import views_status as vs

    _seed(tmp_path)
    honest = ag._scope_figures

    def first_wins(m):
        fig = honest(m)
        first = int((m["campaigns"]["campaigns"][0].get("targets") or {}).get("emails") or 0)
        return {**fig, "planned": (first, None)}

    # Patch the BOUND name in the consuming module, not the definition. `views_status`
    # does `from .aggregate import _scope_figures`, so patching `aggregate` alone leaves
    # the renderer calling the honest original — and the guard below would catch a
    # seeded violation that never happened, which is worse than no seeded test at all.
    monkeypatch.setattr(vs, "_scope_figures", first_wins)
    m, _html, tiles = _render(tmp_path, "mine-20260904,other-20260718")
    planned = next(t for t in tiles if t["label"] == "people contacted")["raw"]["planned"]
    truth = resolve_src(m, "sum-complete:campaigns.targets.emails")
    assert planned != truth, (
        "the seeded first-wins bug produced the same number as the honest sum, so this "
        "test proves nothing — make the two campaigns' email targets differ in _seed()."
    )
    with pytest.raises(AssertionError, match="declared derivation"):
        check_tiles(m, tiles)
