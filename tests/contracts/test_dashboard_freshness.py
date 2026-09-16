"""A rendered page can be shown to be built from what is on disk now — or it says so.

WHY THIS EXISTS (2026-09-05). A stale page renders **identically** to a current one. The
live two-campaign page was seventeen hours behind the ``cells.toml`` it was built from and
looked perfectly current; earlier the same week the first regeneration of a session was a
byte-identical no-op and only a ``grep`` noticed. Nothing in this repo compared a generated
page against its own inputs.

WHAT IS ASSERTED HERE IS THE MECHANISM, NOT THE LIVE PAGE. Asserting that
a tenant's committed ``email_campaign_status.html`` is fresh would fail on any checkout
where someone touched a CSV, which trains people to ignore the failure — the same disease as a
gate that cries wolf. The operator-facing question is
``--check-fresh``; this file proves the machinery under it convicts.

DELIBERATELY DIGESTS, NOT MTIMES — see ``gtm_core/page_inputs.py``'s docstring, which also
carries the four things this check CANNOT catch (upstream truth moving with no local file
changing; stale-but-unchanged content; code changes; time-derived content). Those are
stated there rather than here so the module and its test cannot drift apart on the point.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from gtm_core import email_campaign_dashboard as gd  # noqa: E402
from gtm_core import page_inputs as pi  # noqa: E402
from gtm_core.email_campaign_dashboard.config import input_globs  # noqa: E402

CSV = "First Name,Last Name,Email,Company Name,Company Domain Name,Email Status,GTM_Tier\n"


def _seed(tmp_path, profile="acme"):
    from gtm_core import prospects_consolidate as pc

    pros = pc._prospects_dir(profile, tmp_path)
    (pros / "sequences").mkdir(parents=True, exist_ok=True)
    (pros / "mine-20260904-hubspot.csv").write_text(
        CSV + "Ada,L,ada@analytical.example,Analytical Engine,analytical.example,verified,A\n",
        encoding="utf-8",
    )
    (pros / "sequences" / "cells.toml").write_text("# empty\n", encoding="utf-8")
    (pros / "evals").mkdir(parents=True, exist_ok=True)
    (pros / "evals" / "lanes-state.jsonl").write_text(
        '{"email": "ada@analytical.example", "lane": "personalised", "reason": "personalised"}\n',
        encoding="utf-8",
    )
    camps = pros.parent / "plans" / "campaigns"
    camps.mkdir(parents=True, exist_ok=True)
    (camps / "mine-20260904.campaign.toml").write_text(
        'slug = "mine-20260904"\ntitle = "Singapore agentic builders"\nstatus = "active"\n'
        'roster_globs = ["mine-20260904-hubspot.csv"]\n'
        "[targets]\nprospects = 4\nemails = 8\nreply_rate = 0.018\n"
        "[window]\ndaily_cap = 90\nmailboxes = 9\ntouches = 2\n",
        encoding="utf-8",
    )
    return pros


def _render(tmp_path, **kw):
    return gd.render_dashboard("acme", tmp_path, stubs=False, **kw)


def test_a_just_rendered_page_is_fresh(tmp_path):
    """POSITIVE CONTROL. Without this the check could be a constant "stale" and still pass
    every conviction test below."""
    _seed(tmp_path)
    page = _render(tmp_path, scope="open")
    rep = gd.check_fresh("acme", tmp_path, scope="open")
    assert rep.ok, rep.explain()
    assert pi.inventory_path(page).exists()


def test_a_changed_input_is_convicted(tmp_path):
    """The seventeen-hour case: an input edited after the render."""
    _seed(tmp_path)
    _render(tmp_path, scope="open")
    cells = tmp_path / "acme" / "prospects" / "sequences" / "cells.toml"
    cells.write_text("# edited after the page was written\n", encoding="utf-8")

    rep = gd.check_fresh("acme", tmp_path, scope="open")
    assert not rep.ok
    assert "prospects/sequences/cells.toml" in rep.changed
    assert "STALE" in rep.explain() and "cells.toml" in rep.explain()


def test_an_input_that_APPEARED_after_the_render_is_convicted(tmp_path):
    """The case a digest set alone cannot see, and the reason the globs are recorded.

    A second campaign manifest is dropped in. Every recorded input still hashes the same,
    so a check that only re-digests what it read would report fresh — while the page is
    now missing a whole campaign.
    """
    _seed(tmp_path)
    _render(tmp_path, scope="open")
    (tmp_path / "acme" / "plans" / "campaigns" / "later-20260910.campaign.toml").write_text(
        'slug = "later-20260910"\ntitle = "Added after the render"\nstatus = "active"\n',
        encoding="utf-8",
    )
    rep = gd.check_fresh("acme", tmp_path, scope="open")
    assert not rep.ok and not rep.changed, "nothing recorded changed — only the glob saw it"
    assert "plans/campaigns/later-20260910.campaign.toml" in rep.new
    assert "appeared since render" in rep.explain()


def test_a_deleted_input_is_convicted(tmp_path):
    _seed(tmp_path)
    _render(tmp_path, scope="open")
    (tmp_path / "acme" / "prospects" / "mine-20260904-hubspot.csv").unlink()
    rep = gd.check_fresh("acme", tmp_path, scope="open")
    assert not rep.ok and rep.missing


def test_a_hand_edited_page_is_convicted(tmp_path):
    """A second writer, or a hand tweak, means the page no longer matches what was rendered
    from those inputs — the exact class of bug that made two renderers write one filename."""
    _seed(tmp_path)
    page = _render(tmp_path, scope="open")
    page.write_text(page.read_text(encoding="utf-8") + "<!-- tweak -->", encoding="utf-8")
    rep = gd.check_fresh("acme", tmp_path, scope="open")
    assert not rep.ok and rep.page_edited


def test_a_page_with_no_inventory_is_stale_not_unknown(tmp_path):
    """FAIL-CLOSED. "We cannot tell" must never render as "fine" — that is how a fail-open
    rule passes the defect it was written for while reporting zero errors."""
    _seed(tmp_path)
    page = _render(tmp_path, scope="open")
    pi.inventory_path(page).unlink()
    rep = gd.check_fresh("acme", tmp_path, scope="open")
    assert not rep.ok and rep.no_inventory
    assert "cannot be shown to be current" in rep.explain()


def test_rendering_does_not_make_its_own_page_stale(tmp_path):
    """A glob covering something the renderer WRITES would make every page permanently
    stale on its second check, and the check would be switched off within a day."""
    _seed(tmp_path)
    _render(tmp_path, scope="all")  # this path also writes .pool/status.json + cells.json
    assert gd.check_fresh("acme", tmp_path, scope="all").ok
    _render(tmp_path, scope="open")  # a second page under the same tree
    assert gd.check_fresh("acme", tmp_path, scope="open").ok
    assert gd.check_fresh("acme", tmp_path, scope="all").ok, (
        "rendering one scope marked another scope's page stale — a declared input glob is "
        "matching a file the renderer itself writes. See config.INPUT_GLOBS."
    )


def test_every_scope_gets_its_own_inventory(tmp_path):
    """Two scopes, two pages, two inventories — a shared one would let a scoped page be
    'verified' against the rollup's inputs."""
    _seed(tmp_path)
    a = _render(tmp_path, scope="all")
    b = _render(tmp_path, scope="open")
    assert pi.inventory_path(a) != pi.inventory_path(b)
    for page, mode in ((a, "all"), (b, "open")):
        assert json.loads(pi.inventory_path(page).read_text(encoding="utf-8"))["scope"] == mode


