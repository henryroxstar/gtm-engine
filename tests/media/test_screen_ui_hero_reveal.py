"""gtm_core.screen_ui `hero-reveal` — the action contract, the layout, the dim_rect clip.

Real Pillow, real PNGs, fictional fixtures: the "stills" are synthesized colour blocks, never a
tenant's own render, so nothing here depends on a tenant's art.

Modeled on ``test_screen_ui_phone_walkthrough.py``: the action list is refused before a frame is
drawn when it cannot mean what it says, two renders of the same inputs are byte-identical, and
the pixels prove what each feature is FOR rather than what the code happens to do.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageStat

from gtm_core import screen_ui as su
from gtm_core.screen_ui.scenes import hero_reveal as hr

RENDER = su._SCENES["hero-reveal"]

#: A tenant-neutral kit: a light ground, a warm near-black ink, one saturated action colour.
KIT = {
    "palette": {
        "canvas": "#EFEBE3",
        "surface": "#F6F3EC",
        "ink": "#1C1C18",
        "primary": "#B5502E",
        "accent": "#D08B68",
    },
    "typography": {"font_files": {"caption": "unused-by-this-scene.ttf"}},
}
STILL_W, STILL_H = 300, 300
_CORNER = (20, 20, 24)
_MARK = (90, 140, 200)


def _still(path: Path, *, corner: tuple = _CORNER, mark: tuple = _MARK) -> Path:
    """A flat-cornered still with a marked block in the middle — corners give ``canvas_rgb`` a
    single, known colour; the block gives every pixel test something to look for."""
    img = Image.new("RGB", (STILL_W, STILL_H), corner)
    d = ImageDraw.Draw(img)
    d.rectangle((80, 80, 220, 220), fill=mark)
    img.save(path)
    return path


def _marker_still(path: Path, *, corner: tuple = _CORNER, mark: tuple = _MARK) -> Path:
    """A still whose marked block is centred, for the layout-placement test: a `_resize_cover`
    crop centred on the source always carries the source's own centre to its own centre."""
    img = Image.new("RGB", (STILL_W, STILL_H), corner)
    d = ImageDraw.Draw(img)
    r = 30
    cx = cy = STILL_W // 2
    d.rectangle((cx - r, cy - r, cx + r, cy + r), fill=mark)
    img.save(path)
    return path


@pytest.fixture
def still(tmp_path) -> Path:
    return _still(tmp_path / "still.png")


def _actions(tmp_path, *items, version: bool = True, layout: dict | None = None) -> Path:
    doc = {"actions": list(items)}
    if version:
        doc["version"] = 1
    if layout is not None:
        doc["layout"] = layout
    path = tmp_path / "actions.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _render(
    tmp_path, still_path, *items, ratio="9:16", fps=6, duration_s=1.0, out="out", layout=None
):
    return RENDER(
        kit=KIT,
        ratio=ratio,
        fps=fps,
        duration_s=duration_s,
        out_dir=tmp_path / out,
        image=still_path,
        actions=_actions(tmp_path, *items, layout=layout),
    )


def _frames(out_dir: Path) -> list[Path]:
    return sorted(out_dir.glob("hero-reveal-*.png"))


ENTER = {"type": "enter", "start_s": 0.0, "end_s": 0.4}
HIGHLIGHT = {"type": "highlight", "start_s": 0.4, "end_s": 1.0, "rect": [80, 80, 220, 220]}


# ── the action contract: every refusal is a refusal by name ─────────────────────────────────────


def _refuse(doc, *, duration_s=2.0) -> str:
    with pytest.raises(su.SceneError) as exc:
        hr._parse_actions(doc, duration_s=duration_s)
    return str(exc.value)


