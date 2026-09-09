"""C11/C5 — `cover_frame.sibling_sheet`: N unrelated stills tiled so a human can choose one.

The sibling of `contact_sheet`, and deliberately not a flag on it: that one samples frames along
ONE video's timeline and answers "did this ship correctly"; this one lays out unrelated candidates
and answers "which of these do I want". Same pixels, opposite questions.
"""

from __future__ import annotations

import pytest
from PIL import Image

from gtm_core.cover_frame import sibling_sheet


def _still(path, w, h, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), colour).save(path)
    return path


def test_sixteen_poses_tile_into_a_four_by_four_grid(tmp_path):
    """The element pose-sheet case: sixteen stills, four columns, four rows."""
    paths = [_still(tmp_path / f"{i:02d}.png", 108, 192, (i * 15, 40, 90)) for i in range(16)]
    result = sibling_sheet(paths, tmp_path / "sheet.jpg", columns=4)
    assert (result["tiles"], result["columns"], result["rows"]) == (16, 4, 4)
    assert Image.open(result["out_path"]).size[0] == 320 * 4


def test_a_partial_last_row_still_gets_a_row(tmp_path):
    """Five tiles in four columns is two rows, not one-and-a-quarter."""
    paths = [_still(tmp_path / f"{i}.png", 200, 200, (10, i * 40, 10)) for i in range(5)]
    assert sibling_sheet(paths, tmp_path / "s.jpg", columns=4)["rows"] == 2


def test_mixed_ratios_are_letterboxed_rather_than_stretched(tmp_path):
    """A 9:16 pose beside a 1:1 pose is the NORMAL case for an element, and distorting either
    would misrepresent the very thing the sheet exists to let someone choose between."""
    tall = _still(tmp_path / "tall.png", 108, 192, (200, 0, 0))
    square = _still(tmp_path / "square.png", 200, 200, (0, 0, 200))
    out = sibling_sheet([tall, square], tmp_path / "s.jpg", columns=2, tile_w=100)
    sheet = Image.open(out["out_path"]).convert("RGB")

    # The cell is sized by the TALLEST tile: 100 wide x 178 tall (100 * 192/108). The 9:16 still
    # therefore fills its cell exactly — that is correct, not a bug. The square one cannot: at
    # 100 wide it is 100 tall in a 178-tall cell, so it must sit centred with ground above and
    # below. A stretch-to-fill would have made that whole cell blue, which is the defect.
    assert sheet.size == (200, 178), "cell geometry changed — re-derive before relaxing this"

    # Compared with a tolerance: the sheet is JPEG, so an exact equality here would be asserting
    # the encoder's behaviour rather than the layout's.
    def near(xy, rgb, tol=16):
        return all(abs(a - b) <= tol for a, b in zip(sheet.getpixel(xy), rgb))

    assert near((150, 89), (0, 0, 200)), "the square tile is not centred in its cell"
    assert not near((150, 4), (0, 0, 200)), "the square tile was stretched to fill the cell"
    assert not near((150, 174), (0, 0, 200)), "the square tile was stretched to fill the cell"


def test_a_single_still_is_a_legal_sheet(tmp_path):
    """A pool of one is a real state (four renders failed), and must not be a crash."""
    one = _still(tmp_path / "a.png", 200, 200, (0, 120, 0))
    assert sibling_sheet([one], tmp_path / "s.jpg")["tiles"] == 1


def test_an_empty_list_is_refused(tmp_path):
    """Nothing to choose between is a caller bug, and a blank sheet would hide it."""
    with pytest.raises(ValueError, match="at least one"):
        sibling_sheet([], tmp_path / "s.jpg")


def test_zero_columns_is_refused(tmp_path):
    one = _still(tmp_path / "a.png", 200, 200, (0, 0, 0))
    with pytest.raises(ValueError, match="columns"):
        sibling_sheet([one], tmp_path / "s.jpg", columns=0)


def test_the_same_inputs_produce_the_same_sheet(tmp_path):
    """Deterministic: a sheet that changes between runs cannot be diffed or cached."""
    paths = [_still(tmp_path / f"{i}.png", 200, 200, (i * 60, 20, 20)) for i in range(3)]
    a = sibling_sheet(paths, tmp_path / "a.jpg", columns=2)["out_path"]
    b = sibling_sheet(paths, tmp_path / "b.jpg", columns=2)["out_path"]
    assert open(a, "rb").read() == open(b, "rb").read()
