"""gtm_core.video_finish.find_transient() / mix_sfx_cues() — against real ffmpeg.

Every fixture is synthesized with lavfi; nothing is committed. The click is an exponential decay
at an EXACT known onset and peak, which is what makes "did the cue land where and how loud it was
asked to" an assertion rather than a listen.

Two shipped defects are frozen here as tests: amix's silent 1/n attenuation, and an absolute
silencedetect threshold that could not see a quiet cue at all.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from gtm_core import video_finish as vf

_MAX_VOL = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def _base_clip(path: Path, *, seconds: float = 6.0, level_db: float = -20.0) -> Path:
    """Picture plus a steady tone at a KNOWN level, so any change to the base's level after
    mixing is attributable to the mix and to nothing else."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=gray:size=320x180:rate=25:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:duration={seconds}:sample_rate=48000",
            "-af",
            f"volume={level_db}dB",
            "-ac",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _click(
    path: Path,
    *,
    onset_s: float = 0.12,
    peak: float = 0.5,
    seconds: float = 0.6,
    rate: int = 48000,
    layout: str = "stereo",
) -> Path:
    expr = f"if(gt(t,{onset_s}),{peak}*exp(-60*(t-{onset_s}))*sin(2*PI*1400*t),0)"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc=exprs='{expr}':sample_rate={rate}:duration={seconds}:channel_layout={layout}",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _window_db(path: Path, *, start_s: float, duration_s: float, pan: str = "") -> float:
    """Measured with `atrim`, never `-ss`: output seeking does not reach the filter graph on
    ffmpeg 8.x (every window of a decaying click reports the whole file's peak), and input
    seeking is keyframe-quantised on a compressed file."""
    chain = f"atrim=start={start_s}:end={start_s + duration_s},asetpts=PTS-STARTPTS"
    if pan:
        chain += f",{pan}"
    out = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-i",
            str(path),
            "-map",
            "a:0",
            "-af",
            f"{chain},volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    m = _MAX_VOL.search(out.stderr + out.stdout)
    return float(m.group(1)) if m else float("-inf")


# ── find_transient ───────────────────────────────────────────────────────────────────────


def test_find_transient_lands_on_the_attack_not_the_peak(tmp_path):
    t = vf.find_transient(_click(tmp_path / "c.wav", onset_s=0.12))
    assert 0.07 <= t.onset_s <= 0.13, t
    assert t.onset_s <= t.peak_s
    assert t.peak_dbfs == pytest.approx(-6.02, abs=0.8)


@pytest.mark.parametrize("peak", [0.5, 0.05, 0.02])
def test_find_transient_is_level_invariant(tmp_path, peak):
    """The same click 28 dB quieter must report the same onset. This is the property a relative
    rule has and an absolute threshold cannot be tuned into having."""
    t = vf.find_transient(_click(tmp_path / f"c-{peak}.wav", onset_s=0.12, peak=peak))
    assert 0.07 <= t.onset_s <= 0.13, t


def test_an_absolute_threshold_answer_depends_on_level_and_this_one_does_not(tmp_path):
    """The mechanism behind the shipped defect, isolated.

    ``silencedetect=noise=-35dB:d=0.05`` is what was used, and it is an ABSOLUTE detector: it
    answers "where does this file exceed -35 dBFS", which is the same question as "where does the
    sound start" only when the file's peak sits comfortably above -35 dB. The cue that shipped
    peaked around -34 dBFS, right on the line, so the threshold cut through the middle of the
    signal: the reported onset tracked whichever later moment sustained 50 ms above it (0.18s)
    while the real attack sat at 0.10-0.15s, the head was trimmed past the attack, and what
    shipped was a decay tail at -35.8 dBFS.

    What this asserts is the property underneath that, which is reproducible without contriving a
    waveform: the absolute detector's answer CHANGES with level and eventually disappears
    entirely, while the relative rule returns the same onset at every level. It does not claim to
    reproduce that specific 0.18s reading, which depended on that cue's own envelope.
    """
    loud = _click(tmp_path / "loud.wav", onset_s=0.12, peak=0.5)  # ~ -6 dBFS
    quiet = _click(tmp_path / "quiet.wav", onset_s=0.12, peak=0.01)  # ~ -40 dBFS, under the line

    def _silencedetect_onset(path: Path) -> float | None:
        out = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-v",
                "info",
                "-i",
                str(path),
                "-af",
                "silencedetect=noise=-35dB:d=0.05",
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        ends = [
            float(m)
            for m in re.findall(r"silence_end:\s*(-?\d+(?:\.\d+)?)", out.stderr + out.stdout)
        ]
        inside = [e for e in ends if e < 0.55]  # 0.6 is the file end, not an onset
        return min(inside) if inside else None

    assert _silencedetect_onset(loud) is not None
    assert _silencedetect_onset(quiet) is None, (
        "the absolute detector saw the quiet cue — the fixture no longer sits under -35 dBFS"
    )

    loud_onset = vf.find_transient(loud).onset_s
    quiet_onset = vf.find_transient(quiet).onset_s
    assert loud_onset == quiet_onset, (loud_onset, quiet_onset)
    assert 0.07 <= quiet_onset <= 0.13


