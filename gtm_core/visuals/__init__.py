"""Composable Visual Engine (CVE) package."""

from .brand_embed import BrandEmbedder, FontMissingError
from .compiler import (
    DENSITY_MULTIPLIERS,
    VIEWPORT_SPECS,
    DoesNotFitError,
    LayoutCompiler,
    compute_cache_key,
)
from .lock import VisualsLockError, visuals_render_lock
from .rasterize import rasterize_cve_spec
from .render_html import HTMLArtboardBuilder, sanitize_text
from .schema import (
    ContrastPairBlock,
    CVESpec,
    Footnote,
    ScorecardCell,
    ScorecardGridBlock,
    ScorecardRow,
    StatCalloutBlock,
    StatusPill,
)

__all__ = [
    "BrandEmbedder",
    "CVESpec",
    "ContrastPairBlock",
    "DENSITY_MULTIPLIERS",
    "DoesNotFitError",
    "FontMissingError",
    "Footnote",
    "HTMLArtboardBuilder",
    "LayoutCompiler",
    "ScorecardCell",
    "ScorecardGridBlock",
    "ScorecardRow",
    "StatCalloutBlock",
    "StatusPill",
    "VIEWPORT_SPECS",
    "VisualsLockError",
    "compute_cache_key",
    "rasterize_cve_spec",
    "sanitize_text",
    "visuals_render_lock",
]
