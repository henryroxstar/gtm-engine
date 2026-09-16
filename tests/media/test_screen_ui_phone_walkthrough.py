"""gtm_core.screen_ui `phone-walkthrough` — the action contract, the device, and the motion.

Real Pillow, real PNGs, fictional fixtures: the "screenshots" are synthesized colour bands, never a
product capture, so nothing here depends on a tenant's app.

Three properties are what this scene is FOR, and each is checked on the pixels rather than on the
code that produces them: the action list is refused before a frame is drawn when it cannot mean
what it says; two renders of the same inputs are byte-identical; and the result actually moves
(`gtm_core.video_lint` V9's floor, measured the same way — the mean inter-frame delta).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

from gtm_core import screen_ui as su
from gtm_core.screen_ui.scenes import phone_actions as pa
from gtm_core.screen_ui.scenes import phone_chassis as pc
from gtm_core.video_lint.thresholds import MIN_SHOT_MOTION

RENDER = su._SCENES["phone-walkthrough"]

#: Only the one cross-scene test needs a face: this scene draws no text of its own.
_FONT = next(iter(Path(matplotlib.get_data_path()).rglob("DejaVuSans.ttf")), None)

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
#: Portrait, taller than the phone screen (300 / 0.46 = 652), so it can scroll.
SHOT_W, SHOT_H = 300, 900


def _screenshot(path: Path, *, hue: tuple[int, int, int]) -> Path:
    img = Image.new("RGB", (SHOT_W, SHOT_H), (246, 246, 244))
    d = ImageDraw.Draw(img)
    for i in range(6):
        y = 40 + i * 140
        d.rounded_rectangle((20, y, SHOT_W - 20, y + 110), radius=14, fill=hue)
        d.rectangle((36, y + 20, SHOT_W - 60, y + 34), fill=(90, 90, 96))
    img.save(path)
    return path


@pytest.fixture
def shot(tmp_path) -> Path:
    return _screenshot(tmp_path / "screen-a.png", hue=(206, 214, 210))


@pytest.fixture
def shot_b(tmp_path) -> Path:
    return _screenshot(tmp_path / "screen-b.png", hue=(214, 206, 198))


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
    tmp_path, shot, *items, ratio="9:16", fps=8, duration_s=1.0, out="out", layout=None, **kwargs
):
    return RENDER(
        kit=KIT,
        ratio=ratio,
        fps=fps,
        duration_s=duration_s,
        out_dir=tmp_path / out,
        image=shot,
        actions=_actions(tmp_path, *items, layout=layout),
        **kwargs,
    )


HIGHLIGHT = {"type": "highlight", "start_s": 0.1, "end_s": 0.9, "rect": [20, 40, 280, 150]}
ENTER = {"type": "enter", "start_s": 0.0, "end_s": 0.5}


# ── the action contract: every refusal is a refusal by name ─────────────────────────────────────


def _refuse(doc, *, screens=((SHOT_W, SHOT_H),), duration_s=4.0) -> str:
    with pytest.raises(su.SceneError) as exc:
        pa.parse_actions(doc, screens=list(screens), duration_s=duration_s)
    return str(exc.value)


@pytest.mark.parametrize(
    ("action", "needle"),
    [
        ({"type": "wiggle", "start_s": 0, "end_s": 1}, "unknown action type"),
        (
            {"type": "tap", "start_s": 0, "end_s": 1, "point": [10, 10], "colour": "red"},
            "unknown key",
        ),
        ({"type": "tap", "start_s": 0, "end_s": 1}, "missing required key"),
        ({"type": "tap", "end_s": 1, "point": [10, 10]}, "missing required key"),
        ({"type": "tap", "start_s": 1, "end_s": 1, "point": [10, 10]}, "start_s < end_s"),
        ({"type": "tap", "start_s": -0.5, "end_s": 1, "point": [10, 10]}, "start_s < end_s"),
        ({"type": "tap", "start_s": 0, "end_s": 9, "point": [10, 10]}, "start_s < end_s"),
        ({"type": "tap", "start_s": 0, "end_s": 1, "point": [10, 10, 10]}, "list of 2 numbers"),
        ({"type": "tap", "start_s": 0, "end_s": 1, "point": ["x", 10]}, "finite number"),
        ({"type": "tap", "start_s": 0, "end_s": 1, "point": [10, 4000]}, "outside screen 0"),
        ({"type": "highlight", "start_s": 0, "end_s": 1, "rect": [20, 40, 20, 150]}, "0<=x0<x1"),
        ({"type": "highlight", "start_s": 0, "end_s": 1, "rect": [20, 40, 400, 150]}, "0<=x0<x1"),
        (
            {"type": "highlight", "start_s": 0, "end_s": 1, "rect": [20, 700, 280, 860]},
            "off screen",
        ),
        (
            {
                "type": "highlight",
                "start_s": 0,
                "end_s": 1,
                "rect": [20, 40, 280, 150],
                "shape": "blob",
            },
            "must be one of",
        ),
        (
            {"type": "highlight", "start_s": 0, "end_s": 1, "rect": [20, 40, 280, 150], "dim": 2},
            "within [0, 0.85]",
        ),
        (
            {"type": "zoom", "start_s": 0, "end_s": 1, "rect": [20, 40, 280, 150], "scale": 9},
            "within [1.05, 3]",
        ),
        ({"type": "scroll", "start_s": 0, "end_s": 1, "to_y": 5000}, "to_y must differ"),
        ({"type": "scroll", "start_s": 0, "end_s": 1, "from_y": 90, "to_y": 200}, "omit from_y"),
        (
            {"type": "swap", "start_s": 0, "end_s": 1, "screen": 3},
            "must be another of the 1 screens",
        ),
        (
            {"type": "swap", "start_s": 0, "end_s": 1, "screen": 0},
            "must be another of the 1 screens",
        ),
    ],
)
def test_a_malformed_action_is_refused_by_name(action, needle):
    assert needle in _refuse({"actions": [action]})


@pytest.mark.parametrize(
    ("doc", "needle"),
    [
        ({"acts": []}, 'must be an object with an "actions" list'),
        ({"actions": {}}, 'must be an object with an "actions" list'),
        ([], 'must be an object with an "actions" list'),
        ({"actions": [], "version": 2}, 'only "actions"'),
        ({"actions": [], "notes": "hi"}, 'only "actions"'),
        ({"actions": ["enter"]}, "must be an object"),
    ],
)
def test_a_malformed_document_is_refused(doc, needle):
    assert needle in _refuse(doc, duration_s=1.0)


def test_a_layout_document_is_accepted_by_parse_actions():
    """`parse_actions` only needs to let the key through; `phone_chassis.parse_layout` does the
    deep validation (checked separately below)."""
    plan = pa.parse_actions(
        {"actions": [], "layout": {"phone_h_frac": 0.6, "center_y_frac": 0.4}},
        screens=[(SHOT_W, SHOT_H)],
        duration_s=1.0,
    )
    assert plan == ()


@pytest.mark.parametrize(
    ("layout", "needle"),
    [
        ({"bogus": 1}, "layout: unknown key"),
        ({"phone_h_frac": 0.9}, "layout.phone_h_frac"),
        ({"phone_h_frac": 0.1}, "layout.phone_h_frac"),
        ({"center_y_frac": 0.9}, "layout.center_y_frac"),
        ({"center_y_frac": 0.1}, "layout.center_y_frac"),
        ({"phone_h_frac": "big"}, "layout.phone_h_frac"),
    ],
)
def test_a_bad_layout_value_is_refused_by_name(layout, needle):
    with pytest.raises(su.SceneError, match=needle):
        pc.parse_layout(layout)


def test_two_camera_actions_may_not_run_at_once():
    msg = _refuse(
        {
            "actions": [
                {"type": "zoom", "start_s": 0.0, "end_s": 2.0, "rect": [20, 40, 280, 150]},
                {"type": "zoom", "start_s": 1.5, "end_s": 3.0, "rect": [20, 180, 280, 290]},
            ]
        }
    )
    assert "only one camera action may run at a time" in msg


def test_a_mark_may_not_straddle_a_swap():
    msg = _refuse(
        {
            "actions": [
                {"type": "highlight", "start_s": 0.5, "end_s": 2.5, "rect": [20, 40, 280, 150]},
                {"type": "swap", "start_s": 1.0, "end_s": 1.6, "screen": 1},
            ]
        },
        screens=((SHOT_W, SHOT_H), (SHOT_W, SHOT_H)),
    )
    assert "cannot straddle a swap" in msg


def test_an_action_before_the_phone_enters_is_refused():
    msg = _refuse(
        {
            "actions": [
                {"type": "enter", "start_s": 1.0, "end_s": 1.6},
                {"type": "tap", "start_s": 0.2, "end_s": 0.8, "point": [40, 60]},
            ]
        }
    )
    assert "before the phone enters" in msg


def test_a_second_enter_is_refused():
    msg = _refuse(
        {
            "actions": [
                {"type": "enter", "start_s": 0.0, "end_s": 0.6},
                {"type": "enter", "start_s": 1.0, "end_s": 1.6},
            ]
        }
    )
    assert "at most one enter" in msg


def test_a_hold_longer_than_the_idle_ceiling_is_refused():
    """The scene's answer to V9/V13: a held phone reads as the slideshow this replaced."""
    msg = _refuse(
        {
            "actions": [
                {"type": "highlight", "start_s": 0.0, "end_s": 4.0, "rect": [20, 40, 280, 150]}
            ]
        }
    )
    assert "nothing moves between" in msg and f"{pa.MAX_IDLE_S:g}s" in msg


