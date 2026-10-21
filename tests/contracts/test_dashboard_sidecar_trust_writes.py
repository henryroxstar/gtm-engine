"""Round-2 red team: what the render and the refresh WRITE, and what one bad file may stop.

* **I4** — `--refresh-all` reaches every page that has a sidecar automatically, and a render wrote
  through a symlinked page or sidecar (`Path.write_text` follows links), so a link placed in the
  profile folder became "the refresh overwrote a file elsewhere". A sidecar that was itself a link
  to ANOTHER tenant's sidecar was also read, and its recorded paths printed.
* **Slug traversal** — a manifest `slug = "../../../x"`, or `--campaign ../../../x`, built a page
  path above the profile folder.
* **M3** — an `OSError` while verifying ONE page (a recorded input that became a directory, an
  unreadable file, a path over 255 bytes) raised out of the all-pages check, so the other pages got
  no verdict, and out of the refresh with the rollup page rewritten and its sidecar not.

Real files and real symlinks in tmp; nothing is mocked except the one failing write.
"""

from __future__ import annotations

import json
import os

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import page_inputs as pi
from gtm_core import page_inputs_guard as guard
from gtm_core.email_campaign_dashboard import check
from tests.contracts.test_dashboard_check_every_page import (
    ROLLUP,
    SLUG,
    _days_ago,
    _edit_a_shared_input,
    _edit_sidecar,
    _other_tenant,
    _run,
    _three_pages,
    _tree,
)

NAMED = f"campaign-{SLUG}.html"
OPEN = "campaign-open.html"
LINK_FIX = "it is a symlink — replace it with a regular file or delete it"


def _everything(root):
    """Every path under ``root`` (directories included), and each regular file's bytes."""
    return {
        str(p.relative_to(root)): (None if p.is_dir() else p.read_bytes())
        for p in sorted(root.rglob("*"))
        if not p.is_symlink()
    }


def _link(path, target):
    path.unlink()
    path.symlink_to(target)


# --- I4: no write through a symlink ------------------------------------------------------------


def test_refresh_all_never_writes_through_a_symlinked_page(tmp_path, capsys):
    """Catches: replacing `guard.write_text` with `Path.write_text` at the page write."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    victim = tmp_path / "victim-notes.txt"
    victim.write_text("PRECIOUS USER FILE\n", encoding="utf-8")
    _link(base / NAMED, victim)
    sidecar = pi.inventory_path(base / NAMED)
    sidecar_bytes = sidecar.read_bytes()

    rc, _out, err = _run(tmp_path, capsys, "--check-fresh")
    assert rc == 1 and f"{NAMED}: STALE — {LINK_FIX}." in err

    rc, _out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 1
    assert f"FAILED to re-render {NAMED}: {LINK_FIX}" in err
    assert victim.read_text(encoding="utf-8") == "PRECIOUS USER FILE\n"
    assert (base / NAMED).is_symlink(), "the operator's link is left as it was"
    assert sidecar.read_bytes() == sidecar_bytes


def test_refresh_all_never_writes_through_a_symlinked_sidecar(tmp_path, capsys):
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    victim = tmp_path / "victim-sidecar.txt"
    victim.write_text("PRECIOUS SIDECAR\n", encoding="utf-8")
    _link(pi.inventory_path(base / NAMED), victim)

    rc, _out, err = _run(tmp_path, capsys, "--check-fresh")
    assert rc == 1 and f"{NAMED}: STALE — its campaign-{SLUG}.inputs.json is a symlink" in err
    rc, _out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 1 and f"FAILED to re-render {NAMED}" in err and "is a symlink" in err
    assert victim.read_text(encoding="utf-8") == "PRECIOUS SIDECAR\n"


@pytest.mark.parametrize("which", ["page", "sidecar"])
def test_a_symlinked_rollup_is_refused_and_both_victims_are_untouched(tmp_path, capsys, which):
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    victim = tmp_path / "victim.txt"
    victim.write_text("PRECIOUS\n", encoding="utf-8")
    path = base / ROLLUP if which == "page" else pi.inventory_path(base / ROLLUP)
    _link(path, victim)

    rc, _out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 1
    assert f"FAILED to re-render {ROLLUP}" in err and "symlink" in err
    assert victim.read_text(encoding="utf-8") == "PRECIOUS\n"
    assert _run(tmp_path, capsys, "--check-fresh")[0] == 1


@pytest.mark.parametrize("which", ["page", "sidecar"])
def test_a_direct_render_over_a_link_raises_and_writes_nothing(tmp_path, which):
    """The renderer is the write site, so it refuses on its own — not only through the refresh."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    victim = tmp_path / "victim.txt"
    victim.write_text("PRECIOUS\n", encoding="utf-8")
    page = base / NAMED
    _link(page if which == "page" else pi.inventory_path(page), victim)
    before = _everything(tmp_path)

    with pytest.raises(guard.RefusedWrite) as exc:
        gd.render_dashboard("acme", tmp_path, stubs=False, scope="campaign", campaign=SLUG)
    assert "symlink" in str(exc.value)
    assert _everything(tmp_path) == before, "nothing at all was written"


