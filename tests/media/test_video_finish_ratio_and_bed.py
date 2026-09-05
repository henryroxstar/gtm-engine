"""gtm_core.video_finish.pad_to_ratio() / build_music_bed() — against real ffmpeg.

Two verbs added 2026-08-29 to close gaps that had been filled by hand:

* ``pad_to_ratio`` — ``video_lint --ratio 4:5`` gated an artifact nothing in the repo could
  produce (Reap's reframe emits 9:16 and 1:1 only, and crops).
* ``build_music_bed`` — catalogue cues top out around 137s, so every long-form film needs a
  bed built from a shorter track.

The checks that matter here are the ones a file-exists assertion would miss: that padding
does not CROP, and that a built bed is exactly as long as it was asked for.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gtm_core import video_finish as vf
from gtm_core.video_lint import SAFE_AREAS


def _clip_16x9(path: Path, *, seconds: int = 2) -> Path:
    """A 16:9 clip whose frame is entirely non-black, so any black in the output is padding."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=white:size=1920x1080:duration={seconds}:rate=25",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-f",
            "mp4",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _tone(path: Path, *, seconds: float, freq: int = 220) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={seconds}",
            "-ac",
            "2",
            "-ar",
            "48000",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


# --- pad_to_ratio() -------------------------------------------------------------------------


def test_pad_to_ratio_hits_the_ratios_own_declared_dims(tmp_path):
    """Dims come from SAFE_AREAS, so the produced asset matches what video_lint checks."""
    src = _clip_16x9(tmp_path / "src.mp4")
    out = vf.pad_to_ratio(src, ratio="4:5", out_path=tmp_path / "out.mp4", workdir=tmp_path / "w")
    area = SAFE_AREAS["4:5"]
    assert vf._probe_dims(out) == (area.width, area.height)