def test_find_transient_refuses_silence(tmp_path):
    silent = tmp_path / "silent.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo:d=0.5",
            str(silent),
        ],
        check=True,
        capture_output=True,
    )
    with pytest.raises(vf.SfxError, match="dBFS floor"):
        vf.find_transient(silent)


# ── mix_sfx_cues ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("n_cues", [1, 2, 3])
def test_the_base_track_keeps_unity_gain(tmp_path, n_cues):
    """THE normalize=0 TEST. amix divides every input by n, so 1/2/3 cues would cost the base
    6.02 / 9.54 / 12.04 dB — silently, and invisibly to video_lint, whose dead-air gate reads a
    raised floor as an improvement while the speech underneath gets quieter."""
    clip = _base_clip(tmp_path / "base.mp4")
    cues = [
        vf.SfxCue(
            path=str(_click(tmp_path / f"c{i}.wav")), at_s=1.0 + i, trim_s=0.10, label=f"c{i}"
        )
        for i in range(n_cues)
    ]
    before = _window_db(clip, start_s=4.5, duration_s=0.5)  # a cue-free stretch
    out = vf.mix_sfx_cues(clip, cues, out_path=tmp_path / "mixed.mp4", workdir=tmp_path / "w")
    after = _window_db(out.out_path, start_s=4.5, duration_s=0.5)
    assert abs(after - before) < 0.5, (
        f"base moved {after - before:.2f} dB with {n_cues} cues — amix normalize is back on"
    )


def test_each_cue_lands_at_its_requested_time(tmp_path):
    clip = _base_clip(tmp_path / "base.mp4")
    cues = [
        vf.SfxCue(path=str(_click(tmp_path / "a.wav")), at_s=1.5, trim_s=0.10, label="a"),
        vf.SfxCue(path=str(_click(tmp_path / "b.wav")), at_s=4.2, trim_s=0.10, label="b"),
    ]
    out = vf.mix_sfx_cues(clip, cues, out_path=tmp_path / "mixed.mp4", workdir=tmp_path / "w")
    for cue in cues:
        at = _window_db(out.out_path, start_s=cue.at_s, duration_s=0.15)
        away = _window_db(out.out_path, start_s=cue.at_s + 0.8, duration_s=0.15)
        assert at > away + 6.0, f"no cue energy at {cue.at_s}s ({at:.1f} vs {away:.1f} dBFS)"


def test_a_shortfall_raises_and_writes_nothing(tmp_path):
    """A cue trimmed past its own end mixes in nothing. The measurement catches it; the ordering
    (verify on PCM, encode only after) is what guarantees no file exists to mistake for done."""
    clip = _base_clip(tmp_path / "base.mp4")
    cue = vf.SfxCue(
        path=str(_click(tmp_path / "c.wav", seconds=0.3)), at_s=2.0, trim_s=0.45, label="dead"
    )
    out_path = tmp_path / "mixed.mp4"
    with pytest.raises(vf.SfxError) as exc:
        vf.mix_sfx_cues(clip, [cue], out_path=out_path, workdir=tmp_path / "w")
    assert "dead" in str(exc.value)
    assert "does not exist" in str(exc.value)
    assert not out_path.exists()
    assert not out_path.with_suffix(".mp4.part").exists()