@pytest.mark.parametrize(
    ("doc", "needle"),
    [
        ({"actions": [{"type": "enter", "start_s": 0, "end_s": 1, "bogus": 1}]}, "unknown key"),
        ({"actions": [], "notes": "hi"}, "unknown top-level key"),
        ({"actions": [], "version": 2}, '"version" must be 1'),
        (
            {"actions": [{"type": "enter", "start_s": 0, "end_s": 1, "from": "north"}]},
            "must be one of",
        ),
        (
            {
                "actions": [
                    {"type": "enter", "start_s": 0.0, "end_s": 0.4},
                    {"type": "enter", "start_s": 1.0, "end_s": 1.4},
                ]
            },
            "only one enter",
        ),
        (
            {"actions": [{"type": "highlight", "start_s": 0, "end_s": 1, "rect": [1, 2, 3]}]},
            "4 numbers",
        ),
        ({"actions": [{"type": "enter", "start_s": 1, "end_s": 1}]}, "0 <= start_s < end_s"),
        ({"actions": [{"type": "enter", "start_s": 0, "end_s": 9}]}, "0 <= start_s < end_s"),
        ({"actions": [], "layout": {"scale": 2.0}}, "layout.scale"),
        ({"actions": [], "layout": {"scale": 0.4}}, "layout.scale"),
        ({"actions": [], "layout": {"scale": 0}}, "layout.scale"),
        ({"actions": [], "layout": {"center_x_frac": 1.5}}, "layout.center_x_frac"),
        ({"actions": [], "layout": {"center_y_frac": -0.1}}, "layout.center_y_frac"),
        ({"actions": [], "layout": {"bogus": 1}}, "layout: unknown key"),
        ({"actions": [], "layout": "nope"}, '"layout" must be an object'),
        (
            {
                "actions": [
                    {
                        "type": "highlight",
                        "start_s": 0,
                        "end_s": 1,
                        "rect": [0, 0, 10, 10],
                        "dim_rect": [1, 2, 3],
                    }
                ]
            },
            "dim_rect must be 4 numbers",
        ),
        (
            {
                "actions": [
                    {
                        "type": "highlight",
                        "start_s": 0,
                        "end_s": 1,
                        "rect": [0, 0, 10, 10],
                        "dim_rect": [10, 10, 5, 20],
                    }
                ]
            },
            "dim_rect must satisfy x0<x1",
        ),
    ],
)
def test_a_malformed_document_is_refused_by_name(doc, needle):
    assert needle in _refuse(doc)


def test_the_scene_refuses_a_missing_image_or_action_list(tmp_path, still):
    with pytest.raises(su.SceneError, match="needs --actions"):
        RENDER(kit=KIT, ratio="9:16", fps=4, duration_s=1.0, out_dir=tmp_path / "a", image=still)
    with pytest.raises(su.SceneError, match="needs --image"):
        RENDER(
            kit=KIT,
            ratio="9:16",
            fps=4,
            duration_s=1.0,
            out_dir=tmp_path / "b",
            actions=_actions(tmp_path, HIGHLIGHT),
        )


# ── the pixels ──────────────────────────────────────────────────────────────────────────────────


def test_rerunning_with_the_same_inputs_is_byte_identical(tmp_path, still):
    items = (
        {"type": "enter", "start_s": 0.0, "end_s": 0.4},
        {"type": "highlight", "start_s": 0.4, "end_s": 1.2, "rect": [80, 80, 220, 220]},
        {"type": "exit", "start_s": 1.2, "end_s": 1.6},
    )
    kwargs = {"duration_s": 1.6, "fps": 6}
    a = _render(tmp_path, still, *items, out="a", **kwargs)
    b = _render(tmp_path, still, *items, out="b", **kwargs)
    assert a == b > 0
    for fa, fb in zip(_frames(tmp_path / "a"), _frames(tmp_path / "b"), strict=True):
        assert fa.read_bytes() == fb.read_bytes()


def test_every_frame_is_the_ratios_pixel_size_and_carries_no_alpha(tmp_path, still):
    _render(tmp_path, still, HIGHLIGHT, ratio="9:16", fps=4)
    for png in _frames(tmp_path / "out"):
        with Image.open(png) as img:
            assert img.size == (1080, 1920) and img.mode == "RGB"


def test_the_canvas_is_blank_before_the_enters_start(tmp_path, still):
    """(d)/enter semantics: before an enter's ``start_s`` the layer has not arrived yet, so the
    frame is exactly the still's own corner colour — nothing else drawn."""
    _render(
        tmp_path,
        still,
        {"type": "enter", "start_s": 0.5, "end_s": 0.9},
        duration_s=1.2,
        fps=10,
    )
    with Image.open(_frames(tmp_path / "out")[0]) as frame:
        colors = frame.convert("RGB").getcolors(frame.width * frame.height)
    assert colors is not None and len(colors) == 1, colors
    assert colors[0][1] == _CORNER


def test_no_enter_or_exit_is_fully_visible_on_the_first_and_last_frame(tmp_path, still):
    """(d): with no enter and no exit, the still is present (not the blank canvas) on frame 0
    and on the last frame."""
    _render(tmp_path, still, duration_s=1.0, fps=6)
    frames = _frames(tmp_path / "out")
    for path in (frames[0], frames[-1]):
        with Image.open(path) as frame:
            colors = frame.convert("RGB").getcolors(frame.width * frame.height)
        # a blank canvas is exactly one colour; the still (corner + marked block) is several
        assert colors is None or len(colors) > 1, f"{path.name} looks like a blank canvas"


