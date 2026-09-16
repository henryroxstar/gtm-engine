"""gtm_core.video_finish.frames_to_video() — the sole ffmpeg entry point for turning a
gtm_core.screen_ui frame sequence into a clip. Real ffmpeg, real Pillow frames, no mocking of
either (this directory requires ffmpeg on PATH — see conftest.py).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from gtm_core import video_finish as vf
from gtm_core.frame_sequence import FrameSequenceError


def _write_frames(out_dir: Path, *, count: int, w: int = 320, h: int = 400) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        # A different flat color per frame — enough to prove the ENCODE actually consumed every
        # frame (test_encodes_every_frame_at_the_requested_fps below), not just the first one.
        shade = round(255 * i / max(1, count - 1))
        Image.new("RGB", (w, h), (shade, 0, 255 - shade)).save(out_dir / f"f-{i:04d}.png")
    return str(out_dir / "f-%04d.png")


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_encodes_a_playable_mp4_at_the_requested_fps(tmp_path):
    frames_glob = _write_frames(tmp_path / "frames", count=24)
    out = vf.frames_to_video(frames_glob, fps=12, out_path=tmp_path / "out.mp4")
    assert out.exists()
    streams = _probe(out)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    assert video["width"] == 320 and video["height"] == 400
    # r_frame_rate is "12/1" for a constant 12fps encode.
    assert video["r_frame_rate"].split("/")[0] == "12"


def test_default_output_carries_a_silent_audio_track(tmp_path):
    """mux() and stitch() both assume every segment has an audio stream — a video-only file
    desyncs a downstream concat that expects one."""
    frames_glob = _write_frames(tmp_path / "frames", count=12)
    out = vf.frames_to_video(frames_glob, fps=12, out_path=tmp_path / "out.mp4")
    streams = _probe(out)["streams"]
    assert any(s["codec_type"] == "audio" for s in streams)


def test_silent_audio_false_omits_the_audio_track(tmp_path):
    frames_glob = _write_frames(tmp_path / "frames", count=12)
    out = vf.frames_to_video(frames_glob, fps=12, out_path=tmp_path / "out.mp4", silent_audio=False)
    streams = _probe(out)["streams"]
    assert not any(s["codec_type"] == "audio" for s in streams)


def test_duration_matches_frame_count_over_fps(tmp_path):
    frames_glob = _write_frames(tmp_path / "frames", count=36)
    out = vf.frames_to_video(frames_glob, fps=12, out_path=tmp_path / "out.mp4")
    video = next(s for s in _probe(out)["streams"] if s["codec_type"] == "video")
    assert float(video["duration"]) == pytest.approx(3.0, abs=0.15)


def test_the_output_is_actually_stitchable(tmp_path):
    """The real reason this function exists: its output must be a valid ShotSegment for
    stitch(), the same as a provider render or a HeyGen clip. A frames_to_video output that
    stitch() rejects would defeat the whole point of sharing the pipeline."""
    frames_glob = _write_frames(tmp_path / "frames", count=24, w=1080, h=1350)
    clip = vf.frames_to_video(frames_glob, fps=12, out_path=tmp_path / "clip.mp4")
    out = vf.stitch(
        [vf.ShotSegment(path=str(clip))],
        ratio="4:5",
        out_path=tmp_path / "stitched.mp4",
        workdir=tmp_path / "work",
    )
    assert out.exists()


def test_non_positive_fps_is_refused(tmp_path):
    frames_glob = _write_frames(tmp_path / "frames", count=4)
    with pytest.raises(ValueError, match="fps must be > 0"):
        vf.frames_to_video(frames_glob, fps=0, out_path=tmp_path / "out.mp4")


def test_raises_ffmpeg_unavailable_when_absent(tmp_path, monkeypatch):
    frames_glob = _write_frames(tmp_path / "frames", count=4)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(vf.FfmpegUnavailable):
        vf.frames_to_video(frames_glob, fps=12, out_path=tmp_path / "out.mp4")


def test_never_leaves_a_part_file_behind_on_success(tmp_path):
    frames_glob = _write_frames(tmp_path / "frames", count=6)
    out_path = tmp_path / "out.mp4"
    vf.frames_to_video(frames_glob, fps=12, out_path=out_path)
    assert not out_path.with_suffix(".mp4.part").exists()


# ── frame-sequence validation (plan #8) ──────────────────────────────────────


def test_a_glob_pattern_is_refused_naming_the_printf_form(tmp_path):
    (tmp_path / "frames").mkdir()
    with pytest.raises(FrameSequenceError, match="printf form like 'prefix-%04d.png'"):
        vf.frames_to_video(str(tmp_path / "frames" / "*.png"), fps=12, out_path=tmp_path / "o.mp4")


def test_a_gap_in_the_sequence_is_refused_naming_it(tmp_path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for i in (0, 1, 2, 4):
        Image.new("RGB", (16, 16), (0, 0, 0)).save(frames_dir / f"f-{i:04d}.png")
    with pytest.raises(FrameSequenceError, match="missing f-0003.png"):
        vf.frames_to_video(str(frames_dir / "f-%04d.png"), fps=12, out_path=tmp_path / "out.mp4")


def test_no_matching_frames_is_refused(tmp_path):
    (tmp_path / "frames").mkdir()
    with pytest.raises(FrameSequenceError, match="no frames found matching"):
        vf.frames_to_video(
            str(tmp_path / "frames" / "f-%04d.png"), fps=12, out_path=tmp_path / "out.mp4"
        )


def test_a_contiguous_sequence_starting_at_one_is_accepted(tmp_path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for i in (1, 2, 3):
        Image.new("RGB", (16, 16), (0, 0, 0)).save(frames_dir / f"f-{i:04d}.png")
    out = vf.frames_to_video(str(frames_dir / "f-%04d.png"), fps=12, out_path=tmp_path / "out.mp4")
    assert out.exists()


def test_overlay_frames_gets_the_identical_contiguity_check(tmp_path):
    """Not just frames_to_video — overlay_frames composites a locally-drawn sequence over a
    provider render and must refuse the same gap before ever touching ffmpeg."""
    base_frames = _write_frames(tmp_path / "base", count=8)
    base = vf.frames_to_video(base_frames, fps=8, out_path=tmp_path / "base.mp4")
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    for i in (0, 1, 3):  # gap at 2
        Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(overlay_dir / f"ov-{i:04d}.png")
    with pytest.raises(FrameSequenceError, match="missing ov-0002.png"):
        vf.overlay_frames(
            base, str(overlay_dir / "ov-%04d.png"), fps=8, out_path=tmp_path / "out.mp4"
        )


def test_a_hyphenated_literal_prefix_is_not_misread_as_multiple_prefixes(tmp_path):
    """`hero-reveal-%04d.png` — a hardcoded 'digits after the last hyphen' assumption would
    wrongly refuse this valid pattern."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for i in range(4):
        Image.new("RGB", (16, 16), (0, 0, 0)).save(frames_dir / f"hero-reveal-{i:04d}.png")
    out = vf.frames_to_video(
        str(frames_dir / "hero-reveal-%04d.png"), fps=12, out_path=tmp_path / "out.mp4"
    )
    assert out.exists()