def test_a_cue_too_quiet_to_measure_is_refused_as_unverifiable_not_reported_as_quiet(tmp_path):
    """Not "quiet" — UNMEASURABLE. At 80 dB down the residual sits within a few dB of the
    arithmetic floor, so any number reported for it would be describing rounding noise rather
    than the cue. Saying "shortfall 80 dB" there would be a confident lie; saying "this is not a
    measurement" is the truth."""
    clip = _base_clip(tmp_path / "base.mp4")
    cue = vf.SfxCue(
        path=str(_click(tmp_path / "c.wav")), at_s=1.5, trim_s=0.10, gain_db=-80.0, label="whisper"
    )
    with pytest.raises(vf.SfxError, match="arithmetic floor"):
        vf.mix_sfx_cues(
            clip,
            [cue],
            out_path=tmp_path / "no.mp4",
            workdir=tmp_path / "w",
            shortfall_max_db=200.0,  # isolate the trust check from the shortfall check
        )


def test_the_null_floor_is_deep_on_the_pcm_path(tmp_path):
    """Every shortfall number rests on this. If the PCM intermediates are ever swapped for a
    direct AAC verification, a codec's -40 to -60 dBFS of error puts a floor under the whole
    measurement and this is the test that notices."""
    clip = _base_clip(tmp_path / "base.mp4")
    cue = vf.SfxCue(path=str(_click(tmp_path / "c.wav")), at_s=1.5, trim_s=0.10, label="c")
    result = vf.mix_sfx_cues(clip, [cue], out_path=tmp_path / "m.mp4", workdir=tmp_path / "w")
    assert result.cues[0].null_floor_dbfs < -80.0, result.cues[0]


def test_the_picture_is_stream_copied(tmp_path):
    """Mixing sound must never re-encode a graded picture."""
    clip = _base_clip(tmp_path / "base.mp4")
    cue = vf.SfxCue(path=str(_click(tmp_path / "c.wav")), at_s=1.5, trim_s=0.10, label="c")

    def _video_md5(p: Path) -> str:
        out = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-v",
                "error",
                "-i",
                str(p),
                "-map",
                "0:v",
                "-c",
                "copy",
                "-f",
                "md5",
                "-",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    before = _video_md5(clip)
    out = vf.mix_sfx_cues(clip, [cue], out_path=tmp_path / "m.mp4", workdir=tmp_path / "w")
    assert _video_md5(out.out_path) == before


def test_a_mismatched_cue_layout_is_coerced_not_refused(tmp_path):
    """A 44.1 kHz mono cue beside a 48 kHz stereo base is the concat defect AUDIO_SAMPLE_RATE was
    written down for, arriving through a different door."""
    clip = _base_clip(tmp_path / "base.mp4")
    cue = vf.SfxCue(
        path=str(_click(tmp_path / "c.wav", rate=44100, layout="mono")),
        at_s=1.5,
        trim_s=0.10,
        label="c",
    )
    out = vf.mix_sfx_cues(clip, [cue], out_path=tmp_path / "m.mp4", workdir=tmp_path / "w")
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels",
            "-of",
            "csv=p=0",
            str(out.out_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip().startswith("48000,2")


def test_adelay_reaches_both_channels(tmp_path):
    """The behavioural half of the graph assertion: a future `adelay=4200` regression passes a
    string test only if someone edits the string, but never passes this."""
    clip = _base_clip(tmp_path / "base.mp4")
    cue = vf.SfxCue(path=str(_click(tmp_path / "c.wav")), at_s=2.0, trim_s=0.10, label="c")
    out = vf.mix_sfx_cues(clip, [cue], out_path=tmp_path / "m.mp4", workdir=tmp_path / "w")

    def _chan_db(ch: int) -> float:
        return _window_db(out.out_path, start_s=2.0, duration_s=0.15, pan=f"pan=mono|c0=c{ch}")

    left, right = _chan_db(0), _chan_db(1)
    assert abs(left - right) < 1.0, f"cue is not on both channels: L={left} R={right}"


def test_a_clip_with_no_audio_is_refused(tmp_path):
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x180:rate=25",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(silent),
        ],
        check=True,
        capture_output=True,
    )
    with pytest.raises(vf.SfxError, match="no audio stream"):
        vf.mix_sfx_cues(
            silent,
            [vf.SfxCue(path=str(_click(tmp_path / "c.wav")), at_s=1.0)],
            out_path=tmp_path / "m.mp4",
            workdir=tmp_path / "w",
        )
