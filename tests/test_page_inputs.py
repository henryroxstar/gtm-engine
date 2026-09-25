"""Direct tests of gtm_core.page_inputs — the digest-inventory mechanism the dashboard's
freshness check is built on. Dashboard-specific coverage lives in
tests/contracts/test_dashboard_reads_are_inventoried.py and
tests/contracts/test_dashboard_freshness.py; this file exercises the module in isolation,
principally its tenant-boundary guarantees (CLAUDE.md: a profile name or a profile-file
name is untrusted input that must never be read back from the inventory JSON, and a
recorded content path must never resolve outside its root) — a mutation-testing pass found
none of these had a pinning test.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import page_inputs as pi


def _seed(tmp_path, *, profile="acme"):
    """A content root with one real input, a profiles root with one real PROFILE.md, and a
    page — everything :func:`write_inventory` needs for a legitimate baseline. Returns
    ``(page, root, profiles_root)``."""
    root = tmp_path / "content" / profile
    root.mkdir(parents=True)
    (root / "history.jsonl").write_text('{"event": "seed"}\n', encoding="utf-8")
    profiles_root = tmp_path / "profiles"
    (profiles_root / profile).mkdir(parents=True)
    (profiles_root / profile / "PROFILE.md").write_text(
        "target_markets: [Singapore]\n", encoding="utf-8"
    )
    page = root / "page.html"
    page.write_text("<html></html>", encoding="utf-8")
    return page, root, profiles_root


def _rewrite_inventory(page, patch: dict) -> None:
    """Load the inventory the last write produced, apply ``patch`` on top, write it back —
    simulating a crafted/corrupted inventory JSON arriving from disk."""
    inv_path = pi.inventory_path(page)
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    inv.update(patch)
    inv_path.write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")


# --- tenant boundary: the profile comes from the CALLER, never the JSON -----------------


def test_verify_uses_the_callers_profile_never_a_forged_json_one(tmp_path, monkeypatch):
    """A mutant that made `verify_inventory` prefer `inv["profile"]` over its own argument
    would pass every other test in this suite — this one is built to catch exactly that."""
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    page, root, profiles_root = _seed(tmp_path, profile="acme")
    (profiles_root / "other-tenant").mkdir(parents=True)
    (profiles_root / "other-tenant" / "PROFILE.md").write_text(
        "target_markets: [United States]\n", encoding="utf-8"
    )
    pi.write_inventory(
        page,
        (root, ["history.jsonl"]),
        scope="all",
        profile="acme",
        profile_files=("PROFILE.md",),
    )
    _rewrite_inventory(page, {"profile": "other-tenant"})  # forged; must be ignored

    # Changing the OTHER tenant's file must not matter — if the code ever preferred the
    # forged JSON `profile`, this alone would already report stale.
    (profiles_root / "other-tenant" / "PROFILE.md").write_text(
        "target_markets: [Germany]\n", encoding="utf-8"
    )
    assert pi.verify_inventory(page, root, profile="acme").ok

    # Changing ACME's own file — the CALLER's real profile — must matter.
    (profiles_root / "acme" / "PROFILE.md").write_text(
        "target_markets: [United States]\n", encoding="utf-8"
    )
    rep = pi.verify_inventory(page, root, profile="acme")
    assert rep.ok is False
    assert "PROFILE.md" in rep.explain()


def test_verify_refuses_a_traversal_profile_inputs_path(tmp_path, monkeypatch):
    """A crafted `profile_inputs` row naming `../other/PROFILE.md` must never be
    dereferenced. `_safe_segment` (this module's own tenant-boundary guard) rejects any
    segment containing a path separator, so this fails closed rather than silently reading
    a sibling tenant's file.
    """
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    page, root, _ = _seed(tmp_path, profile="acme")
    pi.write_inventory(
        page,
        (root, ["history.jsonl"]),
        scope="all",
        profile="acme",
        profile_files=("PROFILE.md",),
    )
    _rewrite_inventory(
        page,
        {
            "profile": "other",
            "profile_inputs": [{"path": "../other/PROFILE.md", "sha256": "0" * 64, "bytes": 1}],
        },
    )
    with pytest.raises(ValueError):
        pi.verify_inventory(page, root, profile="acme")


@pytest.mark.parametrize("bad_profile", ["../x", "/etc", "..", ""])
def test_write_refuses_an_unsafe_profile(tmp_path, monkeypatch, bad_profile):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    page, root, _ = _seed(tmp_path, profile="acme")
    with pytest.raises(ValueError):
        pi.write_inventory(
            page,
            (root, ["history.jsonl"]),
            scope="all",
            profile=bad_profile,
            profile_files=("PROFILE.md",),
        )


@pytest.mark.parametrize("bad_profile", ["../x", "/etc", "..", ""])
def test_verify_refuses_an_unsafe_profile(tmp_path, monkeypatch, bad_profile):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    page, root, _ = _seed(tmp_path, profile="acme")
    pi.write_inventory(
        page,
        (root, ["history.jsonl"]),
        scope="all",
        profile="acme",
        profile_files=("PROFILE.md",),
    )
    with pytest.raises(ValueError):
        pi.verify_inventory(page, root, profile=bad_profile)


def test_write_refuses_an_unsafe_profile_file_name(tmp_path, monkeypatch):
    """A malicious `profile_files` entry — not the profile itself — must also be refused
    at write time, before anything under `resolve_profiles_root()` is touched."""
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    page, root, _ = _seed(tmp_path, profile="acme")
    with pytest.raises(ValueError):
        pi.write_inventory(
            page,
            (root, ["history.jsonl"]),
            scope="all",
            profile="acme",
            profile_files=("../other/PROFILE.md",),
        )


# --- a profile file that appears late, and an inventory older than this feature ---------


def test_a_profile_file_that_appears_after_render_is_flagged(tmp_path, monkeypatch):
    """PROFILE.md did not exist at render time (a tenant mid-onboarding). It is still
    recorded, with a null digest, so its later appearance is caught as `new` rather than
    having never been tracked at all."""
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    root = tmp_path / "content" / "acme"
    root.mkdir(parents=True)
    (root / "history.jsonl").write_text("{}\n", encoding="utf-8")
    profile_dir = tmp_path / "profiles" / "acme"
    profile_dir.mkdir(parents=True)  # no PROFILE.md yet
    page = root / "page.html"
    page.write_text("<html></html>", encoding="utf-8")
    pi.write_inventory(
        page,
        (root, ["history.jsonl"]),
        scope="all",
        profile="acme",
        profile_files=("PROFILE.md",),
    )
    assert pi.verify_inventory(page, root, profile="acme").ok

    (profile_dir / "PROFILE.md").write_text("target_markets: [Singapore]\n", encoding="utf-8")
    rep = pi.verify_inventory(page, root, profile="acme")
    assert rep.ok is False
    assert "profile:PROFILE.md" in rep.new


def test_a_pre_profile_tracking_inventory_is_stale_when_profile_is_given(tmp_path, monkeypatch):
    """An inventory written before this feature existed has no `profile_inputs` key at
    all. It must never verify as fresh once a caller starts asking about a profile — the
    back-compat shape (no `profile` argument) is the only way it stays fresh."""
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    page, root, _ = _seed(tmp_path, profile="acme")
    pi.write_inventory(page, (root, ["history.jsonl"]), scope="all")  # no profile at all

    assert pi.verify_inventory(page, root).ok  # the pre-T1.9 call shape

    rep = pi.verify_inventory(page, root, profile="acme")
    assert rep.ok is False
    assert rep.profile_untracked is True
    assert "predates profile tracking" in rep.explain()


# --- content-root confinement (a crafted `inputs[].path` must never escape `root`) ------


@pytest.mark.parametrize(
    "escaping_path", ["/etc/hosts", "../../../../../../etc/hosts", "../sibling/secret.txt"]
)
def test_verify_refuses_a_content_input_path_outside_root(tmp_path, monkeypatch, escaping_path):
    """`verify_inventory` used to compute `root / rel` and hash whatever that resolved to
    — for a `../`-shaped or absolute recorded path, that escapes `root` entirely and could
    hash `/etc/hosts` or a sibling tenant's tree. `globs=[]` on the baseline write keeps the
    fixture from also tripping the (unrelated) "new file appeared" check.

    A refused row must NOT read as fine: "this page cannot be shown to be current" is
    exactly the failure `ok` exists to convict, not a reason to look away from it.
    """
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    page, root, _ = _seed(tmp_path, profile="acme")
    pi.write_inventory(page, (root, []), scope="all")
    _rewrite_inventory(page, {"inputs": [{"path": escaping_path, "sha256": "0" * 64, "bytes": 1}]})
    rep = pi.verify_inventory(page, root)
    assert rep.missing == [] and rep.changed == [] and rep.new == []
    assert escaping_path in rep.refused
    assert rep.ok is False
    assert "refused" in rep.explain()


def test_a_real_in_root_symlink_to_outside_is_followed_not_refused(tmp_path, monkeypatch):
    """The opposite of the escaping-path case above: a symlink whose NAME lives inside
    root but whose TARGET sits outside it is a legitimate layout, not an attack —
    ``content/<tenant>`` backed by external storage is exactly this shape (CLAUDE.md). Its
    lexical, in-root name is confined normally; the bytes hashed are whatever it currently
    points at, followed by the OS exactly as `open()` always follows a symlink — so editing
    the TARGET must still flip this stale. `_confine`'s old `.resolve()`-based check broke
    this: it followed the symlink itself, saw the resolved path was outside `root`, and
    silently skipped the row — so this case used to read as permanently fresh.
    """
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    page, root, _ = _seed(tmp_path, profile="acme")
    outside = tmp_path / "outside-root"
    outside.mkdir()
    target = outside / "real.csv"
    target.write_text("email\nada@example.com\n", encoding="utf-8")
    link = root / "prospects" / "linked.csv"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)

    pi.write_inventory(page, (root, ["prospects/linked.csv"]), scope="all")
    assert pi.verify_inventory(page, root).ok

    target.write_text("email\nada@example.com\nbo@example.com\n", encoding="utf-8")
    rep = pi.verify_inventory(page, root)
    assert rep.ok is False
    assert "prospects/linked.csv" in rep.changed
    assert rep.refused == []


def test_verify_refuses_a_traversal_glob_without_leaking_names(tmp_path, monkeypatch):
    """A crafted `globs` entry naming `../other-tenant/*` used to enumerate a SIBLING
    directory's filenames straight into `new` (`Path.relative_to` accepts `..` as long as
    it trails a real common prefix — it does not resolve). An absolute glob (`/etc/*`) used
    to crash `verify_inventory` outright, since `Path("/etc/passwd").relative_to(root)`
    raises when the two share no prefix at all. Both must now report stale via `refused`,
    never a leak and never a crash.
    """
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    page, root, _ = _seed(tmp_path, profile="acme")
    sibling = root.parent / "other-tenant"
    sibling.mkdir()
    (sibling / "leaked.csv").write_text("secret\n", encoding="utf-8")

    pi.write_inventory(page, (root, []), scope="all")
    _rewrite_inventory(page, {"globs": ["../other-tenant/*"]})
    rep = pi.verify_inventory(page, root)
    assert rep.new == [], "must never enumerate a sibling directory's filenames"
    assert any("other-tenant" in r for r in rep.refused)
    assert rep.ok is False

    _rewrite_inventory(page, {"globs": ["/etc/*"]})
    rep = pi.verify_inventory(page, root)  # must not raise
    assert rep.ok is False
    assert any(r.startswith("glob:/etc") for r in rep.refused)