def test_a_screenshot_that_is_not_phone_shaped_is_refused():
    assert "of a phone screen" in _refuse({"actions": []}, screens=((800, 400),), duration_s=1.0)


def test_a_valid_list_resolves_its_defaults_and_binds_each_action_to_a_screen():
    plan = pa.parse_actions(
        {
            "actions": [
                {"type": "scroll", "start_s": 1.6, "end_s": 2.2, "to_y": 200},
                {"type": "enter", "start_s": 0.0, "end_s": 0.6},
                {"type": "swap", "start_s": 2.3, "end_s": 2.9, "screen": 1},
                {"type": "highlight", "start_s": 0.7, "end_s": 1.5, "rect": [20, 40, 280, 150]},
                {"type": "tap", "start_s": 3.0, "end_s": 3.6, "point": [60, 90]},
            ]
        },
        screens=[(SHOT_W, SHOT_H), (SHOT_W, SHOT_H)],
        duration_s=4.0,
    )
    assert [a.kind for a in plan] == ["enter", "highlight", "scroll", "swap", "tap"]
    assert [a.screen for a in plan] == [0, 0, 0, 1, 1], "a swap rebinds what follows it"
    scroll = plan[2]
    assert scroll.from_y == 0.0 and scroll.to_y == 200
    highlight = plan[1]
    assert 0 < highlight.draw_s <= 0.6 and highlight.fade_s > 0 and highlight.shape == "rect"
    # the scroll is at 200 mid-run and the swap lands the NEXT screen at its top
    assert pa.view_at(plan, 1.9)[0][1] == pytest.approx(100.0, abs=1.0)
    assert pa.view_at(plan, 3.0) == [(1, 0.0, 0.0)]
    assert len(pa.view_at(plan, 2.6)) == 2, "mid-swap both screens are on screen"