def test_pad_to_ratio_pads_rather_than_crops(tmp_path):
    """The whole point: a designed frame must arrive intact, with bars, not trimmed.

    The source is uniformly white, so a cropped result would be white edge to edge. Bars at
    the top and bottom with picture in the middle is the signature of a pad. Cropping a
    full-frame graphic removes labels, which is why this is asserted and not assumed.
    """
    src = _clip_16x9(tmp_path / "src.mp4")
    out = vf.pad_to_ratio(src, ratio="4:5", out_path=tmp_path / "out.mp4", workdir=tmp_path / "w")
    area = SAFE_AREAS["4:5"]
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(out),
            "-vframes",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    assert len(raw) == area.width * area.height

    def row_mean(y: int) -> float:
        start = y * area.width
        return sum(raw[start : start + area.width]) / area.width

    assert row_mean(2) < 40, "top of frame should be padding, not picture"
    assert row_mean(area.height - 3) < 40, "bottom of frame should be padding, not picture"
    assert row_mean(area.height // 2) > 200, "centre of frame should still be the picture"


def test_pad_to_ratio_background_colour_reaches_the_bars(tmp_path):
    src = _clip_16x9(tmp_path / "src.mp4")
    out = vf.pad_to_ratio(
        src,
        ratio="4:5",
        out_path=tmp_path / "out.mp4",
        workdir=tmp_path / "w",
        background="white",
    )
    area = SAFE_AREAS["4:5"]
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(out),
            "-vframes",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    top = sum(raw[2 * area.width : 3 * area.width]) / area.width
    assert top > 200, "white bars requested, got something dark"


def test_pad_to_ratio_refuses_a_ratio_video_lint_does_not_define(tmp_path):
    """A ratio must be defined in ONE place. Refusing here keeps producer and gate in step."""
    src = _clip_16x9(tmp_path / "src.mp4")
    with pytest.raises(ValueError, match="SAFE_AREAS"):
        vf.pad_to_ratio(src, ratio="3:2", out_path=tmp_path / "o.mp4", workdir=tmp_path / "w")


# --- build_music_bed() ----------------------------------------------------------------------


def test_build_music_bed_extends_a_short_track_to_an_exact_length(tmp_path):
    """The load-bearing behaviour: a 10s cue becomes a 25s bed, to the frame."""
    track = _tone(tmp_path / "cue.wav", seconds=10)
    out = vf.build_music_bed(
        track,
        out_path=tmp_path / "bed.m4a",
        duration_s=25.0,
        workdir=tmp_path / "w",
        crossfade_s=1.0,
        fade_out_s=1.0,
    )
    assert vf._probe_duration(out, stream_selector="a:0") == pytest.approx(25.0, abs=0.15)


def test_build_music_bed_trims_when_the_track_is_already_long_enough(tmp_path):
    track = _tone(tmp_path / "cue.wav", seconds=20)
    out = vf.build_music_bed(
        track,
        out_path=tmp_path / "bed.m4a",
        duration_s=8.0,
        workdir=tmp_path / "w",
        fade_out_s=1.0,
    )
    assert vf._probe_duration(out, stream_selector="a:0") == pytest.approx(8.0, abs=0.15)


def test_build_music_bed_honours_body_end_so_a_tail_fade_is_never_looped(tmp_path):
    """body_end_s must actually shorten the usable material, or a cue's own fade-to-silence
    gets repeated into the middle of the film — the defect this parameter exists for."""
    track = _tone(tmp_path / "cue.wav", seconds=30)
    out = vf.build_music_bed(
        track,
        out_path=tmp_path / "bed.m4a",
        duration_s=10.0,
        workdir=tmp_path / "w",
        body_end_s=6.0,
        fade_out_s=1.0,
    )
    assert vf._probe_duration(out, stream_selector="a:0") == pytest.approx(10.0, abs=0.15)


def test_build_music_bed_refuses_a_loop_point_outside_the_body(tmp_path):
    track = _tone(tmp_path / "cue.wav", seconds=10)
    with pytest.raises(ValueError, match="loop_from_s"):
        vf.build_music_bed(
            track,
            out_path=tmp_path / "bed.m4a",
            duration_s=20.0,
            workdir=tmp_path / "w",
            body_end_s=5.0,
            loop_from_s=8.0,
        )


def test_build_music_bed_refuses_a_loop_section_shorter_than_its_crossfade(tmp_path):
    """A section that cannot outrun its own crossfade would loop forever making no progress."""
    track = _tone(tmp_path / "cue.wav", seconds=10)
    with pytest.raises(ValueError, match="crossfade"):
        vf.build_music_bed(
            track,
            out_path=tmp_path / "bed.m4a",
            duration_s=60.0,
            workdir=tmp_path / "w",
            loop_from_s=8.0,
            crossfade_s=4.0,
        )


@pytest.mark.parametrize("bad", [0.0, -3.0])
def test_build_music_bed_refuses_a_non_positive_duration(tmp_path, bad):
    track = _tone(tmp_path / "cue.wav", seconds=5)
    with pytest.raises(ValueError, match="duration_s"):
        vf.build_music_bed(
            track, out_path=tmp_path / "bed.m4a", duration_s=bad, workdir=tmp_path / "w"
        )


def test_build_music_bed_rejects_a_non_m4a_extension(tmp_path):
    track = _tone(tmp_path / "cue.wav", seconds=5)
    with pytest.raises(ValueError, match=r"\.m4a"):
        vf.build_music_bed(
            track, out_path=tmp_path / "bed.mp3", duration_s=3.0, workdir=tmp_path / "w"
        )


def test_level_envelope_flattens_a_swell_in_the_source_track(tmp_path):
    """A cue that swells must come out flat, or the ducker setting is only right in one place.

    The 2026-08-30 track rises 5.8 dB across its middle. That is not a jump, so nothing catches
    it, and the same ``music_gain_db`` is then correct at the top of the film and wrong under the
    argument. Here: a tone that steps +6 dB at 3s, an envelope that takes 6 dB back out, and the
    two halves must land within 1 dB of each other.
    """
    import numpy as np

    src = tmp_path / "swell.wav"
    subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:duration=6",
            "-af",
            "volume=volume='if(lt(t,3),0.25,0.5)':eval=frame",
            "-ac",
            "2",
            "-ar",
            "48000",
            str(src),
        ],
        capture_output=True,
        check=True,
    )
    out = vf.build_music_bed(
        src,
        out_path=tmp_path / "bed.m4a",
        duration_s=6.0,
        workdir=tmp_path / "w",
        body_end_s=6.0,
        fade_out_s=0.1,
        level_envelope=[(0.0, 0.0), (2.9, 0.0), (3.1, -6.0), (6.0, -6.0)],
    )
    raw = subprocess.run(  # noqa: S603
        ["ffmpeg", "-i", str(out), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    first = 20 * np.log10(np.sqrt((x[8000:20000] ** 2).mean()))
    second = 20 * np.log10(np.sqrt((x[28000:40000] ** 2).mean()))
    assert abs(second - first) < 1.0, f"envelope left a {second - first:+.2f} dB step"


def test_level_envelope_refuses_non_increasing_times(tmp_path):
    src = tmp_path / "t.wav"
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=duration=2", "-ac", "2", str(src)],
        capture_output=True,
        check=True,
    )
    with pytest.raises(ValueError, match="strictly increase"):
        vf.build_music_bed(
            src,
            out_path=tmp_path / "b.m4a",
            duration_s=2.0,
            workdir=tmp_path / "w",
            level_envelope=[(0.0, 0.0), (1.0, -3.0), (1.0, -6.0)],
        )
