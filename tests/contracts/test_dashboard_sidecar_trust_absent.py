"""Round-2 red team I3: a key ABSENT from a sidecar must never read as "nothing to check".

`inv.get("inputs", [])`, `inv.get("globs", [])` and `inv.get("page_sha256") and ...` all turned a
missing key into the empty case, which is the granting branch: a sidecar with no `inputs` and no
`globs` tracked nothing, so an edited input read fresh; one with no `globs` could not see a roster
dropped into the imports folder; one with no `page_sha256` could not see a hand-edited page.

The matrix below drops EVERY subset of the sidecar's keys against seven genuine causes of
staleness (the red team's `p2_absent_matrix.py`) and requires that nothing reads fresh.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import page_inputs as pi
from gtm_core.email_campaign_dashboard import check
from tests.contracts.test_dashboard_check_every_page import ROLLUP, _three_pages

PAGE = ROLLUP


def _cells_edited(base):
    (base / "prospects/sequences/cells.toml").write_text("# edited\n", encoding="utf-8")


def _input_deleted(base):
    (base / "prospects/sequences/cells.toml").unlink()


def _new_roster(base):
    (base / "prospects/imports").mkdir(parents=True, exist_ok=True)
    (base / "prospects/imports/new-export.csv").write_text("a,b\n1,2\n", encoding="utf-8")


def _page_edited(base):
    page = base / PAGE
    page.write_text(page.read_text(encoding="utf-8") + "<!-- edit -->", encoding="utf-8")


def _profile_changed(base):
    (pi.resolve_profiles_root() / "acme" / "PROFILE.md").write_text("# changed\n", encoding="utf-8")


def _new_dossier(base):
    folder = base / "accounts" / "bigco"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "account-dossier-bigco-2026-10-01.md").write_text("x", encoding="utf-8")


def _nothing(base):
    return None


#: cause -> (what changes on disk, how many days later the check is asked)
CAUSES = {
    "cells_edited": (_cells_edited, 0),
    "input_deleted": (_input_deleted, 0),
    "new_roster_csv": (_new_roster, 0),
    "page_hand_edited": (_page_edited, 0),
    "profile_changed": (_profile_changed, 0),
    "new_dossier": (_new_dossier, 0),
    "figures_20d_old": (_nothing, 20),
}


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    (root / "acme").mkdir(parents=True)
    (root / "acme" / "PROFILE.md").write_text("# acme\n", encoding="utf-8")
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(root))
    return root


def _subsets(keys):
    for size in range(1, len(keys) + 1):
        yield from itertools.combinations(keys, size)


@pytest.mark.parametrize("cause", list(CAUSES))
def test_no_subset_of_missing_keys_reads_fresh_for_a_genuine_staleness(tmp_path, profiles, cause):
    """The whole matrix: 1,023 key subsets per cause. The baseline (no key dropped) must convict
    first, or the matrix could pass on a check that is always red.
    Catches: restoring `inv.get("inputs", [])`, `inv.get("globs", [])` or the
    `inv.get("page_sha256") and ...` short-circuit in `page_inputs.verify_inventory`."""
    base = _three_pages(tmp_path, fetched=datetime.now(UTC).date().isoformat())
    gd.render_dashboard("acme", tmp_path, stubs=False, scope="all")
    page, sidecar = base / PAGE, pi.inventory_path(base / PAGE)
    original = json.loads(sidecar.read_text(encoding="utf-8"))
    change, days = CAUSES[cause]
    change(base)
    when = datetime.now(UTC) + timedelta(days=days)

    assert not check.check_page(page, base, "acme", now=when).ok, "the cause must convict"
    leaks = []
    for dropped in _subsets(list(original)):
        sidecar.write_text(
            json.dumps({k: v for k, v in original.items() if k not in dropped}), encoding="utf-8"
        )
        if check.check_page(page, base, "acme", now=when).ok:
            leaks.append(dropped)
    assert not leaks, f"{len(leaks)} subsets read fresh, e.g. {leaks[:3]}"


@pytest.mark.parametrize("key", ["inputs", "globs", "page_sha256"])
@pytest.mark.parametrize("how", ["absent", "null", "empty"])
def test_an_inventory_missing_a_tracking_key_is_stale_with_a_named_reason(
    tmp_path, profiles, key, how
):
    """Even with NOTHING else wrong: the page is current, the key is gone, and the answer is still
    stale — "we cannot tell" is not "fine". `[]` is a legitimate value for `inputs`/`globs`, so
    only absent/null are tested for those; `""` is only legitimate for a page that was not there."""
    if how == "empty" and key != "page_sha256":
        pytest.skip("an empty list is a real, recorded value")
    base = _three_pages(tmp_path, fetched=datetime.now(UTC).date().isoformat())
    gd.render_dashboard("acme", tmp_path, stubs=False, scope="all")
    sidecar = pi.inventory_path(base / PAGE)
    rec = json.loads(sidecar.read_text(encoding="utf-8"))
    assert check.check_page(base / PAGE, base, "acme").ok
    if how == "absent":
        del rec[key]
    else:
        rec[key] = None if how == "null" else ""
    sidecar.write_text(json.dumps(rec), encoding="utf-8")

    rep = check.check_page(base / PAGE, base, "acme")
    assert rep.ok is False
    assert f"inventory is incomplete: missing {key}" in rep.explain()


def test_an_empty_page_digest_is_only_fine_for_a_page_that_was_not_there(tmp_path):
    """`write_inventory` records `""` for a page that did not exist yet; that is the one place the
    digest may be empty. Once the page exists, an empty digest cannot show it unedited."""
    root = tmp_path / "root"
    root.mkdir()
    page = root / "p.html"
    pi.write_inventory(page, (root, []), scope="all")
    assert json.loads(pi.inventory_path(page).read_text(encoding="utf-8"))["page_sha256"] == ""
    assert "page_sha256" not in " ".join(pi.verify_inventory(page, root).incomplete)
    page.write_text("<html>appeared later</html>", encoding="utf-8")
    rep = pi.verify_inventory(page, root)
    assert rep.incomplete == ["page_sha256"] and rep.ok is False


def test_a_legacy_inventory_still_says_it_predates_tracking(tmp_path, profiles):
    """The honest message is kept: a sidecar older than a NEWER tracked field says "predates", not
    "incomplete" — the two are different advice (re-render vs. the file was damaged)."""
    base = _three_pages(tmp_path, fetched=datetime.now(UTC).date().isoformat())
    sidecar = pi.inventory_path(base / PAGE)
    rec = json.loads(sidecar.read_text(encoding="utf-8"))
    for key in ("meta", "name_globs", "names", "profile_inputs"):
        rec.pop(key, None)
    sidecar.write_text(json.dumps(rec), encoding="utf-8")
    text = check.check_page(base / PAGE, base, "acme").explain()
    assert "predates" in text and "incomplete" not in text
