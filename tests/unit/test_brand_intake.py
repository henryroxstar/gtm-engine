"""Unit tests for Design System Intake & Atomic Promotion (T10).

Verifies export/stage/diff/promote round-trip, staged consistency refusal, and atomic rollback
while preserving the closed writer set for profiles/ using fictional profile fixtures.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from gtm_core import knowledge_staging as ks
from gtm_core.brand_intake import (
    diff_staged_intake,
    export_profile_spec,
    promote_staged_intake,
    stage_intake,
)
from gtm_core.brandkit import check_brand_consistency, load_brand_kit
from gtm_core.brandkit_css import render_tokens_css

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_ROOT = REPO_ROOT / "profiles"


def _setup_sandbox(tmp_path: Path) -> tuple[Path, Path]:
    profiles_dir = tmp_path / "profiles"
    content_dir = tmp_path / "content"
    acme_dir = profiles_dir / "acme"
    (acme_dir / "knowledge" / "brand").mkdir(parents=True, exist_ok=True)
    content_dir.mkdir(parents=True, exist_ok=True)

    template_dir = PROFILES_ROOT / "_template"
    shutil.copy(template_dir / "knowledge" / "BRAND.toml", acme_dir / "knowledge" / "BRAND.toml")
    shutil.copy(
        template_dir / "knowledge" / "brand" / "tokens.css",
        acme_dir / "knowledge" / "brand" / "tokens.css",
    )

    prod_dir = acme_dir / "products" / "widget"
    prod_dir.mkdir(parents=True, exist_ok=True)
    prod_toml = (
        '[meta]\nprofile = "acme"\nproduct = "widget"\n\n'
        '[palette]\ngradient = "linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%)"\n'
    )
    (prod_dir / "BRAND.toml").write_text(prod_toml, encoding="utf-8")
    return profiles_dir, content_dir


def test_export_round_trips_through_stage_and_check(tmp_path: Path) -> None:
    profiles_dir, content_dir = _setup_sandbox(tmp_path)
    exported_toml = export_profile_spec(profiles_dir, "acme")
    assert "[design_system]" in exported_toml
    assert "[palette]" in exported_toml
    assert "[products.widget.palette]" in exported_toml

    ok, topics, errors = stage_intake(
        profiles_dir,
        content_dir,
        "acme",
        exported_toml,
        today=date(2026, 10, 19),
    )
    assert ok, f"Round-trip stage failed: {errors}"
    assert errors == []
    assert "BRAND.toml" in topics
    assert "brand/tokens.css" in topics


def test_stage_diff_and_atomic_promote_updates_all_files_and_keeps_css_clean(
    tmp_path: Path,
) -> None:
    profiles_dir, content_dir = _setup_sandbox(tmp_path)
    live_brand_before = (profiles_dir / "acme" / "knowledge" / "BRAND.toml").read_text(
        encoding="utf-8"
    )

    exported_toml = export_profile_spec(profiles_dir, "acme")
    # Update primary color in intake spec from #FFFFFF to #2563EB
    updated_spec = exported_toml.replace('primary = "#FFFFFF"', 'primary = "#2563EB"')

    ok, topics, errors = stage_intake(
        profiles_dir,
        content_dir,
        "acme",
        updated_spec,
        today=date(2026, 10, 19),
    )
    assert ok, f"Stage failed: {errors}"

    # Live file must be untouched before promote
    assert (profiles_dir / "acme" / "knowledge" / "BRAND.toml").read_text(
        encoding="utf-8"
    ) == live_brand_before

    # Unified diff must reflect the staged primary color update in both BRAND.toml and tokens.css
    diff_out = diff_staged_intake(profiles_dir, content_dir, "acme")
    assert "#2563EB" in diff_out
    assert "#2563eb" in diff_out

    # Promote atomically
    prom_ok, promoted_paths, prom_errors = promote_staged_intake(
        profiles_dir,
        content_dir,
        "acme",
        today=date(2026, 10, 19),
        source="unit-test-intake",
    )
    assert prom_ok, f"Promote failed: {prom_errors}"
    assert len(promoted_paths) >= 2

    # Verify promoted tokens.css has NO YAML frontmatter and matches render_tokens_css byte-for-byte
    tokens_css_path = profiles_dir / "acme" / "knowledge" / "brand" / "tokens.css"
    tokens_css_text = tokens_css_path.read_text(encoding="utf-8")
    assert not tokens_css_text.startswith("---")
    kit_after = load_brand_kit(profiles_dir, "acme")
    assert kit_after["palette"]["primary"] == "#2563EB"
    assert tokens_css_text == render_tokens_css(kit_after)

    # Live profile passes check_brand_consistency after promotion
    live_ok, live_errors = check_brand_consistency(profiles_dir, "acme")
    assert live_ok, f"Post-promote consistency check failed: {live_errors}"


def test_stage_refuses_illegal_product_override_under_suite_policy(tmp_path: Path) -> None:
    profiles_dir, content_dir = _setup_sandbox(tmp_path)
    exported_toml = export_profile_spec(profiles_dir, "acme")
    # Inject illegal palette.canvas into [products.widget.palette]
    bad_spec = exported_toml.replace(
        "[products.widget.palette]\n",
        '[products.widget.palette]\ncanvas = "#FFFFFF"\n',
    )
    ok, _topics, errors = stage_intake(
        profiles_dir,
        content_dir,
        "acme",
        bad_spec,
        today=date(2026, 10, 19),
    )
    assert not ok
    assert any("DS-POLICY-01" in e and "palette.canvas" in e for e in errors)


def test_promote_rolls_back_all_files_on_mid_promotion_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles_dir, content_dir = _setup_sandbox(tmp_path)
    brand_path = profiles_dir / "acme" / "knowledge" / "BRAND.toml"
    tokens_path = profiles_dir / "acme" / "knowledge" / "brand" / "tokens.css"
    brand_before = brand_path.read_text(encoding="utf-8")
    tokens_before = tokens_path.read_text(encoding="utf-8")

    exported_toml = export_profile_spec(profiles_dir, "acme")
    updated_spec = exported_toml.replace('primary = "#FFFFFF"', 'primary = "#0EA5E9"')
    ok, _, _ = stage_intake(
        profiles_dir,
        content_dir,
        "acme",
        updated_spec,
        today=date(2026, 10, 19),
    )
    assert ok

    orig_promote = ks.promote
    call_count = 0

    def _failing_promote(*args: object, **kwargs: object) -> Path:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Simulated mid-promotion failure on 2nd file")
        return orig_promote(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ks, "promote", _failing_promote)

    prom_ok, promoted_paths, prom_errors = promote_staged_intake(
        profiles_dir,
        content_dir,
        "acme",
        today=date(2026, 10, 19),
    )
    assert not prom_ok
    assert promoted_paths == []
    assert any("Simulated mid-promotion failure" in e for e in prom_errors)

    # First file was rolled back!
    assert brand_path.read_text(encoding="utf-8") == brand_before
    assert tokens_path.read_text(encoding="utf-8") == tokens_before
