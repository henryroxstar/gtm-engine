"""`gtm_core.screen_ui` chat-bubble — a message beside a phone, drawn to land ON a shot.

Real Pillow, no mocking, same as `test_screen_ui_message_card.py`. The properties pinned here each
fail SILENTLY without a test: a flattened sequence blacks out the plate; a bubble on the wrong
side of the frame points its tail away from the phone; a chip that wraps is a bubble; a truncated
message reads as a complete one; a copy key nobody validates ships the placeholder as if it were
the film's line; and a glyph the face lacks renders as a tofu box on every frame.

Fixture font is matplotlib's bundled DejaVuSans.ttf — no tenant identity, and it has no emoji,
which is exactly what the glyph test needs. Copy is fictional throughout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib
import pytest
from PIL import Image

from gtm_core import screen_ui as su
from gtm_core.screen_ui.scenes import chat_bubble as cb

_FONT = next(iter(Path(matplotlib.get_data_path()).rglob("DejaVuSans.ttf")), None)


def _kit() -> dict:
    return {
        "palette": {
            "canvas": "#EDE8DC",
            "surface": "#F5F1E8",
            "ink": "#1E1C16",
            "accent": "#D98E6A",
        },
        "typography": {"font_files": {"caption": str(_FONT)}},
    }


@pytest.fixture(autouse=True)
def _skip_without_font():
    if _FONT is None or not _FONT.is_file():
        pytest.skip("no bundled TTF fixture found")


def _render(tmp_path, **kw):
    defaults = {"kit": _kit(), "ratio": "9:16", "fps": 8, "duration_s": 2.0, "out_dir": tmp_path}
    defaults.update(kw)
    return cb.render_chat_bubble_frames(**defaults)


def _last(tmp_path) -> Image.Image:
    return Image.open(sorted(tmp_path.glob("chat-bubble-*.png"))[-1]).convert("RGBA")


def _half_alpha_means(img: Image.Image) -> tuple[float, float]:
    """Mean alpha of the left and right halves — where the ink is, without reading a layout."""
    alpha = img.getchannel("A")
    w, h = img.size
    left = alpha.crop((0, 0, w // 2, h))
    right = alpha.crop((w // 2, 0, w, h))
    n = (w // 2) * h
    return sum(left.get_flattened_data()) / n, sum(right.get_flattened_data()) / n


# --- it composites, it does not replace ---------------------------------------------------------


def test_the_bubble_writes_real_alpha_so_the_plate_survives_under_it(tmp_path):
    _render(tmp_path)
    img = _last(tmp_path)
    assert img.mode == "RGBA"
    lo, hi = img.getchannel("A").getextrema()
    assert hi > 200, "nothing opaque was drawn"
    assert lo == 0, "no fully transparent region — the plate would be replaced, not overlaid"


def test_frame_zero_is_fully_transparent_so_the_shot_opens_on_its_own_picture(tmp_path):
    """`video_finish.overlays` relies on this: it pads the sequence before and after the bubble's
    window with COPIES of frame zero, so this is the one frame that must never carry ink."""
    _render(tmp_path)
    first = Image.open(sorted(tmp_path.glob("chat-bubble-*.png"))[0]).convert("RGBA")
    assert first.getchannel("A").getextrema() == (0, 0)


def test_no_full_frame_scrim_the_picture_stays_visible_around_the_bubble(tmp_path):
    """`message-card` scrims the whole frame; a bubble beside a phone must not — the phone and
    the hand holding it ARE the shot. Far from the bubble the frame is untouched."""
    _render(tmp_path, bubble={"kind": "outgoing", "text": "One short line."})
    alpha = _last(tmp_path).getchannel("A")
    w, h = alpha.size
    corner = alpha.crop((0, 0, w // 4, h // 4))
    assert corner.getextrema() == (0, 0)


@pytest.mark.parametrize("ratio,size", [("9:16", (1080, 1920)), ("4:5", (1080, 1350))])
def test_every_frame_is_the_target_ratios_pixel_size(tmp_path, ratio, size):
    _render(tmp_path, ratio=ratio)
    for png in tmp_path.glob("chat-bubble-*.png"):
        with Image.open(png) as img:
            assert img.size == size


def test_rerunning_with_the_same_inputs_is_byte_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _render(a)
    _render(b)
    pairs = zip(sorted(a.glob("*.png")), sorted(b.glob("*.png")), strict=True)
    for left, right in pairs:
        assert left.read_bytes() == right.read_bytes()


# --- the side is the tail's direction, and the tail points at the phone -------------------------


def test_an_outgoing_bubble_hugs_the_right_edge_by_default(tmp_path):
    """The overlay grammar the shot list was written to: outgoing sits RIGHT of the phone."""
    _render(tmp_path, bubble={"kind": "outgoing", "text": "One short line."})
    left, right = _half_alpha_means(_last(tmp_path))
    assert right > 0 and left == 0, (left, right)


@pytest.mark.parametrize("kind", ["incoming", "chip"])
def test_incoming_and_chip_hug_the_left_edge_by_default(tmp_path, kind):
    _render(tmp_path, bubble={"kind": kind, "text": "One short line."})
    left, right = _half_alpha_means(_last(tmp_path))
    assert left > 0 and right == 0, (kind, left, right)


def test_side_overrides_the_kinds_default(tmp_path):
    _render(tmp_path, bubble={"kind": "outgoing", "text": "One short line.", "side": "left"})
    left, right = _half_alpha_means(_last(tmp_path))
    assert left > 0 and right == 0, (left, right)


def test_the_shot_list_spellings_normalise_to_the_three_kinds():
    assert cb.resolve_bubble({"kind": "outgoing bubble", "text": "x"})["side"] == "right"
    assert cb.resolve_bubble({"kind": "incoming bubble", "text": "x"})["side"] == "left"
    chip = cb.resolve_bubble({"kind": "Action Chip", "text": "x"})
    assert (chip["kind"], chip["side"], chip["w_frac"]) == ("chip", "left", 0.40)


# --- the geometry the sidecar records is measured, not guessed ----------------------------------


def test_bubble_geometry_bounds_the_opaque_pixels_and_sits_inside_the_frame(tmp_path):
    """`video_finish.overlays` writes this box into its sidecar for a stitch-time reader and a
    lint. It must be where the ink IS: every opaque pixel inside it, and it inside the frame."""
    bubble = {"kind": "incoming", "text": "Could you send that over before the call?"}
    _render(tmp_path, bubble=bubble)
    box = cb.bubble_geometry(kit=_kit(), ratio="9:16", bubble=bubble)
    img = _last(tmp_path)
    w, h = img.size
    assert 0 <= box["x"] and box["x"] + box["w"] <= w
    assert 0 <= box["y"] and box["y"] + box["h"] <= h
    # Opaque ink only — the drop shadow is soft and low-alpha by design and is NOT in the box.
    opaque = img.getchannel("A").point(lambda v: 255 if v >= 250 else 0)
    ink = opaque.getbbox()
    assert ink is not None
    assert ink[0] >= box["x"] and ink[1] >= box["y"]
    assert ink[2] <= box["x"] + box["w"] and ink[3] <= box["y"] + box["h"]


def test_a_tall_bubble_is_lifted_clear_of_the_caption_band_and_the_box_says_so(tmp_path):
    """`_text_band` exists because two legible strings in the same rows is the defect. A bubble
    asked to centre low enough to reach the caption's rows is lifted, and the measured box —
    not the asked-for centre — is what a reader gets."""
    bubble = {
        "kind": "incoming",
        "text": "Could you send that over before the call?",
        "y_frac": 0.9,
    }
    _render(tmp_path, bubble=bubble)
    box = cb.bubble_geometry(kit=_kit(), ratio="9:16", bubble=bubble)
    img = _last(tmp_path)
    w, h = img.size
    band_top = int(h * su.CAPTION_BAND_TOP_FRAC)
    assert box["y"] + box["h"] <= band_top
    below = img.getchannel("A").crop((0, band_top, w, h))
    assert below.getextrema()[1] < 250, "opaque bubble pixels inside the caption band"


# --- it refuses rather than truncating, and says what to do ---------------------------------------


def test_a_message_too_long_for_the_bubble_is_refused_with_a_width_remedy(tmp_path):
    """At 9:16 the default width holds about fourteen characters a line at the floor size, and a
    two-line message from a real shot list wrapped to six. "Shorten the copy" is one of two
    decisions; the refusal has to name the other, because only the person looking at the plate
    knows how much room the phone can spare."""
    text = "write a post about the release, in my own voice, and link the commit\nplus the notes"
    with pytest.raises(su.SceneError, match=r"shorten the copy, or set w_frac=0\.\d\d") as exc:
        _render(tmp_path, bubble={"kind": "outgoing", "text": text})
    # and the named remedy is real: the same copy renders at the width the refusal suggested.
    suggested = re.search(r"w_frac=(0\.\d\d)", str(exc.value)).group(1)
    _render(tmp_path, bubble={"kind": "outgoing", "text": text, "w_frac": suggested})


def test_copy_past_any_width_is_refused_without_a_remedy(tmp_path):
    with pytest.raises(su.SceneError, match="shorten the copy$"):
        _render(tmp_path, bubble={"kind": "outgoing", "text": "word " * 80})


def test_a_chip_is_one_line_and_a_hard_break_is_refused(tmp_path):
    assert cb._MAX_LINES["chip"] == 1
    with pytest.raises(su.SceneError, match="1-line ceiling"):
        _render(tmp_path, bubble={"kind": "chip", "text": "Open\nthe app"})


def test_a_glyph_the_face_lacks_is_refused_naming_the_character(tmp_path):
    """DejaVuSans has no emoji. A rendered tofu box would make the emoji-or-not decision silently
    and in the wrong direction on every frame; the caller makes it, told which character."""
    with pytest.raises(su.SceneError, match="no glyph for \\['🚀'\\]"):
        _render(tmp_path, bubble={"kind": "incoming", "text": "Thrilled to announce 🚀"})


def test_symbols_the_face_does_carry_are_not_false_positives(tmp_path):
    """The chip in the film reads "↓ <name>"; an arrow, an em dash and an ellipsis are glyphs
    DejaVuSans has, and the missing-glyph check must not mistake them for tofu."""
    _render(tmp_path / "chip", bubble={"kind": "chip", "text": "↓ Open…"})
    _render(tmp_path / "bubble", bubble={"kind": "incoming", "text": "Ready — send it…"})


def test_an_unknown_copy_key_is_refused_rather_than_silently_dropped(tmp_path):
    with pytest.raises(su.SceneError, match="unknown key"):
        _render(tmp_path, bubble={"txet": "hello"})


@pytest.mark.parametrize(
    "bad,needle",
    [
        ({"kind": "bubble"}, "kind 'bubble'"),
        ({"text": "  "}, "empty string"),
        ({"side": "up"}, "side 'up'"),
        ({"y_frac": "1.2"}, "y_frac"),
        ({"w_frac": "0"}, "w_frac"),
        ({"arrive_s": "-1"}, "arrive_s"),
    ],
)
def test_a_bad_value_is_refused_by_name(tmp_path, bad, needle):
    with pytest.raises(su.SceneError, match=needle):
        _render(tmp_path, bubble={"kind": "outgoing", "text": "hello", **bad})


def test_a_bubble_arriving_after_the_shot_ends_is_refused(tmp_path):
    with pytest.raises(su.SceneError, match="would never appear"):
        _render(tmp_path, duration_s=1.0, bubble={"arrive_s": "1.5"})


def test_the_defaults_carry_no_digits():
    """A bubble dramatizes a real-looking exchange; a digit reads as a quantity someone can be
    held to. Same rule, and same reason, as `message-card`'s defaults."""
    joined = " ".join(v for k, v in cb._BUBBLE_DEFAULTS.items() if k == "text")
    assert not any(ch.isdigit() for ch in joined), joined


