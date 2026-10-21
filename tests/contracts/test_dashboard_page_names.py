"""Item 2 (PRD §9) — a page that depends on a file's NAME, not its bytes, notices it appear or vanish.

The dossier lookup (``account_has_dossier``) globs an account's folder and never opens what it
finds: a dossier that APPEARS flips a Tier-A account from "needs a dossier" to "has one", and a
digest inventory cannot see it, because there was nothing to digest. ``name_globs`` records the
matched NAMES; an edit to a dossier is (correctly) not a change — its bytes never reached the page.

Real files, real renders into a temp root, no mocks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import page_inputs
from gtm_core.email_campaign_dashboard import freshness
from gtm_core.email_campaign_dashboard.config import NAME_GLOBS
from gtm_core.prospects_consolidate.dossier import _DOSSIER_GLOB_PATTERNS
from tests.contracts.test_dashboard_check_every_page import ROLLUP, _three_pages

ACCOUNT = "accounts/zebra-quill-robotics"
DOSSIER = f"{ACCOUNT}/dossier-zebra-quill-robotics-2026-09-22.md"


def _check(tmp_path):
    return freshness.check_all_pages("acme", tmp_path)


def _rollup_report(tmp_path):
    return next(r for r in _check(tmp_path).reports if r.page.name == ROLLUP)


def _write(base: Path, rel: str, text: str = "# research\n") -> Path:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _inventory(base: Path) -> dict:
    return json.loads((base / ROLLUP.replace(".html", ".inputs.json")).read_text(encoding="utf-8"))


def test_the_name_globs_are_the_dossier_patterns_and_nothing_else():
    assert NAME_GLOBS == tuple(f"accounts/*/{p}" for p in _DOSSIER_GLOB_PATTERNS)


@pytest.mark.parametrize("pattern", _DOSSIER_GLOB_PATTERNS)
def test_every_dossier_pattern_is_noticed_appearing(tmp_path, pattern):
    """One case per pattern in ``dossier.py`` — a pattern added there is covered here, and one the
    page cannot see appear fails by name rather than by a count that happens to add up."""
    base = _three_pages(tmp_path)
    assert _check(tmp_path).ok
    _write(base, f"{ACCOUNT}/{pattern.replace('*', 'zq')}")
    rep = _rollup_report(tmp_path)
    assert rep.ok is False
    assert [n for n in rep.new if n.startswith("name:")], rep.explain()


def test_a_dossier_appearing_is_reported_by_name(tmp_path):
    base = _three_pages(tmp_path)
    _write(base, DOSSIER)
    rep = _rollup_report(tmp_path)
    assert rep.new == [f"name:{DOSSIER}"]
    assert "appeared since render, never read" in rep.explain()


def test_a_dossier_vanishing_is_reported_by_name(tmp_path):
    base = _three_pages(tmp_path)
    _write(base, DOSSIER)
    gd.render_dashboard("acme", tmp_path, stubs=False, scope="all")
    assert _rollup_report(tmp_path).ok
    (base / DOSSIER).unlink()
    rep = _rollup_report(tmp_path)
    assert rep.missing == [f"name:{DOSSIER}"]
    assert rep.ok is False


def test_editing_a_dossier_is_not_a_change(tmp_path):
    """The page never read its bytes, so convicting an edit would send an operator to
    ``--refresh-all`` for nothing — the cry-wolf failure the code fingerprint is report-only for."""
    base = _three_pages(tmp_path)
    _write(base, DOSSIER)
    gd.render_dashboard("acme", tmp_path, stubs=False, scope="all")
    _write(base, DOSSIER, "# edited entirely\n" * 40)
    assert _rollup_report(tmp_path).ok


def test_a_folder_named_like_a_dossier_counts_because_the_page_counts_it(tmp_path):
    """``_folder_has_dossier`` is ``any(folder.glob(pat))`` — a DIRECTORY matches too. Tracking
    only files would let the page say "has a dossier" on a name the inventory never recorded."""
    base = _three_pages(tmp_path)
    (base / ACCOUNT / "account-dossier-zq").mkdir(parents=True)
    assert _rollup_report(tmp_path).new == [f"name:{ACCOUNT}/account-dossier-zq"]


def test_a_render_records_the_names_and_the_globs_it_matched(tmp_path):
    base = _three_pages(tmp_path)
    _write(base, DOSSIER)
    gd.render_dashboard("acme", tmp_path, stubs=False, scope="all")
    inv = _inventory(base)
    assert inv["name_globs"] == sorted(NAME_GLOBS)
    assert inv["names"] == [DOSSIER]
    assert page_inputs.verify_inventory(base / ROLLUP, base, profile="acme", expect_names=True).ok


def test_an_inventory_from_before_name_tracking_is_stale_when_names_are_expected(tmp_path):
    """ "Never recorded" must not read as "nothing to record". The verdict is asked for by the
    caller that renders dossiers (``expect_names``); a caller that does not ask is unchanged."""
    base = _three_pages(tmp_path)
    path = base / ROLLUP.replace(".html", ".inputs.json")
    inv = json.loads(path.read_text(encoding="utf-8"))
    del inv["name_globs"], inv["names"]
    path.write_text(json.dumps(inv), encoding="utf-8")
    old = page_inputs.verify_inventory(base / ROLLUP, base, profile="acme", expect_names=True)
    assert old.ok is False and any("name tracking" in c for c in old.meta_stale)
    assert page_inputs.verify_inventory(base / ROLLUP, base, profile="acme").ok
    assert _rollup_report(tmp_path).ok is False  # the real check asks


@pytest.mark.parametrize(
    "field,value",
    [
        ("name_globs", ["../other-tenant/*"]),
        ("name_globs", ["/etc/*"]),
        ("name_globs", [7]),
        ("name_globs", "accounts/*/x"),
        ("names", "not-a-list"),
        ("names", [7]),
    ],
)
def test_a_crafted_name_field_is_refused_never_raised_or_followed(tmp_path, field, value):
    base = _three_pages(tmp_path)
    path = base / ROLLUP.replace(".html", ".inputs.json")
    inv = json.loads(path.read_text(encoding="utf-8"))
    inv[field] = value
    path.write_text(json.dumps(inv), encoding="utf-8")
    rep = page_inputs.verify_inventory(base / ROLLUP, base, profile="acme", expect_names=True)
    assert rep.ok is False
    assert not any(str(n).startswith("name:") and "other-tenant" in n for n in rep.new)


@pytest.mark.parametrize("drop", ["names", "name_globs"])
def test_a_half_recorded_name_field_is_refused_even_when_names_are_not_expected(tmp_path, drop):
    """One of the pair gone is damage, not "predates name tracking": it must be named whether or
    not the caller asked, because a verdict that depends on the caller's flag hides it."""
    base = _three_pages(tmp_path)
    path = base / ROLLUP.replace(".html", ".inputs.json")
    inv = json.loads(path.read_text(encoding="utf-8"))
    del inv[drop]
    path.write_text(json.dumps(inv), encoding="utf-8")
    rep = page_inputs.verify_inventory(base / ROLLUP, base, profile="acme")
    assert rep.ok is False and rep.damaged == ["names:malformed"]
    # A half-recorded pair is DAMAGE, not an escape: it must not borrow the path-escape wording,
    # which would send an operator hunting for a traversal that does not exist.
    assert rep.refused == []
    assert "recorded in a damaged form: names:malformed" in rep.explain()
    assert "escapes root" not in rep.explain()


