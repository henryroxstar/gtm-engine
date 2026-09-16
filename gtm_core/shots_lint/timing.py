from __future__ import annotations

from .audio import _AUDIO_BED_FIELD
from .camera import _normalize

_VALID_ROLES = frozenset({"presenter", "broll", "screen"})

#: How this shot list's footage comes into existence. ``"rendered"`` (the default, and what an
#: ABSENT key means) is the synthetic path every rule here was written for. ``"live_action"`` is
#: the `live-action-video` lane: the script is written first and shot on a real camera afterwards,
#: so three rules that assume a rendered list would otherwise refuse every valid script —
#:
#:   * the presenter ENGINE check (the engine is a camera and a human; `resolve_engine` correctly
#:     returns nothing, and demanding a synthetic engine refuses the lane outright),
#:   * the presenter SHARE ceiling (it prices a generated talking head as the expensive,
#:     low-quality shot; a real person talking to camera is the format, not a defect), and
#:   * `synthetic_disclosure`, which flips from permitted to REFUSED — declaring it over footage
#:     of a real person is a false statement about provenance, and the Art. 50 duty does not
#:     attach to anything that was not synthesised.
#:
#: An UNKNOWN value is an error rather than a silent fallback in either direction: a typo must
#: neither open the live-action exemptions nor quietly re-impose the rendered ones.
_CAPTURE_MODES = frozenset({"rendered", "live_action"})
_DEFAULT_CAPTURE_MODE = "rendered"

#: The highest per-call duration confirmed for any model in
#: plugin/skills/video-render/references/provider-duration-ceilings.md.
_MAX_CONFIRMED_CEILING_S = 15

#: Beat-total drift from total_duration_s past this is worth a warning.
_DURATION_DRIFT_TOLERANCE_S = 1.0

#: Speech rate for the register these scripts are written in, words per second. MEASURED, not
#: assumed: 2026-08-19, across the six rendered VO tracks of
#: one tenant's six-shot presenter script (90 spoken words, 32.48s of audio).
#: The per-shot spread was 2.26-3.43 wps, so this is a central estimate rather than a bound —
#: hence the explicit tolerances below instead of a false claim of precision.
VO_WORDS_PER_SEC = 2.77

#: Speech rate is a BAND, not a point — re-measured 2026-08-19 after the single constant above
#: proved 27% off for the engine actually in use. Same voice element, same six-line register, two
#: TTS renders: the 2026-08-18 set ran 90 words / 32.56s = 2.76 wps, and the 2026-08-19
#: ``text2speech_v2``/ElevenLabs set ran 82 words / 23.36s = 3.51 wps.
#:
#: A single mid-band constant fails asymmetrically, which is why this is two numbers. The overrun
#: ERROR must not cry wolf, so it estimates with the SLOWEST rate (the longest plausible VO). The
#: dead-air WARN must not go blind, so it estimates with the FASTEST rate (the shortest plausible
#: VO) — and that is the check that catches mouth-motion spread, the lip-sync root cause. Tuned to
#: the slow rate alone it under-fires: a 5/4/5/5/7/5 shot list whose real VO needed 4/3/4/4/4/3
#: passed this linter with zero findings on 2026-08-19, a 9-second overstatement across six shots.
VO_WORDS_PER_SEC_SLOW = 2.76
#: Clamped to 2.85 wps per V-4: Higgsfield and HeyGen provider authoring ceiling is 2.9 wps
#: before syllable truncation and RUSHED rejection fire.
VO_WORDS_PER_SEC_FAST = 2.85

