"""Unit tests for Composable Visual Engine, Diagram, and MC Video compliance (T5, T7, T8, T9).

Verifies 90/10 surface-ratio compliance, non-shadowing @font-face rules, Typekit injection,
and 2D-only / no-gradient-fill / denied-token video validation using template and fictional fixtures.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from gtm_core.brandkit import load_brand_kit
from gtm_core.screen_ui.html_lint import _lint_text
from gtm_core.screen_ui.mc_video.validate import validate_script
from gtm_core.visuals.brand_embed import BrandEmbedder
from gtm_core.visuals.render_html import HTMLArtboardBuilder
from gtm_core.visuals.schema import CVESpec

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_ROOT = REPO_ROOT / "profiles"


def _sample_cve_spec() -> CVESpec:
    return CVESpec.model_validate(
        {
            "title": "Verifiable Delegation Telemetry",
            "subtitle": "Cryptographic proof across agent call chains",
            "profile": "_template",
            "viewport": "social-4x5",
            "density": "standard",
            "theme": "dark",
            "blocks": [
                {
                    "type": "StatCallout",
                    "id": "stat-1",
                    "value": "99.98%",
                    "label": "Chain Verification across delegated scopes",
                    "delta": "+1.2%",
                }
            ],
        }
    )


# ── T5: CVE respects Mission Control 90/10 policy, glass-hairline, and glow_on_focal=false ──


def test_cve_respects_mission_control_90_10_policy() -> None:
    embedder = BrandEmbedder("_template", profiles_root=PROFILES_ROOT)

    # 1. background_plates_in_ui = false suppresses plate URI unless for_hero_cover=True
    assert embedder.resolve_bg_plate_uri() is None
    assert embedder.resolve_bg_plate_uri(for_hero_cover=False) is None

    # 2. Rendered HTML uses #000000 canvas, #111111 surface, glass-hairline panels, and no radial glow
    spec = _sample_cve_spec()
    html_out = HTMLArtboardBuilder(spec, embedder=embedder).build()

    assert "--cve-bg: #000000;" in html_out
    assert "--cve-surface: #111111;" in html_out
    assert "--cve-bg-plate: none;" in html_out
    assert "rgba(255, 255, 255, 0.03)" in html_out
    assert "radial-gradient(circle at 85% 15%" not in html_out
    assert "background-image: url('data:image/" not in html_out


# ── T7: Font-face does not shadow Tenon/SF Mono & Typekit injection obeys GTM_OFFLINE_RENDER ─


def test_font_face_does_not_shadow_tenon_or_sf_mono() -> None:
    embedder = BrandEmbedder("_template", profiles_root=PROFILES_ROOT)
    font_css = embedder.build_font_face_css()

    assert "font-family: 'Tenon'" not in font_css
    assert "font-family: 'SF Mono'" not in font_css

    # Also verify mc_video/assets/template.html does not shadow Tenon or SF Mono
    mc_template = (
        REPO_ROOT / "gtm_core" / "screen_ui" / "mc_video" / "assets" / "template.html"
    ).read_text(encoding="utf-8")
    assert "font-family: 'Tenon'" not in mc_template
    assert "font-family: 'SF Mono'" not in mc_template


def test_font_face_emits_matching_font_when_binary_exists(tmp_path: Path) -> None:
    profiles_dir = tmp_path / "profiles"
    acme_dir = profiles_dir / "acme"
    (acme_dir / "knowledge" / "brand" / "fonts").mkdir(parents=True, exist_ok=True)
    template_dir = PROFILES_ROOT / "_template"
    shutil.copy(template_dir / "knowledge" / "BRAND.toml", acme_dir / "knowledge" / "BRAND.toml")

    fonts_dir = acme_dir / "knowledge" / "brand" / "fonts"
    (fonts_dir / "CustomDisplay-Bold.ttf").write_bytes(b"dummy font content")

    # Update typography.display to CustomDisplay in acme's BRAND.toml
    brand_file = acme_dir / "knowledge" / "BRAND.toml"
    text = re.sub(
        r'display\s*=\s*"[^"]+"',
        'display = "CustomDisplay"',
        brand_file.read_text(encoding="utf-8"),
    )
    brand_file.write_text(text, encoding="utf-8")

    embedder = BrandEmbedder("acme", profiles_root=profiles_dir)
    font_css = embedder.build_font_face_css()
    assert "font-family: 'CustomDisplay'" in font_css


def test_typekit_url_injected_when_set_and_suppressed_when_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles_dir = tmp_path / "profiles"
    acme_dir = profiles_dir / "acme"
    (acme_dir / "knowledge" / "brand").mkdir(parents=True, exist_ok=True)
    template_dir = PROFILES_ROOT / "_template"
    shutil.copy(template_dir / "knowledge" / "BRAND.toml", acme_dir / "knowledge" / "BRAND.toml")

    # Add typekit_url to [typography]
    brand_file = acme_dir / "knowledge" / "BRAND.toml"
    brand_file.write_text(
        brand_file.read_text(encoding="utf-8").replace(
            "[typography]\n", '[typography]\ntypekit_url = "https://use.typekit.net/abc1xyz.css"\n'
        ),
        encoding="utf-8",
    )

    spec = CVESpec.model_validate(
        {
            "title": "Verifiable Telemetry",
            "subtitle": "Proof across chains",
            "profile": "acme",
            "viewport": "social-4x5",
            "density": "standard",
            "theme": "dark",
            "blocks": [],
        }
    )

    monkeypatch.delenv("GTM_OFFLINE_RENDER", raising=False)
    embedder_online = BrandEmbedder("acme", profiles_root=profiles_dir)
    html_online = HTMLArtboardBuilder(spec, embedder=embedder_online).build()
    assert '<link rel="stylesheet" href="https://use.typekit.net/abc1xyz.css">' in html_online

    monkeypatch.setenv("GTM_OFFLINE_RENDER", "1")
    embedder_offline = BrandEmbedder("acme", profiles_root=profiles_dir)
    html_offline = HTMLArtboardBuilder(spec, embedder=embedder_offline).build()
    assert "use.typekit.net" not in html_offline


# ── T8: HTML linter enforces H-MC-2D-ONLY and H-MC-NO-GRADIENT-FILL ─────────────────────────


@pytest.mark.parametrize(
    "bad_snippet",
    [
        '<div style="transform: perspective(800px) rotateX(12deg);">Card</div>',
        '<div style="transform: rotateY(20deg);">Card</div>',
        '<div style="transform: rotate3d(1, 1, 0, 15deg);">Card</div>',
        '<div style="transform: translateZ(40px);">Card</div>',
        '<div style="transform-style: preserve-3d;">Card</div>',
    ],
)
def test_html_lint_enforces_2d_only(bad_snippet: str) -> None:
    kit = load_brand_kit(PROFILES_ROOT, "_template")
    errors = _lint_text(bad_snippet, kit, "16:9")
    assert any("H-MC-2D-ONLY" in e for e in errors)

    non_mc_kit = {"design_system": {"archetype": "editorial-grid"}}
    errors_non_mc = _lint_text(bad_snippet, non_mc_kit, "16:9")
    assert not any("H-MC-2D-ONLY" in e for e in errors_non_mc)


def test_html_lint_2d_only_allows_clean_2d_transforms() -> None:
    kit = load_brand_kit(PROFILES_ROOT, "_template")
    clean = '<div style="transform: translateY(-4px) scale(1.02);">2D Telemetry</div>'
    errors = _lint_text(clean, kit, "16:9")
    assert not any("H-MC-2D-ONLY" in e for e in errors)


def test_html_lint_enforces_no_gradient_fill_on_buttons() -> None:
    kit = load_brand_kit(PROFILES_ROOT, "_template")
    bad_btn = (
        '<button class="cta" style="background: linear-gradient(90deg, var(--color-primary), var(--color-accent));">'
        "Run</button>"
    )
    errors = _lint_text(bad_btn, kit, "16:9")
    assert any("H-MC-NO-GRADIENT-FILL" in e for e in errors)

    bad_css = "<style>.btn-primary { background: linear-gradient(135deg, var(--color-primary), var(--color-accent)); }</style>"
    errors_css = _lint_text(bad_css, kit, "16:9")
    assert any("H-MC-NO-GRADIENT-FILL" in e for e in errors_css)

    good_html = (
        '<button class="btn-primary" style="background: var(--color-primary); color: var(--color-ink);">Verify</button>'
        '<div style="border-image: linear-gradient(90deg, var(--color-primary), var(--color-accent)) 1;">Frame</div>'
    )
    errors_good = _lint_text(good_html, kit, "16:9")
    assert not any("H-MC-NO-GRADIENT-FILL" in e for e in errors_good)


# ── T9: mc_video validate_script rejects denied_fonts and denied_hexes in inline SVG/scenes ──


def _valid_mc_script_doc(payload_text: str) -> dict[str, object]:
    return {
        "product": "Telemetry Plane",
        "brief": {
            "placement": "LinkedIn",
            "formats": "16:9 1080p",
            "maxDuration": 20,
            "sound": "off",
            "audience": "Platform Engineers",
            "message": "Verify telemetry chains",
            "cta": "acme.example",
            "claimsOwner": "Product",
            "confirmed": True,
        },
        "scenes": [
            {
                "type": "hook-constellation",
                "line1": "System Telemetry",
                "line2": payload_text,
            },
            {
                "type": "intro",
                "logo": "symbol",
                "line": "Realtime telemetry proof",
            },
            {
                "type": "endcard",
                "logo": "symbol",
                "cta": "Start building",
                "url": "acme.example",
            },
        ],
    }


@pytest.mark.parametrize(
    "bad_payload",
    [
        '<svg viewBox="0 0 400 200"><text font-family="FatFrank">Drift</text></svg>',
        '<svg viewBox="0 0 400 200"><text style="font-family: \'Figtree\'">Drift</text></svg>',
        '<svg viewBox="0 0 400 200"><rect width="100" height="50" fill="#68FAFD"/></svg>',
        '<svg viewBox="0 0 400 200"><rect width="100" height="50" fill="#1D58FC"/></svg>',
    ],
)
def test_mc_video_validate_rejects_denied_fonts_and_hexes_in_svg(bad_payload: str) -> None:
    kit = {
        "design_system": {
            "archetype": "mission-control",
            "denied_fonts": ["FatFrank", "Figtree"],
            "denied_hexes": ["#68FAFD", "#1D58FC"],
        }
    }
    doc = _valid_mc_script_doc(bad_payload)
    is_valid, errors, _warnings, _dur, _jargon = validate_script(
        doc, brand_kit=kit, profile="_template"
    )
    assert not is_valid
    assert any("DS-DRIFT-03" in e for e in errors), (
        f"Expected DS-DRIFT-03 error for {bad_payload}, got: {errors}"
    )


def test_mc_video_validate_accepts_canonical_mcdl_svg() -> None:
    kit = load_brand_kit(PROFILES_ROOT, "_template")
    good_svg = (
        '<svg viewBox="0 0 400 200">'
        '<rect width="400" height="200" fill="#111111" stroke="#FFFFFF"/>'
        '<text font-family="system-ui" fill="#FFFFFF">Telemetry Chain</text>'
        "</svg>"
    )
    doc = _valid_mc_script_doc(good_svg)
    _is_valid, errors, _warnings, _dur, _jargon = validate_script(
        doc, brand_kit=kit, profile="_template"
    )
    drift_errors = [e for e in errors if "DS-DRIFT-03" in e]
    assert drift_errors == []
