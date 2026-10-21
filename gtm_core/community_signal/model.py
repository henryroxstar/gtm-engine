"""The signal-model contract: palette, color/escape helpers, and validation.

The signal model is a plain dict (JSON on disk; contract in
``schemas/community-signal-model.schema.json``). It is deliberately generic — no vendor
or company names — and every section is optional so a sparse model still renders.

Security posture (§R5 / stored-XSS): all strings that may derive from untrusted match
content flow through :func:`esc` (HTML-escaped) before reaching the page, and every URL
through :func:`safe_url` (only ``http``/``https`` survive; ``javascript:`` etc. are dropped).
The renderer NEVER interpolates a raw model string into HTML without one of these.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Palette — an Okabe-Ito-informed, colorblind-aware categorical set, aligned to the
# in-repo brand hexes for the first four (green/blue/amber). Extensible to N: the
# renderer assigns colors by index when a category doesn't pin its own. These literal
# hexes are used inline (like the source dashboard's bar fills), so they read on both
# the dark and light surfaces without per-theme variants.
# --------------------------------------------------------------------------- #
PALETTE: tuple[str, ...] = (
    "#199e70",  # green
    "#3987e5",  # blue
    "#c98500",  # amber
    "#cc79a7",  # reddish-purple (Okabe-Ito)
    "#56b4e9",  # sky (Okabe-Ito)
    "#d55e00",  # vermillion (Okabe-Ito)
    "#6a8f3f",  # olive
    "#7d74d6",  # violet
)
#: Neutral used for an explicit "other/uncategorized" bucket.
NEUTRAL = "#8b8a84"


def color_for(index: int) -> str:
    """A stable categorical color for the ``index``-th category (wraps the palette)."""
    return PALETTE[index % len(PALETTE)]


def esc(value: Any) -> str:
    """HTML-escape any value for safe text/attribute interpolation (quotes included)."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def safe_url(value: Any) -> str:
    """Return an escaped ``http(s)`` URL, or ``""`` for anything else.

    Drops ``javascript:``/``data:``/relative/other schemes so an untrusted source URL in a
    match can't become a script or exfiltration vector when the report is opened.
    """
    if not value:
        return ""
    raw = str(value).strip()
    low = raw.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return html.escape(raw, quote=True)
    return ""


def is_light(hex_color: str) -> bool:
    """True if ``hex_color`` is light enough that white text on it would be low-contrast
    (so the renderer flips to dark text). Uses relative luminance; tolerant of bad input."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return False
    try:
        r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return False
    # Rec. 601 luma is fine for a light/dark decision.
    return (0.299 * r + 0.587 * g + 0.114 * b) > 0.62


class ModelError(ValueError):
    """The signal model is malformed in a way the renderer can't safely proceed from."""


def resolve_category_colors(model: dict) -> dict[str, str]:
    """Map each declared category key → its color (pinned ``color`` or palette-by-index)."""
    colors: dict[str, str] = {}
    for i, cat in enumerate(model.get("categories") or []):
        if not isinstance(cat, dict):
            continue
        key = str(cat.get("key", "") or f"cat{i}")
        colors[key] = str(cat.get("color") or color_for(i))
    return colors


def validate_model(model: Any) -> dict:
    """Lightweight structural validation. Raises :class:`ModelError` on hard problems;
    tolerates missing optional sections (they simply don't render).

    Hard requirements: ``model`` is a dict and ``meta.title`` is a non-empty string.
    """
    if not isinstance(model, dict):
        raise ModelError("signal model must be a JSON object")
    meta = model.get("meta")
    if not isinstance(meta, dict) or not str(meta.get("title", "")).strip():
        raise ModelError("meta.title is required")
    # Sections, if present, must be lists (the renderer iterates them).
    for section in (
        "kpis",
        "categories",
        "share_of_voice",
        "momentum",
        "platforms",
        "signals",
        "moves",
        "plays",
        "filter_suggestions",
    ):
        val = model.get(section)
        if val is not None and not isinstance(val, list):
            raise ModelError(f"section '{section}' must be a list when present")
    return model


def load_model(source: str | Path | dict) -> dict:
    """Load + validate a signal model from a path, a JSON string, or an in-memory dict."""
    if isinstance(source, dict):
        return validate_model(source)
    text: str
    p = Path(source)
    if p.exists():
        text = p.read_text(encoding="utf-8")
    else:
        text = str(source)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelError(f"invalid JSON: {exc}") from exc
    return validate_model(data)