#: The provider's OWN authoring ceiling, above which Higgsfield's `narrator` workflow rejects a
#: take as ``rate=RUSHED`` (probed 2026-08-29). Recorded, deliberately NOT enforced.
#:
#: It sits BELOW our fast end, and the two do not contradict: 3.51 is what the engine was measured
#: DOING, 2.9 is what the provider will ACCEPT. The consequence is real but narrow — a line at the
#: top of our band passes the parity check below and would be refused by the provider's own gate.
#: Checked against two real shot lists before leaving it alone (12 spoken shots, max 2.86 wps, none
#: over), so tightening the band would be a change with no observed defect behind it, and re-pinning
#: a MEASURED constant onto a DOCS figure is the restated-numbers-go-stale error running backwards.
#: Revisit if a parity warning ever fires on a line above this. PENDING.md V-4.
PROVIDER_RUSHED_WORDS_PER_SEC = 2.9

#: Slack absorbed before a short ``duration_s`` is an error rather than estimator noise.
_VO_PARITY_TOLERANCE_S = 0.25

#: A ``duration_s`` this far past the spoken line's estimated length leaves the video model
#: spreading mouth motion across silence, then the mux truncating the tail.
_VO_DEAD_AIR_TOLERANCE_S = 1.5

#: The lowest provider ``duration`` any current audio-capable video model accepts (seedance_2_0
#: min 4; wan2_7 min 2). Both take an INTEGER — a fractional request is clamped, which silently
#: desyncs this file's stated timing from what renders.
_MIN_PRACTICAL_DURATION_S = 4


def _estimated_vo_seconds(spoken: str, words_per_sec: float = VO_WORDS_PER_SEC) -> float:
    """Estimated spoken length of a line, from its word count at a calibrated register rate.

    Callers pass :data:`VO_WORDS_PER_SEC_SLOW` or :data:`VO_WORDS_PER_SEC_FAST` to get the long or
    short end of the measured band; the default keeps the single-rate behaviour for callers that
    only want a nominal figure.
    """
    return len(spoken.split()) / words_per_sec


#: Words that tell an image-to-video model the subject is TALKING. Verified 2026-08-19: a
#: presenter shot whose prompt carried only a gesture ("he shakes his head once, then opens a palm
#: toward the camera") plus a stability clause rendered with the mouth CLOSED in 9 of 10 sampled
#: frames, over a full sentence of voice-over. Adding "he is speaking directly to camera,
#: articulating each word" to the same start frame and the same VO produced continuous mouth
#: articulation — with and without an ``audio_references`` input, which changed nothing on its own.
#: The lip sync was never driven by the audio parameter; it was never asked for in words.
_SPEECH_CUES = frozenset(
    {
        "speak",
        "speaks",
        "speaking",
        "talk",
        "talks",
        "talking",
        "says",
        "saying",
        "tells",
        "telling",
        "addresses",
        "addressing",
        "delivers",
        "delivering",
        "mouth",
        "lips",
        "articulate",
        "articulates",
        "articulating",
        "recounts",
        "explains",
    }
)


def _lint_speech_cue(shot: dict, prefix: str, errors: list[str]) -> None:
    """A shot whose subject speaks must SAY SO in its motion direction.

    ``audio_references`` is not a lip-sync switch — it is one input among several, and on its own
    it did not move the mouth at all (see :data:`_SPEECH_CUES`). The model animates what the prompt
    describes; a prompt that lists a hand gesture and then pins everything else with a stability
    clause gets a silent, smiling presenter. Only shots that both carry a spoken line and show a
    person are checked — a screen capture or b-roll frame has no mouth.
    """
    spoken = str(shot.get("spoken", "") or "").strip()
    role = str(shot.get("role", "") or "").strip().lower()
    if not spoken or role not in {"presenter", "talking_head", "piece_to_camera"}:
        return
    # motion_prompt ONLY. The frame-anchored prompt shape (prompt-recipes.md shape A) assembles
    # camera + motion_prompt + stability + negative and deliberately drops `expression` and
    # `visual` — the approved start frame already carries both. Scanning them would pass a shot on
    # an incidental word: "telling a friend", "delivering bad news" both matched here on
    # 2026-08-19 while the text the model actually received said nothing about a mouth.
    blob = str(shot.get("motion_prompt", "") or "").lower()
    if any(cue in _normalize(blob).split() for cue in _SPEECH_CUES):
        return
    errors.append(
        f"{prefix} has a spoken line and role={role!r} but nothing in motion_prompt/expression "
        "says the subject is speaking — image-to-video models animate what the prompt describes, "
        "and a gesture-only direction renders a closed mouth through the whole line (verified "
        "2026-08-19, mouth shut in 9 of 10 frames). Add the speech to motion_prompt, e.g. "
        "'he speaks directly to camera, articulating each word, and ...'."
    )


