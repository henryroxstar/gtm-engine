"""Guards for the cross-tenant checker — the gate that keeps a profile bundle portable.

Two properties, and the second matters as much as the first: it catches the reference
shapes that actually confused a recipient on 2026-09-08, and it stays quiet on the
self-references and placeholders every bundle is full of. A gate that fires on
`profiles/<active>/` would fire on nearly every file in `profiles/`, and a gate that
noisy is one people learn to skip.

Slugs here are invented (`alpha`, `beta`, `gamma`). The module under test is listed as
SELF_REFERENTIAL and so is not scanned, but this file is not exempt from the point of the
rule — a test for a tenant-isolation gate should not need real tenants to make its case.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "cross_tenant_check", ROOT / "tests/lint/cross_tenant_check.py"
)
ctc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctc)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A fake repo with three tenants, wired into the module's roots."""
    profiles, content = tmp_path / "profiles", tmp_path / "content"
    for slug in ("alpha", "beta"):
        (profiles / slug / "knowledge").mkdir(parents=True)
    (profiles / "_template").mkdir(parents=True)
    (content / "gamma").mkdir(parents=True)
    (content / "alpha").mkdir(parents=True)

    monkeypatch.setattr(ctc, "ROOT", tmp_path)
    monkeypatch.setattr(ctc, "PROFILES", profiles)
    monkeypatch.setattr(ctc, "CONTENT", content)
    monkeypatch.setattr(ctc, "ALLOWLIST", tmp_path / "allow.txt")
    return tmp_path


def _write(tree: Path, rel: str, body: str) -> None:
    p = tree / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _run(tree: Path) -> int:
    return ctc.main([])


def test_tenants_are_derived_not_typed(tree):
    # Union of both trees; `_template` and dotfiles are not tenants.
    assert ctc.tenants() == {"alpha", "beta", "gamma"}


# --- the reference shapes that actually confused a recipient -------------------------- #


@pytest.mark.parametrize(
    "leak",
    [
        "# see profiles/beta/knowledge/syften-filters.json for the other partition",
        "# mirrored at content/gamma/probe-2026-07-26.md",
        "# The beta kit points at a DIFFERENT live Soul.",
        "> so a Beta match can never reach this dashboard",  # case-insensitive
    ],
)
def test_foreign_reference_is_caught(tree, leak):
    _write(tree, "profiles/alpha/knowledge/BRAND.toml", leak)
    assert _run(tree) == 1


def test_every_occurrence_is_reported_not_just_the_first(tree):
    # The fix is per ANNOTATION. A lint that reveals line 2 only after you fix line 1
    # turns one triage into N rounds — that was a real defect in the first draft.
    _write(tree, "profiles/alpha/PROFILE.md", "# beta here\n# and beta again\n# beta once more\n")
    findings = ctc.scan(
        tree / "profiles/alpha/PROFILE.md",
        "alpha",
        (tree / "profiles/alpha/PROFILE.md").read_text(),
        ctc.tenants(),
        set(),
    )
    assert [f[1] for f in findings] == [1, 2, 3]


# --- and the things it must stay quiet about ------------------------------------------ #


@pytest.mark.parametrize(
    "benign",
    [
        "output_folder: content/alpha/",  # a bundle naming ITSELF is the common case
        "# resolve via profiles/<active>/knowledge/voice.md",  # placeholder
        "# writes under content/<profile>/costs.jsonl",  # placeholder
        "# the alphabet of shot roles",  # `alpha` inside a longer word — word-bounded
        "# betamethasone is not a tenant",  # `beta` as a prefix
    ],
)
def test_benign_reference_is_not_caught(tree, benign):
    _write(tree, "profiles/alpha/knowledge/voice.md", benign)
    assert _run(tree) == 0


def test_template_may_not_name_a_real_tenant(tree):
    # `_template` is not a tenant, but it IS an owner — it ships in the public carve, so a
    # real slug reaching it is the worst version of this leak.
    _write(tree, "profiles/_template/PROFILE.md", "# copied from profiles/beta/PROFILE.md")
    assert _run(tree) == 1


def test_file_directly_under_profiles_owns_no_tenant(tree):
    # profiles/README.md documents the system and cites tenants as worked examples; it is
    # not part of any bundle, so it is out of scope by construction rather than by rule.
    _write(tree, "profiles/README.md", "# worked example: profiles/beta/")
    assert _run(tree) == 0


# --- the allowlist -------------------------------------------------------------------- #


def test_allowlist_suppresses_by_file_and_slug(tree):
    _write(tree, "profiles/alpha/knowledge/platform.md", "# alpha runs on beta")
    assert _run(tree) == 1
    _write(
        tree,
        "allow.txt",
        "# beta is alpha's platform vendor\nprofiles/alpha/knowledge/platform.md:beta\n",
    )
    assert _run(tree) == 0


def test_allowlist_does_not_leak_to_another_file(tree):
    _write(tree, "allow.txt", "profiles/alpha/knowledge/platform.md:beta\n")
    _write(tree, "profiles/alpha/knowledge/other.md", "# beta again, elsewhere")
    assert _run(tree) == 1


# --- the noise filter ----------------------------------------------------------------- #


def test_non_distinctive_slug_keeps_the_path_rule_only(tree, monkeypatch):
    # A slug that is also an ordinary word must not fire on prose, but `content/<slug>/`
    # is unambiguous and still does. Losing BOTH rules would be the silent-hole version.
    (tree / "content" / "personal").mkdir(parents=True)
    _write(tree, "profiles/alpha/knowledge/voice.md", "# keep it personal and direct\n")
    assert _run(tree) == 0
    _write(tree, "profiles/alpha/knowledge/voice.md", "# staged under content/personal/\n")
    assert _run(tree) == 1


# --- the regression guard ------------------------------------------------------------- #


def test_the_real_tree_is_clean():
    """The live repo passes. This is what makes the allowlist a ratchet: a new
    cross-tenant reference fails here, in the suite, not at hand-over time."""
    assert ctc.main([]) == 0
