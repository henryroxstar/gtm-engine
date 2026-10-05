"""F6 — a page records a fingerprint of the code that built it, and a check REPORTS a change.

WHY THIS IS REPORT-ONLY (PRD §9, 2026-09-30). The digest inventory catches every input that
moved. It cannot see the renderer itself: a changed view function changes what the same bytes
turn into, and a page built by last week's code reads exactly like one built by today's. The
fingerprint closes that — but a code change does NOT always change a number, so convicting on it
would send an operator to ``--refresh-all`` for a comment edit. It therefore never touches
``Report.ok``; it is a NOTE beside the page's verdict.

Real files, no mocks: the "different code" case is a real copy of the package with one file
edited, passed through the ``package_dir`` argument the check already threads.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from gtm_core import email_campaign_dashboard as gd
from gtm_core.email_campaign_dashboard import fingerprint, freshness
from tests.contracts.test_dashboard_check_every_page import (
    ROLLUP,
    _three_pages,
)

PKG = Path(fingerprint.__file__).resolve().parent


def _package_copy(tmp_path: Path) -> Path:
    """A real copy of the package and its sibling ``sequencers.toml``, so the fingerprint of the
    copy equals the fingerprint of the original until something in it is edited."""
    root = tmp_path / "codecopy" / "gtm_core"
    shutil.copytree(PKG, root / PKG.name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(PKG.parent / "sequencers.toml", root / "sequencers.toml")
    return root / PKG.name


def _recorded(base: Path, page: str = ROLLUP) -> dict:
    return json.loads((base / page.replace(".html", ".inputs.json")).read_text(encoding="utf-8"))


def test_two_renders_with_no_change_record_the_same_fingerprint(tmp_path):
    base = _three_pages(tmp_path)
    first = _recorded(base)["meta"]["code_fingerprint"]
    gd.render_dashboard("acme", tmp_path, stubs=False, scope="all")
    second = _recorded(base)["meta"]["code_fingerprint"]
    assert first == second and len(first) == 64


def test_a_copy_of_the_package_has_the_same_fingerprint(tmp_path):
    """The fingerprint is over content and RELATIVE paths, never absolute ones — otherwise it
    would differ on every machine and every checkout, and every page would carry the note."""
    assert fingerprint.code_fingerprint(_package_copy(tmp_path)) == fingerprint.code_fingerprint()


def test_a_changed_package_file_is_reported_and_does_not_change_ok(tmp_path):
    _three_pages(tmp_path)
    pkg = _package_copy(tmp_path)
    clean = freshness.check_all_pages("acme", tmp_path, package_dir=pkg)
    assert clean.ok and not clean.notes

    (pkg / "views_overview.py").write_text(
        (pkg / "views_overview.py").read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8"
    )
    rep = freshness.check_all_pages("acme", tmp_path, package_dir=pkg)
    assert rep.ok, "a code change must never convict a page"
    assert all(r.ok for r in rep.reports)
    assert set(rep.notes) == {ROLLUP, "campaign-open.html", "campaign-mine-20260904.html"}
    text = rep.explain()
    assert "built by different code" in text and "--refresh-all" in text
    assert "3 pages checked: 3 fresh, 0 stale, 0 retired candidate(s)." in text


def test_the_sibling_config_file_the_page_reads_is_covered(tmp_path):
    """`sequencers.toml` is read by the page and by nothing the inventory digests; it is the one
    input that is neither a package file nor tenant data."""
    _three_pages(tmp_path)
    pkg = _package_copy(tmp_path)
    seq = pkg.parent / "sequencers.toml"
    seq.write_text(seq.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    assert freshness.check_all_pages("acme", tmp_path, package_dir=pkg).notes


def test_pycache_is_not_part_of_the_fingerprint(tmp_path):
    pkg = _package_copy(tmp_path)
    before = fingerprint.code_fingerprint(pkg)
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"\x00\x01")
    assert fingerprint.code_fingerprint(pkg) == before


def test_an_inventory_with_no_fingerprint_says_it_predates_fingerprinting(tmp_path):
    base = _three_pages(tmp_path)
    inv = base / "email_campaign_status.inputs.json"
    rec = json.loads(inv.read_text(encoding="utf-8"))
    del rec["meta"]["code_fingerprint"]
    inv.write_text(json.dumps(rec), encoding="utf-8")

    rep = freshness.check_all_pages("acme", tmp_path)
    assert rep.ok, "the missing fingerprint is a note, not a conviction"
    assert "predates fingerprinting" in rep.notes[ROLLUP]
    assert set(rep.notes) == {ROLLUP}


def test_a_non_string_fingerprint_is_treated_as_absent(tmp_path):
    base = _three_pages(tmp_path)
    inv = base / "email_campaign_status.inputs.json"
    rec = json.loads(inv.read_text(encoding="utf-8"))
    rec["meta"]["code_fingerprint"] = ["not", "a", "digest"]
    inv.write_text(json.dumps(rec), encoding="utf-8")
    assert "predates fingerprinting" in freshness.check_all_pages("acme", tmp_path).notes[ROLLUP]


def test_a_page_with_no_inventory_gets_no_code_note(tmp_path):
    """Nothing was recorded, so there is nothing to compare — and the page is already stale
    and named for the stronger reason."""
    base = _three_pages(tmp_path)
    (base / "campaign-legacy-20240101.html").write_text("<html/>", encoding="utf-8")
    rep = freshness.check_all_pages("acme", tmp_path)
    assert "campaign-legacy-20240101.html" not in rep.notes


def test_the_single_page_check_prints_the_same_note(tmp_path, capsys):
    base = _three_pages(tmp_path)
    inv = base / "email_campaign_status.inputs.json"
    rec = json.loads(inv.read_text(encoding="utf-8"))
    del rec["meta"]["code_fingerprint"]
    inv.write_text(json.dumps(rec), encoding="utf-8")

    code = gd._cli(
        ["--profile", "acme", "--content-root", str(tmp_path), "--check-fresh", "--scope", "all"]
    )
    out = capsys.readouterr()
    assert code == 0
    assert "predates fingerprinting" in out.out


def test_a_fresh_page_prints_no_code_note(tmp_path, capsys):
    _three_pages(tmp_path)
    code = gd._cli(["--profile", "acme", "--content-root", str(tmp_path), "--check-fresh"])
    out = capsys.readouterr()
    assert code == 0
    assert "different code" not in out.out and "fingerprint" not in out.out


def test_consolidate_does_not_stamp_its_inventory_with_the_dashboards_fingerprint(
    tmp_path, monkeypatch
):
    """Consolidate writes an inventory too, through the same `write_inventory`. It is not a
    dashboard page: stamping it would make a code change to a renderer look like a change to the
    master list it never touched."""
    from gtm_core import prospects_consolidate as pc

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    p_dir = tmp_path / "acme" / "prospects"
    p_dir.mkdir(parents=True)
    header = (
        "first,last,email,title,company,company_domain,city,country,segment,tier,signal_clause,"
        "why_now,case_study,src,suppression,suppression_date,email_status\n"
    )
    row = "Jane,Doe,jane@example.test,CTO,Acme,acme.example,SF,USA,enterprise,A,S,W,C,src,,,verified\n"
    (p_dir / "prospects-20260920-hubspot.csv").write_text(header + row, encoding="utf-8")
    pc.consolidate("acme", content_root=tmp_path)

    others = [
        p
        for p in (tmp_path / "acme").rglob("*.inputs.json")
        if p.name != "email_campaign_status.inputs.json"
    ]
    assert others, "fixture precondition: consolidate wrote its own inventory"
    for inv in others:
        text = inv.read_text(encoding="utf-8")
        assert "code_fingerprint" not in text, inv.name
        assert "meta" not in json.loads(text), inv.name
    dash = json.loads((tmp_path / "acme" / "email_campaign_status.inputs.json").read_text())
    assert len(dash["meta"]["code_fingerprint"]) == 64
