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


def test_duck_music_bed_does_not_attenuate_the_voice(voice_audio, music_audio, tmp_path):
    """Laying a bed must not cost the VOICE level — the regression this guards shipped.

    ffmpeg's ``amix`` scales every input by ``1/n`` unless told otherwise, so mixing any bed
    under a finished voice track quietly pulled the speech down 6 dB (measured -21.3 -> -27.2
    dBFS on the 2026-08-29 long-form master). Nothing errored, and ``video_lint``'s dead-air
    gate got GREENER as it happened, because a bed raises the floor whether or not it also
    lowers the speech. With the bed pushed 40 dB down it is inaudible, so the mix must come out
    at essentially the voice's own level.
    """
    mixed = vf.duck_music_bed(
        voice_audio,
        music_audio,
        out_path=tmp_path / "quiet-bed.m4a",
        workdir=tmp_path / "w",
        music_gain_db=-40.0,
    )
    assert _mean_volume_db(mixed) > _mean_volume_db(voice_audio) - 1.5


def test_duck_music_bed_music_gain_reaches_the_filtergraph(voice_audio, music_audio, tmp_path):
    """A louder bed makes a louder mix — proves ``music_gain_db`` is not decorative.

    ``duck_ratio=1.0`` (compression off) is load-bearing here, not tidiness. The voice fixture
    is a constant full-scale tone, so at the default ratio the compressor holds the bed down
    for the WHOLE take and a 40 dB gain change moves the mix by under 1 dB — a real measurement
    of the ducker working, which would read as this parameter being ignored. Isolate one
    variable at a time.
    """
    quiet = vf.duck_music_bed(
        voice_audio,
        music_audio,
        out_path=tmp_path / "q.m4a",
        workdir=tmp_path / "wq",
        music_gain_db=-40.0,
        duck_ratio=1.0,
    )
    loud = vf.duck_music_bed(
        voice_audio,
        music_audio,
        out_path=tmp_path / "l.m4a",
        workdir=tmp_path / "wl",
        music_gain_db=0.0,
        duck_ratio=1.0,
    )
    assert _mean_volume_db(loud) > _mean_volume_db(quiet) + 1.0


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


def test_crossfade_output_pins_yuv420p_so_it_can_be_concatenated_afterwards():
    """The xfade path must pin the pixel format, not inherit it.

    xfade promotes to yuv444p, libx264 encodes that happily, and the resulting file then cannot be
    stream-copy concatenated with any yuv420p segment. A nested stitch — hard cuts inside groups,
    dissolves between them — therefore produced a master with mangled timestamps and no error at
    all (a 2:42 film came out 9:45 at 9.47fps). yuv420p is also what broad playback requires.
    Asserted on the argv the module builds, so it holds without invoking ffmpeg.
    """
    import inspect

    from gtm_core import video_finish as vf

    src = inspect.getsource(vf.stitch)
    assert '"-pix_fmt",' in src and '"yuv420p",' in src, (
        "stitch's crossfade re-encode must pin -pix_fmt yuv420p"
    )


def test_normalize_forces_a_reencode_on_every_segment():
    """`normalize=True` must re-encode segments that are already at the target dimensions.

    The default path returns such a segment untouched, which is right when everything came from
    one encoder and wrong when it did not: two files can agree on width, height, pix_fmt, profile
    and time_base and still carry different SPS/PPS, and an MP4 holds only one. The stream-copy
    concat then yields a file that decodes for a few segments and is corrupt after, silently.
    """
    import inspect

    from gtm_core import video_finish as vf

    sig = inspect.signature(vf.stitch)
    assert "normalize" in sig.parameters, "stitch must expose a normalize lever"
    assert sig.parameters["normalize"].default is False, "normalize must be opt-in"
    src = inspect.getsource(vf.stitch)
    assert "seg.reframed or normalize" in src, (
        "normalize must reach _normalize_shot's force_reencode, not just be accepted and dropped"
    )


