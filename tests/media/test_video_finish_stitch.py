"""gtm_core.video_finish.stitch() / duck_music_bed() (Phase E) — against real ffmpeg. This
directory requires ffmpeg on PATH (see conftest.py).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gtm_core import video_finish as vf

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_clip(path: Path, *, w: int, h: int, duration: float = 1.0) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size={w}x{h}:rate=24",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _probe_dims(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    s = json.loads(out.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


def _mean_volume_db(path: Path) -> float:
    out = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    m = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", out.stderr)
    assert m, f"no mean_volume in ffmpeg output: {out.stderr}"
    return float(m.group(1))


# --- stitch() ------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def matching_dims_clip(tmp_path_factory):
    d = tmp_path_factory.mktemp("stitch_match")
    return _make_clip(d / "match.mp4", w=1080, h=1080)


@pytest.fixture(scope="session")
def mismatched_dims_clip(tmp_path_factory):
    d = tmp_path_factory.mktemp("stitch_mismatch")
    return _make_clip(d / "mismatch.mp4", w=640, h=480)


def test_probe_dims_reads_width_and_height(matching_dims_clip):
    assert vf._probe_dims(matching_dims_clip) == (1080, 1080)


def test_normalize_shot_passes_through_when_dims_already_match_and_not_reframed(
    matching_dims_clip, tmp_path
):
    result = vf._normalize_shot(
        matching_dims_clip, target_w=1080, target_h=1080, workdir=tmp_path, force_reencode=False
    )
    assert result == matching_dims_clip  # zero re-encode work — same path, not a copy


def test_normalize_shot_reencodes_when_dims_mismatch(mismatched_dims_clip, tmp_path):
    result = vf._normalize_shot(
        mismatched_dims_clip, target_w=1080, target_h=1080, workdir=tmp_path, force_reencode=False
    )
    assert result != mismatched_dims_clip
    assert vf._probe_dims(result) == (1080, 1080)


def test_normalize_shot_reencodes_when_force_reencode_even_if_dims_already_match(
    matching_dims_clip, tmp_path
):
    """The caller's own 'this shot was reframed' signal always wins, even when the shot happens
    to already sit at target dims — never inferred silently from dims alone."""
    result = vf._normalize_shot(
        matching_dims_clip, target_w=1080, target_h=1080, workdir=tmp_path, force_reencode=True
    )
    assert result != matching_dims_clip


def test_stitch_joins_segments_and_produces_target_dims(
    matching_dims_clip, mismatched_dims_clip, tmp_path
):
    segments = [
        vf.ShotSegment(path=str(matching_dims_clip), reframed=False),
        vf.ShotSegment(path=str(mismatched_dims_clip), reframed=True),
    ]
    out_path = tmp_path / "joined.mp4"
    result = vf.stitch(segments, ratio="1:1", out_path=out_path, workdir=tmp_path / "work")
    assert result == out_path
    assert out_path.is_file()
    assert vf._probe_dims(out_path) == (1080, 1080)


def test_stitch_with_crossfade_produces_target_dims_and_longer_workflow(
    matching_dims_clip, mismatched_dims_clip, tmp_path
):
    segments = [
        vf.ShotSegment(path=str(matching_dims_clip), reframed=False),
        vf.ShotSegment(path=str(mismatched_dims_clip), reframed=True),
        vf.ShotSegment(path=str(matching_dims_clip), reframed=False),
    ]
    out_path = tmp_path / "joined_crossfade.mp4"
    result = vf.stitch(
        segments,
        ratio="1:1",
        out_path=out_path,
        workdir=tmp_path / "work",
        crossfade_s=0.2,
    )
    assert result == out_path
    assert out_path.is_file()
    assert vf._probe_dims(out_path) == (1080, 1080)


def test_stitch_with_crossfade_rejects_a_transition_longer_than_a_segment(
    matching_dims_clip, tmp_path
):
    """A 1s segment cannot host a 0.6s crossfade (needs >= 1.2s). Fail closed."""
    short = tmp_path / "short.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1.0:size=1080x1080:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.0",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(short),
        ],
        check=True,
        capture_output=True,
    )
    segments = [
        vf.ShotSegment(path=str(matching_dims_clip), reframed=False),
        vf.ShotSegment(path=str(short), reframed=False),
    ]
    with pytest.raises(ValueError, match="crossfade_s"):
        vf.stitch(
            segments,
            ratio="1:1",
            out_path=tmp_path / "joined.mp4",
            workdir=tmp_path / "work",
            crossfade_s=0.6,
        )


def test_stitch_rejects_empty_segments(tmp_path):
    with pytest.raises(ValueError, match="at least one segment"):
        vf.stitch([], ratio="1:1", out_path=tmp_path / "out.mp4", workdir=tmp_path)


def test_stitch_rejects_an_unknown_ratio(matching_dims_clip, tmp_path):
    with pytest.raises(ValueError, match="unknown ratio"):
        vf.stitch(
            [vf.ShotSegment(path=str(matching_dims_clip))],
            ratio="21:9",
            out_path=tmp_path / "out.mp4",
            workdir=tmp_path,
        )


def test_stitch_raises_ffmpeg_unavailable_when_absent(matching_dims_clip, tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(vf.FfmpegUnavailable):
        vf.stitch(
            [vf.ShotSegment(path=str(matching_dims_clip))],
            ratio="1:1",
            out_path=tmp_path / "out.mp4",
            workdir=tmp_path,
        )


# --- duck_music_bed() ------------------------------------------------------------------------


@pytest.fixture(scope="session")
def voice_audio(tmp_path_factory):
    d = tmp_path_factory.mktemp("duck_voice")
    p = d / "voice.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:a",
            "aac",
            "-f",
            "mp4",
            str(p),
        ],
        check=True,
        capture_output=True,
    )
    return p


@pytest.fixture(scope="session")
def music_audio(tmp_path_factory):
    d = tmp_path_factory.mktemp("duck_music")
    p = d / "music.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=2",
            "-c:a",
            "aac",
            "-f",
            "mp4",
            str(p),
        ],
        check=True,
        capture_output=True,
    )
    return p


def test_duck_music_bed_produces_a_playable_m4a(voice_audio, music_audio, tmp_path):
    out_path = tmp_path / "mix.m4a"
    result = vf.duck_music_bed(
        voice_audio, music_audio, out_path=out_path, workdir=tmp_path / "work"
    )
    assert result == out_path
    assert out_path.is_file()
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(out_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float(json.loads(out.stdout)["format"]["duration"])
    assert duration == pytest.approx(2.0, abs=0.3)


def test_duck_music_bed_rejects_a_non_m4a_extension(voice_audio, music_audio, tmp_path):
    with pytest.raises(ValueError, match="\\.m4a"):
        vf.duck_music_bed(voice_audio, music_audio, out_path=tmp_path / "mix.mp3", workdir=tmp_path)


def test_duck_music_bed_raises_ffmpeg_unavailable_when_absent(
    voice_audio, music_audio, tmp_path, monkeypatch
):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(vf.FfmpegUnavailable):
        vf.duck_music_bed(voice_audio, music_audio, out_path=tmp_path / "mix.m4a", workdir=tmp_path)


def test_duck_music_bed_higher_ratio_suppresses_the_mix_more(voice_audio, music_audio, tmp_path):
    """A constant 'voice' signal present throughout means a more aggressive compression ratio
    must duck the music harder, producing a QUIETER overall mix — a genuine acoustic check that
    the ratio parameter actually reaches the filtergraph, not just a file-exists check."""
    mild = vf.duck_music_bed(
        voice_audio,
        music_audio,
        out_path=tmp_path / "mild.m4a",
        workdir=tmp_path / "w1",
        duck_ratio=1.0,
    )
    aggressive = vf.duck_music_bed(
        voice_audio,
        music_audio,
        out_path=tmp_path / "aggressive.m4a",
        workdir=tmp_path / "w2",
        duck_ratio=20.0,
    )
    assert _mean_volume_db(aggressive) < _mean_volume_db(mild)


# --- mux(): attaching a VO to a silent rendered shot ---------------------------------------
#
# Added 2026-08-19. video-render generates every shot with generate_audio:false (the VO rides
# audio_references, which drives mouth motion, not the output track), so each rendered shot
# arrives SILENT and its real audio is attached here. The whole reason this is a function and
# not a one-liner is the duration relationship: a VO longer than its shot either gets its tail
# cut off by -shortest (on a payoff beat, that silently deletes the point of the video) or gets
# audibly distorted by a large atempo. mux() picks explicitly and reports which.


def _make_silent_clip(path: Path, *, w: int, h: int, duration: float) -> Path:
    """Video with NO audio stream — the actual shape a seedance render comes back in."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size={w}x{h}:rate=24",
            "-c:v",
            "libx264",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _make_audio(path: Path, *, duration: float) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _has_audio_stream(path: Path) -> bool:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(json.loads(out.stdout).get("streams"))