def test_a_target_below_the_fold_is_allowed_once_a_scroll_reaches_it():
    plan = pa.parse_actions(
        {
            "actions": [
                {"type": "scroll", "start_s": 0.0, "end_s": 1.0, "to_y": 240},
                {"type": "highlight", "start_s": 1.0, "end_s": 2.0, "rect": [20, 700, 280, 810]},
            ]
        },
        screens=[(SHOT_W, SHOT_H)],
        duration_s=2.0,
    )
    assert plan[1].rect == (20, 700, 280, 810)


# ── the CLI and the scene's own inputs ──────────────────────────────────────────────────────────


def test_the_scene_refuses_a_missing_image_or_action_list(tmp_path, shot):
    with pytest.raises(su.SceneError, match="needs --actions"):
        RENDER(kit=KIT, ratio="9:16", fps=4, duration_s=1.0, out_dir=tmp_path / "a", image=shot)
    with pytest.raises(su.SceneError, match="needs --image"):
        RENDER(
            kit=KIT,
            ratio="9:16",
            fps=4,
            duration_s=1.0,
            out_dir=tmp_path / "b",
            actions=_actions(tmp_path, HIGHLIGHT),
        )
    with pytest.raises(su.SceneError, match="--actions not found"):
        RENDER(
            kit=KIT,
            ratio="9:16",
            fps=4,
            duration_s=1.0,
            out_dir=tmp_path / "c",
            image=shot,
            actions=tmp_path / "nope.json",
        )
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(su.SceneError, match="not valid JSON"):
        RENDER(
            kit=KIT,
            ratio="9:16",
            fps=4,
            duration_s=1.0,
            out_dir=tmp_path / "d",
            image=shot,
            actions=bad,
        )
    with pytest.raises(su.SceneError, match="--image not found"):
        RENDER(
            kit=KIT,
            ratio="9:16",
            fps=4,
            duration_s=1.0,
            out_dir=tmp_path / "e",
            image=tmp_path / "missing.png",
            actions=_actions(tmp_path, HIGHLIGHT),
        )