def test_the_single_page_check_also_asks_for_names(tmp_path):
    """``--check-fresh --scope X`` is a second call site from the all-pages check; one that did
    not ask would call a pre-name-tracking page fresh."""
    from gtm_core.email_campaign_dashboard.render import check_fresh

    base = _three_pages(tmp_path)
    path = base / ROLLUP.replace(".html", ".inputs.json")
    inv = json.loads(path.read_text(encoding="utf-8"))
    del inv["name_globs"], inv["names"]
    path.write_text(json.dumps(inv), encoding="utf-8")
    rep = check_fresh("acme", tmp_path, scope="all")
    assert rep.ok is False and any("name tracking" in c for c in rep.meta_stale)


def test_a_content_root_containing_glob_characters_still_matches_its_names(tmp_path):
    """`matching` built `str(root / glob)`, so a root with `[` or `*` in its path was read as a
    pattern and matched nothing — a dossier that appeared under it was never seen, and the page
    read fresh. The root is escaped; only the recorded globs are patterns.
    Catches: removing `glob.escape` in `page_input_names.matching`."""
    from gtm_core import page_input_names

    root = tmp_path / "con[tent]*root"
    dossier = root / "accounts" / "zq" / "account-dossier-zq-2026-10-01.md"
    dossier.parent.mkdir(parents=True)
    dossier.write_text("x", encoding="utf-8")
    assert page_input_names.matching(root, list(NAME_GLOBS)) == [
        "accounts/zq/account-dossier-zq-2026-10-01.md"
    ]


def test_a_content_root_containing_glob_characters_still_resolves_its_inputs(tmp_path):
    """The same hazard one function over: `_resolve` feeds the digest inventory."""
    root = tmp_path / "con[tent]*root"
    (root / "prospects").mkdir(parents=True)
    (root / "prospects" / "a-hubspot.csv").write_text("x", encoding="utf-8")
    assert [p.name for p in page_inputs._resolve(root, ["prospects/*-hubspot.csv"])] == [
        "a-hubspot.csv"
    ]