def test_the_inventory_survives_its_tree_being_moved(tmp_path):
    """The case that breaks an mtime check outright: a clone, a `cp -r`, a restore. Every
    mtime is rewritten at once, so an mtime check passes on everything — the worst failure
    for a check whose job is to convict. Digests plus relative paths survive it."""
    import shutil

    _seed(tmp_path)
    _render(tmp_path, scope="open")
    moved = tmp_path.parent / (tmp_path.name + "-moved")
    shutil.copytree(tmp_path, moved)
    page = moved / "acme" / "campaign-open.html"
    assert pi.verify_inventory(page, moved / "acme").ok


def test_the_declared_globs_cover_what_the_model_actually_reads(tmp_path):
    """The stale-allowlist half. A file the model reads but INPUT_GLOBS does not name is a
    file whose change silently passes the check — worse than not checking, because the
    green result is a claim. Both directions are asserted: nothing named here is missing
    from the tree the fixture builds, and the manifests/CSVs the model demonstrably opens
    are matched.
    """
    _seed(tmp_path)
    root, globs = input_globs("acme", tmp_path)
    must_match = [
        "plans/campaigns/mine-20260904.campaign.toml",
        "prospects/mine-20260904-hubspot.csv",
        "prospects/sequences/cells.toml",
        # PS14 — the router's last route feeds the status tiles' `available`/`counts`.
        "prospects/evals/lanes-state.jsonl",
    ]
    for rel in must_match:
        assert (root / rel).exists(), f"fixture does not create {rel}"
        assert any((root / rel) in set(root.glob(g)) for g in globs), (
            f"{rel} is read by the model but matches no glob from config.input_globs(), "
            "so a change to it would pass --check-fresh while reporting the page fresh — "
            "a green result that is a false claim. Add a glob."
        )