def test_the_cli_renders_a_walkthrough_and_reports_its_frames(tmp_path, shot, capsys):
    kit_json = tmp_path / "kit.json"
    kit_json.write_text(json.dumps(KIT), encoding="utf-8")
    code = su.main(
        [
            "phone-walkthrough",
            "--kit-json",
            str(kit_json),
            "--ratio",
            "9:16",
            "--fps",
            "4",
            "--duration-s",
            "1.0",
            "--out-dir",
            str(tmp_path / "cli"),
            "--image",
            str(shot),
            "--actions",
            str(_actions(tmp_path, ENTER, HIGHLIGHT)),
        ]
    )
    assert code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["scene"] == "phone-walkthrough" and doc["frames"] == 4
    assert "timing_source" not in doc


def test_the_cli_reports_a_refused_action_list_as_error_json(tmp_path, shot, capsys):
    kit_json = tmp_path / "kit.json"
    kit_json.write_text(json.dumps(KIT), encoding="utf-8")
    bad = tmp_path / "actions.json"
    bad.write_text(json.dumps({"actions": [{"type": "nudge", "start_s": 0, "end_s": 1}]}), "utf-8")
    code = su.main(
        [
            "phone-walkthrough",
            "--kit-json",
            str(kit_json),
            "--ratio",
            "9:16",
            "--fps",
            "4",
            "--duration-s",
            "1.0",
            "--out-dir",
            str(tmp_path / "cli-bad"),
            "--image",
            str(shot),
            "--actions",
            str(bad),
        ]
    )
    assert code == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["scene"] == "phone-walkthrough" and "unknown action type" in doc["error"]
    assert not (tmp_path / "cli-bad").exists(), "a refusal writes no frames"


def test_an_actions_flag_never_reaches_another_scene(tmp_path, capsys):
    """`_SCENE_EXTRAS` again: --actions aimed at a card is inert, not a TypeError."""
    if _FONT is None or not _FONT.is_file():
        pytest.skip("no bundled TTF fixture found")
    kit_json = tmp_path / "kit.json"
    kit_json.write_text(
        json.dumps({**KIT, "typography": {"font_files": {"caption": str(_FONT)}}}), "utf-8"
    )
    assert "actions" not in su._SCENE_EXTRAS["title-close"]
    code = su.main(
        [
            "title-close",
            "--kit-json",
            str(kit_json),
            "--ratio",
            "16:9",
            "--fps",
            "2",
            "--duration-s",
            "1.0",
            "--out-dir",
            str(tmp_path / "t"),
            "--actions",
            str(tmp_path / "nope.json"),
        ]
    )
    assert code == 0 and json.loads(capsys.readouterr().out)["frames"] == 2


# ── the pixels ──────────────────────────────────────────────────────────────────────────────────


def _frames(out_dir: Path) -> list[Path]:
    return sorted(out_dir.glob("phone-walkthrough-*.png"))


def test_rerunning_with_the_same_inputs_is_byte_identical(tmp_path, shot, shot_b):
    items = (
        ENTER,
        {
            "type": "highlight",
            "start_s": 0.6,
            "end_s": 1.4,
            "rect": [20, 40, 280, 150],
            "shape": "circle",
        },
        {"type": "tap", "start_s": 1.5, "end_s": 2.0, "point": [60, 95]},
        {"type": "scroll", "start_s": 2.0, "end_s": 2.6, "to_y": 220},
        {"type": "zoom", "start_s": 2.7, "end_s": 3.6, "rect": [20, 320, 280, 430]},
        {"type": "swap", "start_s": 3.7, "end_s": 4.2, "screen": 1},
    )
    kwargs = {"stills": [shot_b], "duration_s": 4.4, "fps": 6}
    a = _render(tmp_path, shot, *items, out="a", **kwargs)
    b = _render(tmp_path, shot, *items, out="b", **kwargs)
    assert a == b > 0
    for fa, fb in zip(_frames(tmp_path / "a"), _frames(tmp_path / "b"), strict=True):
        assert fa.read_bytes() == fb.read_bytes()


