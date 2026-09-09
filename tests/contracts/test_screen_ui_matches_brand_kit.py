"""The video card renderer may not disagree with the profile's BRAND.toml.

WHY THIS EXISTS
---------------
This is the gate the video surface never had, and its absence is the whole reason it is being
written now.

On 2026-09-01 nine declared palettes were collapsed onto one source
(``profiles/<p>/knowledge/BRAND.toml``) and every consumer re-pointed at it. Two of the three
generated surfaces got a drift gate at the same time: the deck theme has
``test_deck_theme_matches_brand_kit.py`` (five contracts, including one for *Vue component colour
fallbacks* added because a hardcoded fallback "is painted in the retired website palette, and no
CSS test could ever have seen it"), and the carousel surface inherits it. ``gtm_core.screen_ui``
got neither the token system nor a test.

The cost was measurable. The "Four calls that all worked" film shipped on 2026-08-31 with every
card drawn on ``#0B1A2E`` / ``#00C6C6`` — values the consolidation retired the next day — and no
check in the repo could see it. Alongside that, the module's own secondary text colour was
``#8FA3B8``, a blue-grey that appears in no kit in this repo at all, so every card in every film
drew its dim type in a colour no tenant ever declared.

WHAT IS CHECKED, AND WHY THESE
------------------------------
Each contract is chosen to hold across tenants rather than to encode one tenant's numbers — the same
posture the deck test takes. Three are static (they read the code) and two are rendered (they read
pixels), because the static ones cannot see a colour that arrives through a blend and the rendered
ones cannot see a literal that is currently unreachable.

1. No CHROMATIC colour literal in engine code. A grey is a tenant-neutral fallback; a hue is
   somebody's brand. The signal colours are the one allowed exception and are named here, for the
   same reason the deck theme keeps ``--signal-stop`` / ``--signal-warn`` outside its brand
   contract: a row that says "denied" must read as denied in every palette.

2. No bare integer stroke width. Every other dimension in these scenes scales with the frame;
   ``width=2`` does not, so a rule that is 2px against 54px type at 1080 is 2px against 96px type
   at 1920. ``caller_record.py`` carried both conventions inside one scene.

3. No corner radius that multiplies frame height by a float literal. Radii ran ``h*0.005`` to
   ``h*0.018`` — a 3.8x spread — for panels doing the same job. They now come from
   ``design_tokens.RADIUS``. A radius derived from a LOCAL box (``round(badge_h * 0.5)`` for a
   pill) stays legal: that is a shape relationship, not a scale choice.

4. A kit's ``[motion]`` / ``[layout]`` choices must RESOLVE. Both resolvers degrade silently by
   design (a thin kit must still render), which means a typo — ``default_tempo = "brsik"`` —
   renders on the default and looks fine. Silent degradation is right for an ABSENT key and wrong
   for a PRESENT one, so a declared value is checked here rather than at runtime.

5. A rendered card's ground is the kit's canvas, and every saturated pixel in it carries a hue the
   kit declares. This is the contract that would have caught the film: it fails the moment a card
   renders in a colour the tenant did not choose, no matter which layer put it there.
"""

from __future__ import annotations

import ast
import colorsys
import re
from collections import Counter
from pathlib import Path

import pytest

from gtm_core.brandkit import load_brand_kit
from gtm_core.design_tokens import _DENSITY_SCALE, TEMPOS, resolve_layout, resolve_motion
from gtm_core.screen_ui import _SCENES
from gtm_core.screen_ui.palette import _hex_to_rgb

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "profiles"
PKG = ROOT / "gtm_core" / "screen_ui"

#: Profiles whose kit this contract is enforced against — every tenant that ships a BRAND.toml,
#: discovered rather than listed, so a new profile is covered the day it lands. ``_template`` is
#: the scaffold a new profile is copied FROM, not a tenant, and its kit is deliberately unfilled.
KIT_PROFILES = tuple(
    sorted(
        p.name
        for p in PROFILES.iterdir()
        if not p.name.startswith("_") and (p / "knowledge" / "BRAND.toml").is_file()
    )
)

#: The two colours engine code may state as literals, and the only two. Both mean a STATE, not a
#: brand: "this was refused" and "this succeeded". Every tenant needs them to read the same way,
#: which is exactly why they cannot degrade to that tenant's accent.
SIGNAL_LITERALS = {"#F2A93B", "#3BD68A"}