def _lint_vo_measured_parity(
    measured_s: float, duration: float, prefix: str, errors: list[str]
) -> None:
    """Parity against a *measured* VO, once one exists — the word-rate band is only a stand-in.

    The band spans two TTS engines (2.76-3.51 wps) because that is the honest spread of what this
    provider produces, and a spread that wide cannot adjudicate a single integer duration: a 17-word
    line estimates anywhere from 4.84s to 6.16s, so 4s reads as an overrun against the slow end and
    5s reads as dead air against the fast end. Both cannot be right, and neither is evidence.

    So once ``video-render`` has generated the VO it records ``vo_seconds`` on the shot, and this
    check replaces the estimate with the same two ceilings :func:`gtm_core.video_finish.mux` will
    actually enforce — which turns a pre-spend heuristic into the real gate, one render earlier.
    """
    from gtm_core.video_finish import MUX_ATEMPO_MAX, MUX_TRUNCATE_MAX_S, _suggest_duration

    tail = duration - measured_s
    if 0 <= tail <= MUX_TRUNCATE_MAX_S:
        return
    if tail < 0 and measured_s / duration <= MUX_ATEMPO_MAX:
        return

    fits = _suggest_duration(measured_s)
    if tail > MUX_TRUNCATE_MAX_S:
        problem = (
            f"leaves a {tail:.2f}s silent tail, past the {MUX_TRUNCATE_MAX_S}s mux ceiling — the "
            "model spreads mouth motion across silence the audio never fills"
        )
    else:
        problem = (
            f"needs atempo={measured_s / duration:.3f} to fit, past the {MUX_ATEMPO_MAX} ceiling — "
            "compressing the cloned voice that hard is audible"
        )
    remedy = (
        f"render at duration={fits}s"
        if fits is not None
        else "no integer duration fits this VO; re-roll it (TTS length varies run to run)"
    )
    errors.append(
        f"{prefix} duration_s={duration:g} against a measured {measured_s:.2f}s VO {problem}; "
        f"{remedy}"
    )


