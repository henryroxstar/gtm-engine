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
