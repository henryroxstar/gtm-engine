"""PS20 T1.9 — every file the render reads or name-matches is in the page's inventory."""

import builtins
import glob as globmod
import json
from pathlib import Path

from gtm_core import email_campaign_dashboard as gd
from gtm_core import page_inputs
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard.config import NAME_GLOBS, PROFILE_FILES, input_globs
from tests.test_email_campaign_dashboard import _seed


def reads_during(fn, monkeypatch) -> tuple[list[Path], set[Path]]:
    """``(seen, via_glob)`` — every file OPENED for reading or returned by a glob name
    match during ``fn``, plus the SUBSET that came through a glob call specifically
    (``glob.glob``/``Path.glob``). Tracking the subset separately is what lets a test prove
    the glob hooks are actually wired and firing, rather than merely being dead weight that
    ``open`` happens to make redundant on every real render.
    """
    seen: list[Path] = []
    via_glob: set[Path] = set()
    r_open, r_popen, r_glob, r_pglob = builtins.open, Path.open, globmod.glob, Path.glob

    def o(f, mode="r", *a, **k):
        if not any(c in mode for c in "wax"):
            seen.append(Path(f))
        return r_open(f, mode, *a, **k)

    def po(p, mode="r", *a, **k):
        if not any(c in mode for c in "wax"):
            seen.append(Path(p))
        return r_popen(p, mode, *a, **k)

    def g(pat, *a, **k):
        hits = r_glob(pat, *a, **k)
        for h in hits:
            seen.append(Path(h))
            via_glob.add(Path(h))
        return hits

    def pg(p, pat, *a, **k):
        hits = list(r_pglob(p, pat, *a, **k))
        seen.extend(hits)
        via_glob.update(hits)
        return iter(hits)

    for obj, name, fake in (
        (builtins, "open", o),
        (Path, "open", po),
        (globmod, "glob", g),
        (Path, "glob", pg),
    ):
        monkeypatch.setattr(obj, name, fake)
    fn()
    return seen, via_glob


def not_inventoried(seen, root: Path, globs, name_globs=()) -> list[Path]:
    """Reads under ``root`` that neither a digest glob nor a NAME glob covers. ``name_globs`` are
    the paths the page only MATCHES BY NAME (never opens) — a file the render saw through a glob
    and that only ``name_globs`` accounts for is covered, but only for what a name can tell."""
    covered = {Path(x).resolve() for x in page_inputs._resolve(root, [*globs, *name_globs])}
    return sorted(
        {
            p
            for p in seen
            if root in p.resolve().parents and p.is_file() and p.resolve() not in covered
        }
    )


def not_profile_covered(seen, profiles_root: Path, profile: str, names) -> list[Path]:
    """The ``profile_files`` analogue of :func:`not_inventoried`: profile-rooted reads (a
    DIFFERENT root than ``root`` above) not named in ``config.PROFILE_FILES``."""
    base = (profiles_root / profile).resolve()
    covered = {(base / n).resolve() for n in names}
    return sorted(
        {
            p
            for p in seen
            if base in p.resolve().parents and p.is_file() and p.resolve() not in covered
        }
    )