def test_every_frame_is_the_ratios_pixel_size_and_carries_no_alpha(tmp_path, shot):
    _render(tmp_path, shot, HIGHLIGHT, ratio="9:16", fps=4)
    for png in _frames(tmp_path / "out"):
        with Image.open(png) as img:
            assert img.size == (1080, 1920) and img.mode == "RGB"


def _phone_box(frame: Image.Image) -> tuple[int, int, int, int]:
    """The device's bounding box: pixels far enough from the canvas to be chassis, not shadow."""
    canvas = Image.new("RGB", frame.size, (0xEF, 0xEB, 0xE3))
    diff = ImageChops.difference(frame, canvas).convert("L")
    return diff.point(lambda v: 255 if v > 90 else 0).getbbox()


def test_the_chassis_is_one_centred_device_of_the_declared_size(tmp_path, shot):
    _render(tmp_path, shot, ratio="9:16", fps=2, duration_s=1.0)
    with Image.open(_frames(tmp_path / "out")[0]) as frame:
        box = _phone_box(frame.convert("RGB"))
        w, h = frame.size
    x0, y0, x1, y1 = box
    assert 0.70 <= (y1 - y0) / h <= 0.76, f"phone height {(y1 - y0) / h:.3f} of the frame"
    assert abs((x0 + x1) / 2 - w / 2) <= 3 and abs((y0 + y1) / 2 - h / 2) <= 6
    assert 0.44 <= (x1 - x0) / (y1 - y0) <= 0.52, "one phone-shaped chassis, not a slab"
    assert x0 > 0 and y0 > 0 and x1 < w and y1 < h, "the device sits inside the frame"


def test_a_layout_matching_the_defaults_is_byte_identical_to_no_layout_key(tmp_path, shot):
    kwargs = {"ratio": "9:16", "fps": 4, "duration_s": 1.0}
    a = _render(tmp_path, shot, ENTER, HIGHLIGHT, out="a", **kwargs)
    b = _render(
        tmp_path,
        shot,
        ENTER,
        HIGHLIGHT,
        out="b",
        layout={"phone_h_frac": pc.PHONE_H_FRAC, "center_y_frac": 0.5},
        **kwargs,
    )
    assert a == b > 0
    for fa, fb in zip(_frames(tmp_path / "a"), _frames(tmp_path / "b"), strict=True):
        assert fa.read_bytes() == fb.read_bytes()


def test_a_layout_resizes_and_moves_the_phone(tmp_path, shot):
    """The task's own worked example: 0.57/0.37 puts the phone roughly at y in [160, 1255] on a
    1920-tall frame."""
    _render(
        tmp_path,
        shot,
        ratio="9:16",
        fps=2,
        duration_s=1.0,
        layout={"phone_h_frac": 0.57, "center_y_frac": 0.37},
    )
    with Image.open(_frames(tmp_path / "out")[0]) as frame:
        box = _phone_box(frame.convert("RGB"))
        w, h = frame.size
    x0, y0, x1, y1 = box
    assert (y1 - y0) / h == pytest.approx(0.57, abs=0.01)
    assert abs((x0 + x1) / 2 - w / 2) <= 3, "still horizontally centred"
    assert 155 <= y0 <= 170 and 1245 <= y1 <= 1265, (y0, y1)


