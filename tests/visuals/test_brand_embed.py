from pathlib import Path

import pytest

from gtm_core.visuals.brand_embed import BrandEmbedder, FontMissingError


def test_brand_embedder_font_aliasing_and_missing_policy(fictional_brand_profile):
    embedder = BrandEmbedder(
        profile=fictional_brand_profile["profile"],
        profiles_root=fictional_brand_profile["profiles_root"],
    )
    css = embedder.generate_embedded_css()

    assert "@font-face" in css
    assert "font-family: 'Tenon'" in css
    assert "font-family: 'standin-sans'" in css  # Alias registered
    assert "data:font/truetype;base64," in css
    assert "--cve-font-scale: 1.0;" in css


def test_missing_logo_safe_area_fallback(fictional_brand_profile):
    embedder = BrandEmbedder(
        profile=fictional_brand_profile["profile"],
        profiles_root=fictional_brand_profile["profiles_root"],
    )
    logo_html = embedder.get_logo_html()
    assert 'class="cve-logo-safe-area"' in logo_html
    assert "Official Mark Area" in logo_html


def test_strict_brand_missing_font_raises(tmp_path: Path):
    profile_dir = tmp_path / "profiles" / "strictcorp" / "knowledge"
    profile_dir.mkdir(parents=True)
    brand_toml = profile_dir / "BRAND.toml"
    brand_toml.write_text("""
[typography]
primary = "CustomFont"
font_files = ["knowledge/brand/fonts/missing.ttf"]
""")
    embedder = BrandEmbedder(
        profile="strictcorp",
        profiles_root=tmp_path / "profiles",
        strict_brand=True,
    )
    with pytest.raises(FontMissingError, match="missing.ttf not found"):
        embedder.generate_embedded_css()


def test_light_mode_contrast_adjustment(fictional_brand_profile):
    embedder = BrandEmbedder(
        profile=fictional_brand_profile["profile"],
        profiles_root=fictional_brand_profile["profiles_root"],
    )
    css_light = embedder.generate_embedded_css(theme="light")
    # Neon cyan (#72F5FA) should be remapped on light backgrounds for WCAG AA
    assert (
        "--cve-accent-text: #3464FD;" in css_light
        or "--cve-accent-text: #3464fd;" in css_light.lower()
    )


def test_existing_logo_svg_inlined(tmp_path: Path):
    profile_dir = tmp_path / "profiles" / "logocorp" / "knowledge"
    profile_dir.mkdir(parents=True)
    svg_file = profile_dir / "logo.svg"
    svg_file.write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h10v10H0z"/></svg>')

    brand_toml = profile_dir / "BRAND.toml"
    brand_toml.write_text("""
[assets]
logo_svg = "knowledge/logo.svg"
missing_policy = "leave safe area"
""")

    embedder = BrandEmbedder(
        profile="logocorp",
        profiles_root=tmp_path / "profiles",
    )
    logo_html = embedder.get_logo_html()
    assert '<div class="cve-logo">' in logo_html
    assert "<svg" in logo_html
    assert "</svg>" in logo_html
    assert 'class="cve-logo-safe-area"' not in logo_html


def test_permissive_missing_font_ignored(tmp_path: Path):
    profile_dir = tmp_path / "profiles" / "laxcorp" / "knowledge"
    profile_dir.mkdir(parents=True)
    brand_toml = profile_dir / "BRAND.toml"
    brand_toml.write_text("""
[typography]
primary = "CustomFont"
font_files = ["knowledge/brand/fonts/missing.ttf"]
""")
    embedder = BrandEmbedder(
        profile="laxcorp",
        profiles_root=tmp_path / "profiles",
        strict_brand=False,
    )
    css = embedder.generate_embedded_css()
    assert "@font-face" not in css
    assert "--cve-font-family: 'CustomFont', -apple-system, sans-serif;" in css
    assert "--cve-font-scale: 1.0;" in css


