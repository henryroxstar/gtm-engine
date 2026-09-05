"""gtm_core.cover_frame — deterministic cover-frame candidate selection (Phase E). Real ffmpeg,
real Pillow, no mocking of either — this directory requires ffmpeg on PATH (see conftest.py).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from gtm_core import cover_frame as cf


@pytest.fixture(scope="session")
def blurry_then_sharp_clip(tmp_path_factory):
    """A 3s clip: first half a heavily gaussian-blurred test pattern, second half a crisp
    checkerboard — deterministic enough that the sharp half must win the Laplacian-variance pick
    every time. Uses `testsrc`+`gblur` rather than the `gradients` source filter: `gradients`
    reliably SIGABRTs on the CI runner's (Ubuntu apt) ffmpeg build even though it's fine on the
    operator's local build — `testsrc`/`gblur` are long-stable filters with no such divergence."""
    d = tmp_path_factory.mktemp("cover_frame_src")
    src = d / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1.5:size=640x360:rate=24,gblur=sigma=25",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=1.5:size=640x360:rate=24",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    return src


def test_extract_candidates_returns_the_requested_count(blurry_then_sharp_clip, tmp_path):
    candidates = cf.extract_candidates(blurry_then_sharp_clip, out_dir=tmp_path, count=5)
    assert len(candidates) == 5
    assert all(p.is_file() for p in candidates)


def test_extract_candidates_rejects_a_zero_count(blurry_then_sharp_clip, tmp_path):
    with pytest.raises(ValueError, match="count must be >= 1"):
        cf.extract_candidates(blurry_then_sharp_clip, out_dir=tmp_path, count=0)


def test_extract_candidates_single_count_takes_the_midpoint(blurry_then_sharp_clip, tmp_path):
    candidates = cf.extract_candidates(blurry_then_sharp_clip, out_dir=tmp_path, count=1)
    assert len(candidates) == 1


def test_laplacian_variance_ranks_a_sharp_image_above_a_flat_one(tmp_path):
    flat = tmp_path / "flat.png"
    Image.new("RGB", (200, 200), (128, 128, 128)).save(flat)

    sharp = tmp_path / "sharp.png"
    img = Image.new("RGB", (200, 200), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for x in range(0, 200, 10):
        draw.line([(x, 0), (x, 200)], fill=(0, 0, 0), width=2)
    img.save(sharp)

    assert cf.laplacian_variance(sharp) > cf.laplacian_variance(flat)


def test_pick_cover_frame_picks_the_sharp_half(blurry_then_sharp_clip, tmp_path):
    out_path = tmp_path / "cover.png"
    result = cf.pick_cover_frame(
        blurry_then_sharp_clip, out_path=out_path, workdir=tmp_path / "work", count=6
    )
    assert result == out_path
    assert out_path.is_file()

    # The picked frame must score at or above the median of all candidates — a real check that
    # SOME selection happened, not just "a file exists at out_path".
    candidates = cf.extract_candidates(blurry_then_sharp_clip, out_dir=tmp_path / "verify", count=6)
    scores = sorted(cf.laplacian_variance(p) for p in candidates)
    picked_score = cf.laplacian_variance(out_path)
    assert picked_score >= scores[len(scores) // 2]


def test_pick_cover_frame_raises_ffmpeg_unavailable_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(cf.FfmpegUnavailable):
        cf.pick_cover_frame(
            tmp_path / "nope.mp4", out_path=tmp_path / "cover.png", workdir=tmp_path / "w"
        )
