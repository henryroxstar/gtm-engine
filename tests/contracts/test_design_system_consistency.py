"""Contract tests for Design System Authority & Consistency Gate (T1–T4, T6).

Verifies [design_system] schema and product override policy enforcement, compiled tokens.css
parity, and cross-renderer token agreement using template and fictional fixtures.
"""

from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path

import pytest

from gtm_core.brandkit import (
    check_brand_consistency,
    load_brand_kit,
)
from gtm_core.brandkit import (
    main as brandkit_main,
)
from gtm_core.brandkit_css import render_tokens_css
from gtm_core.diagrams.render import resolve_diagram_theme
from gtm_core.screen_ui.palette import _load_palette
from gtm_core.visuals.brand_embed import BrandEmbedder

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_ROOT = REPO_ROOT / "profiles"


def _discover_kit_profiles() -> list[str]:
    if not PROFILES_ROOT.is_dir():
        return []
    return sorted(
        d.name
        for d in PROFILES_ROOT.iterdir()
        if d.is_dir() and not d.name.startswith(".") and (d / "knowledge" / "BRAND.toml").is_file()
    )


# ── T1: Live profiles pass brandkit check ─────────────────────────────────────────


@pytest.mark.parametrize("profile", _discover_kit_profiles())
def test_live_profiles_pass_brandkit_check(profile: str) -> None:
    ok, errors = check_brand_consistency(PROFILES_ROOT, profile)
    assert ok, f"check_brand_consistency failed for {profile}: {errors}"
    assert errors == []
    assert brandkit_main(["check", "--profile", profile]) == 0


# ── T2: Product override policy enforcement (DS-SCHEMA-01 & DS-POLICY-01) ─────────


def _copy_template_fixture(tmp_path: Path) -> Path:
    profiles_dir = tmp_path / "profiles"
    acme_dir = profiles_dir / "acme"
    (acme_dir / "knowledge" / "brand").mkdir(parents=True, exist_ok=True)
    template_dir = PROFILES_ROOT / "_template"
    shutil.copy(template_dir / "knowledge" / "BRAND.toml", acme_dir / "knowledge" / "BRAND.toml")
    shutil.copy(
        template_dir / "knowledge" / "brand" / "tokens.css",
        acme_dir / "knowledge" / "brand" / "tokens.css",
    )
    return profiles_dir


def test_product_override_policy_suite_gradient_and_logo_only_rejects_illegal_overrides(
    tmp_path: Path,
) -> None:
    profiles_dir = _copy_template_fixture(tmp_path)
    prod_dir = profiles_dir / "acme" / "products" / "widget"
    prod_dir.mkdir(parents=True, exist_ok=True)
    prod_toml = prod_dir / "BRAND.toml"

    # Overriding palette.canvas + [typography] + [layout] + assets.bg_plate must fail DS-POLICY-01
    prod_toml.write_text(
        '[meta]\nprofile = "acme"\nproduct = "widget"\n\n'
        '[palette]\ncanvas = "#FFFFFF"\ngradient = "custom"\n\n'
        '[typography]\ndisplay = "Inter"\n\n'
        '[layout]\ngrid = "custom"\n\n'
        '[assets]\nproduct_logo_dark = "brand/logos/widget.svg"\nbg_plate = "brand/plate.png"\n',
        encoding="utf-8",
    )
    ok, errors = check_brand_consistency(profiles_dir, "acme")
    assert not ok
    policy_errors = [e for e in errors if "DS-POLICY-01" in e]
    assert any("palette.canvas" in e for e in policy_errors)
    assert any("[typography]" in e for e in policy_errors)
    assert any("[layout]" in e for e in policy_errors)
    assert any("assets.bg_plate" in e for e in policy_errors)