# --- the CLI flag reaches the scene, and only this scene -----------------------------------------


def test_the_cli_bubble_flag_is_wired_through_to_the_drawing(tmp_path, capsys):
    kit_json = tmp_path / "kit.json"
    kit_json.write_text(json.dumps(_kit()), encoding="utf-8")
    argv = ["chat-bubble", "--kit-json", str(kit_json), "--ratio", "9:16", "--fps", "4"]
    argv += ["--duration-s", "1.0", "--bubble", "kind=incoming", "--bubble", "text=Coffee?"]
    assert su.main([*argv, "--out-dir", str(tmp_path / "a")]) == 0
    assert json.loads(capsys.readouterr().out)["frames"] == 4
    assert su.main([*argv, "--out-dir", str(tmp_path / "b"), "--bubble", "side=right"]) == 0
    left_a, _ = _half_alpha_means(_last(tmp_path / "a"))
    _, right_b = _half_alpha_means(_last(tmp_path / "b"))
    assert left_a > 0 and right_b > 0


def test_the_flag_is_declared_for_this_scene_only():
    assert su._SCENE_EXTRAS["chat-bubble"] == frozenset({"bubble"})
    assert all("bubble" not in v for k, v in su._SCENE_EXTRAS.items() if k != "chat-bubble")
