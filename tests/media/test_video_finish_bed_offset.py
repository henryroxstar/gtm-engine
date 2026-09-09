"""C6 — where the music bed OPENS (`build_music_bed(start_at_s=...)`), and how we know.

A catalogue cue is written to be listened to from the top; a film is not obliged to start there.
`start_at_s` skips a slow build so the picture opens on the music rather than thirty seconds ahead
of it — and it is a different question from `loop_from_s`, which picks where a mid-film REPEAT is
taken from. The two look alike, which is why they are tested against each other here.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from gtm_core.video_finish.audio import build_music_bed

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _track(tmp_path):
    """A 20s track: near-silence for 5s, then a loud tone. The 'drop' is at 5.0s."""
    out = tmp_path / "track.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-filter_complex",
            "sine=frequency=440:duration=5,volume=0.02[quiet];"
            "sine=frequency=440:duration=15,volume=0.7[loud];"
            "[quiet][loud]concat=n=2:v=0:a=1[a]",
            "-map",
            "[a]",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def _rms_dbfs(path, *, start: float, dur: float) -> float:
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-ss",
            str(start),
            "-t",
            str(dur),
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    for line in out.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0].strip())
    raise AssertionError(f"no mean_volume in ffmpeg output for {path}")


def test_the_bed_opens_on_the_drop_when_start_at_s_names_it(tmp_path):
    """The property, measured: the first moment of the bed is loud, not the track's quiet intro."""
    track = _track(tmp_path)
    at_top = build_music_bed(
        track,
        out_path=tmp_path / "top.m4a",
        duration_s=6.0,
        workdir=tmp_path / "w1",
        body_end_s=20.0,
        fade_out_s=0.5,
    )
    at_drop = build_music_bed(
        track,
        out_path=tmp_path / "drop.m4a",
        duration_s=6.0,
        workdir=tmp_path / "w2",
        body_end_s=20.0,
        start_at_s=5.0,
        fade_out_s=0.5,
    )
    head_at_top = _rms_dbfs(at_top, start=0.0, dur=1.0)
    head_at_drop = _rms_dbfs(at_drop, start=0.0, dur=1.0)
    assert head_at_drop > head_at_top + 10.0, (
        f"the bed did not open on the drop: {head_at_drop:.1f} dB vs {head_at_top:.1f} dB at the "
        "top of the track — a difference this small means start_at_s was ignored"
    )


def test_the_default_is_byte_identical_to_not_passing_it(tmp_path):
    """C6 must be additive: every existing caller renders exactly what it rendered before."""
    track = _track(tmp_path)
    a = build_music_bed(
        track, out_path=tmp_path / "a.m4a", duration_s=6.0, workdir=tmp_path / "wa", body_end_s=20.0
    )
    b = build_music_bed(
        track,
        out_path=tmp_path / "b.m4a",
        duration_s=6.0,
        workdir=tmp_path / "wb",
        body_end_s=20.0,
        start_at_s=0.0,
    )
    assert a.read_bytes() == b.read_bytes(), "the default start_at_s changed the output"


def test_a_start_beyond_the_usable_body_is_refused(tmp_path):
    """There would be no bed left to lay under the film."""
    track = _track(tmp_path)
    with pytest.raises(ValueError, match="start_at_s"):
        build_music_bed(
            track,
            out_path=tmp_path / "x.m4a",
            duration_s=4.0,
            workdir=tmp_path / "w",
            body_end_s=20.0,
            start_at_s=25.0,
        )


def test_a_loop_point_earlier_than_the_start_is_refused(tmp_path):
    """The two arguments look alike and are not. A repeat taken from BEFORE the bed opens splices
    the intro the bed deliberately skipped back into the middle of the film."""
    track = _track(tmp_path)
    with pytest.raises(ValueError, match="earlier than start_at_s"):
        build_music_bed(
            track,
            out_path=tmp_path / "x.m4a",
            duration_s=30.0,
            workdir=tmp_path / "w",
            body_end_s=20.0,
            start_at_s=8.0,
            loop_from_s=2.0,
        )