def test_probe_duration_reads_an_audio_only_file(tmp_path):
    """Regression: _probe_duration used -select_streams v:0 with a ',' section separator, so an
    audio-only input matched no stream AND never actually requested format.duration. It raised
    IndexError, then FfmpegUnavailable. A video file always has a v:0 duration, which is why this
    stayed hidden until the mux path probed a .mp3."""
    audio = _make_audio(tmp_path / "vo.m4a", duration=2.0)
    assert vf._probe_duration(audio, stream_selector=None) == pytest.approx(2.0, abs=0.15)


def test_mux_attaches_audio_to_a_silent_shot(tmp_path):
    video = _make_silent_clip(tmp_path / "shot.mp4", w=1080, h=1350, duration=5.0)
    assert not _has_audio_stream(video)  # precondition: the render really is silent
    # Tail inside MUX_TRUNCATE_MAX_S — a real render's integer duration vs a fractional VO.
    audio = _make_audio(tmp_path / "vo.m4a", duration=4.8)

    result = vf.mux(video, audio, out_path=tmp_path / "out.mp4", workdir=tmp_path / "work")

    assert result.strategy == "truncate-video"
    assert result.atempo is None
    assert _has_audio_stream(result.out_path)


def test_mux_truncates_a_small_silent_tail_rather_than_holding_dead_air(tmp_path):
    video = _make_silent_clip(tmp_path / "shot.mp4", w=1080, h=1350, duration=5.0)
    audio = _make_audio(tmp_path / "vo.m4a", duration=4.75)

    result = vf.mux(video, audio, out_path=tmp_path / "out.mp4", workdir=tmp_path / "work")

    # Output tracks the AUDIO length, not the video's — a quarter-second of a motionless
    # presenter saying nothing is worse than a marginally shorter shot.
    assert vf._probe_duration(result.out_path) == pytest.approx(4.75, abs=0.3)