def _seed_all_inputs(tmp_path, profile="acme"):
    """_seed plus the inputs the inventory used to miss."""
    profile = _seed(tmp_path, profile)
    base = pc._prospects_dir(profile, tmp_path)
    (tmp_path / profile / "outcomes.jsonl").write_text(
        '{"event": "reply"}\n'
    )  # path per read_outcomes
    (tmp_path / profile / "preflight").mkdir(parents=True, exist_ok=True)
    (tmp_path / profile / "preflight" / "latest.json").write_text("{}")
    ev = base / "evals"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "retarget-queue-2026-09-22.jsonl").write_text("")  # glob per roster.py
    (ev / "labeler-2026-09-22-cold.html").write_text("<html></html>")
    (ev / "sheet-2026-09-22.md").write_text("`send_it:` ___\n")
    (base / "sequences" / ".pool" / "needs-verification.csv").write_text("email\n")
    # The hold sheet the lede links (name per health.review_sheet). Both siblings:
    # `review_sheet` prefers the `.html` for its link but still reads the `.csv` for a
    # row count.
    (ev / "hold-2026-09-22.csv").write_text("email\n")
    (ev / "hold-2026-09-22.html").write_text("<html></html>")
    # `prospect_readiness.load_readiness` STATS this (mtime/size via `fingerprints()`),
    # never opens it — path per prospect_readiness.suppression_ledger / input_paths.
    (base / "sequences" / ".pool" / "suppression.csv").write_text("email\n")
    # F5 (2026-09-30) — the three the 2026-09-30 assessment found the model OPENING and the
    # inventory not tracking. The enrichment queue is content-rooted
    # (`prospects_backlog.enrichment_queue_path`, read by `prospects_dashboard.build_status`);
    # the other two are PROFILE-rooted, so they belong to `PROFILE_FILES` and are seeded by
    # `_pin_profiles_root` below, not here.
    (base / "sequences" / ".pool" / "enrichment-queue.csv").write_text(
        "account_id,company,domain\n", encoding="utf-8"
    )
    # Item 2 (2026-09-30) — the dossier lookup. `build_status` runs `account_has_dossier` for every
    # Tier-A account, which GLOBS the account's folder for the dossier patterns and never opens
    # the hit, so the read is a name and only a name. A fictional account (`gtm_core.fictionalize`
    # shape: two-word company, `.example` domain) holding one.
    (base / "latest.json").write_text(
        json.dumps(
            {
                "kind": "prospects",
                "profile": profile,
                "items": [
                    {
                        "company": "Zebra Quill Robotics",
                        "domain": "zebraquill.example",
                        "tier": "A",
                        "score": 9,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dossier = tmp_path / profile / "accounts" / "zebra-quill-robotics"
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "dossier-zebra-quill-robotics-2026-09-22.md").write_text("# research\n")
    return profile


def _pin_profiles_root(tmp_path, monkeypatch, profile="acme", markets="Singapore"):
    """A profiles root under tmp, holding a real PROFILE.md — so `market_split` (reached
    through `build_model`) actually reads one during the render, instead of silently
    missing the real repo's ``profiles/<profile>/`` and leaving the market gate off."""
    profiles_root = tmp_path / "profiles-root"
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles_root))
    (profiles_root / profile / "knowledge").mkdir(parents=True, exist_ok=True)
    (profiles_root / profile / "PROFILE.md").write_text(
        f"target_markets: [{markets}]\n", encoding="utf-8"
    )
    # F5 — the two PROFILE-rooted files the render opens: `config.resolve_seat_coverage` reads
    # the role vocabulary (company rung, no product, no overlay) and `health.resolve_brand_palette`
    # reads the brand kit. Until these existed in the fixture, `not_profile_covered` could not see
    # them: it skips a path that is not a file, so the two were invisible rather than passing.
    (profiles_root / profile / "knowledge" / "role-vocabulary.toml").write_text(
        'default_persona = "other"\nsegments = ["unspecified"]\n'
        '[[persona]]\nname = "other"\ncues = ["other"]\n'
        '[[seat]]\nname = "cto"\npersonas = ["other"]\nstakes = ["delivery"]\n',
        encoding="utf-8",
    )
    (profiles_root / profile / "knowledge" / "BRAND.toml").write_text(
        '[palette]\nprimary = "#123456"\n', encoding="utf-8"
    )
    return profiles_root


def test_every_read_is_inventoried(tmp_path, monkeypatch):
    profiles_root = _pin_profiles_root(tmp_path, monkeypatch)
    profile = _seed_all_inputs(tmp_path)
    root, globs = input_globs(profile, tmp_path)
    seen, via_glob = reads_during(
        lambda: gd.render_html(gd.build_model(profile, tmp_path)), monkeypatch
    )

    # Specific, known reads — not just "the spy saw *something*" (which a spy wired to the
    # wrong hooks, or a render that reads nothing at all, would also satisfy).
    seen_names = {p.name for p in seen}
    for expected in ("sequence-stats.json", "outcomes.jsonl", "PROFILE.md"):
        assert expected in seen_names, f"positive control: the spy never saw {expected} read"

    # The glob hooks specifically: `open`/`Path.open` catch most reads on their own (e.g.
    # `roster.judge_queue` globs for the retarget queue AND THEN opens it), which can make a
    # broken `glob`/`Path.glob` monkeypatch invisible — nothing above would fail even if
    # neither glob hook ever fired. `evals.iterdir()` (the labeler/hold-sheet lookups) is
    # NOT a glob and must not satisfy this either, so the retarget-queue file — reached via
    # `roster.judge_queue`'s `base.glob("retarget-queue-*.jsonl")` — is the positive control.
    via_glob_names = {p.name for p in via_glob}
    assert any(n.startswith("retarget-queue-") for n in via_glob_names), (
        "positive control: no known input was seen via a glob name-match — the glob spy "
        "(glob.glob / Path.glob) may not be firing at all"
    )

    # Positive control for the NAME half (item 2): the render really did match a dossier by
    # name, and WITHOUT `NAME_GLOBS` that read is uncovered. A spy whose new argument changed
    # nothing would pass the assertion below with the argument deleted.
    dossier_seen = {p for p in via_glob if p.name.startswith("dossier-zebra-quill")}
    assert dossier_seen, "positive control: the dossier lookup never globbed the account folder"
    assert {p.name for p in not_inventoried(seen, root, globs)} == {p.name for p in dossier_seen}

    assert not_inventoried(seen, root, globs, NAME_GLOBS) == []
    assert not_profile_covered(seen, profiles_root, profile, PROFILE_FILES) == []


def test_known_inputs_are_inventoried(tmp_path):
    """Some inputs are matched by NAME only — `iterdir` + a filename regex (the labeler
    page, the hold sheet's `.html`), or by `Path.stat()` (`suppression.csv`, PS20 review
    round 2) — never by `open`/`read_text`/`glob`. The spy in `test_every_read_is_inventoried`
    cannot see any of those change or vanish from INPUT_GLOBS, because it only instruments
    `open`/`glob`. Checked directly here instead: every file `_seed_all_inputs` creates must
    resolve from the CONFIGURED globs, independent of whether the render actually opens it.
    """
    profile = _seed_all_inputs(tmp_path)
    root, globs = input_globs(profile, tmp_path)
    resolved = set(page_inputs._resolve(root, globs))
    known = [
        "outcomes.jsonl",
        "prospects/sequences/.pool/enrichment-queue.csv",
        "preflight/latest.json",
        "prospects/evals/retarget-queue-2026-09-22.jsonl",
        "prospects/evals/labeler-2026-09-22-cold.html",
        "prospects/evals/sheet-2026-09-22.md",
        "prospects/sequences/.pool/needs-verification.csv",
        "prospects/evals/hold-2026-09-22.csv",
        "prospects/evals/hold-2026-09-22.html",
        "prospects/sequences/.pool/suppression.csv",
    ]
    for rel in known:
        p = root / rel
        assert p.is_file(), f"fixture does not create {rel}"
        assert p in resolved, f"{rel} matches no glob from config.INPUT_GLOBS — add one"
    # The dossier is the opposite: a NAME read, so it must resolve from NAME_GLOBS and must NOT
    # be a digest input (hashing a dossier's bytes would convict the page for an edit that
    # cannot change a figure on it).
    dossier = root / "accounts/zebra-quill-robotics/dossier-zebra-quill-robotics-2026-09-22.md"
    assert dossier.is_file() and dossier in set(page_inputs._resolve(root, list(NAME_GLOBS)))
    assert dossier not in resolved


def test_checker_catches_an_unlisted_read(tmp_path):
    root = tmp_path / "acme"
    stray = root / "prospects" / "unlisted.json"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("{}")
    assert not_inventoried([stray], root, ["history.jsonl"]) == [stray]


def test_star_does_not_cross_a_directory(tmp_path):
    """The inventory's own glob semantics: sequences/*.csv must NOT cover .pool/x.csv."""
    root = tmp_path / "acme"
    hidden = root / "prospects" / "sequences" / ".pool" / "needs-verification.csv"
    hidden.parent.mkdir(parents=True, exist_ok=True)
    hidden.write_text("email\n")
    assert not_inventoried([hidden], root, ["prospects/sequences/*.csv"]) == [hidden]


def test_the_profile_rooted_files_are_named_in_profile_files(tmp_path, monkeypatch):
    """T31's profile half. `not_profile_covered` in `test_every_read_is_inventoried` can only
    convict a file that EXISTS — it skips anything `is_file()` says no to — so before the
    fixture created these two they were untracked *and* invisible. Named directly here, the
    same way `test_known_inputs_are_inventoried` names the content-rooted ones.
    """
    profiles_root = _pin_profiles_root(tmp_path, monkeypatch)
    profile = _seed_all_inputs(tmp_path)
    for rel in ("PROFILE.md", "knowledge/role-vocabulary.toml", "knowledge/BRAND.toml"):
        assert (profiles_root / profile / rel).is_file(), f"fixture does not create {rel}"
        assert rel in PROFILE_FILES, f"{rel} is read by the model but not in config.PROFILE_FILES"


def test_both_profile_roots_resolve_to_one_role_vocabulary(tmp_path, monkeypatch):
    """T35 — TWO code paths reach the role vocabulary: `config.resolve_seat_coverage` (what the
    page reads) and `page_inputs._profile_root` (what the inventory hashes). Inventorying a file
    the render does not actually read is a check that convicts the wrong edit, so the
    relationship is asserted: they are ONE file.

    `resolve_seat_coverage` used to prefer a `profiles/` SIBLING of the content root, which
    diverged from the inventoried root whenever GTM_PROFILES_ROOT pointed elsewhere (red-team
    F12). It now takes the inventory's root, so the divergence is gone rather than documented;
    the last assertions below plant the old divergent layout and require the page to ignore it.
    """
    from gtm_core import page_inputs
    from gtm_core.role_vocabulary import vocabulary_path

    profiles_root = _pin_profiles_root(tmp_path, monkeypatch)
    profile = _seed_all_inputs(tmp_path)

    inventoried = page_inputs._profile_root(profile) / "knowledge/role-vocabulary.toml"
    assert inventoried == profiles_root / profile / "knowledge" / "role-vocabulary.toml"

    # The seat-coverage path, with the arguments `model.build_model` actually passes.
    sibling = tmp_path.parent / "profiles"
    assert not sibling.is_dir(), "fixture precondition: no sibling to prefer"
    assert vocabulary_path(profile, profiles_root, None, None) == inventoried

    # THE ONE SHAPE WHERE THEY USED TO DIVERGE: a `profiles/` directory beside the content root
    # while GTM_PROFILES_ROOT points elsewhere. `resolve_seat_coverage` read the sibling's
    # vocabulary while the inventory tracked the other one, so editing the file the page actually
    # read left `--check-fresh` green (red-team F12, repro r07). The inventory's root is the
    # authority (PRD F5), so the page now reads it too and the two are ONE file by construction;
    # the old assertion here (`is not None`) could not fail, because the function always returns
    # a dict.
    sibling_vocab = tmp_path / "profiles" / profile / "knowledge"
    sibling_vocab.mkdir(parents=True, exist_ok=True)
    (sibling_vocab / "role-vocabulary.toml").write_text(
        'default_persona = "other"\nsegments = ["unspecified"]\n'
        '[[persona]]\nname = "other"\ncues = ["other"]\n'
        '[[seat]]\nname = "sibling-only-seat"\npersonas = ["other"]\nstakes = ["delivery"]\n',
        encoding="utf-8",
    )
    from gtm_core import role_vocabulary
    from gtm_core.email_campaign_dashboard.config import resolve_seat_coverage

    role_vocabulary.clear_cache()
    read_by_the_page = resolve_seat_coverage(profile, content_root=tmp_path / profile)
    read_by_the_inventory = resolve_seat_coverage(profile, profiles_root=profiles_root)
    assert "sibling-only-seat" not in read_by_the_page, "the page read a file nothing inventories"
    assert read_by_the_page == read_by_the_inventory
    assert set(read_by_the_page) == {"cto"}


def test_editing_the_inventoried_vocabulary_is_what_moves_the_seat_coverage(tmp_path, monkeypatch):
    """The other half of F12, end to end: change the file the inventory hashes and the page's
    seat coverage changes with it (so the check being stale is TRUE), and a sibling directory's
    file changes neither. Mutation caught: restoring the sibling preference in
    ``config.resolve_seat_coverage``."""
    from gtm_core import role_vocabulary
    from gtm_core.email_campaign_dashboard.config import resolve_seat_coverage

    profiles_root = _pin_profiles_root(tmp_path, monkeypatch)
    profile = _seed_all_inputs(tmp_path)
    content = tmp_path / profile
    sibling = tmp_path / "profiles" / profile / "knowledge"
    sibling.mkdir(parents=True)
    (sibling / "role-vocabulary.toml").write_text(
        'default_persona = "other"\nsegments = ["unspecified"]\n'
        '[[persona]]\nname = "other"\ncues = ["other"]\n'
        '[[seat]]\nname = "ghost"\npersonas = ["other"]\nstakes = ["x"]\n',
        encoding="utf-8",
    )
    role_vocabulary.clear_cache()
    assert set(resolve_seat_coverage(profile, content_root=content)) == {"cto"}
    (profiles_root / profile / "knowledge" / "role-vocabulary.toml").write_text(
        'default_persona = "other"\nsegments = ["unspecified"]\n'
        '[[persona]]\nname = "other"\ncues = ["other"]\n'
        '[[seat]]\nname = "founder"\npersonas = ["other"]\nstakes = ["margin"]\n',
        encoding="utf-8",
    )
    role_vocabulary.clear_cache()
    assert set(resolve_seat_coverage(profile, content_root=content)) == {"founder"}


def test_a_real_profiles_reads_are_all_inventoried(monkeypatch):
    """OPT-IN, READ-ONLY. A real tenant has read shapes no fixture anticipates — that is how the
    dossier lookup was found. Set ``GTM_AUDIT_PROFILE=<profile>`` (and optionally
    ``GTM_AUDIT_CONTENT_ROOT``) to build the model against it and fail on ANY read the inventory
    does not track. Skipped otherwise, so CI never depends on a tenant's data.

    Read-only by construction: it calls ``build_model`` + ``render_html``, never
    ``render_dashboard``/``write_inventory``, and asserts afterwards that nothing under the
    content root was opened for writing.
    """
    import os

    import pytest

    profile = os.environ.get("GTM_AUDIT_PROFILE")
    if not profile:
        pytest.skip("set GTM_AUDIT_PROFILE=<profile> to trace a real profile (read-only)")
    content_root = os.environ.get("GTM_AUDIT_CONTENT_ROOT")
    content_root = Path(content_root) if content_root else None

    root, globs = input_globs(profile, content_root)
    writes: list[Path] = []
    r_open, r_popen = builtins.open, Path.open

    def watch(f, mode="r", *a, **k):
        if any(c in mode for c in "wax+"):
            writes.append(Path(f))
        return r_open(f, mode, *a, **k)

    def pwatch(p, mode="r", *a, **k):
        if any(c in mode for c in "wax+"):
            writes.append(Path(p))
        return r_popen(p, mode, *a, **k)

    # Installed BEFORE `reads_during`, which wraps whatever `open` is current — so this sits
    # underneath the read spy and both see every call.
    monkeypatch.setattr(builtins, "open", watch)
    monkeypatch.setattr(Path, "open", pwatch)
    seen, _ = reads_during(
        lambda: gd.render_html(gd.build_model(profile, content_root)), monkeypatch
    )
    assert not [w for w in writes if root in Path(w).resolve().parents]
    unread = not_inventoried(seen, root, globs, NAME_GLOBS)
    assert unread == [], f"{len(unread)} read(s) the inventory does not track, first: {unread[:5]}"
