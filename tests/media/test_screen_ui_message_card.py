"""`gtm_core.screen_ui` message-card — the one scene that lands ON a shot rather than being one.

Real Pillow, no mocking, same as `test_screen_ui.py`. These pin the four properties the scene was
written for, because each of them fails SILENTLY: a flattened sequence blacks out the plate it was
meant to sit on, a truncated body reads as a complete sentence, a card that grows past the caption
band puts two legible strings in the same pixels, and a copy key nobody validates lets a typo ship
the generic default as if it were the film's own line.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import pytest
from PIL import Image

from gtm_core import screen_ui as su

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
    return su.render_message_card_frames(**defaults)


# --- it composites, it does not replace ---------------------------------------------------------


def test_the_card_writes_real_alpha_so_the_plate_survives_under_it(tmp_path):
    """Flattened to RGB this would composite as an opaque rectangle over the whole shot."""
    _render(tmp_path)
    img = Image.open(sorted(tmp_path.glob("*.png"))[-1])
    assert img.mode == "RGBA"
    lo, hi = img.convert("RGBA").getchannel("A").getextrema()
    assert hi > 200, "nothing opaque was drawn"
    assert lo < 200, "no translucent region — the plate would be replaced, not overlaid"


def test_frame_zero_is_fully_transparent_so_the_shot_opens_on_its_own_picture(tmp_path):
    _render(tmp_path)
    first = Image.open(sorted(tmp_path.glob("*.png"))[0]).convert("RGBA")
    assert first.getchannel("A").getextrema() == (0, 0)


@pytest.mark.parametrize("ratio,size", [("9:16", (1080, 1920)), ("4:5", (1080, 1350))])
def test_every_frame_is_the_target_ratios_pixel_size(tmp_path, ratio, size):
    _render(tmp_path, ratio=ratio)
    for png in tmp_path.glob("*.png"):
        with Image.open(png) as img:
            assert img.size == size


def test_rerunning_with_the_same_inputs_is_byte_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _render(a)
    _render(b)
    for left, right in zip(sorted(a.glob("*.png")), sorted(b.glob("*.png")), strict=True):
        assert left.read_bytes() == right.read_bytes()


# --- the copy is data, and unvalidated data is the failure --------------------------------------


def test_an_unknown_copy_key_is_refused_rather_than_silently_dropped(tmp_path):
    """A typo'd key would otherwise ship the generic default as if it were the film's line."""
    with pytest.raises(su.SceneError, match="unknown key"):
        _render(tmp_path, message={"snder": "A. Mercer"})


def test_an_empty_copy_value_is_refused(tmp_path):
    with pytest.raises(su.SceneError, match="empty string"):
        _render(tmp_path, message={"body": "   "})


def test_the_supplied_copy_is_what_gets_drawn_not_the_default(tmp_path):
    """Two different bodies must not produce the same pixels — the only proof `message=` is wired
    through to the drawing rather than accepted and discarded."""
    a, b = tmp_path / "a", tmp_path / "b"
    _render(a, message={"body": "One short line."})
    _render(b, message={"body": "A different short line."})
    assert sorted(a.glob("*.png"))[-1].read_bytes() != sorted(b.glob("*.png"))[-1].read_bytes()


def test_the_defaults_carry_no_digits(tmp_path):
    """The card dramatizes a real-looking inbound message. A digit in it reads as a real quantity
    someone can be held to, which is the line between a dramatization and a fabricated record."""
    joined = " ".join(su._MESSAGE_DEFAULTS.values())
    assert not any(ch.isdigit() for ch in joined), joined


# --- it refuses rather than truncating ----------------------------------------------------------


def test_a_body_too_long_for_the_card_is_refused_not_cut_off(tmp_path):
    with pytest.raises(su.SceneError, match="shorten the message"):
        _render(tmp_path, message={"body": "word " * 120})


def test_a_body_that_fills_the_card_still_renders(tmp_path):
    """The refusal above has to be a real ceiling, not a scene that refuses everything past one
    line — a test that only proves the failure path would pass on a broken scene."""
    _render(
        tmp_path,
        message={
            "body": "Read your post on agent reliability. Free for fifteen minutes this week?"
        },
    )
    assert len(list(tmp_path.glob("*.png"))) == 16


# --- it stays out of the caption band -----------------------------------------------------------


@pytest.mark.parametrize("ratio", ["9:16", "4:5"])
def test_the_card_clears_the_band_a_caption_is_burned_into(tmp_path, ratio):
    """`_text_band` exists because two legible strings in the same rows is the defect. The card is
    laid out inside that band, so nothing it draws may reach the caption's own rows."""
    _render(tmp_path, ratio=ratio)
    img = Image.open(sorted(tmp_path.glob("*.png"))[-1]).convert("RGBA")
    w, h = img.size
    band_top = int(h * su.CAPTION_BAND_TOP_FRAC)
    alpha = img.getchannel("A")
    # The scrim covers the whole frame at partial alpha by design; only the CARD (opaque) is
    # bound by the band, so the check is for opaque ink, not for any alpha at all.
    for y in range(band_top, h):
        row = [alpha.getpixel((x, y)) for x in range(0, w, 8)]
        assert max(row) < 200, f"opaque card pixels at y={y}, inside the caption band"


def test_the_body_is_legible_early_because_a_message_arrives(tmp_path):
    """The card is a HOOK. Its first cut started the body at 0.22 of shot progress, which on a 3.5s
    beat put the proposition on screen at ~1.2s — a third of the beat spent on a sender's name
    before the sentence worth reading exists. The body must be substantially up by 20% in."""
    _render(
        tmp_path, fps=20, duration_s=3.5, message={"body": "Free for fifteen minutes this week?"}
    )
    frames = sorted(tmp_path.glob("*.png"))
    early = Image.open(frames[int(len(frames) * 0.20)]).convert("RGBA")
    final = Image.open(frames[-1]).convert("RGBA")
    w, h = final.size

    # Ink is dark pixels inside the card; count them in the body's own half of the card.
    def ink(img):
        band = img.convert("L").crop((0, int(h * 0.30), w, int(h * 0.60)))
        return sum(1 for p in band.getdata() if p < 90)

    assert ink(final) > 0, "no body ink in the final frame — the probe band is wrong"
    assert ink(early) > 0.5 * ink(final), (
        f"only {ink(early)}/{ink(final)} of the body ink is up at 20% of the shot — the hook's "
        "proposition arrives too late to hold a cold scroll"
    )