#: How far apart a hex's channels may be and still count as tenant-neutral. `#0B0B0F` (the canvas
#: last-resort) spans 4; `#9AA0A6` (the accent last-resort) spans 12. `#8FA3B8`, the blue-grey
#: that was removed, spans 41.
ACHROMATIC_MAX_SPREAD = 16

_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}")


def _py_sources() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def _spread(hex_value: str) -> int:
    r, g, b = _hex_to_rgb(hex_value, field="literal")
    return max(r, g, b) - min(r, g, b)


def _hue_deg(rgb: tuple[int, int, int]) -> float:
    r, g, b = (c / 255 for c in rgb)
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360.0


def _hue_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


# ── 1. static: no tenant colour may live in engine code ────────────────────────────────────────


def test_no_chromatic_colour_literal_in_engine_code():
    """AST, not grep: a hex QUOTED IN PROSE is documentation, a hex that is a whole string is a
    value. ``palette.py``'s own docstring names the two literals it was fixed for removing, and a
    line scan flags that as the violation it is describing."""
    offenders: list[str] = []
    for path in _py_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if not _HEX_RE.fullmatch(node.value):
                continue
            up = node.value.upper()
            if up in SIGNAL_LITERALS or _spread(up) <= ACHROMATIC_MAX_SPREAD:
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.value}")
    assert not offenders, (
        "chromatic colour literals in engine code — a hue is a tenant's brand and belongs in "
        "BRAND.toml, not here (see gtm_core/screen_ui/palette.py's own de-brand note):\n  "
        + "\n  ".join(offenders)
    )


# ── 2/3. static: geometry comes from the token scale, not from per-scene numbers ───────────────


def _calls(tree: ast.AST):
    return (n for n in ast.walk(tree) if isinstance(n, ast.Call))


def test_no_bare_integer_stroke_width():
    offenders: list[str] = []
    for path in _py_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _calls(tree):
            for kw in call.keywords:
                if kw.arg != "width":
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{kw.value.lineno} width={kw.value.value}"
                    )
    assert not offenders, (
        "stroke widths hardcoded in pixels — use design_tokens.stroke_px(name, h) so a rule keeps "
        "its weight relative to the type beside it at every ratio:\n  " + "\n  ".join(offenders)
    )


def test_no_radius_scales_frame_height_by_a_literal():
    """``radius=round(h * 0.014)`` is a scale decision; ``round(badge_h * 0.5)`` is a shape one."""
    offenders: list[str] = []
    for path in _py_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _calls(tree):
            for kw in call.keywords:
                if kw.arg not in {"radius", "radius_frac"}:
                    continue
                for node in ast.walk(kw.value):
                    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Mult):
                        continue
                    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                    literal = any(
                        isinstance(n, ast.Constant) and isinstance(n.value, float)
                        for n in (node.left, node.right)
                    )
                    if "h" in names and literal:
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, (
        "corner radii derived from frame height by a bare float — use design_tokens.RADIUS so the "
        "card family shares one ladder instead of 3.8x of drift:\n  " + "\n  ".join(offenders)
    )


# ── 4. the kit's own choices must be real ──────────────────────────────────────────────────────


@pytest.mark.parametrize("profile", KIT_PROFILES)
def test_kit_motion_and_layout_choices_resolve(profile: str):
    kit = load_brand_kit(PROFILES, profile)
    declared_tempo = (kit.get("motion") or {}).get("default_tempo")
    if declared_tempo is not None:
        assert declared_tempo in TEMPOS, (
            f"{profile}: [motion].default_tempo = {declared_tempo!r} is not a tempo "
            f"({sorted(TEMPOS)}). An ABSENT key degrades to the engine default by design; a "
            f"PRESENT one that resolves to the default is a typo rendering silently."
        )
        assert resolve_motion(kit).default_tempo.name == declared_tempo

    declared_density = (kit.get("layout") or {}).get("density")
    if declared_density is not None:
        assert declared_density in _DENSITY_SCALE, (
            f"{profile}: [layout].density = {declared_density!r} is not a density "
            f"({sorted(_DENSITY_SCALE)})"
        )
    layout = resolve_layout(kit)
    assert 0.5 <= layout.safe_box_fill_min <= layout.safe_box_fill_max <= 1.0, (
        f"{profile}: [layout] fill bounds are not an ordered pair inside (0.5, 1.0] — "
        f"{layout.safe_box_fill_min} .. {layout.safe_box_fill_max}"
    )


