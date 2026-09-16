from __future__ import annotations

#: Minimum acceptable video bitrate at 1080p — below this the file is visibly compressed on a
#: phone screen. Not fixture-provable at 200ms (a short CBR clip's measured rate is dominated by
#: its I-frame); honoured only via the pure layer's boundary tests plus the real-asset calibration.
MIN_BITRATE_1080P_BPS = 6_000_000
MIN_FPS_ACCEPTABLE = 24.0  # below this: ERROR. 24-29.9: WARN (cinematic-only). >=30: clean.
MIN_FPS_CLEAN = 30.0
#: Aspect-ratio equality tolerance for V2. NOT a pixel tolerance on the frame dims directly —
#: V1 already gates minimum SIZE, so V2 must check the frame's SHAPE independent of its size
#: (the real shipped asset was 720x1280: exactly 9:16, at the wrong resolution — V2 must stay
#: clean on it while V1 correctly errors). ~0.003 approximates a ±1-2px wobble at 1080x1920;
#: the soul_2 4:5->3:4 coercion this tier exists to catch differs by ~0.05, an order of
#: magnitude over this floor.
ASPECT_TOLERANCE = 0.003
PREDICTOR_MAX_DURATION_S = 14.9

#: Reading load. A caption screen the viewer cannot finish before it cuts is worse than no
#: caption: it converts the asset into a race. The per-screen ceiling is generous (a 4-word
#: screen may flash for 1s); the whole-asset ceiling is stricter because burned text competes
#: with a voice-over for the same attention. Both are words/second.
MAX_WORDS_PER_SEC_SCREEN = 4.0
MAX_WORDS_PER_SEC_ASSET = 3.0


#: Dead air. Not "is there an audio stream" — the defect asset HAD an AAC stream carrying nothing,
#: so ``Probe.has_audio`` was True and told nobody anything. These are measured against the signal.
#:
#: Two independent triggers, because one number cannot cover both shapes of the defect: a long
#: film with one dead stretch, and a short film that is mostly dead. A deliberate 3s beat before a
#: payoff clears both; five seconds of nothing does not, at any runtime.
SILENCE_FLOOR_DBFS = -50.0
SILENCE_MIN_RUN_S = 2.0  # detection floor: shorter gaps are inter-sentence pauses, not dead air
MAX_DEAD_AIR_FRACTION = 0.15
MAX_DEAD_AIR_RUN_S = 5.0

#: Floor-only soundtrack. V10's dead-air rules measure LEVEL, and a soundtrack that is nothing
#: but level clears them: on 2026-09-07 a 30s film shipped whose only audio was a synthesized
#: room-tone floor, laid so that dead-air would pass, with none of the designed cues or bed —
#: 0% silence, -13.9 LUFS integrated, a fully green V10. "Audio present" and "a flat noise floor
#: with no events" are the same thing to a silence detector, so this rule reads the 100ms
#: momentary-loudness series from the same ebur128 pass and asks whether the loudness ever
#: MOVES ABRUPTLY. A soundtrack has events (a word, a tick, a chime) and events are steps; a
#: floor is smooth however loud it is normalised, and a fade is a ramp, which is why the
#: measure is the SECOND difference (see ``measure.momentary_dynamics``).
#:
#: Calibrated 2026-09-11 on three real finished assets plus a module-synthesized room tone. The
#: summary numbers first proposed for this rule — loudness range (LRA) and crest factor — did
#: NOT separate them: the floor-only film measured LRA 4.7 LU / crest 13.7 dB against two
#: voice-led films at LRA 2.7 & 4.8 LU / crest 14.5 & 14.6 dB, because every finished asset is
#: loudnorm'd and loudnorm flattens both. A threshold on those would have been a check that
#: cannot discriminate. The momentary dynamics did, with margin on both sides:
#:
#:   abruptness (mean |Δ²M|, LU, saturated at 8)   event fraction (|Δ²M| > 2 LU)
#:   floor-only film      0.57                         0.031             MUST FIRE
#:   synthesized tone     0.30                         0.000             MUST FIRE
#:   voice-led film A     2.33                         0.361             must not
#:   voice-led film B     2.28                         0.361             must not
#:
#: Both must sit under their ceiling to fire. The abruptness ceiling sits at the geometric
#: midpoint of the two populations (~2.1x above the loudest floor, ~1.9x below the quietest
#: soundtrack); the event-fraction ceiling ~3.9x / ~3x. A deliberate drone score with no onset
#: at all can land here — that is what a suppression with a reason is for; the rule cannot tell
#: a designed drone from a substitute floor, and it should not pretend to.
AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU = 1.2
AUDIO_FLOOR_ONLY_MAX_EVENT_FRACTION = 0.12
#: A second difference over this many LU is an event (a step), not drift.
AUDIO_FLOOR_ONLY_EVENT_STEP_LU = 2.0
#: Each frame's second difference saturates here before it enters the mean. A hard gap into
#: digital silence is a ~55 LU step; unsaturated, four such corners in a 30s floor add ~0.7 LU
#: to the mean and two gaps would carry a substitute floor past the ceiling. An event counts
#: as an event, not as its magnitude.
AUDIO_FLOOR_ONLY_EVENT_SATURATION_LU = 8.0
#: Under this runtime a single held bed can legitimately be the whole soundtrack.
AUDIO_FLOOR_ONLY_MIN_DURATION_S = 10.0