def test_zoom_and_highlight_still_work_with_a_moved_phone(tmp_path, shot):
    """(a): zoom, enter/exit travel, highlights and taps keep working with the moved phone —
    exercised here as a zoom that still enlarges its target region and returns, same as
    `test_a_zoom_fills_the_frame_with_one_region_and_comes_back` but at a custom layout."""
    rect = [20, 40, 280, 150]
    layout = {"phone_h_frac": 0.57, "center_y_frac": 0.37}
    _render(
        tmp_path,
        shot,
        {"type": "zoom", "start_s": 0.0, "end_s": 2.0, "rect": rect, "ease_s": 0.5},
        fps=6,
        duration_s=2.5,
        layout=layout,
    )
    frames = _frames(tmp_path / "out")
    hue = (206, 214, 210)
    geom = pc.phone_geometry(1080, 1920, layout=pc.PhoneLayout(**layout))
    f = (geom.phone_w - 2 * geom.bezel) / SHOT_W
    rest_y = round(geom.origin[1] + geom.bezel + (rect[1] + rect[3]) / 2 * f)
    with Image.open(frames[0]) as first:
        at_rest = _band_width(first.convert("RGB"), rest_y, hue)
    with Image.open(frames[len(frames) // 2]) as middle:
        zoomed = _band_width(middle.convert("RGB"), round(1920 * 0.42), hue)
    with Image.open(frames[-1]) as last:
        returned = _band_width(last.convert("RGB"), rest_y, hue)
    assert at_rest > 0, at_rest
    assert zoomed > at_rest * 1.4, f"the region reads {zoomed}px zoomed vs {at_rest}px at rest"
    assert returned == pytest.approx(at_rest, abs=4), "and the camera comes back"


@pytest.mark.parametrize(
    ("layout", "needle"),
    [
        ({"bogus": 1}, "layout: unknown key"),
        ({"phone_h_frac": 0.9}, "layout.phone_h_frac"),
        ({"center_y_frac": 0.9}, "layout.center_y_frac"),
    ],
)
def test_the_render_refuses_a_bad_layout_before_writing_frames(tmp_path, shot, layout, needle):
    with pytest.raises(su.SceneError, match=needle):
        _render(tmp_path, shot, HIGHLIGHT, layout=layout)
    assert not (tmp_path / "out").exists(), "a refusal writes no frames"


def test_no_enter_or_exit_is_fully_visible_on_the_first_and_last_frame(tmp_path, shot):
    """(d): with no enter and no exit, the phone is at rest and fully opaque throughout —
    `MAX_IDLE_S` still applies, so this stays inside its 2.5s ceiling."""
    _render(tmp_path, shot, ratio="9:16", fps=4, duration_s=1.0)
    frames = _frames(tmp_path / "out")
    with Image.open(frames[0]) as first, Image.open(frames[-1]) as last:
        box0 = _phone_box(first.convert("RGB"))
        box1 = _phone_box(last.convert("RGB"))
    for box in (box0, box1):
        assert box is not None
        x0, y0, x1, y1 = box
        assert x0 > 0 and y0 > 0 and x1 > x0 and y1 > y0


def test_the_chassis_is_the_same_device_at_every_ratio(tmp_path, shot):
    shapes = []
    for ratio in ("16:9", "9:16"):
        _render(tmp_path, shot, ratio=ratio, fps=2, duration_s=1.0, out=ratio.replace(":", "x"))
        with Image.open(_frames(tmp_path / ratio.replace(":", "x"))[0]) as frame:
            x0, y0, x1, y1 = _phone_box(frame.convert("RGB"))
        shapes.append((x1 - x0) / (y1 - y0))
    assert shapes[0] == pytest.approx(shapes[1], abs=0.01)


def test_the_screenshot_reaches_the_screen_unredrawn(tmp_path, shot):
    """Scale only: the colour under the phone's screen is the screenshot's, not a repaint."""
    _render(tmp_path, shot, ratio="9:16", fps=2, duration_s=1.0)
    with Image.open(_frames(tmp_path / "out")[0]) as frame:
        rgb = frame.convert("RGB")
        geom = pc.phone_geometry(*rgb.size)
    ox, oy = geom.origin
    patch = rgb.crop(
        (
            round(ox + geom.phone_w * 0.45),
            round(oy + geom.phone_h * 0.20),
            round(ox + geom.phone_w * 0.55),
            round(oy + geom.phone_h * 0.24),
        )
    )
    mean = ImageStat.Stat(patch).mean
    assert all(abs(m - c) < 12 for m, c in zip(mean, (206, 214, 210), strict=True)), mean


def test_a_scroll_moves_the_screenshot_inside_the_screen(tmp_path, shot):
    _render(
        tmp_path,
        shot,
        {"type": "scroll", "start_s": 0.0, "end_s": 1.0, "to_y": 240},
        fps=6,
        duration_s=1.0,
    )
    first, last = _frames(tmp_path / "out")[0], _frames(tmp_path / "out")[-1]
    with Image.open(first) as a, Image.open(last) as b:
        assert ImageStat.Stat(ImageChops.difference(a.convert("L"), b.convert("L"))).mean[0] > 2
        assert _phone_box(a.convert("RGB")) == _phone_box(b.convert("RGB")), (
            "the device holds still"
        )


def _band_width(frame: Image.Image, y: int, hue: tuple[int, int, int]) -> int:
    """How wide the screenshot's coloured band reads at row ``y`` — the apparent size of a region."""
    strip = frame.crop((0, max(0, y - 4), frame.width, min(frame.height, y + 4)))
    flat = Image.new("RGB", strip.size, hue)
    r, g, b = ImageChops.difference(strip, flat).split()
    worst = ImageChops.lighter(ImageChops.lighter(r, g), b)
    mask = worst.point(lambda v: 255 if v < 18 else 0)
    # Matched pixels per row, not the bounding box: one stray match in the chassis would widen a
    # box by 300px and say nothing about how big the region reads.
    return round(mask.histogram()[255] / max(1, strip.height))


def test_a_zoom_fills_the_frame_with_one_region_and_comes_back(tmp_path, shot):
    """The whole point of the zoom: the REGION gets bigger, not a 1.06x push on the whole card."""
    rect = [20, 40, 280, 150]
    _render(
        tmp_path,
        shot,
        {"type": "zoom", "start_s": 0.0, "end_s": 2.0, "rect": rect, "ease_s": 0.5},
        fps=6,
        duration_s=2.5,  # past the zoom, so the last frame is the camera back at rest
    )
    frames = _frames(tmp_path / "out")
    hue = (206, 214, 210)
    geom = pc.phone_geometry(1080, 1920)
    f = (geom.phone_w - 2 * geom.bezel) / SHOT_W
    rest_y = round(geom.origin[1] + geom.bezel + (rect[1] + rect[3]) / 2 * f)
    with Image.open(frames[0]) as first:
        at_rest = _band_width(first.convert("RGB"), rest_y, hue)
    with Image.open(frames[len(frames) // 2]) as middle:
        zoomed = _band_width(middle.convert("RGB"), round(1920 * 0.42), hue)
    with Image.open(frames[-1]) as last:
        returned = _band_width(last.convert("RGB"), rest_y, hue)
    assert at_rest > 400, at_rest
    assert zoomed > at_rest * 1.4, f"the region reads {zoomed}px zoomed vs {at_rest}px at rest"
    assert returned == pytest.approx(at_rest, abs=4), "and the camera comes back"


def test_the_shot_clears_the_video_lint_motion_floor(tmp_path, shot):
    """V9 measures the mean inter-frame delta; this is that number, computed on the PNGs.

    A walkthrough that fails here is the defect the scene exists to fix — `still-push`'s 1.06x
    push measured under the floor and shipped as a static shot.
    """
    _render(
        tmp_path,
        shot,
        {"type": "enter", "start_s": 0.0, "end_s": 0.6},
        {"type": "highlight", "start_s": 0.7, "end_s": 1.4, "rect": [20, 40, 280, 150]},
        {"type": "scroll", "start_s": 1.5, "end_s": 2.0, "to_y": 240},
        fps=30,
        duration_s=2.0,
    )
    frames = _frames(tmp_path / "out")
    deltas = []
    previous = None
    for path in frames:
        with Image.open(path) as img:
            current = img.convert("L")
        if previous is not None:
            deltas.append(ImageStat.Stat(ImageChops.difference(current, previous)).mean[0] / 255)
        previous = current
    motion = sum(deltas) / len(deltas)
    assert motion >= MIN_SHOT_MOTION, f"mean inter-frame delta {motion:.4f} < {MIN_SHOT_MOTION}"


def test_the_highlight_paints_the_kits_own_action_colour(tmp_path, shot):
    _render(
        tmp_path,
        shot,
        {
            "type": "highlight",
            "start_s": 0.0,
            "end_s": 1.0,
            "rect": [20, 40, 280, 150],
            "draw_s": 0.1,
        },
        fps=4,
        duration_s=1.0,
    )
    with Image.open(_frames(tmp_path / "out")[1]) as frame:
        colors = frame.convert("RGB").getcolors(1920 * 1080) or []
    target = (0xB5, 0x50, 0x2E)
    assert any(
        count > 200 and all(abs(a - b) < 26 for a, b in zip(pixel, target, strict=True))
        for count, pixel in colors
    ), "no run of the kit's primary reached the frame"
