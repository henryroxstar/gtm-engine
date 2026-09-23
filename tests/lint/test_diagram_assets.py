"""Contract: a README diagram's text must fit inside the shape that holds it.

Excalidraw stores a text element's ``width`` as *data*. ``exportToSvg`` centres bound text on
its container and does **not** re-wrap it, so a width narrower than the real glyph run spills
out of the shape — permanently, and only visibly in the rendered PNG. The JSON looks fine.

The demonstrated case (2026-09-22): every text width in ``see-it-work-workflows.excalidraw``
was a hand-typed round number — 100, 120, 220 — rather than a measured one. Nine of its twelve
labels rendered wider than their shapes. "Published Post" declared 120px and rendered 134px;
"Build Dossier & SPIN Questions" declared 220px and rendered 288px. The skill that produced it
ships a Playwright render-and-inspect loop for exactly this, and it was not run, because
nothing failed.

So this gate recomputes the width the renderer will actually produce and refuses a shape that
cannot hold it. It is deliberately a *lint*, not a render: it needs no browser, so it runs
everywhere, every time — which is the property the Playwright loop did not have.

Metrics are calibrated against the fonts the renderer loads (headless Chromium, the same
``@excalidraw/excalidraw`` build):

* family 3 (Comic Shanns, mono) — exactly 0.600 em/char;
* family 2 (Nunito, sans) — ~0.50 em/char mean, 0.833 em/char worst case (``M``).

``EM_PER_CHAR`` uses 0.60 for both: exact for mono, and a conservative upper bound for the
sans, so a diagram that passes here is always at least as wide as its real render.

The style contract these diagrams are authored against — palette, font family, and the rule
that a container is sized *from* its text rather than beside it — sits next to the diagrams
themselves, in the assets directory this module scans.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "docs" / "assets"

# Conservative upper bound on advance width, per Excalidraw font family. See module docstring.
EM_PER_CHAR = 0.60

# Excalidraw's bound-text padding, per side.
BOUND_TEXT_PADDING = 5

# fontFamily 3 is the CODE font. Prose uses 2 (sans). A diagram that needs 3 is showing a code
# snippet and should say so by adding its id here.
CODE_FONT = 3
CODE_FONT_ALLOW: set[str] = set()

# Three roles, not nine. The original diagrams drifted to 8–11 stroke hues apiece by taking the
# stock palette of whatever tool drew them, which is most of why they read as unserious. Amber is
# the only accent and it is spent entirely on human gates — the product's whole argument — so a
# diagram that puts amber anywhere else has spent the emphasis it had on something that does not
# matter. That last part is a judgement a test cannot make; the palette being CLOSED is not, and
# closing it is what stops the drift.
PALETTE = {
    "transparent",
    "#f8fafc",
    "#475569",  # structure
    "#e2e8f0",
    "#0f172a",  # emphasis
    "#fef3c7",
    "#b45309",  # human gate
    "#1e293b",  # text on a light fill
    "#cbd5e1",
    "#94a3b8",  # frames, arrows
}


def _diagrams() -> list[Path]:
    return sorted(ASSETS.glob("*.excalidraw"))


def _rendered_width(text: str, font_size: float) -> float:
    """Upper bound on the width exportToSvg will draw for this string."""
    if not text:
        return 0.0
    longest = max(text.split("\n"), key=len)
    return len(longest) * font_size * EM_PER_CHAR


def test_diagrams_exist() -> None:
    """A silent glob over an empty directory is a gate that cannot fail."""
    assert _diagrams(), f"no .excalidraw files under {ASSETS} — this gate would pass vacuously"


@pytest.mark.parametrize("path", _diagrams(), ids=lambda p: p.stem)
def test_prose_does_not_use_the_code_font(path: Path) -> None:
    """fontFamily 3 is monospace; it reads as terminal output, not as a diagram."""
    elements = json.loads(path.read_text())["elements"]
    offenders = [
        el.get("id")
        for el in elements
        if el.get("type") == "text"
        and el.get("fontFamily") == CODE_FONT
        and el.get("id") not in CODE_FONT_ALLOW
    ]
    assert not offenders, (
        f"{path.name}: {len(offenders)} text element(s) use fontFamily 3 (the CODE font): "
        f"{offenders[:5]}. Prose uses fontFamily 2. If one genuinely renders a code snippet, "
        f"add its id to CODE_FONT_ALLOW with a reason."
    )


@pytest.mark.parametrize("path", _diagrams(), ids=lambda p: p.stem)
def test_bound_text_fits_its_container(path: Path) -> None:
    """The rendered glyph run must fit inside the shape that holds it."""
    elements = json.loads(path.read_text())["elements"]
    by_id = {el["id"]: el for el in elements}

    failures = []
    for el in elements:
        if el.get("type") != "text" or not el.get("containerId"):
            continue
        container = by_id.get(el["containerId"])
        if container is None:
            failures.append(f"{el.get('id')}: containerId {el['containerId']!r} does not exist")
            continue

        needed = _rendered_width(el.get("text", ""), el.get("fontSize", 16))
        available = container["width"] - 2 * BOUND_TEXT_PADDING
        if needed > available:
            longest = max(el.get("text", "").split("\n"), key=len)
            failures.append(
                f"{longest!r} needs {needed:.0f}px, container {container['id']} offers "
                f"{available:.0f}px (overflow {needed - available:.0f}px)"
            )

        # A declared width under the real render is the same bug one layer down: the text
        # element itself is the box exportToSvg centres.
        if needed > el["width"] + 0.5:
            longest = max(el.get("text", "").split("\n"), key=len)
            failures.append(
                f"{longest!r} declares width {el['width']:.0f}px but renders {needed:.0f}px"
            )

    assert not failures, f"{path.name}: text overflows its shape:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("path", _diagrams(), ids=lambda p: p.stem)
def test_palette_stays_closed(path: Path) -> None:
    """Every colour in a diagram is one of the three declared roles."""
    elements = json.loads(path.read_text())["elements"]
    used: dict[str, set[str]] = {}
    for el in elements:
        for key in ("strokeColor", "backgroundColor"):
            value = el.get(key)
            if value and value not in PALETTE:
                used.setdefault(value, set()).add(el.get("id", "?"))

    assert not used, (
        f"{path.name}: {len(used)} colour(s) outside the closed palette: "
        + ", ".join(f"{c} ({len(ids)}x)" for c, ids in sorted(used.items()))
        + ". Pick a role from the style doc beside these diagrams, or widen PALETTE "
        "deliberately — the drift this catches is a diagram taking some tool's stock colours."
    )