def _lint_vo_duration_parity(
    shot: dict, prefix: str, errors: list[str], warnings: list[str]
) -> None:
    """A shot's ``duration_s`` must fit its own spoken line — this is the lip-sync root cause.

    A video model spreads mouth motion across the WHOLE duration it generates. Render 5s of video
    against a 4.08s VO and the mouth performs 5s worth of phonemes while 4.08s of audio plays;
    the mux then truncates the tail, and the visible result is progressive drift. Verified
    2026-08-18: every shot of a shipped asset declared ``duration_s: 5`` while its VO ran
    3.20-7.92s. Four of six overran. The operator's first note was "lip sync wasn't working".

    Free to check here, before any spend — which is the whole point.
    """
    spoken = str(shot.get("spoken", "") or "").strip()
    duration = shot.get("duration_s")
    if not spoken or not isinstance(duration, (int, float)) or duration <= 0:
        return

    measured = shot.get("vo_seconds")
    if isinstance(measured, (int, float)) and measured > 0:
        _lint_vo_measured_parity(float(measured), float(duration), prefix, errors)
        return

    est_long = _estimated_vo_seconds(spoken, VO_WORDS_PER_SEC_SLOW)
    est_short = _estimated_vo_seconds(spoken, VO_WORDS_PER_SEC_FAST)
    words = len(spoken.split())
    if duration + _VO_PARITY_TOLERANCE_S < est_long:
        errors.append(
            f"{prefix} duration_s={duration:g} is shorter than its own spoken line "
            f"({words} words ≈ {est_long:.2f}s at {VO_WORDS_PER_SEC_SLOW} words/sec, the slow end "
            "of the measured band) — the VO will overrun the shot, forcing the mux to truncate or "
            "time-stretch, which is exactly what reads as broken lip sync. Round up to "
            f"{int(est_long) + 1}s, or cut words."
        )
    elif duration > est_short + _VO_DEAD_AIR_TOLERANCE_S:
        warnings.append(
            f"{prefix} duration_s={duration:g} runs {duration - est_short:.2f}s past its spoken "
            f"line ({words} words ≈ {est_short:.2f}s at {VO_WORDS_PER_SEC_FAST} words/sec, the "
            "fast end of the measured band) — the model stretches mouth motion across the silence "
            "and the tail gets cut; trim the shot toward the line's real length"
        )

    if isinstance(duration, float) and not duration.is_integer():
        warnings.append(
            f"{prefix} duration_s={duration:g} is fractional — every current audio-capable video "
            "model takes an INTEGER duration and clamps anything else, desyncing this file's "
            "stated timing from what actually renders"
        )
    if duration < _MIN_PRACTICAL_DURATION_S:
        warnings.append(
            f"{prefix} duration_s={duration:g} is below {_MIN_PRACTICAL_DURATION_S}s, the minimum "
            "seedance_2_0 accepts — the provider will clamp it up and the shot will run longer "
            "than this file claims"
        )


#: A shot list may declare, at the top level, where its voice-over comes from when the brand kit
#: holds no ``identity.voice_id`` — an operator-recorded WAV, a licensed read, an already-rendered
#: HeyGen track. Free text, because the point is that a human named a source, not that they picked
#: from a menu. This is the documented escape hatch from :func:`_lint_spoken_has_a_voice`.
_VO_SOURCE_FIELD = "vo_source"


def _lint_spoken_has_a_voice(
    shot: dict, prefix: str, errors: list[str], *, voice_id: str, vo_source: str
) -> None:
    """A shot may not carry a spoken line that nothing will voice — the converse of the audio-bed
    rule, and the layer that makes silent-VO unrepresentable rather than merely detectable.

    ``_lint_audio_bed`` fires only when ``spoken`` is EMPTY: a shot *with* a line is assumed to
    carry audio. Nothing checked that assumption. ``video-render`` Step 4a generates the VO only
    when the merged kit has a non-empty ``identity.voice_id``, and there is no ``else`` — so on a
    profile with no voice the line is simply never spoken. Every shot renders, the lint passes,
    and the defect surfaces at ``video_lint`` V10 (dead air) on the finished asset, after the full
    render spend.

    This is the failure the faceless lane carries by default: a bare profile has no ``voice_id``
    by definition, and that is the lane the router recommends most confidently.
    """
    if not str(shot.get("spoken", "") or "").strip():
        return
    if voice_id.strip() or vo_source.strip():
        return
    errors.append(
        f"{prefix} has a `spoken` line but the brand kit records no `identity.voice_id` and this "
        f"shot list declares no `{_VO_SOURCE_FIELD}` — nothing will voice it, so the line renders "
        "as silence and only `video_lint` V10 catches it, after every shot has been paid for. "
        "Two ways out: clone a voice (`identity-kit`, which owns the Art. 50 consent gate) and "
        "record it as identity.voice_id; or make this a CAPTIONS-ONLY cut — move the line to the "
        f"shot's `caption`, clear `spoken`, and give it an `{_AUDIO_BED_FIELD}`. A captions-and-"
        "music cut is a legitimate deliverable, not a downgrade; silence nobody chose is not. If "
        f"the VO comes from somewhere else entirely, name it in top-level `{_VO_SOURCE_FIELD}`."
    )
