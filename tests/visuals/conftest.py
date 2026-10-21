from pathlib import Path

import pytest


@pytest.fixture
def fictional_brand_profile(tmp_path: Path):
    profile_dir = tmp_path / "profiles" / "mockcorp"
    knowledge_dir = profile_dir / "knowledge"
    fonts_dir = knowledge_dir / "brand" / "fonts"
    fonts_dir.mkdir(parents=True)

    # Fake font binary
    fake_font = fonts_dir / "standin-sans-600.ttf"
    fake_font.write_bytes(b"MOCK_TTF_DATA")

    brand_toml = knowledge_dir / "BRAND.toml"
    brand_toml.write_text("""
[meta]
surface = "all"
[palette]
primary = "#3464FD"
background = "#0A182A"
accent = "#72F5FA"
text = "#FFFFFF"
[typography]
primary = "Tenon"
font_files = ["knowledge/brand/fonts/standin-sans-600.ttf"]
[assets]
missing_policy = "leave safe area"
""")
    return {"profile": "mockcorp", "profiles_root": tmp_path / "profiles"}
