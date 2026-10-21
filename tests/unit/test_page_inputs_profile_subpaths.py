"""F5 — ``PROFILE_FILES`` may name a SUB-PATH, and one helper guards both halves.

WHY THIS EXISTS (2026-09-30). ``PROFILE_FILES`` held exactly one entry, ``PROFILE.md``, so both
``write_inventory`` and ``verify_inventory`` guarded it with a bare ``_safe_segment`` — which
refuses a ``/`` by design. The two files the assessment found the page reading and not tracking
(``knowledge/role-vocabulary.toml`` and ``knowledge/BRAND.toml``) are sub-paths, so the first
one added would have RAISED out of ``_verify_profile_inputs`` (page_inputs.py:303) and taken
the whole ``--check-fresh`` run down with it — including every other page in the same run.

A sub-path is a path with MORE segments, never a weaker guard: every segment still passes
``_safe_segment`` and the joined result still passes ``_confine``. What changes is the failure
MODE — an entry that cannot be confined makes its page stale and named, instead of raising.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import page_inputs as pi

#: Each of these must be refused by the shared helper. ``knowledge/../../etc`` is the one that
#: matters most: it is confinable segment-by-segment only if you never look at the join, and
#: `..` is a segment `_safe_segment` refuses — so it fails twice, deliberately.
UNSAFE = [
    "../x",
    "/etc/passwd",
    "..",
    "",
    "knowledge/../../etc/passwd",
    "knowledge/\x00BRAND.toml",
    "knowledge\\BRAND.toml",
]
SAFE = ["PROFILE.md", "knowledge/BRAND.toml", "knowledge/role-vocabulary.toml"]


def _seed(tmp_path, monkeypatch, profile="acme"):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    root = tmp_path / "content" / profile
    root.mkdir(parents=True)
    (root / "history.jsonl").write_text('{"event": "seed"}\n', encoding="utf-8")
    pdir = tmp_path / "profiles" / profile
    (pdir / "knowledge").mkdir(parents=True)
    (pdir / "PROFILE.md").write_text("target_markets: [Singapore]\n", encoding="utf-8")
    (pdir / "knowledge" / "BRAND.toml").write_text("[palette]\nprimary = '#123456'\n", "utf-8")
    (pdir / "knowledge" / "role-vocabulary.toml").write_text("[[seat]]\nid = 'cto'\n", "utf-8")
    page = root / "page.html"
    page.write_text("<html></html>", encoding="utf-8")
    return page, root, pdir


def _write(page, root, **kw):
    return pi.write_inventory(page, (root, ["history.jsonl"]), scope="all", **kw)


# --- the helper itself -----------------------------------------------------------------------


@pytest.mark.parametrize("name", SAFE)
def test_a_safe_sub_path_survives_the_helper(name):
    assert pi._profile_rel(name) == name


@pytest.mark.parametrize("name", UNSAFE)
def test_the_helper_refuses_and_returns_none_rather_than_raising(name):
    """T32's core. Returning None is the whole point: raising here is what would abort a check
    over 1,583 inputs because ONE recorded row was malformed (§R5 — untrusted input makes its
    own page stale, it does not get a veto over the others)."""
    assert pi._profile_rel(name) is None


# --- write and verify share it (T32) ---------------------------------------------------------


@pytest.mark.parametrize("name", UNSAFE)
def test_an_unsafe_entry_is_recorded_refused_and_never_dereferenced(tmp_path, monkeypatch, name):
    """Refused at write AND reported at verify, never raised at either — and never silently
    dropped, which would leave the page claiming to track a file nobody checks. The entry is
    recorded with a null digest precisely so the later check can convict it."""
    page, root, pdir = _seed(tmp_path, monkeypatch)
    inv = _write(page, root, profile="acme", profile_files=(name,))  # must not raise
    recorded = json.loads(inv.read_text(encoding="utf-8"))["profile_inputs"]
    assert [r["sha256"] for r in recorded] == [None], "an unsafe name must not be hashed"

    rep = pi.verify_inventory(page, root, profile="acme")  # must not raise
    assert rep.ok is False
    assert rep.refused and all(r.startswith("profile:") for r in rep.refused)
    assert "escapes root" in rep.explain()


def test_one_refused_entry_does_not_hide_the_others(tmp_path, monkeypatch):
    """A refusal is not a short-circuit. The two legitimate files beside it must still be
    digested, or a crafted row would be a way to switch tracking OFF for its siblings."""
    page, root, pdir = _seed(tmp_path, monkeypatch)
    _write(page, root, profile="acme", profile_files=("knowledge/BRAND.toml", "../x", "PROFILE.md"))
    (pdir / "PROFILE.md").write_text("target_markets: [United States]\n", encoding="utf-8")
    rep = pi.verify_inventory(page, root, profile="acme")
    assert rep.refused == ["profile:../x"]
    assert rep.changed == ["profile:PROFILE.md"]


# --- the positive control (T33) --------------------------------------------------------------


def test_a_recorded_sub_path_is_tracked_through_its_whole_life(tmp_path, monkeypatch):
    """T33 — changed, missing, and appeared-after-the-render, on a SUB-PATH entry. Without
    this the helper could confine correctly and never actually hash anything."""
    page, root, pdir = _seed(tmp_path, monkeypatch)
    brand = pdir / "knowledge" / "BRAND.toml"
    files = ("knowledge/BRAND.toml",)

    _write(page, root, profile="acme", profile_files=files)
    assert pi.verify_inventory(page, root, profile="acme").ok

    brand.write_text("[palette]\nprimary = '#654321'\n", encoding="utf-8")
    assert pi.verify_inventory(page, root, profile="acme").changed == [
        "profile:knowledge/BRAND.toml"
    ]

    brand.unlink()
    assert pi.verify_inventory(page, root, profile="acme").missing == [
        "profile:knowledge/BRAND.toml"
    ]

    # Recorded while ABSENT (a tenant mid-onboarding), then it appears: `new`, not untracked.
    _write(page, root, profile="acme", profile_files=files)
    assert pi.verify_inventory(page, root, profile="acme").ok
    brand.write_text("[palette]\nprimary = '#111111'\n", encoding="utf-8")
    assert pi.verify_inventory(page, root, profile="acme").new == ["profile:knowledge/BRAND.toml"]


# --- old inventories on disk (T34) -----------------------------------------------------------


def test_an_inventory_predating_sub_paths_verifies_and_says_what_it_lacks(tmp_path, monkeypatch):
    """T34 — every ``.inputs.json`` already on a live tenant's disk was written when
    ``PROFILE_FILES`` was ``("PROFILE.md",)``. It must verify without crashing, stay fresh on
    the files it DID record, and be silent about the two it never could have."""
    page, root, pdir = _seed(tmp_path, monkeypatch)
    _write(page, root, profile="acme", profile_files=("PROFILE.md",))  # the old shape, verbatim
    rep = pi.verify_inventory(page, root, profile="acme")
    assert rep.ok, rep.explain()
    assert rep.refused == []

    # It tracks what it recorded and nothing more: editing a file it never listed cannot make
    # it stale, which is why `--refresh-all` (not this check) is the remedy for an old page.
    (pdir / "knowledge" / "BRAND.toml").write_text("[palette]\nprimary = '#abcabc'\n", "utf-8")
    assert pi.verify_inventory(page, root, profile="acme").ok
    (pdir / "PROFILE.md").write_text("target_markets: [Japan]\n", encoding="utf-8")
    assert pi.verify_inventory(page, root, profile="acme").ok is False


def test_an_inventory_with_a_wrong_typed_profile_row_is_stale_not_a_crash(tmp_path, monkeypatch):
    """§4.4 — a producer's schema drift, or a hand edit. An int where a path belongs, and a
    row that is not a dict at all."""
    page, root, _ = _seed(tmp_path, monkeypatch)
    _write(page, root, profile="acme", profile_files=("PROFILE.md",))
    inv_path = pi.inventory_path(page)
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    inv["profile_inputs"] = [{"path": 12345, "sha256": None}, ["not", "a", "dict"], None]
    inv_path.write_text(json.dumps(inv), encoding="utf-8")

    rep = pi.verify_inventory(page, root, profile="acme")
    assert rep.ok is False
    assert len(rep.refused) == 3