def test_the_highlight_ring_appears_during_its_window_and_is_gone_after(tmp_path, still):
    _render(
        tmp_path,
        still,
        {
            "type": "highlight",
            "start_s": 0.0,
            "end_s": 1.0,
            "rect": [80, 80, 220, 220],
            "draw_s": 0.1,
            "fade_s": 0.1,
        },
        duration_s=1.4,
        fps=10,
    )
    frames = _frames(tmp_path / "out")
    target = (0xB5, 0x50, 0x2E)  # KIT primary, the ring colour

    def has_ring(path: Path) -> bool:
        with Image.open(path) as img:
            colors = img.convert("RGB").getcolors(img.width * img.height) or []
        return any(
            count > 20 and all(abs(a - b) < 26 for a, b in zip(pixel, target, strict=True))
            for count, pixel in colors
        )

    assert has_ring(frames[3]), "mid-highlight (t=0.3s), well past draw_s"
    assert not has_ring(frames[-1]), "t=1.3s, after the highlight's end_s"


def test_layout_scale_places_the_stills_centre_at_the_declared_frame_point(tmp_path):
    marker = _marker_still(tmp_path / "marker.png")
    RENDER(
        kit=KIT,
        ratio="9:16",
        fps=4,
        duration_s=1.0,
        out_dir=tmp_path / "out",
        image=marker,
        actions=_actions(
            tmp_path, layout={"scale": 0.5, "center_x_frac": 0.3, "center_y_frac": 0.7}
        ),
    )
    with Image.open(_frames(tmp_path / "out")[0]) as frame:
        rgb = frame.convert("RGB")
        w, h = rgb.size
    ex, ey = round(0.3 * w), round(0.7 * h)
    patch = rgb.crop((ex - 8, ey - 8, ex + 8, ey + 8))
    mean = ImageStat.Stat(patch).mean
    assert all(abs(m - c) < 20 for m, c in zip(mean, _MARK, strict=True)), mean
    # far from the declared centre, at scale 0.5 the kit-neutral canvas still shows through
    corner_mean = ImageStat.Stat(rgb.crop((0, 0, 20, 20))).mean
    assert all(abs(m - c) < 12 for m, c in zip(corner_mean, _CORNER, strict=True)), corner_mean


def test_layout_defaults_are_byte_identical_to_no_layout_key(tmp_path, still):
    items = (ENTER, HIGHLIGHT)
    kwargs = {"duration_s": 1.4, "fps": 6}
    a = _render(tmp_path, still, *items, out="a", **kwargs)
    b = _render(
        tmp_path,
        still,
        *items,
        out="b",
        layout={"scale": 1.0, "center_x_frac": 0.5, "center_y_frac": 0.5},
        **kwargs,
    )
    assert a == b > 0
    for fa, fb in zip(_frames(tmp_path / "a"), _frames(tmp_path / "b"), strict=True):
        assert fa.read_bytes() == fb.read_bytes()


# ── dim_rect (direct on `_draw_highlights`, to isolate it from layout/idle-drift camera math) ────


def test_dim_rect_leaves_pixels_outside_it_undimmed():
    img = Image.new("RGB", (300, 300), (200, 200, 200))
    action = hr._parse_one(
        {
            "type": "highlight",
            "start_s": 0.0,
            "end_s": 1.0,
            "rect": [120, 120, 180, 180],
            "dim_rect": [50, 50, 250, 250],
            "draw_s": 0.01,
            "fade_s": 0.01,
            "dim": 0.9,
        },
        0,
        1.0,
    )
    hr._draw_highlights(img, [action], 0.5, (181, 80, 46), (10, 10, 10))
    outside = img.getpixel((10, 10))  # outside dim_rect entirely
    inside_dim = img.getpixel((60, 60))  # inside dim_rect, outside the ring
    assert outside == (200, 200, 200), "pixels outside dim_rect must be untouched"
    assert inside_dim != (200, 200, 200), "pixels inside dim_rect (outside the ring) are dimmed"


def test_without_dim_rect_the_whole_image_is_dimmed_outside_the_ring():
    """Default behaviour, unchanged: no ``dim_rect`` means the dim reaches the whole frame."""
    img = Image.new("RGB", (300, 300), (200, 200, 200))
    action = hr._parse_one(
        {
            "type": "highlight",
            "start_s": 0.0,
            "end_s": 1.0,
            "rect": [120, 120, 180, 180],
            "draw_s": 0.01,
            "fade_s": 0.01,
            "dim": 0.9,
        },
        0,
        1.0,
    )
    hr._draw_highlights(img, [action], 0.5, (181, 80, 46), (10, 10, 10))
    assert img.getpixel((10, 10)) != (200, 200, 200)