def _make_clip_with_audio_layout(
    path: Path, *, w: int, h: int, duration: float, sample_rate: int, channels: int
) -> Path:
    """A clip whose AUDIO layout is set independently of its picture, for the concat check below."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size={w}x{h}:rate=25",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _audio_packet_times(path: Path) -> list[float]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "packet=pts_time",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [float(x) for x in out.stdout.split() if x]


def test_normalize_unifies_audio_layout_so_the_concat_keeps_every_segments_sound(tmp_path):
    """A segment whose audio is 44.1kHz mono must not lose its sound in the joined file.

    The concat demuxer writes ONE audio stream descriptor for the whole output. A segment that
    disagrees with it has its packets emitted under the wrong descriptor and dropped at decode —
    silently, with every later segment sliding earlier by the dropped span. Observed 2026-08-30 on
    a 208s master: nine card shots muxed from 44.1kHz mono TTS WAVs lost their voice-over entirely
    and the film ran 6.1s out of sync by the end, while ffprobe still reported a clean 1920x1080
    h264+aac file. Uniform WIDTH and HEIGHT are not enough; ``normalize`` must cover audio too.
    """
    a = _make_clip_with_audio_layout(
        tmp_path / "a.mp4", w=640, h=360, duration=1.0, sample_rate=48000, channels=2
    )
    b = _make_clip_with_audio_layout(
        tmp_path / "b.mp4", w=640, h=360, duration=1.0, sample_rate=44100, channels=1
    )
    out = vf.stitch(
        [vf.ShotSegment(path=str(a), reframed=False), vf.ShotSegment(path=str(b), reframed=False)],
        ratio="16:9",
        out_path=tmp_path / "joined.mp4",
        workdir=tmp_path / "w",
        normalize=True,
    )

    video_s = vf._probe_duration(out)
    audio_s = vf._probe_duration(out, stream_selector="a:0")
    assert audio_s == pytest.approx(video_s, abs=0.05), (
        f"audio ({audio_s:.2f}s) must span the whole picture ({video_s:.2f}s) — a shortfall is a "
        "segment whose packets were dropped"
    )
    times = _audio_packet_times(out)
    gaps = [round(times[i] - times[i - 1], 3) for i in range(1, len(times))]
    assert max(gaps) < 0.05, f"audio timeline has a gap where a segment was dropped: {gaps}"

    # The 0.05s tolerance above is deliberately tight. Negative control, run 2026-08-30 by
    # stripping the -ar/-ac pair back out of the normalize encode: these same two clips joined to
    # video 2.00s against audio 1.88s, with ffmpeg logging non-monotonic audio DTS at the join. A
    # looser bound passes on a 2s fixture while that same per-join loss costs a 208s master 6.1s.


# --- sidecar merge: per-shot caption geometry carried onto the master's timeline -----------

import importlib  # noqa: E402

# The package re-exports the stitch FUNCTION under the submodule's name; go to the module.
stitch_mod = importlib.import_module("gtm_core.video_finish.stitch")


def _box(y: int = 1500) -> dict:
    return {"x": 100, "y": y, "w": 880, "h": 120}


def _write_sidecar(seg: Path, *, kind: str = "captions", frame=(1080, 1080), entries=None) -> Path:
    list_key = stitch_mod.SIDECAR_KINDS[kind]
    payload = {"frame": list(frame), "shot_id": seg.stem, list_key: entries or []}
    path = stitch_mod.sidecar_path(seg, kind)
    path.write_text(json.dumps(payload))
    return path


def _stitch(tmp_path: Path, clips: list[Path], **kw) -> Path:
    return vf.stitch(
        [vf.ShotSegment(path=str(c), reframed=False) for c in clips],
        ratio="1:1",
        out_path=tmp_path / "master.mp4",
        workdir=tmp_path / "w",
        **kw,
    )


def test_stitch_merges_per_segment_caption_sidecars_with_cumulative_offsets(tmp_path):
    a = _make_clip(tmp_path / "a.mp4", w=1080, h=1080, duration=2.0)
    b = _make_clip(tmp_path / "b.mp4", w=1080, h=1080, duration=1.0)
    _write_sidecar(
        a,
        entries=[
            {"index": 0, "text": "one", "start_s": 0.0, "end_s": 1.0, "box": _box()},
            {"index": 1, "text": "two", "start_s": 1.0, "end_s": 2.0, "box": _box()},
        ],
    )
    _write_sidecar(
        b, entries=[{"index": 0, "text": "three", "start_s": 0.0, "end_s": 1.0, "box": _box()}]
    )
    out = _stitch(tmp_path, [a, b])
    merged_path = tmp_path / "master.captions.json"
    assert stitch_mod.stitched_sidecars(out) == {"captions": str(merged_path)}
    merged = json.loads(merged_path.read_text())
    dur_a = vf._probe_duration(a)
    assert merged["frame"] == [1080, 1080]
    assert [s["index"] for s in merged["screens"]] == [0, 1, 2]
    assert [s["shot_id"] for s in merged["screens"]] == ["a", "a", "b"]
    assert merged["screens"][0]["start_s"] == 0.0
    assert merged["screens"][2]["start_s"] == pytest.approx(dur_a, abs=0.005)
    assert merged["screens"][2]["end_s"] == pytest.approx(dur_a + 1.0, abs=0.005)
    assert merged["screens"][2]["box"] == _box()  # geometry is carried, never re-derived
    assert [seg["offset_s"] for seg in merged["segments"]] == [0.0, pytest.approx(dur_a, abs=0.005)]


def test_stitch_crossfade_offsets_subtract_the_overlap(tmp_path):
    a = _make_clip(tmp_path / "a.mp4", w=1080, h=1080, duration=2.0)
    b = _make_clip(tmp_path / "b.mp4", w=1080, h=1080, duration=2.0)
    _write_sidecar(a, entries=[{"start_s": 0.0, "end_s": 2.0, "box": _box()}])
    _write_sidecar(b, entries=[{"start_s": 0.5, "end_s": 1.5, "box": _box()}])
    _stitch(tmp_path, [a, b], crossfade_s=0.4)
    merged = json.loads((tmp_path / "master.captions.json").read_text())
    dur_a = vf._probe_duration(a)
    assert merged["screens"][1]["start_s"] == pytest.approx(dur_a - 0.4 + 0.5, abs=0.005)


def test_stitch_refuses_sidecars_whose_frames_disagree(tmp_path):
    a = _make_clip(tmp_path / "a.mp4", w=1080, h=1080)
    b = _make_clip(tmp_path / "b.mp4", w=1080, h=1080)
    _write_sidecar(a, frame=(1080, 1080), entries=[{"start_s": 0.0, "end_s": 1.0, "box": _box()}])
    _write_sidecar(b, frame=(1080, 1920), entries=[{"start_s": 0.0, "end_s": 1.0, "box": _box()}])
    with pytest.raises(ValueError, match="disagree on frame"):
        _stitch(tmp_path, [a, b])
    assert not (tmp_path / "master.mp4").exists(), "refused before the encode, not after"


def test_stitch_without_segment_sidecars_writes_no_merged_sidecar_and_clears_a_stale_one(tmp_path):
    a = _make_clip(tmp_path / "a.mp4", w=1080, h=1080)
    stale = tmp_path / "master.captions.json"
    stale.write_text(json.dumps({"frame": [1080, 1080], "screens": []}))
    out = _stitch(tmp_path, [a])
    assert out.is_file()
    assert stitch_mod.stitched_sidecars(out) == {}
    assert not stale.exists(), "a merged sidecar from an earlier stitch must not outlive it"


def test_an_overlays_sidecar_merges_by_the_same_generic_rule(tmp_path):
    """The overlay track's sidecar has a different list key and entry shape; only the timed
    fields move. `anchor` and `kind` ride through untouched."""
    a = _make_clip(tmp_path / "a.mp4", w=1080, h=1080, duration=1.0)
    b = _make_clip(tmp_path / "b.mp4", w=1080, h=1080, duration=1.0)
    entry = {
        "kind": "lower-third",
        "text": "acme",
        "start_s": 0.2,
        "end_s": 0.8,
        "box": _box(),
        "anchor": {"corner": "bottom-left"},
    }
    _write_sidecar(a, kind="overlays", entries=[entry])
    _write_sidecar(b, kind="overlays", entries=[entry])
    out = _stitch(tmp_path, [a, b])
    assert set(stitch_mod.stitched_sidecars(out)) == {"overlays"}
    merged = json.loads((tmp_path / "master.overlays.json").read_text())
    dur_a = vf._probe_duration(a)
    second = merged["overlays"][1]
    assert second["start_s"] == pytest.approx(dur_a + 0.2, abs=0.005)
    assert second["anchor"] == {"corner": "bottom-left"} and second["kind"] == "lower-third"
    assert not (tmp_path / "master.captions.json").exists()
