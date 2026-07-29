"""Unit tests for `agent.profiles` (spec §13) — SDK-INDEPENDENT.

These run against whatever profile bundles exist in the checkout (profile-agnostic: at
least `_template` is always present). They never import `claude_agent_sdk`. If the sibling
`agent.profiles` / `agent.config` modules (component 0.12) are not present yet during a
parallel build, the whole module is skipped rather than erroring.

Locked interface under test:
  list_profiles(profiles_root) -> list[str]      # dirs containing PROFILE.md
  profile_dir(profiles_root, name) -> Path       # raise ValueError if missing
  load_products(profiles_root, name) -> list[dict]  # [{slug,name,capabilities:[...]}] or []
  system_prompt_for(name, cfg) -> str            # the per-profile injection string
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Defer the imports so a missing sibling module skips this file instead of collection-erroring.
pytest.importorskip("agent.profiles", reason="agent.profiles not built yet (component 0.12)")
pytest.importorskip("agent.config", reason="agent.config not built yet (component 0.12)")

from agent import profiles  # noqa: E402
from agent.config import Config  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_ROOT = REPO_ROOT / "profiles"


def _sorted_profiles() -> list[str]:
    """Discovered profile names, deterministically ordered (`_template` sorts first)."""
    return sorted(profiles.list_profiles(PROFILES_ROOT))


def _a_profile_with_products() -> str:
    """First profile that declares a products: list, else skip (public-cut safe)."""
    for name in _sorted_profiles():
        if profiles.load_products(PROFILES_ROOT, name):
            return name
    pytest.skip("no profile with a products: list present in this checkout")


# ── list_profiles ─────────────────────────────────────────────────────────────


def test_list_profiles_finds_profile_dirs_only():
    names = profiles.list_profiles(PROFILES_ROOT)
    assert isinstance(names, list)
    # At least one bundle (e.g. _template) MUST be discoverable.
    assert names, "no profiles discovered under profiles/"
    # Every returned name must be a dir that actually contains a PROFILE.md.
    for name in names:
        assert (PROFILES_ROOT / name / "PROFILE.md").is_file(), name


def test_list_profiles_excludes_dirs_without_profile_md(tmp_path):
    # Two real profile dirs + one decoy with no PROFILE.md.
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "PROFILE.md").write_text("# alpha\n")
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "PROFILE.md").write_text("# beta\n")
    (tmp_path / "not_a_profile").mkdir()  # no PROFILE.md → must be skipped

    names = profiles.list_profiles(tmp_path)
    assert sorted(names) == ["alpha", "beta"]


# ── profile_dir ───────────────────────────────────────────────────────────────


def test_profile_dir_returns_existing_path():
    name = _sorted_profiles()[0]
    p = profiles.profile_dir(PROFILES_ROOT, name)
    assert Path(p) == PROFILES_ROOT / name
    assert (Path(p) / "PROFILE.md").is_file()


def test_profile_dir_raises_valueerror_for_missing_profile():
    with pytest.raises(ValueError):
        profiles.profile_dir(PROFILES_ROOT, "does-not-exist")


# ── load_products ─────────────────────────────────────────────────────────────


def test_load_products_parses_product_list_shape():
    name = _a_profile_with_products()
    products = profiles.load_products(PROFILES_ROOT, name)
    assert isinstance(products, list)
    assert products, f"{name} PROFILE.md declares a products: list"

    # Shape contract: each product is a dict with slug, name, capabilities[list].
    for prod in products:
        assert isinstance(prod["slug"], str) and prod["slug"]
        assert isinstance(prod["name"], str) and prod["name"]
        assert isinstance(prod["capabilities"], list)


def test_load_products_returns_empty_list_when_absent(tmp_path):
    # A PROFILE.md with no products: block must yield [] (best-effort parse).
    (tmp_path / "noprod").mkdir()
    (tmp_path / "noprod" / "PROFILE.md").write_text(
        "# noprod\n\n## Identity\n```\nname: Nobody\n```\n"
    )
    assert profiles.load_products(tmp_path, "noprod") == []


# ── system_prompt_for ─────────────────────────────────────────────────────────


def test_system_prompt_for_injects_active_profile_and_boundaries():
    cfg = Config.from_env(repo_root=REPO_ROOT)
    name = _sorted_profiles()[0]
    prompt = profiles.system_prompt_for(name, cfg)
    assert isinstance(prompt, str) and prompt

    # The locked injection must name the active profile and the read/write boundaries.
    assert f"ACTIVE PROFILE = {name}" in prompt
    assert f"profiles/{name}/" in prompt
    # Company facts come from the profile, never the plugin.
    assert "Never read company facts from plugin/" in prompt
    # Product-bound skills gate on capability provided by a product in PROFILE.md.
    assert "requires_capability" in prompt
    # Runtime state is written under the per-profile content tree.
    assert f"content/{name}/" in prompt


def test_system_prompt_for_is_profile_specific():
    names = _sorted_profiles()
    if len(names) < 2:
        pytest.skip("need ≥2 profiles to assert profile-specificity")
    cfg = Config.from_env(repo_root=REPO_ROOT)
    a, b = names[0], names[1]
    prompt_a = profiles.system_prompt_for(a, cfg)
    prompt_b = profiles.system_prompt_for(b, cfg)
    # The injection must vary by profile name — no shared/global active profile.
    assert f"ACTIVE PROFILE = {a}" in prompt_a
    assert f"ACTIVE PROFILE = {b}" in prompt_b
    assert prompt_a != prompt_b
