"""gtm_core.video_finish.voice_polish.polish_voice() — against real ffmpeg. This directory
requires ffmpeg on PATH (see conftest.py).

The source WAV is built with Python's own `wave` + `numpy`, never ffmpeg — a 40Hz sine (the
rumble `highpass_hz` exists to remove) plus a 1kHz sine (a stand-in for the body of a voice) plus
a little noise. The claim under test is a RELATIVE one: the 40Hz component must come out smaller
relative to the 1kHz one than it went in, not merely "quieter than before" (the compressor's
makeup gain could make everything louder and still pass a naive check).

Output is read back with a hand-rolled RIFF chunk parser rather than the stdlib `wave` module or
`audioop` (removed in 3.13): the point of a lossless 24-bit PCM output is exactly the range `wave`
cannot decode into ints on its own, so decoding it by hand is the only way to prove the bit depth
shipped rather than assumed. It always returns samples shaped ``(n_frames, channels)``, mono
included, so a channel-count regression cannot hide behind a caller that only ever indexed [0].
"""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

import numpy as np
import pytest

from gtm_core import video_finish as vf
from gtm_core.video_finish.voice_polish import polish_voice

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

SRC_RATE = 44100
SRC_SECONDS = 2.0
ONSET_S = 0.5
ONSET_TOTAL_S = 1.5


def _make_source_wav(
    path: Path, *, rate: int = SRC_RATE, seconds: float = SRC_SECONDS
) -> np.ndarray:
    """A mono 16-bit source: 40Hz rumble + 1kHz body + a little noise. Returns the float
    samples actually written (normalized to [-1, 1]), so the test can FFT the source without
    re-decoding its own fixture.
    """
    import wave

    n = int(rate * seconds)
    t = np.arange(n) / rate
    rng = np.random.default_rng(1729)
    samples = 0.3 * np.sin(2 * np.pi * 40 * t) + 0.3 * np.sin(2 * np.pi * 1000 * t)
    samples += 0.01 * rng.standard_normal(n)
    samples = np.clip(samples, -1.0, 1.0)
    pcm16 = (samples * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16.tobytes())
    return samples


def _make_stereo_wav(path: Path, *, rate: int = SRC_RATE, seconds: float = SRC_SECONDS) -> None:
    """Stereo source: a 1kHz tone on the LEFT channel only, near-silence on the right — channel
    separation a regression that force-downmixed to mono (or swapped/summed channels) would
    destroy.
    """
    import wave

    n = int(rate * seconds)
    t = np.arange(n) / rate
    rng = np.random.default_rng(4242)
    left = np.clip(0.3 * np.sin(2 * np.pi * 1000 * t) + 0.005 * rng.standard_normal(n), -1.0, 1.0)
    right = np.clip(0.005 * rng.standard_normal(n), -1.0, 1.0)
    stereo = np.empty(n * 2, dtype=np.float64)
    stereo[0::2] = left
    stereo[1::2] = right
    pcm16 = (stereo * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16.tobytes())


def _make_onset_wav(path: Path, *, rate: int = SRC_RATE) -> None:
    """Silence, then a clean 1kHz tone starting at exactly ``ONSET_S`` — nothing else in the
    file, so a threshold crossing anywhere but ``ONSET_S`` in the output can only be explained
    by processing-induced latency.
    """
    import wave

    n = int(rate * ONSET_TOTAL_S)
    t = np.arange(n) / rate
    onset_n = int(round(ONSET_S * rate))
    samples = np.zeros(n, dtype=np.float64)
    samples[onset_n:] = 0.5 * np.sin(2 * np.pi * 1000 * (t[onset_n:] - ONSET_S))
    pcm16 = (samples * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16.tobytes())


def _read_wav(path: Path) -> tuple[int, int, np.ndarray]:
    """(sample_rate, channels, float samples in [-1, 1] shaped (n_frames, channels)) — a manual
    RIFF/PCM parse, so 24-bit output is decoded on its own terms rather than trusted to a library
    that only knows 8/16/32.
    """
    data = path.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE", "not a RIFF/WAVE file"
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        (chunk_size,) = struct.unpack_from("<I", data, pos + 4)
        body = data[pos + 8 : pos + 8 + chunk_size]
        if chunk_id == b"fmt ":
            _fmt_tag, channels, rate, _byte_rate, _block_align, bits = struct.unpack_from(
                "<HHIIHH", body
            )
            fmt = (rate, channels, bits)
        elif chunk_id == b"data":
            pcm = body
        pos += 8 + chunk_size + (chunk_size & 1)  # chunks are word-aligned
    assert fmt is not None and pcm is not None, "missing fmt/data chunk"
    rate, channels, bits = fmt
    assert bits == 24, f"expected 24-bit PCM, got {bits}-bit"
    n = len(pcm) // 3
    raw = np.frombuffer(pcm[: n * 3], dtype=np.uint8).reshape(n, 3)
    ints = (
        raw[:, 0].astype(np.int32)
        | (raw[:, 1].astype(np.int32) << 8)
        | (raw[:, 2].astype(np.int32) << 16)
    )
    ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
    samples = ints.astype(np.float64) / float(1 << 23)
    frames = len(samples) // channels
    return rate, channels, samples[: frames * channels].reshape(frames, channels)