def test_product_override_policy_none_forbids_any_product_brand_toml(tmp_path: Path) -> None:
    profiles_dir = _copy_template_fixture(tmp_path)
    company_toml = profiles_dir / "acme" / "knowledge" / "BRAND.toml"
    text = re.sub(
        r'product_override_policy\s*=\s*"suite_gradient_and_logo_only"',
        'product_override_policy = "none"',
        company_toml.read_text(encoding="utf-8"),
    )
    company_toml.write_text(text, encoding="utf-8")

    prod_dir = profiles_dir / "acme" / "products" / "widget"
    prod_dir.mkdir(parents=True, exist_ok=True)
    (prod_dir / "BRAND.toml").write_text(
        '[meta]\nprofile = "acme"\nproduct = "widget"\n',
        encoding="utf-8",
    )

    ok, errors = check_brand_consistency(profiles_dir, "acme")
    assert not ok
    assert any("DS-POLICY-01" in e and "product_override_policy = 'none'" in e for e in errors)


def test_product_override_policy_full_subbrand_allows_complete_subbrand(tmp_path: Path) -> None:
    profiles_dir = _copy_template_fixture(tmp_path)
    company_toml = profiles_dir / "acme" / "knowledge" / "BRAND.toml"
    text = re.sub(
        r'product_override_policy\s*=\s*"suite_gradient_and_logo_only"',
        'product_override_policy = "full_subbrand"',
        company_toml.read_text(encoding="utf-8"),
    )
    company_toml.write_text(text, encoding="utf-8")

    prod_dir = profiles_dir / "acme" / "products" / "widget"
    prod_dir.mkdir(parents=True, exist_ok=True)
    prod_toml = prod_dir / "BRAND.toml"
    prod_toml.write_text(
        '[meta]\nprofile = "acme"\nproduct = "widget"\n\n'
        '[palette]\ncanvas = "#04080F"\nprimary = "#2563EB"\ngradient = "custom"\n\n'
        '[typography]\ndisplay = "Inter"\n',
        encoding="utf-8",
    )
    ok, errors = check_brand_consistency(profiles_dir, "acme")
    assert ok, f"Expected full_subbrand override to pass, got: {errors}"


# ── T3: Compiled tokens.css parity & DS-TOKENS-02 ─────────────────────────────────


def test_compiled_tokens_css_template_matches_mc_video_assets() -> None:
    template_toml = PROFILES_ROOT / "_template" / "knowledge" / "BRAND.toml"
    template_kit = tomllib.loads(template_toml.read_text(encoding="utf-8"))
    compiled_template = render_tokens_css(template_kit)
    mc_video_tokens_css = (
        REPO_ROOT / "gtm_core" / "screen_ui" / "mc_video" / "assets" / "tokens.css"
    ).read_text(encoding="utf-8")
    assert compiled_template == mc_video_tokens_css


def test_tokens_css_drift_triggers_ds_tokens_02(tmp_path: Path) -> None:
    profiles_dir = _copy_template_fixture(tmp_path)
    tokens_file = profiles_dir / "acme" / "knowledge" / "brand" / "tokens.css"
    tokens_file.write_text(
        tokens_file.read_text(encoding="utf-8").replace("#000000", "#101828"),
        encoding="utf-8",
    )
    ok, errors = check_brand_consistency(profiles_dir, "acme")
    assert not ok
    assert any("DS-TOKENS-02" in e for e in errors)


# ── T6: Cross-renderer token agreement on template ────────────────────────────────


def test_cross_renderer_token_agreement_template() -> None:
    kit = load_brand_kit(PROFILES_ROOT, "_template")
    css_text = render_tokens_css(kit)
    embedder = BrandEmbedder("_template", profiles_root=PROFILES_ROOT)
    diag_theme = resolve_diagram_theme(kit)
    su_pal = _load_palette(kit)

    # 1. Canvas (#000000)
    assert kit["palette"]["canvas"].lower() == "#000000"
    assert "--surface-canvas: #000000;" in css_text
    assert embedder.get_color("canvas").lower() == "#000000"
    assert diag_theme["paper"].lower() == "#000000"
    assert su_pal.canvas.lower() == "#000000"

    # 2. Surface (#111111)
    assert kit["palette"]["surface"].lower() == "#111111"
    assert "--surface-elevated: #111111;" in css_text
    assert embedder.get_color("surface").lower() == "#111111"
    assert su_pal.surface.lower() == "#111111"

    # 3. Primary (#FFFFFF)
    assert embedder.get_color("primary").lower() == "#ffffff"
    assert diag_theme["primary"].lower() == "#ffffff"
