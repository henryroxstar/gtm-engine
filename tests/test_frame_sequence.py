"""gtm_core.frame_sequence — printf pattern parsing and contiguity checking (plan change #8,
shared with the screen_ui build-record driver's own separate track, change #3).

Stdlib only, no ffmpeg or PIL required — these are pure path/regex tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.frame_sequence import (
    FrameSequenceError,
    ParsedPattern,
    check_contiguous,
    find_frames,
    parse_pattern,
    pattern,
    sequences,
    validate_sequence,
)


def _touch(dir_: Path, *names: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    for n in names:
        (dir_ / n).write_bytes(b"png")


# ── parse_pattern ────────────────────────────────────────────────────────────


def test_parses_a_simple_hyphenated_printf_pattern(tmp_path: Path):
    parsed = parse_pattern(str(tmp_path / "f-%04d.png"))
    assert parsed == ParsedPattern(directory=tmp_path, prefix="f-", width=4, suffix=".png")


def test_a_literal_hyphen_in_the_prefix_is_not_mistaken_for_multiple_prefixes(tmp_path: Path):
    """`hero-reveal-%04d.png` — the whole point of splitting on the placeholder itself rather
    than on the last hyphen. A hardcoded 'digits after the last hyphen' assumption would slice
    this as prefix='hero-reveal-' vs. some other reading; either way it must not be refused."""
    parsed = parse_pattern(str(tmp_path / "hero-reveal-%04d.png"))
    assert parsed.prefix == "hero-reveal-"
    assert parsed.width == 4
    assert parsed.suffix == ".png"


def test_a_non_hyphenated_prefix_parses_too(tmp_path: Path):
    parsed = parse_pattern(str(tmp_path / "frame%05d.png"))
    assert parsed.prefix == "frame"
    assert parsed.width == 5


def test_asterisk_and_question_mark_globs_are_refused_naming_the_printf_form(tmp_path: Path):
    with pytest.raises(FrameSequenceError, match="printf form like 'prefix-%04d.png'"):
        parse_pattern(str(tmp_path / "*.png"))
    with pytest.raises(FrameSequenceError, match="printf form like 'prefix-%04d.png'"):
        parse_pattern(str(tmp_path / "f-000?.png"))


def test_no_printf_placeholder_is_refused(tmp_path: Path):
    with pytest.raises(FrameSequenceError, match="no printf placeholder"):
        parse_pattern(str(tmp_path / "f-0001.png"))


def test_more_than_one_placeholder_is_refused(tmp_path: Path):
    with pytest.raises(FrameSequenceError, match="exactly one printf placeholder"):
        parse_pattern(str(tmp_path / "f-%04d-%02d.png"))


# ── pattern / find_frames ───────────────────────────────────────────────────


def test_pattern_builds_the_screen_ui_convention():
    assert pattern("walkthrough", 4) == "walkthrough-%04d.png"
    assert pattern("walkthrough", 4, suffix=".jpg") == "walkthrough-%04d.jpg"


def test_find_frames_only_matches_the_exact_width(tmp_path: Path):
    _touch(tmp_path, "f-0000.png", "f-0001.png", "f-0002.png", "f-00003.png")
    parsed = parse_pattern(str(tmp_path / "f-%04d.png"))
    # f-00003.png has a 5-digit run and must not be picked up by a %04d pattern.
    assert find_frames(parsed) == [0, 1, 2]


def test_find_frames_on_a_missing_directory_returns_empty(tmp_path: Path):
    parsed = parse_pattern(str(tmp_path / "nope" / "f-%04d.png"))
    assert find_frames(parsed) == []


# ── check_contiguous ─────────────────────────────────────────────────────────


def test_check_contiguous_accepts_a_run_starting_at_zero():
    check_contiguous([0, 1, 2, 3])  # no raise


def test_check_contiguous_accepts_a_run_starting_at_one():
    check_contiguous([1, 2, 3])  # no raise


def test_check_contiguous_accepts_unsorted_input():
    check_contiguous([3, 1, 2, 0])  # no raise


def test_check_contiguous_names_the_first_gap():
    with pytest.raises(FrameSequenceError, match="missing frame index 2"):
        check_contiguous([0, 1, 3, 4])


def test_check_contiguous_refuses_an_empty_list():
    with pytest.raises(FrameSequenceError, match="no frames found"):
        check_contiguous([])


# ── validate_sequence ────────────────────────────────────────────────────────


def test_validate_sequence_passes_for_a_contiguous_run_from_zero(tmp_path: Path):
    _touch(tmp_path, "f-0000.png", "f-0001.png", "f-0002.png")
    parsed = validate_sequence(str(tmp_path / "f-%04d.png"))
    assert parsed.prefix == "f-" and parsed.width == 4


def test_validate_sequence_passes_for_a_contiguous_run_from_one(tmp_path: Path):
    _touch(tmp_path, "f-0001.png", "f-0002.png", "f-0003.png")
    validate_sequence(str(tmp_path / "f-%04d.png"))  # no raise


def test_validate_sequence_names_a_gap_at_0003(tmp_path: Path):
    _touch(tmp_path, "f-0000.png", "f-0001.png", "f-0002.png", "f-0004.png")
    with pytest.raises(FrameSequenceError, match="missing f-0003.png"):
        validate_sequence(str(tmp_path / "f-%04d.png"))


def test_validate_sequence_refuses_zero_matching_files(tmp_path: Path):
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(FrameSequenceError, match="no frames found matching"):
        validate_sequence(str(tmp_path / "f-%04d.png"))


def test_validate_sequence_refuses_a_glob(tmp_path: Path):
    with pytest.raises(FrameSequenceError, match="printf form"):
        validate_sequence(str(tmp_path / "f-*.png"))


def test_a_literal_hyphenated_prefix_with_a_gap_is_still_caught_correctly(tmp_path: Path):
    """The regression case: `hero-reveal-%04d.png` with a real gap must name the right file,
    proving the width/prefix split (not a last-hyphen guess) drives the on-disk match too."""
    _touch(tmp_path, "hero-reveal-0000.png", "hero-reveal-0001.png", "hero-reveal-0003.png")
    with pytest.raises(FrameSequenceError, match="missing hero-reveal-0002.png"):
        validate_sequence(str(tmp_path / "hero-reveal-%04d.png"))


# ── sequences ────────────────────────────────────────────────────────────────


def test_sequences_groups_by_prefix(tmp_path: Path):
    _touch(
        tmp_path,
        "walkthrough-0000.png",
        "walkthrough-0001.png",
        "card-0000.png",
        "card-0001.png",
        "card-0002.png",
    )
    assert sequences(tmp_path) == {
        "walkthrough": [0, 1],
        "card": [0, 1, 2],
    }


def test_sequences_handles_a_hyphenated_prefix(tmp_path: Path):
    _touch(tmp_path, "hero-reveal-0000.png", "hero-reveal-0001.png")
    assert sequences(tmp_path) == {"hero-reveal": [0, 1]}


def test_sequences_ignores_files_that_do_not_match_the_shape(tmp_path: Path):
    _touch(tmp_path, "walkthrough-0000.png", "build-record.json", "notes.txt")
    assert sequences(tmp_path) == {"walkthrough": [0]}


def test_sequences_on_a_missing_directory_returns_empty(tmp_path: Path):
    assert sequences(tmp_path / "nope") == {}


def test_sequences_on_an_empty_directory_returns_empty(tmp_path: Path):
    tmp_path.mkdir(exist_ok=True)
    assert sequences(tmp_path) == {}
