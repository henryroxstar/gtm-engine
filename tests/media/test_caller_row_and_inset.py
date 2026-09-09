"""The v8 Act-1 composition: a four-up call row, and the footage placed into it.

These cover the three pieces added on 2026-09-04 — `screen_ui`'s `caller-row` scene,
`video_finish.inset_video`, and `burn_captions`' `caption_segments` — and specifically the
properties that were WRONG in the first build, because those are the ones a later change will
break again.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import pytest
from PIL import Image, ImageDraw

import gtm_core.screen_ui as su
from gtm_core.screen_ui.base import SceneError
from gtm_core.screen_ui.draw import CAPTION_BAND_TOP_FRAC
from gtm_core.screen_ui.scenes.caller_row import (
    _CallerRowSpec,
    _slots,
    active_rect,
    agent_panel_rect,
    render_caller_row_frames,
)
from gtm_core.video_finish import inset_video

_FONT = next(iter(Path(matplotlib.get_data_path()).rglob("DejaVuSans.ttf")), None)


def _kit() -> dict:
    return {
        "palette": {
            "canvas": "#0B1A2E",
            "surface": "#1A2B3C",
            "ink": "#FFFFFF",
            "accent": "#00C6C6",
        },
        "typography": {"font_files": {"caption": str(_FONT)}},
    }


@pytest.fixture(autouse=True)
def _skip_without_font():
    if _FONT is None or not _FONT.is_file():
        pytest.skip("no bundled TTF fixture found")


def _stills(tmp_path: Path, n: int = 4) -> list[Path]:
    out = []
    for i in range(n):
        p = tmp_path / f"caller-{i}.png"
        img = Image.new("RGB", (1920, 1080), (20 + i * 40, 30, 40))
        ImageDraw.Draw(img).ellipse((820, 220, 1100, 560), fill=(200, 180 - i * 20, 160))
        img.save(p)
        out.append(p)
    return out


# --- the row's geometry ------------------------------------------------------------------------


@pytest.mark.parametrize("active", [0, 1, 2, 3])
def test_nothing_in_the_layout_moves_with_the_active_caller(active):
    """NOTHING moves — the property the 2026-09-04 rework exists to give.

    Before it, the lit caller sat inside the strip and the three dimmed ones were pushed either
    side of it, so their positions changed every beat and the strip read as broken rather than as
    a participant rail. Now the large tile, the four thumb slots and the agent's panel are all
    fixed, and the only thing that changes between beats is which thumb carries the ring.
    """
    assert active_rect(1920, 1080, active) == (210, 100, 1020, 574)
    assert agent_panel_rect(1920, 1080, active) == (1290, 100, 420, 574)
    assert [r[:2] for r in _slots(1920, 1080, active)] == [
        (517, 692),
        (623, 692),
        (729, 692),
        (835, 692),
    ]


def test_the_participant_strip_is_centred_under_the_large_tile():
    """Four 88px thumbs occupy 406 of the tile's 1020px. Hung off its left edge they read as a
    strip that ran out; centred they read as a rail."""
    ax, _, aw, _ = active_rect(1920, 1080)
    rects = _slots(1920, 1080)
    strip_mid = (rects[0][0] + rects[-1][0] + rects[-1][2]) / 2
    assert abs(strip_mid - (ax + aw / 2)) <= 1


@pytest.mark.parametrize("active", [0, 1, 2, 3])
def test_every_tile_clears_the_burned_caption_band(active):
    for x, y, w, h in [active_rect(1920, 1080, active), *_slots(1920, 1080, active)]:
        assert y >= round(1080 * 0.08), "a tile started above the safe area"
        assert y + h <= round(1080 * CAPTION_BAND_TOP_FRAC), "a tile ran into the caption band"


# --- the refusals ------------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 3, 5])
def test_the_row_refuses_anything_but_four_stills(tmp_path, count):
    """A missing still would otherwise render as a blank tile that looks deliberate in QA."""
    with pytest.raises(SceneError, match="exactly 4 caller stills"):
        render_caller_row_frames(
            kit=_kit(),
            ratio="16:9",
            fps=6,
            duration_s=0.5,
            out_dir=tmp_path,
            stills=_stills(tmp_path, count) if count else None,
        )


def test_an_org_label_that_would_draw_through_the_call_meta_is_refused(tmp_path):
    """`_fit_tracked` RETURNS ITS FLOOR when even the floor overruns — its own docstring says so
    and asks for a refusing twin before the first long tracked string. This is that string: the
    v8 build drew "THE PHONE COMPANY'S AGENT" straight through the live timer and reported
    success. The label is the payload of the whole composition (the only statement of whose agent
    this is), so the fix is shorter copy or a wider column, never smaller type — and when this
    fired on the real "A PHONE COMPANY" the column moved rather than the register rules."""
    spec = _CallerRowSpec(
        prefix="caller-row-overrun",
        active=0,
        org="A DEPARTMENTOFREDUNDANTAGENCIES",
        index=1,
        start_s=6,
    )
    with pytest.raises(SceneError, match="too long for the"):
        render_caller_row_frames(
            kit=_kit(),
            ratio="16:9",
            fps=6,
            duration_s=0.5,
            out_dir=tmp_path,
            stills=_stills(tmp_path),
            spec=spec,
        )


def test_every_registered_caller_row_label_fits_at_the_payload_floor(tmp_path):
    """The refusal above is only useful if the SHIPPING labels are known to clear it."""
    for name in [n for n in su._SCENES if n.startswith("caller-row-")]:
        su._SCENES[name](
            kit=_kit(),
            ratio="16:9",
            fps=6,
            duration_s=0.5,
            out_dir=tmp_path / name,
            stills=_stills(tmp_path),
        )
        assert list((tmp_path / name).glob("*.png"))


# --- inset_video's argument guards ---------------------------------------------------------------


@pytest.mark.parametrize(
    "rect,message",
    [((0, 0, 0, 100), "w/h must be > 0"), ((-1, 0, 10, 10), "x/y must be >= 0")],
)
def test_inset_video_refuses_a_rect_that_is_not_a_box(tmp_path, rect, message):
    with pytest.raises(ValueError, match=message):
        inset_video(tmp_path / "a.mp4", tmp_path / "b.mp4", rect=rect, out_path=tmp_path / "o.mp4")


def test_inset_video_refuses_an_unknown_audio_source(tmp_path):
    """'inset' or 'base' — a typo silently falling through to one of them would drop the caller's
    own voice out of their own beat."""
    with pytest.raises(ValueError, match="audio_from"):
        inset_video(
            tmp_path / "a.mp4",
            tmp_path / "b.mp4",
            rect=(0, 0, 10, 10),
            out_path=tmp_path / "o.mp4",
            audio_from="neither",
        )


def test_inset_video_refuses_a_negative_start(tmp_path):
    with pytest.raises(ValueError, match="start_s"):
        inset_video(
            tmp_path / "a.mp4",
            tmp_path / "b.mp4",
            rect=(0, 0, 10, 10),
            out_path=tmp_path / "o.mp4",
            start_s=-0.5,
        )


# --- the padding that shipped at 13px --------------------------------------------------------


@pytest.mark.parametrize("ratio_h", [1080, 1350, 1920])
def test_compare_rows_padding_comes_from_the_token_ladder(ratio_h):
    """The 2026-09-04 "infographics look like shit" note, pinned.

    `compare_rows` set its padding as hand-tuned fractions of frame height, which resolved to
    **13px on a 1596px-wide panel** — under `INSET`'s smallest step. Status chips then drew flush
    against the panel border and every panel caption was drawn through it. The module knew: its
    own comment said the card was "below the scale the deck surface was tuned for" and left it
    there, so the same defect was reported twice.

    Padding must be a ladder step. A card that cannot afford one is too dense, and the fix is
    dropping content — not shaving the inset until the collision looks deliberate.
    """
    from gtm_core.design_tokens import INSET, px
    from gtm_core.screen_ui.scenes.compare_rows import panel_insets

    pad, pad_y = panel_insets(ratio_h)
    steps = {px(v, ratio_h) for v in INSET.values()}
    assert pad in steps, f"horizontal padding {pad}px is not an INSET step"
    assert pad_y in steps, f"vertical padding {pad_y}px is not an INSET step"
    assert pad >= px(INSET["xs"], ratio_h)
    assert pad_y >= px(INSET["xs"], ratio_h)


def test_no_rows_panel_still_declares_a_caption():
    """The per-panel caption was the element doing the colliding, and it restated the panel label
    and the burned caption between them. Removing the field is what pays for the padding above —
    if one comes back, the space it needs has to be argued again."""
    from gtm_core.screen_ui.scenes import compare_rows_specs as specs

    assert not hasattr(specs._Panel, "caption")
    assert "caption" not in specs._Panel.__dataclass_fields__


def test_the_record_grid_asks_one_question_not_four():
    """Four cells each carrying "who asked?" read as an interrogation repeated four times, and
    once Act 1's calls became verified calls there was nothing left for them to ask."""
    from pathlib import Path as _P

    from gtm_core.screen_ui.scenes import record_grid as rg

    src = _P(rg.__file__).read_text()
    # The literal may survive in the comment recording why it went; what must not survive is code
    # that draws it.
    hits = [ln for ln in src.splitlines() if '"who asked?"' in ln]
    assert all(ln.lstrip().startswith("#") for ln in hits), hits
    assert rg.QUESTION.endswith("?")