def test_mux_refuses_a_silent_tail_big_enough_to_desync_the_mouth(tmp_path):
    """The other half of the 2026-08-18 lip-sync defect, and the one that used to pass silently.

    Discarding silence *looks* lossless, so `-shortest` on a 5.04s shot carrying a 4.08s VO read
    as free. It is not: the model already spread 5.04s of mouth motion across audio that only
    fills 4.08s, so the mouth runs slow from frame one and no amount of tail-trimming recovers the
    frames already shown. Five of six shots shipped this way.
    """
    video = _make_silent_clip(tmp_path / "shot.mp4", w=1080, h=1350, duration=5.0)
    audio = _make_audio(tmp_path / "vo.m4a", duration=4.0)  # 1.0s tail — well past the ceiling

    with pytest.raises(vf.PlanError) as exc:
        vf.mux(video, audio, out_path=tmp_path / "out.mp4", workdir=tmp_path / "work")

    msg = str(exc.value)
    assert "silent tail" in msg
    # Must name the actionable fix — the integer duration to re-render at — not just complain.
    assert "duration=4s" in msg
    assert "shots_lint" in msg


def test_mux_honours_a_raised_truncate_ceiling(tmp_path):
    """An explicit, deliberate override still works — the guard is a default, not a wall."""
    video = _make_silent_clip(tmp_path / "shot.mp4", w=1080, h=1350, duration=5.0)
    audio = _make_audio(tmp_path / "vo.m4a", duration=4.0)

    result = vf.mux(
        video,
        audio,
        out_path=tmp_path / "out.mp4",
        workdir=tmp_path / "work",
        truncate_max_s=1.5,
    )

    assert result.strategy == "truncate-video"