def test_write_inventory_refuses_a_linked_sidecar_and_a_linked_page(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<html></html>", encoding="utf-8")
    victim = tmp_path / "victim.txt"
    victim.write_text("PRECIOUS\n", encoding="utf-8")
    inv = pi.inventory_path(page)
    inv.symlink_to(victim)
    with pytest.raises(guard.RefusedWrite):
        pi.write_inventory(page, (tmp_path, []), scope="all")
    assert victim.read_text(encoding="utf-8") == "PRECIOUS\n"
    inv.unlink()
    real = tmp_path / "real.html"
    real.write_text("<html></html>", encoding="utf-8")
    page.unlink()
    page.symlink_to(real)
    with pytest.raises(guard.RefusedWrite):
        pi.write_inventory(page, (tmp_path, []), scope="all")
    assert not inv.exists()


def test_a_sidecar_linked_to_another_tenants_sidecar_is_stale_and_never_read(tmp_path, capsys):
    """S4. The walk read `*.inputs.json` through links, and `_own_page` passed because both
    sidecars name the rollup, so tenant A's check printed tenant B's recorded paths.
    Catches: removing the link test from `pages.classify_sidecar` / `page_inputs_io.load_inventory`."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _other_tenant(tmp_path)
    _link(
        pi.inventory_path(base / ROLLUP), tmp_path / "other" / "email_campaign_status.inputs.json"
    )

    rc, out, err = _run(tmp_path, capsys, "--check-fresh")
    text = out + err
    assert rc == 1
    assert f"{ROLLUP}: STALE" in text and "is a symlink" in text
    assert "bigcustomer" not in text and "dossier-2026" not in text
    rc, out, err = _run(tmp_path, capsys, "--check-fresh", "--scope", "all")
    assert rc == 1 and "bigcustomer" not in out + err


def test_the_loader_alone_refuses_a_linked_sidecar(tmp_path):
    from gtm_core.page_inputs_io import load_inventory

    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "x.inputs.json"
    link.symlink_to(real)
    rec, why = load_inventory(link)
    assert rec is None and "symlink" in why


def test_the_profile_folder_itself_may_be_a_link(tmp_path, capsys):
    """CLAUDE.md: a `content/<tenant>` tree backed by external storage is a legitimate symlink.
    Only the page and the sidecar LEAVES are refused."""
    real = tmp_path / "external"
    real.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    base = _three_pages(real, fetched=_days_ago(1))
    (store / "acme").symlink_to(base)
    assert _run(store, capsys, "--refresh-all")[0] == 0


# --- slug traversal ----------------------------------------------------------------------------


def _evil_manifest(base, slug="../../../escaped-by-slug"):
    (base / "plans" / "campaigns" / "evil.campaign.toml").write_text(
        f'slug = "{slug}"\ntitle = "x"\nstatus = "active"\n'
        'roster_globs = ["mine-20260904-hubspot.csv"]\n[targets]\nprospects = 1\n',
        encoding="utf-8",
    )


def test_a_manifest_slug_that_climbs_out_is_refused_by_the_render(tmp_path, capsys):
    """Catches: removing `_safe_segment` from `render.page_path`."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _evil_manifest(base)
    before = _everything(tmp_path)

    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, capsys, "--scope", "campaign", "--campaign", "../../../escaped-by-slug")
    assert "unsafe campaign slug" in str(exc.value)
    assert _everything(tmp_path) == before, "no file and no directory was created anywhere"