def _band_magnitude(samples: np.ndarray, rate: int, freq: float) -> float:
    """Peak FFT magnitude within a few bins of ``freq`` — a Hanning window plus a small search
    band tolerates the sub-bin frequency shift a ~2ms duration difference would otherwise cause.
    """
    n = len(samples)
    spec = np.abs(np.fft.rfft(samples * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, d=1.0 / rate)
    idx = int(np.argmin(np.abs(freqs - freq)))
    lo, hi = max(0, idx - 5), idx + 6
    return float(spec[lo:hi].max())


def _onset_time(samples: np.ndarray, rate: int, *, threshold: float) -> float:
    """First sample whose magnitude crosses ``threshold`` — a plain energy-threshold onset
    finder, adequate for one clean burst with silence ahead of it.
    """
    idx = int(np.argmax(np.abs(samples) > threshold))
    assert abs(samples[idx]) > threshold, "no sample crossed the onset threshold"
    return idx / rate


def test_polish_voice_output_format(tmp_path):
    src = tmp_path / "src.wav"
    _make_source_wav(src)
    out = tmp_path / "out.wav"
    result = polish_voice(src, out)

    assert result["out_path"] == out
    assert out.is_file()
    rate, channels, samples = _read_wav(out)
    assert rate == 48000
    assert channels == 1  # same channel count as the mono source
    assert result["sample_rate"] == 48000
    assert result["channels"] == 1

    out_duration_s = len(samples) / rate
    assert abs(out_duration_s - SRC_SECONDS) <= 0.002
    assert abs(result["output_duration_s"] - result["input_duration_s"]) <= 0.002
    assert result["input_duration_s"] == pytest.approx(SRC_SECONDS, abs=0.01)


def test_polish_voice_attenuates_the_rumble_relative_to_the_body(tmp_path):
    src = tmp_path / "src.wav"
    src_samples = _make_source_wav(src)
    out = tmp_path / "out.wav"
    polish_voice(src, out)
    out_rate, _channels, out_samples = _read_wav(out)

    in_ratio = _band_magnitude(src_samples, SRC_RATE, 40) / _band_magnitude(
        src_samples, SRC_RATE, 1000
    )
    out_ratio = _band_magnitude(out_samples[:, 0], out_rate, 40) / _band_magnitude(
        out_samples[:, 0], out_rate, 1000
    )
    # highpass_hz=80 sits one octave above the 40Hz rumble; the mud/presence EQ and compressor
    # do not discriminate between 40Hz and 1kHz the way the highpass does — so the 40:1kHz ratio
    # must come down by a wide, unambiguous margin, not just move a little.
    assert out_ratio < in_ratio * 0.5, (in_ratio, out_ratio)


def test_polish_voice_preserves_stereo_channel_separation(tmp_path):
    """A regression that forced ``-ac 1`` (or otherwise mixed the channels) would pass every
    mono assertion in this file and still ship broken — this is the case only stereo catches.
    """
    src = tmp_path / "src.wav"
    _make_stereo_wav(src)
    out = tmp_path / "out.wav"
    result = polish_voice(src, out)
    assert result["channels"] == 2
    rate, channels, samples = _read_wav(out)
    assert channels == 2

    left_energy = float(np.mean(samples[:, 0] ** 2))
    right_energy = float(np.mean(samples[:, 1] ** 2))
    assert left_energy > right_energy * 10, (left_energy, right_energy)


def test_polish_voice_does_not_shift_the_onset(tmp_path):
    """No trim, no pad, anywhere in the chain (per the module docstring) — so a tone that starts
    at ONSET_S in the source must start within 1ms of ONSET_S in the output, found by a plain
    energy threshold rather than assumed from the duration numbers alone.
    """
    src = tmp_path / "src.wav"
    _make_onset_wav(src)
    out = tmp_path / "out.wav"
    polish_voice(src, out)
    out_rate, channels, samples = _read_wav(out)
    assert channels == 1
    tone = samples[:, 0]
    peak = float(np.max(np.abs(tone)))
    onset = _onset_time(tone, out_rate, threshold=peak * 0.1)
    assert abs(onset - ONSET_S) <= 0.001, onset


def test_polish_voice_is_byte_deterministic(tmp_path):
    src = tmp_path / "src.wav"
    _make_source_wav(src)
    out_a, out_b = tmp_path / "a.wav", tmp_path / "b.wav"
    polish_voice(src, out_a)
    polish_voice(src, out_b)
    assert out_a.read_bytes() == out_b.read_bytes()


def test_polish_voice_rejects_a_non_wav_extension(tmp_path):
    src = tmp_path / "src.wav"
    _make_source_wav(src)
    with pytest.raises(ValueError, match=r"\.wav"):
        polish_voice(src, tmp_path / "out.m4a")


def test_cli_voice_polish_refuses_an_out_outside_the_content_root(tmp_path, capsys):
    root = tmp_path / "content"
    root.mkdir()
    src = tmp_path / "src.wav"
    _make_source_wav(src)
    rc = vf.main(
        [
            "voice-polish",
            "--in",
            str(src),
            "--out",
            str(tmp_path / "elsewhere" / "out.wav"),
            "--content-root",
            str(root),
        ]
    )
    assert rc == 2
    assert "outside the resolved content root" in capsys.readouterr().err


def test_cli_voice_polish_runs_the_path_and_reports_json(tmp_path, capsys):
    root = tmp_path / "content"
    root.mkdir()
    src = tmp_path / "src.wav"
    _make_source_wav(src)
    out = root / "out.wav"
    rc = vf.main(
        [
            "voice-polish",
            "--in",
            str(src),
            "--out",
            str(out),
            "--content-root",
            str(root),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert str(out) in captured.out
    assert out.is_file()