def test_mux_timecompresses_a_slightly_long_vo_to_fit(tmp_path):
    video = _make_silent_clip(tmp_path / "shot.mp4", w=1080, h=1350, duration=5.0)
    audio = _make_audio(tmp_path / "vo.m4a", duration=5.3)  # 6% over — inside the ceiling

    result = vf.mux(video, audio, out_path=tmp_path / "out.mp4", workdir=tmp_path / "work")

    assert result.strategy == "atempo"
    assert result.atempo == pytest.approx(5.3 / 5.0, abs=0.05)
    assert _has_audio_stream(result.out_path)


def test_mux_refuses_a_vo_too_long_to_fit_transparently(tmp_path):
    """The defect this exists for: shot 6's payoff line was 7.9s against a 5.0s render (58% over).
    -shortest would have cut off '...not just who's signed in' — the thesis of the whole video —
    and a 1.58 atempo would have chipmunked the cloned voice. Refuse, and name both real fixes."""
    video = _make_silent_clip(tmp_path / "shot.mp4", w=1080, h=1350, duration=5.0)
    audio = _make_audio(tmp_path / "vo.m4a", duration=7.9)

    with pytest.raises(vf.PlanError) as exc:
        vf.mux(video, audio, out_path=tmp_path / "out.mp4", workdir=tmp_path / "work")

    msg = str(exc.value)
    assert "script-length problem" in msg
    assert "re-render the shot longer" in msg
    assert not (tmp_path / "out.mp4").exists()  # nothing half-written


def test_mux_honours_a_raised_atempo_ceiling(tmp_path):
    """The ceiling is a craft bound, not a technical one, so it is a parameter — but raising it is
    an explicit caller decision, never a silent fallback when the default refuses."""
    video = _make_silent_clip(tmp_path / "shot.mp4", w=1080, h=1350, duration=5.0)
    audio = _make_audio(tmp_path / "vo.m4a", duration=6.0)  # 20% over — past the 1.15 default

    with pytest.raises(vf.PlanError):
        vf.mux(video, audio, out_path=tmp_path / "out.mp4", workdir=tmp_path / "work")

    result = vf.mux(
        video, audio, out_path=tmp_path / "out.mp4", workdir=tmp_path / "work", atempo_max=1.5
    )
    assert result.strategy == "atempo"


def test_mux_rejects_an_atempo_max_below_one(tmp_path):
    video = _make_silent_clip(tmp_path / "shot.mp4", w=1080, h=1350, duration=2.0)
    audio = _make_audio(tmp_path / "vo.m4a", duration=1.0)
    with pytest.raises(ValueError, match="atempo_max must be >= 1.0"):
        vf.mux(
            video, audio, out_path=tmp_path / "out.mp4", workdir=tmp_path / "work", atempo_max=0.9
        )


def test_confined_output_refuses_a_path_outside_the_content_root(tmp_path):
    """mux/stitch exist so ffmpeg runs from inside gtm_core rather than a raw Bash call; that
    boundary would be theatre if the module then wrote wherever it was pointed."""
    root = tmp_path / "content"
    (root / "acme").mkdir(parents=True)
    inside = vf._confined_output(root / "acme" / "ok.mp4", content_root=root)
    assert inside.parent == (root / "acme").resolve()

    with pytest.raises(vf.PolishError, match="outside the resolved content root"):
        vf._confined_output(tmp_path / "elsewhere" / "escape.mp4", content_root=root)