def test_a_slug_that_climbs_out_is_refused_by_the_check_too(tmp_path, capsys):
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _evil_manifest(base)
    before = _everything(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run(
            tmp_path,
            capsys,
            "--check-fresh",
            "--scope",
            "campaign",
            "--campaign",
            "../../../escaped-by-slug",
        )
    assert "unsafe campaign slug" in str(exc.value)
    assert _everything(tmp_path) == before


@pytest.mark.parametrize("slug", ["../x", "a/b", "..", "a\\b", "$HOME", "%X%"])
def test_every_unsafe_slug_shape_is_refused_at_the_one_place_page_paths_are_built(tmp_path, slug):
    from gtm_core.email_campaign_dashboard.render import page_path
    from gtm_core.email_campaign_dashboard.scope import resolve

    sc = resolve("campaign", slug, [{"slug": slug, "status": "active"}])
    with pytest.raises(SystemExit) as exc:
        page_path("acme", tmp_path, sc)
    assert "unsafe campaign slug" in str(exc.value)


def test_refresh_all_with_a_climbing_manifest_slug_writes_nothing_outside(tmp_path, capsys):
    """The open page does not put a slug in a path, so it still renders; the refresh must not
    follow a manifest's slug anywhere."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _evil_manifest(base)
    outside_before = {k: v for k, v in _everything(tmp_path).items() if not k.startswith("acme/")}
    _run(tmp_path, capsys, "--refresh-all")
    outside_after = {k: v for k, v in _everything(tmp_path).items() if not k.startswith("acme/")}
    assert outside_after == outside_before


# --- M3: one OSError does not abort the others -------------------------------------------------


def test_a_recorded_input_that_became_a_directory_convicts_by_name_and_never_raises(tmp_path):
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    cells = base / "prospects" / "sequences" / "cells.toml"
    cells.unlink()
    cells.mkdir()

    rep = check.check_all_pages("acme", tmp_path)  # must not raise
    assert rep.ok is False and len(rep.reports) == 3, "every page still got a verdict"
    text = rep.explain()
    assert "could not be read" in text and "prospects/sequences/cells.toml" in text
    assert "IsADirectoryError" in text


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a chmod 000 file")
def test_an_unreadable_recorded_input_convicts_by_name_and_never_raises(tmp_path):
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    cells = base / "prospects" / "sequences" / "cells.toml"
    cells.chmod(0)
    try:
        rep = check.check_all_pages("acme", tmp_path)
    finally:
        cells.chmod(0o644)
    assert rep.ok is False and len(rep.reports) == 3
    assert "PermissionError" in rep.explain()


def test_one_pages_overlong_recorded_path_does_not_decide_for_the_others(tmp_path):
    """Only the open page's sidecar carries the 300-byte row: it is stale and named, the rollup
    and the named page are judged on their merits."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    inv = pi.inventory_path(base / OPEN)
    rec = json.loads(inv.read_text(encoding="utf-8"))
    rec["inputs"].append({"path": "x" * 300 + "/y.csv", "sha256": "0" * 64, "bytes": 1})
    inv.write_text(json.dumps(rec), encoding="utf-8")

    text = check.check_all_pages("acme", tmp_path).explain()  # must not raise
    assert f"{ROLLUP}: fresh" in text and f"{NAMED}: fresh" in text
    assert f"{OPEN}: STALE" in text and "could not be read" in text


def test_a_refresh_that_cannot_read_an_input_names_each_page_and_never_raises(
    tmp_path, capsys, monkeypatch
):
    """The failure sits where the red team saw it: after the model is built, while the new
    inventory is being digested. Nothing is written for ANY page — the page and its sidecar are
    still the consistent pair they were."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _edit_a_shared_input(base)
    before = _tree(base)
    real = pi.digest

    def flaky(path):
        if path.name == "cells.toml":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(pi, "digest", flaky)
    rc, _out, err = _run(tmp_path, capsys, "--refresh-all")  # must not raise
    assert rc == 1
    for page in (ROLLUP, OPEN, NAMED):
        assert f"FAILED to re-render {page}" in err
    after = _tree(base)
    for name in before:
        if name.endswith((".html", ".inputs.json")):
            assert after[name] == before[name], f"{name} was left half-written"


# --- the pair is written together, or the old pair stays --------------------------------------


def test_the_sidecar_digest_is_taken_from_the_bytes_that_were_written(tmp_path):
    """The record is built BEFORE anything is written, so the page digest cannot come from the
    file on disk; it must still be the digest of exactly what lands there."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    for page in (ROLLUP, OPEN, NAMED):
        rec = json.loads(pi.inventory_path(base / page).read_text(encoding="utf-8"))
        assert rec["page_sha256"] == pi.digest(base / page)


def test_an_inventory_edit_is_still_reported_after_the_rewrite(tmp_path, capsys):
    """Positive control for the write path: a hand edit made AFTER a refresh convicts."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    (base / NAMED).write_text("<html>hand edit</html>", encoding="utf-8")
    _edit_sidecar(base, OPEN)  # no-op rewrite of another sidecar
    rc, _out, err = _run(tmp_path, capsys, "--check-fresh")
    assert rc == 1 and f"{NAMED}: STALE — the page itself was edited" in err


def test_a_plain_render_over_a_linked_page_aborts_by_name_with_exit_one(tmp_path, capsys):
    """Not a traceback: the CLI reports the refusal like any other abort.
    Catches: removing the `RefusedWrite` handler in `cli._cli`."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    victim = tmp_path / "victim.txt"
    victim.write_text("PRECIOUS\n", encoding="utf-8")
    _link(base / ROLLUP, victim)
    rc, _out, err = _run(tmp_path, capsys, "--scope", "all")
    assert rc == 1 and f"ABORTED: refused: {ROLLUP} — {LINK_FIX}. Nothing was written." in err
    assert victim.read_text(encoding="utf-8") == "PRECIOUS\n"


def test_a_dangling_sidecar_link_is_named_as_a_link_by_the_single_page_check(tmp_path, capsys):
    """A link whose target is gone is not "no inventory": it is a link, and says so (the check
    must look for the LINK, not for what it points at).
    Catches: `os.path.lexists` -> `Path.exists` in `pages.classify_page`."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    sidecar = pi.inventory_path(base / OPEN)
    sidecar.unlink()
    sidecar.symlink_to(tmp_path / "nowhere.json")
    rc, _out, err = _run(tmp_path, capsys, "--check-fresh", "--scope", "open")
    assert rc == 1 and f"{OPEN}: STALE — its campaign-open.inputs.json is a symlink" in err


def test_a_failed_page_write_keeps_the_previous_pair(tmp_path, monkeypatch):
    """The page differs from the old one (so a rollback is observable), the SIDECAR write fails, and
    the old page must be back, byte for byte, with no backup left behind.
    Catches: removing `os.replace(backup, out)` in `freshness.write_page`."""
    from gtm_core.email_campaign_dashboard import render

    base = _three_pages(tmp_path, fetched=_days_ago(1))
    page = base / NAMED
    old_page, old_inv = page.read_bytes(), pi.inventory_path(page).read_bytes()
    real = guard.atomic_write_text

    def fail_on_sidecar(path, text, **kw):
        if str(path).endswith(".inputs.json"):
            raise OSError(28, "No space left on device")
        return real(path, text, **kw)

    monkeypatch.setattr(render, "render_html", lambda model: "<html>a different page</html>")
    monkeypatch.setattr(guard, "atomic_write_text", fail_on_sidecar)
    with pytest.raises(OSError):
        gd.render_dashboard("acme", tmp_path, stubs=False, scope="campaign", campaign=SLUG)
    assert page.read_bytes() == old_page
    assert pi.inventory_path(page).read_bytes() == old_inv
    assert [p.name for p in base.iterdir() if p.name.startswith(".")] == []
