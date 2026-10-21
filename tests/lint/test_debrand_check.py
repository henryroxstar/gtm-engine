"""Test Mission Control video engine de-branding (T9).

Ensures zero hardcoded tenant names, brand hexes, or product slugs exist in the
gtm_core/screen_ui/mc_video runtime files.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MC_VIDEO_DIR = REPO / "gtm_core" / "screen_ui" / "mc_video"
DENYLIST_FILE = REPO / "tests" / "linter" / "safe_share_denylist.txt"


def _load_denylist_tokens() -> list[str]:
    tokens = []
    if DENYLIST_FILE.is_file():
        for line in DENYLIST_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("token:") or line.startswith("rtoken:"):
                tok = line.split(":", 1)[1].strip()
                if tok and len(tok) > 3:  # skip short generic tokens
                    tokens.append(tok)
    return tokens


# Banned literals that must not be hardcoded in engine runtime assets
BANNED_ENGINE_LITERALS = _load_denylist_tokens() + [
    "#3464FD",
    "#72F5FA",
    "#0A182A",
    "#68FAFD",
    "FatFrank",
    "Figtree",
]


def test_mc_video_runtime_is_debranded():
    """T9: Engine runtime files must not contain tenant tokens or hardcoded brand hexes."""
    runtime_assets = [
        MC_VIDEO_DIR / "assets" / "mc-video.js",
        MC_VIDEO_DIR / "assets" / "mc-video.css",
        MC_VIDEO_DIR / "assets" / "template.html",
        MC_VIDEO_DIR / "assets" / "tokens.css",
        MC_VIDEO_DIR / "render.mjs",
    ]

    for asset_path in runtime_assets:
        assert asset_path.is_file(), f"Missing runtime asset: {asset_path}"
        content = asset_path.read_text(encoding="utf-8")

        for banned in BANNED_ENGINE_LITERALS:
            assert banned.lower() not in content.lower(), (
                f"Tenant literal {banned!r} found in {asset_path.name} — "
                "engine must remain 100% tenant-agnostic"
            )


def test_mc_video_python_modules_free_of_tenant_tokens():
    """T9: Python modules in mc_video must not contain safe-share denylist tokens."""
    tokens = _load_denylist_tokens()
    py_files = sorted(MC_VIDEO_DIR.glob("*.py"))
    assert py_files, "No python files found in mc_video"

    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        for tok in tokens:
            pattern = re.compile(rf"\b{re.escape(tok)}\b", re.IGNORECASE)
            matches = pattern.findall(content)
            assert not matches, f"Denylist token {tok!r} found in {py_file.relative_to(REPO)}"
