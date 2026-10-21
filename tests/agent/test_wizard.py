"""Tests for agent.wizard — profile knowledge scaffolding.

Covers:
  - profile-level knowledge files written for all profiles
  - per-product stubs scaffolded when PROFILE.md declares ≥2 products
  - single-product profiles: no per-product files written
  - existing per-product files are NOT overwritten (protects hand-crafted content)
  - knowledge_status() includes per_product keys
  - knowledge_status() complete flag based on profile-level only
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("agent.wizard", reason="agent package not available")

from agent.wizard import (  # noqa: E402
    WizardState,
    knowledge_status,
    write_knowledge_files,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ANSWERS = {
    "tagline": "Acme — close the books in a day, not a fortnight.",
    "voice": "Bold, precise, irreverent. Never: jargon-heavy, passive.",
    "wedge": "The only ledger that reconciles sub-ledgers continuously.",
    "icp": "Mid-market retail, Financial Controller, pain: manual close, trigger: audit.",
    "competitors": "1. Contoso — we reconcile continuously.\n2. Northwind — we're API-first.\n3. Globex — we self-serve.",
    "pillars": "1. Continuous close\n2. Audit-readiness\n3. Finance-engineering collaboration",
}


def _state(profile: str) -> WizardState:
    s = WizardState(profile=profile)
    s.answers = dict(_ANSWERS)
    s.step = 6
    return s


def _write_profile_md(profiles_root: Path, slug: str, products_yaml: str = "") -> None:
    """Write a minimal PROFILE.md so load_products() can parse it."""
    p = profiles_root / slug
    p.mkdir(parents=True, exist_ok=True)
    fence_block = (
        f"```\nproducts:\n{products_yaml}```\n" if products_yaml else "```\nproducts: []\n```\n"
    )
    (p / "PROFILE.md").write_text(f"# {slug}\n\n> Profile.\n\n{fence_block}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Profile-level knowledge files
# ---------------------------------------------------------------------------


def test_writes_profile_level_files(tmp_path):
    root = tmp_path / "profiles"
    _write_profile_md(root, "acme")
    state = _state("acme")
    write_knowledge_files(root, state)

    know = root / "acme" / "knowledge"
    assert (know / "voice.md").is_file()
    assert (know / "icp-personas.md").is_file()
    assert (know / "competitors.md").is_file()
    assert (know / "pillars.md").is_file()


def test_icp_answer_in_profile_knowledge(tmp_path):
    root = tmp_path / "profiles"
    _write_profile_md(root, "acme")
    write_knowledge_files(root, _state("acme"))
    text = (root / "acme" / "knowledge" / "icp-personas.md").read_text()
    assert "Mid-market retail" in text


# ---------------------------------------------------------------------------
# Single-product profile — no per-product stubs
# ---------------------------------------------------------------------------


def test_single_product_no_per_product_files(tmp_path):
    root = tmp_path / "profiles"
    _write_profile_md(
        root,
        "solo",
        "  - { slug: alpha, name: Alpha, capabilities: [search] }\n",
    )
    write_knowledge_files(root, _state("solo"))
    assert not (root / "solo" / "products" / "alpha" / "icp-personas.md").exists()
    assert not (root / "solo" / "products" / "alpha" / "market-scan-config.md").exists()


# ---------------------------------------------------------------------------
# Multi-product profile — per-product stubs scaffolded
# ---------------------------------------------------------------------------


def test_multi_product_scaffolds_per_product_files(tmp_path):
    root = tmp_path / "profiles"
    _write_profile_md(
        root,
        "multi",
        "  - { slug: alpha, name: Alpha, capabilities: [search] }\n"
        "  - { slug: beta,  name: Beta,  capabilities: [write] }\n",
    )
    write_knowledge_files(root, _state("multi"))

    for slug in ("alpha", "beta"):
        assert (root / "multi" / "products" / slug / "icp-personas.md").is_file(), slug
        assert (root / "multi" / "products" / slug / "market-scan-config.md").is_file(), slug


def test_per_product_icp_contains_general_icp_answer(tmp_path):
    root = tmp_path / "profiles"
    _write_profile_md(
        root,
        "multi",
        "  - { slug: alpha, name: Alpha, capabilities: [search] }\n"
        "  - { slug: beta,  name: Beta,  capabilities: [write] }\n",
    )
    write_knowledge_files(root, _state("multi"))
    text = (root / "multi" / "products" / "alpha" / "icp-personas.md").read_text()
    assert "Mid-market retail" in text


def test_per_product_scan_contains_competitors_answer(tmp_path):
    root = tmp_path / "profiles"
    _write_profile_md(
        root,
        "multi",
        "  - { slug: alpha, name: Alpha, capabilities: [search] }\n"
        "  - { slug: beta,  name: Beta,  capabilities: [write] }\n",
    )
    write_knowledge_files(root, _state("multi"))
    text = (root / "multi" / "products" / "beta" / "market-scan-config.md").read_text()
    assert "Contoso" in text


# ---------------------------------------------------------------------------
# Existing per-product files are NOT overwritten
# ---------------------------------------------------------------------------


def test_does_not_overwrite_existing_product_files(tmp_path):
    root = tmp_path / "profiles"
    _write_profile_md(
        root,
        "multi",
        "  - { slug: alpha, name: Alpha, capabilities: [search] }\n"
        "  - { slug: beta,  name: Beta,  capabilities: [write] }\n",
    )
    # Pre-write a hand-crafted icp file for alpha.
    alpha_dir = root / "multi" / "products" / "alpha"
    alpha_dir.mkdir(parents=True, exist_ok=True)
    handcrafted = "# My hand-crafted ICP\nDo not overwrite me.\n"
    (alpha_dir / "icp-personas.md").write_text(handcrafted)

    write_knowledge_files(root, _state("multi"))

    assert (alpha_dir / "icp-personas.md").read_text() == handcrafted


# ---------------------------------------------------------------------------
# knowledge_status
# ---------------------------------------------------------------------------


def test_knowledge_status_complete_after_wizard(tmp_path):
    root = tmp_path / "profiles"
    _write_profile_md(root, "acme")
    write_knowledge_files(root, _state("acme"))
    status = knowledge_status(root, "acme")
    assert status["complete"] is True


def test_knowledge_status_incomplete_before_wizard(tmp_path):
    root = tmp_path / "profiles"
    _write_profile_md(root, "acme")
    status = knowledge_status(root, "acme")
    assert status["complete"] is False


def test_knowledge_status_per_product_populated(tmp_path):
    root = tmp_path / "profiles"
    _write_profile_md(
        root,
        "multi",
        "  - { slug: alpha, name: Alpha, capabilities: [search] }\n"
        "  - { slug: beta,  name: Beta,  capabilities: [write] }\n",
    )
    write_knowledge_files(root, _state("multi"))
    status = knowledge_status(root, "multi")

    assert "alpha" in status["per_product"]
    assert "beta" in status["per_product"]
    assert status["per_product"]["alpha"]["icp"] is True
    assert status["per_product"]["alpha"]["market_scan"] is True


def test_knowledge_status_complete_ignores_per_product(tmp_path):
    """complete flag must not require per-product files — they're optional overrides."""
    root = tmp_path / "profiles"
    _write_profile_md(
        root,
        "multi",
        "  - { slug: alpha, name: Alpha, capabilities: [search] }\n"
        "  - { slug: beta,  name: Beta,  capabilities: [write] }\n",
    )
    # Write only profile-level files, no per-product files.
    know = root / "multi" / "knowledge"
    know.mkdir(parents=True, exist_ok=True)
    for f in ("voice.md", "icp-personas.md", "competitors.md", "pillars.md"):
        (know / f).write_text("stub\n")

    status = knowledge_status(root, "multi")
    assert status["complete"] is True
    assert status["per_product"]["alpha"]["icp"] is False


def test_knowledge_status_no_products(tmp_path):
    root = tmp_path / "profiles"
    _write_profile_md(root, "acme")
    status = knowledge_status(root, "acme")
    assert status["per_product"] == {}