# ── 5. rendered: the pixels agree with the kit ─────────────────────────────────────────────────

#: One scene per component family in the card vocabulary. Not all 26: this contract is about the
#: RENDERER honouring the kit, and a fifth `rows-*` variant re-proves nothing while costing a
#: 1080p render per run.
RENDERED_SCENES = ("record-grid", "rows-identity", "checkpoint-flow", "title-claim")


def _kit_hues(kit: dict) -> list[float]:
    """Hues the kit declares, from palette roles, colour families and gradient stops."""
    found: set[str] = set()

    def walk(node):
        if isinstance(node, str):
            found.update(h.upper() for h in _HEX_RE.findall(node))
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for section in ("palette", "gradients"):
        walk(kit.get(section, {}))
    hues = [_hue_deg(_hex_to_rgb(h, field="kit")) for h in found if _spread(h) > 12]
    hues += [_hue_deg(_hex_to_rgb(h, field="signal")) for h in SIGNAL_LITERALS]
    return hues


@pytest.fixture(scope="module")
def _tenant_kit() -> dict:
    """The first discovered kit that can actually render — scenes refuse to draw without an
    explicit caption font file, so a kit that declares none proves nothing about pixels."""
    for slug in KIT_PROFILES:
        kit = load_brand_kit(PROFILES, slug)
        if ((kit.get("typography") or {}).get("font_files") or {}).get("caption"):
            return kit
    pytest.skip("no discovered profile declares [typography.font_files].caption")


@pytest.mark.parametrize("scene", RENDERED_SCENES)
def test_rendered_card_ground_is_the_kit_canvas(scene, tmp_path_factory, _tenant_kit):
    from PIL import Image

    out = tmp_path_factory.mktemp(f"ground-{scene}")
    _SCENES[scene](
        kit=_tenant_kit,
        ratio="16:9",
        fps=2,
        duration_s=0.5,
        out_dir=out,
        repo_root=ROOT,
    )
    frame = Image.open(sorted(out.glob("*.png"))[-1]).convert("RGB")
    small = frame.resize((192, 108))
    modal = max(small.getcolors(192 * 108), key=lambda c: c[0])[1]
    canvas = _hex_to_rgb(_tenant_kit["palette"]["canvas"], field="canvas")
    # Generous: `_backdrop` lifts the top and sinks the bottom of the field by up to 20% toward
    # white/black, so the modal pixel is a graded neighbour of the canvas rather than the canvas.
    dist = max(abs(a - b) for a, b in zip(modal, canvas, strict=True))
    assert dist <= 40, (
        f"{scene}: the card's ground is {modal}, {dist} away from the kit canvas {canvas}. This "
        f"is the check that the 2026-08-31 master would have failed — it shipped every card on a "
        f"palette the kit had retired, and nothing in the repo could see it."
    )


@pytest.mark.parametrize("scene", RENDERED_SCENES)
def test_rendered_card_carries_no_hue_the_kit_never_declared(scene, tmp_path_factory, _tenant_kit):
    from PIL import Image

    out = tmp_path_factory.mktemp(f"hue-{scene}")
    _SCENES[scene](
        kit=_tenant_kit,
        ratio="16:9",
        fps=2,
        duration_s=0.5,
        out_dir=out,
        repo_root=ROOT,
    )
    hues = _kit_hues(_tenant_kit)
    frame = Image.open(sorted(out.glob("*.png"))[-1]).convert("RGB")
    strays: Counter = Counter()
    small = frame.resize((480, 270))
    for count, pixel in small.getcolors(480 * 270) or []:
        # Only strongly saturated pixels: a dark blend of accent and canvas carries the accent's
        # hue but at a chroma where quantisation noise dominates, and a test that fails on noise
        # gets deleted rather than fixed.
        if max(pixel) - min(pixel) < 70:
            continue
        hue = _hue_deg(pixel)
        if min(_hue_gap(hue, k) for k in hues) > 20:
            strays[pixel] += count
    assert not strays, (
        f"{scene}: {sum(strays.values())} saturated pixels carry a hue this kit never declared — "
        f"commonest {strays.most_common(3)}. A colour can reach a frame through a blend without "
        f"ever appearing as a literal, which is why this is checked in pixels and not only in AST."
    )
