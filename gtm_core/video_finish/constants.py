from __future__ import annotations

#: 15.041667s (361 frames @ 24fps) is the observed predictor-cap failure duration. A keyframe-
#: accurate stream copy at -t 14.9 lands right back on a GOP boundary near 15.04s, so the trim
#: MUST re-encode — never -c copy.
PREDICTOR_TRIM_S = 14.9

#: Pinned encode parameters — part of the idempotency contract (F: "-threads 0" is host-dependent).
DEFAULT_CRF = 20
DEFAULT_PRESET = "medium"
DEFAULT_GOP = 48
DEFAULT_THREADS = 4
DEFAULT_LOUDNESS_LUFS = -14.0

#: The audio layout every segment is forced to before a concat. Uniform WIDTH/HEIGHT is not enough:
#: the concat demuxer writes ONE audio stream descriptor for the whole file, so a segment whose
#: audio is 44.1kHz mono beside 48kHz stereo neighbours has its packets emitted under the wrong
#: descriptor and DROPPED at decode — silently, and with every later segment sliding earlier by the
#: dropped span. Observed 2026-08-30 on a 208s master: nine card shots muxed from 44.1kHz mono TTS
#: WAVs lost their voice-over entirely and the film ran 6.1s out of sync by the end, while ffprobe
#: reported a clean 1920x1080 25fps h264+aac file.
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2

#: How far :func:`mux` may speed a VO up to fit its shot before refusing. atempo is transparent at
#: small ratios and audibly distorts a cloned voice well before its own 2.0 limit, so the ceiling is
#: a craft bound, not a technical one. Past it, the mismatch is a script-length problem to fix at
#: the source (a longer render, or a shorter line) — see :func:`mux`.
MUX_ATEMPO_MAX = 1.15

#: Ceiling for a shot whose mouth was animated against this exact VO. `atempo` rescales the audio
#: AFTER the model has already committed the mouth to the original timing, so any value but 1.0
#: slides the words off the lips by exactly that factor — 1.12 on a 3.36s shot is ~400 ms of drift
#: by the end of the line, which is the "lip sync isn't working" the operator reported on
#: 2026-08-19. A speed-up that is inaudible on a disembodied voice-over is NOT harmless once a
#: face is speaking it, which is why this is a second, much tighter number rather than a re-tune
#: of the one above.
MUX_ATEMPO_MAX_LIP_SYNCED = 1.02

#: How much silent video tail ``mux`` will trim before it treats the mismatch as a render defect
#: rather than a rounding artefact.
#:
#: The ``truncate-video`` branch looked free — it only ever discarded silence. It is not. A video
#: model spreads mouth motion across the WHOLE duration it was asked to generate, so a 5.04s shot
#: carrying a 4.08s VO performs 5.04s of phonemes against 4.08s of audio: the mouth runs slow and
#: drifts further out of step every second, and cutting the tail does not fix the 4.08s already
#: shown. Verified 2026-08-18 — 5 of 6 shots of a shipped asset truncated this way, and the
#: operator's first note on the result was "lip sync wasn't working, it looks really off".
#:
#: A small trim is still legitimate (provider durations are integers, VO lengths are not), which is
#: why this is a tolerance and not zero.
MUX_TRUNCATE_MAX_S = 0.35