def test_path_traversal_confinement(tmp_path: Path):
    profile_dir = tmp_path / "profiles" / "evilcorp" / "knowledge"
    profile_dir.mkdir(parents=True)
    brand_toml = profile_dir / "BRAND.toml"
    brand_toml.write_text("""
[typography]
primary = "EvilFont"
font_files = ["../../../../etc/passwd"]

[assets]
logo_svg = "../../../../etc/passwd"
missing_policy = "leave safe area"
""")
    embedder = BrandEmbedder(
        profile="evilcorp",
        profiles_root=tmp_path / "profiles",
        strict_brand=True,
    )
    with pytest.raises(FontMissingError):
        embedder.generate_embedded_css()

    logo_html = embedder.get_logo_html()
    assert "root:x:" not in logo_html
    assert 'class="cve-logo-safe-area"' in logo_html


def test_cross_tenant_asset_confinement(tmp_path: Path):
    profiles_root = tmp_path / "profiles"
    victim_dir = profiles_root / "victimcorp" / "knowledge"
    victim_dir.mkdir(parents=True)
    (victim_dir / "secret_logo.svg").write_text("<svg><text>VICTIM SECRET</text></svg>")
    (victim_dir / "secret_font.ttf").write_text("DUMMY_FONT_BYTES")

    attacker_dir = profiles_root / "attackercorp" / "knowledge"
    attacker_dir.mkdir(parents=True)
    brand_toml = attacker_dir / "BRAND.toml"
    brand_toml.write_text("""
[typography]
primary = "StolenFont"
font_files = ["../../victimcorp/knowledge/secret_font.ttf"]

[assets]
logo_svg = "../../victimcorp/knowledge/secret_logo.svg"
missing_policy = "leave safe area"
""")

    embedder = BrandEmbedder(
        profile="attackercorp",
        profiles_root=profiles_root,
        strict_brand=True,
    )
    # Stolen font must be refused under strict brand
    with pytest.raises(FontMissingError):
        embedder.generate_embedded_css()

    # Stolen logo must be refused and fall back to safe area
    logo_html = embedder.get_logo_html()
    assert "VICTIM SECRET" not in logo_html
    assert 'class="cve-logo-safe-area"' in logo_html


def test_svg_logo_sanitization(tmp_path: Path):
    profile_dir = tmp_path / "profiles" / "svgcorp" / "knowledge"
    profile_dir.mkdir(parents=True)
    svg_payload = """<svg xmlns="http://www.w3.org/2000/svg">
    <script>alert("xss")</script>
    <foreignObject><div>bad element</div></foreignObject>
    <circle cx="5" cy="5" r="5" onload="stealTokens()" onerror="exploit()" />
</svg>"""
    (profile_dir / "logo.svg").write_text(svg_payload)

    brand_toml = profile_dir / "BRAND.toml"
    brand_toml.write_text("""
[assets]
logo_svg = "knowledge/logo.svg"
missing_policy = "leave safe area"
""")

    embedder = BrandEmbedder(
        profile="svgcorp",
        profiles_root=tmp_path / "profiles",
    )
    logo_html = embedder.get_logo_html()
    assert "<script>" not in logo_html
    assert "alert" not in logo_html
    assert "<foreignObject>" not in logo_html
    assert "bad element" not in logo_html
    assert "onload=" not in logo_html
    assert "onerror=" not in logo_html
    assert "<circle" in logo_html


def test_css_font_family_breakout_sanitized(tmp_path: Path):
    profile_dir = tmp_path / "profiles" / "injectcorp" / "knowledge"
    profile_dir.mkdir(parents=True)
    brand_toml = profile_dir / "BRAND.toml"
    brand_toml.write_text("""
[palette]
primary = "#3464FD"
[typography]
primary = "Inter</style><script>alert(1)</script>"
""")

    embedder = BrandEmbedder(
        profile="injectcorp",
        profiles_root=tmp_path / "profiles",
    )
    css = embedder.generate_embedded_css()
    assert "</style>" not in css
    assert "<script>" not in css
    assert "alert(1)" not in css
    assert "Inter" in css