#: Burned type must clear WCAG AA (4.5:1) against whatever is actually behind it. Not a style
#: preference: a caption exists to be read on a phone, in daylight, with the sound off.
MIN_CAPTION_CONTRAST_RATIO = 4.5

#: Mean absolute inter-frame difference, normalised 0-1, below which a shot reads as motionless.
#: A slow push-in only registers as motion above roughly this floor — a 1.08x zoom spread across
#: 3s sits an order of magnitude under it and is imperceptible on a phone.
MIN_SHOT_MOTION = 0.006


# ── Cadence and rhythm (V6, V12, V13) ─────────────────────────────────────────────────────────
#
# Hoisted out of `evaluate.py` by C6, where they were inline literals. The move is the point: a
# threshold that only exists at its use site cannot be monkeypatched by a test, so a test that
# tries reads as passing while measuring nothing. `cadence.py` reads these BY ATTRIBUTE
# (`thresholds.X`) rather than by from-import, so patching the module actually flips the verdict.

#: Past this runtime, a film with NO visual change at all has stopped being a film.
V6_NO_CHANGE_MIN_DURATION_S = 15.0
#: Below this average interval, cuts are churn rather than rhythm.
V6_MIN_AVG_CUT_INTERVAL_S = 1.5
#: Past this runtime, fewer than one change per this many seconds reads as static.
V6_SPARSE_WINDOW_S = 30.0

#: The single longest stretch a viewer will hold with nothing changing on screen. Distinct from
#: V6's AVERAGE: a film can average a healthy interval and still park for eleven seconds in the
#: middle, and the average is exactly what hides that. Head and tail count — a static opening is
#: the most expensive place to lose someone.
MAX_SECONDS_WITHOUT_CHANGE = 5.0

#: Transitions per minute, above which the edit is calling attention to itself rather than to
#: what it is cutting between. Advisory: recorded as the weakest of the three cadence numbers
#: because it rests on the smallest sample.
MAX_TRANSITIONS_PER_MINUTE = 6.0

#: The smallest mean inter-frame delta that is a real change rather than rounding. 8-bit luma
#: quantises at 1/255, so two levels is the floor below which "it changed" cannot be told from
#: "it rounded". Measured, not guessed: a red-to-blue half-second dissolve at 30fps produces
#: about 0.0118 per frame (three luma levels), and an earlier 0.02 floor sat ABOVE that and
#: reported every dissolve as no transition at all.
TRANSITION_MIN_FRAME_DELTA = 2.0 / 255.0

#: How many consecutive sub-threshold frames may sit INSIDE one blend without ending it. A
#: dissolve quantised to 8 bits has frames where two consecutive outputs round identically — the
#: same red-to-blue fade above shows a zero-delta frame roughly every fourth frame — so a
#: zero-tolerance run detector reports one dissolve as three or four transitions.
TRANSITION_MAX_GAP_FRAMES = 1

#: A CUT is a single-frame jump; a TRANSITION is a multi-frame blend. This is the boundary
#: between them, and it is why the two are counted separately: a dissolve-heavy edit and a
#: cut-heavy one have the same scene-change count and completely different rhythms.
TRANSITION_MIN_BLEND_FRAMES = 3

#: The ffmpeg `select='gt(scene,X)'` threshold behind every scene-change count. Was inline in an
#: filter STRING in measure.py, which is the least patchable place a number can live.
SCENE_CHANGE_THRESHOLD = 0.3

#: V5 — how far under the voice a music bed has to sit before it stops competing with it.
V5_BED_SEPARATION_DB = 6.0