def test_a_loop_point_at_or_after_the_start_is_accepted(tmp_path):
    """Positive control for the refusal above — the combination is legal, only the order is not."""
    track = _track(tmp_path)
    out = build_music_bed(
        track,
        out_path=tmp_path / "ok.m4a",
        duration_s=30.0,
        workdir=tmp_path / "w",
        body_end_s=20.0,
        start_at_s=5.0,
        loop_from_s=8.0,
        crossfade_s=1.0,
    )
    assert out.is_file()


def test_a_shortened_head_still_reaches_the_requested_duration(tmp_path):
    """Opening late leaves less head material, so the repeat count has to account for it —
    otherwise a bed that starts at the drop comes up short and the film ends in silence."""
    track = _track(tmp_path)
    out = build_music_bed(
        track,
        out_path=tmp_path / "long.m4a",
        duration_s=28.0,
        workdir=tmp_path / "w",
        body_end_s=20.0,
        start_at_s=15.0,
        loop_from_s=15.0,
        crossfade_s=1.0,
        fade_out_s=1.0,
    )
    probed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert float(probed.stdout.strip()) == pytest.approx(28.0, abs=0.3), (
        "the bed came up short once the head was trimmed — the film would end in silence"
    )


# ── the plan side: WHERE the number came from is recorded beside the number ───────────────────


def test_the_plan_records_both_the_offset_and_how_it_was_arrived_at():
    """A provider-stated peak and a detected one are both numbers; only one is evidence."""
    from gtm_core.video_finish.plan import _audio_context_from_spec

    stated = _audio_context_from_spec(
        {"has_music_bed": True, "has_voice": True, "music_bed": {"peak_offset_s": 12.5}}
    )
    assert stated["bed_start_s"] == 12.5
    assert stated["bed_offset_source"] == "peak_offset"

    none = _audio_context_from_spec({"has_music_bed": True, "has_voice": True})
    assert none["bed_start_s"] == 0.0
    assert none["bed_offset_source"] == "none", (
        "a bed with no stated offset must be distinguishable from one detected at zero"
    )


def test_a_spec_declaring_nothing_still_returns_none():
    """The existing contract: "declared nothing" and "declared no bed" are different answers,
    and C6 must not have collapsed them by always adding two keys."""
    from gtm_core.video_finish.plan import _audio_context_from_spec

    assert _audio_context_from_spec({"caption_text": "hi"}) is None


def test_an_unset_loop_point_follows_the_start_so_the_skipped_intro_never_returns(tmp_path):
    """Review finding: the guard was `if loop_from_s and …`, so the 0.0 default sailed through and
    the first repeat was cut from [0.0, body_end] — the intro the bed had just skipped. Asserted
    on the repeat segment's own duration, which is body_end - loop_from_s by construction."""
    track = _track(tmp_path)
    work = tmp_path / "w"
    build_music_bed(
        track,
        out_path=tmp_path / "b.m4a",
        duration_s=30.0,
        workdir=work,
        body_end_s=20.0,
        start_at_s=5.0,
        crossfade_s=1.0,
        fade_out_s=1.0,
    )
    loop_seg = work / "bed_loop.wav"
    assert loop_seg.is_file(), "the bed needed a repeat and none was materialised"
    probed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(loop_seg),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert float(probed.stdout.strip()) == pytest.approx(20.0 - 5.0, abs=0.1), (
        "the repeat was cut from the top of the track, not from where the bed opens"
    )


def test_a_provider_peak_at_exactly_zero_is_a_stated_offset_not_an_absent_one():
    """Falsy-zero: the drop being at 0.0 is a real number the provider gave us."""
    from gtm_core.video_finish.plan import _audio_context_from_spec

    ctx = _audio_context_from_spec(
        {"has_music_bed": True, "has_voice": True, "music_bed": {"peak_offset_s": 0.0}}
    )
    assert ctx["bed_start_s"] == 0.0
    assert ctx["bed_offset_source"] == "peak_offset"
