"""PS20 T1.9 — every file the render reads or name-matches is in the page's inventory."""

import builtins
import glob as globmod
from pathlib import Path

from gtm_core import email_campaign_dashboard as gd
from gtm_core import page_inputs
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard.config import PROFILE_FILES, input_globs
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


def not_inventoried(seen, root: Path, globs) -> list[Path]:
    covered = {Path(x).resolve() for x in page_inputs._resolve(root, list(globs))}
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
    return profile


def _pin_profiles_root(tmp_path, monkeypatch, profile="acme", markets="Singapore"):
    """A profiles root under tmp, holding a real PROFILE.md — so `market_split` (reached
    through `build_model`) actually reads one during the render, instead of silently
    missing the real repo's ``profiles/<profile>/`` and leaving the market gate off."""
    profiles_root = tmp_path / "profiles-root"
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles_root))
    (profiles_root / profile).mkdir(parents=True, exist_ok=True)
    (profiles_root / profile / "PROFILE.md").write_text(
        f"target_markets: [{markets}]\n", encoding="utf-8"
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

    assert not_inventoried(seen, root, globs) == []
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
