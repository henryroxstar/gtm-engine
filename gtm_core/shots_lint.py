"""Shot-list linter — deterministic craft checks on a ``<slug>.shots.json`` before render spends.

Phase 18 (R5/R6): mechanizes the two prompt-craft rules every current video model's own guide
agrees on, which previously lived only as prose in the ``video-script`` skill body:

* **One camera move per shot.** Stacked moves ("dolly in while zooming and panning left") are the
  documented cause of warped geometry and unstable framing on Kling, Hailuo, Runway, and Sora
  alike. A named compound ("dolly zoom", "whip pan") is one move, not two.
* **Positive phrasing in prompt fields.** Negation ("no movement", "without a logo") is a
  documented failure mode — the banned noun stays "in play". Exclusions belong in
  ``style_scaffold.negative`` (plain nouns) or the shot's ``stability`` clause, stated
  positively. ``spoken`` (dialogue for TTS) and ``negative`` (the designated exclusion field)
  are exempt.

Errors (exit 1) block hand-off; warnings are advisory (exit 0): an unrecognized camera term, a
``duration_s`` past every confirmed provider ceiling, and a beat-total drift from
``total_duration_s``. Structural requirements mirror ``schemas/shots.schema.json`` minimally —
the schema contract itself is enforced by tests; this CLI has no third-party deps by design.

Run by ``video-script`` at Step 1.5 hand-off (free, no spend)::

    uv run python -m gtm_core.shots_lint content/<profile>/scripts/<date>-<slug>.shots.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from gtm_core import render_engines

#: Named compounds are matched (and consumed) first so "dolly zoom" counts as ONE move family,
#: not dolly + zoom. Order matters within this list.
_COMPOUND_MOVES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("dolly zoom", r"dolly[\s-]?zoom(?:s|ing|ed)?"),
        ("crash zoom", r"crash[\s-]?zoom(?:s|ing|ed)?"),
        ("whip pan", r"whip[\s-]?pan(?:s|ning|ned)?"),
        ("push-in", r"push(?:es|ing|ed)?[\s-]?in"),
        ("pull-out", r"pull(?:s|ing|ed)?[\s-]?(?:out|back)"),
    )
)

_SIMPLE_MOVES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("zoom", r"\bzoom(?:s|ing|ed)?\b"),
        ("pan", r"\bpan(?:s|ning|ned)?\b"),
        ("tilt", r"\btilt(?:s|ing|ed)?\b"),
        ("dolly", r"\bdoll(?:y(?:ing)?|ies)\b"),
        ("truck", r"\btruck(?:s|ing)?\b"),
        ("orbit", r"\borbit(?:s|ing)?\b"),
        ("arc", r"\barc(?:s|ing)?\b"),
        ("crane", r"\bcrane(?:s)?\b"),
        ("jib", r"\bjib\b"),
        ("pedestal", r"\bpedestal(?:s)?\b"),
        ("tracking", r"\btrack(?:s|ing)?\b"),
        ("hyperlapse", r"\bhyperlapse\b"),
        ("timelapse", r"\btime[\s-]?lapse\b"),
        ("roll", r"\broll(?:s|ing)?\b"),
        ("fpv/drone", r"\bfpv\b|\bdrone\b"),
    )
)

#: Non-move camera vocabulary — framing, angle, rig, and stillness terms. Recognition only:
#: matching one of these (or a move) is what makes a camera string "recognized".
_FRAMING_TERMS = re.compile(
    r"\bstatic\b|\blocked\b|\bhandheld\b|\bsteadicam\b|\bgimbal\b|\bwide\b|\bclose[\s-]?up\b"
    r"|\bextreme close\b|\bmedium\b|\bfull shot\b|\bmacro\b|\boverhead\b|\baerial\b"
    r"|\blow angle\b|\bhigh angle\b|\beye level\b|\bpov\b|\bdutch\b|\bover[\s-]the[\s-]shoulder\b"
    r"|\bestablishing\b|\btwo[\s-]shot\b|\bscreen (?:capture|record(?:ing)?)\b|\bfisheye\b"
    r"|\bremains still\b|\bstill\b|\bbird'?s[\s-]eye\b|\bworm'?s[\s-]eye\b"
)

#: Negation phrasing that must not appear in a prompt field (positive-phrasing rule).
_NEGATION_RE = re.compile(
    r"\bno\b|\bnot\b|\bnever\b|\bwithout\b|\bavoid(?:s|ing)?\b|\bdon'?t\b|\bdo not\b"
    r"|\bdoesn'?t\b|\bwon'?t\b",
    re.IGNORECASE,
)

#: Per-shot free-text fields the negation rule scans. ``spoken`` (dialogue) and the designated
#: exclusion surfaces (``negative``, at scaffold level) are exempt by design.
_NEGATION_SCANNED_SHOT_FIELDS = (
    "camera",
    "visual",
    "motion_prompt",
    "lighting",
    "lens",
    "wardrobe",
    "expression",
    "stability",
    "sfx",
)

_VALID_ROLES = frozenset({"presenter", "broll", "screen"})

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
VO_WORDS_PER_SEC_FAST = 3.51

#: Slack absorbed before a short ``duration_s`` is an error rather than estimator noise.
_VO_PARITY_TOLERANCE_S = 0.25

#: A ``duration_s`` this far past the spoken line's estimated length leaves the video model
#: spreading mouth motion across silence, then the mux truncating the tail.
_VO_DEAD_AIR_TOLERANCE_S = 1.5

#: Ceiling on the share of shots whose role is ``presenter``. Above this the asset is a talking
#: head with cutaways rather than a video, which is what "it feels boring, it's almost entirely
#: just face shots" describes.
MAX_PRESENTER_SHARE = 0.6

#: Below this many shots the presenter share is advisory — a 2-shot piece cannot be split finely
#: enough for a ratio to mean anything.
_PRESENTER_QUOTA_MIN_SHOTS = 4

#: The lowest provider ``duration`` any current audio-capable video model accepts (seedance_2_0
#: min 4; wan2_7 min 2). Both take an INTEGER — a fractional request is clamped, which silently
#: desyncs this file's stated timing from what renders.
_MIN_PRACTICAL_DURATION_S = 4


def _move_families(camera_text: str) -> list[str]:
    """Distinct camera-move families named in the text, compounds counted once."""
    text = camera_text.lower()
    found: list[str] = []
    for name, pattern in _COMPOUND_MOVES:
        if pattern.search(text):
            found.append(name)
            text = pattern.sub(" ", text)
    for name, pattern in _SIMPLE_MOVES:
        if pattern.search(text):
            found.append(name)
    return found


def _lint_camera(camera_text: str, prefix: str, errors: list[str], warnings: list[str]) -> None:
    moves = _move_families(camera_text)
    if len(moves) > 1:
        errors.append(
            f"{prefix} stacks {len(moves)} camera moves ({', '.join(moves)}) in one shot — "
            "one move per shot; split the beat instead (documented warped-geometry failure "
            "mode on every current video model)"
        )
    if not moves and not _FRAMING_TERMS.search(camera_text.lower()):
        warnings.append(
            f"{prefix} camera {camera_text!r} matches no known move or framing term — "
            "prefer the controlled vocabulary (video-render/references/prompt-recipes.md)"
        )


def _lint_negation(value: str, where: str, errors: list[str]) -> None:
    m = _NEGATION_RE.search(value)
    if m:
        errors.append(
            f"{where} uses negation phrasing ({m.group(0)!r}) — prompt fields are "
            "positive-only; move exclusions to style_scaffold.negative as plain nouns, or "
            "state the constraint positively in `stability`"
        )


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _lint_motion_prompt(shot: dict, prefix: str, errors: list[str], warnings: list[str]) -> None:
    """``motion_prompt`` is the field that says what HAPPENS. Absent it, an image-to-video model
    is handed a start frame, a camera note, and no action — and renders a person sitting still.

    Not hypothetical: on 2026-08-18 all six shots of a shipped asset omitted this field, V9 then
    found 3 of 6 motionless (47% of runtime), and the operator's verdict was "it feels boring,
    it's almost entirely just face shots". The field existed in the schema the whole time; nothing
    required it.
    """
    raw = shot.get("motion_prompt")
    motion = str(raw or "").strip()
    if not motion:
        errors.append(
            f"{prefix} missing required field `motion_prompt` — the model needs an ACTION, not "
            "just a start frame and a camera note, or it renders a person sitting still "
            "(documented 2026-08-18: 6/6 shots omitted this, V9 found 3/6 motionless)"
        )
        return

    camera = str(shot.get("camera", "") or "").strip()
    if camera and _normalize(motion) == _normalize(camera):
        errors.append(
            f"{prefix} motion_prompt merely restates `camera` ({motion!r}) — camera is how the "
            "LENS moves, motion_prompt is what the SUBJECT does; a restatement leaves the "
            "subject with nothing to do"
        )
        return

    if len(motion.split()) < 4:
        warnings.append(
            f"{prefix} motion_prompt {motion!r} is {len(motion.split())} words — too thin to "
            "direct a render; name the subject's action, and what changes over the shot"
        )


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


def _lint_shot_mix(shots: list, errors: list[str], warnings: list[str]) -> None:
    """Cap the presenter share, and ask for at least one shot that is not the presenter's face.

    A quota on ``presenter`` alone is not sufficient: 3 presenter shots plus 3 abstract b-roll
    inserts clears any ratio and is still six variations on "a man talking". So the presenter
    ceiling is an error, and the absence of a ``screen`` shot — a real capture of the thing being
    discussed — is a warning, because that is the shot that carries an enterprise story.
    """
    roles = [
        str((s.get("role") or "presenter") if isinstance(s, dict) else "presenter") for s in shots
    ]
    total = len(roles)
    presenters = sum(1 for r in roles if r == "presenter")
    share = presenters / total if total else 0.0

    if total >= _PRESENTER_QUOTA_MIN_SHOTS and share > MAX_PRESENTER_SHARE:
        errors.append(
            f"{presenters} of {total} shots are role=presenter ({share:.0%}) — ceiling is "
            f"{MAX_PRESENTER_SHARE:.0%}. An asset that is almost entirely face shots reads as "
            "boring however good the script is; convert beats to role=screen (a capture of the "
            "actual thing) or role=broll (data, motion graphic, product surface)"
        )
    elif total < _PRESENTER_QUOTA_MIN_SHOTS and share == 1.0 and total > 1:
        warnings.append(
            f"all {total} shots are role=presenter — too few shots for the "
            f"{MAX_PRESENTER_SHARE:.0%} ceiling to bind, but the asset is still all face"
        )

    if total >= _PRESENTER_QUOTA_MIN_SHOTS and not any(r == "screen" for r in roles):
        warnings.append(
            "no shot has role=screen — nothing in this asset shows the thing being talked about. "
            "For a product or incident story, a real screen capture is the highest-value shot in "
            "the piece and the cheapest to produce"
        )


#: Phrases that ask a VIDEO model to render legible text inside the frame. Diffusion video models
#: paint pixel patterns that *resemble* text — they do not typeset — so this reliably produces the
#: garbled signage the operator reported on 2026-08-19. Universal practice across every vendor
#: researched: generate clean video, burn text in post (which `gtm_core.captions` already does).
#:
#: ⚠ SCOPE: video shot lists ONLY, and only `visual` / `motion_prompt`. `carousel-visuals` Mode V5
#: renders full-text cards in-image ON PURPOSE via nano_banana_pro, with its own
#: verify-the-returned-text loop. That is a STILL — checkable before use — so none of the video
#: failure mode applies, and this rule must never be reachable from the carousel path.
_IN_FRAME_TEXT_RE = re.compile(
    r"\b("
    r"(?:sign|banner|placard|poster|screen|caption|subtitle|label|headline|title|logo|wordmark)"
    r"\s+(?:that\s+)?(?:read(?:s|ing)?|say(?:s|ing)?|display(?:s|ing)?|show(?:s|ing)?|with)"
    r"|text\s+(?:read(?:s|ing)?|say(?:s|ing)?|overlay|on\s+screen|appears)"
    r"|(?:the\s+)?words?\s+[\"\u201c]"
    r")",
    re.IGNORECASE,
)

#: Fields a text-in-frame instruction can actually reach the model through. Deliberately narrow,
#: for the reason `_lint_speech_cue` is narrow: scanning `expression` once passed on an incidental
#: "telling a friend". A rule that fires on prose nobody sends is a rule people learn to ignore.
_IN_FRAME_TEXT_SCANNED_FIELDS = ("visual", "motion_prompt")


def _lint_no_in_frame_text(shot: dict, prefix: str, errors: list[str]) -> None:
    """Refuse a prompt that asks the video model to render text inside the frame."""
    for field_name in _IN_FRAME_TEXT_SCANNED_FIELDS:
        value = str(shot.get(field_name, "") or "")
        match = _IN_FRAME_TEXT_RE.search(value)
        if not match:
            continue
        errors.append(
            f"{prefix}.{field_name} asks the video model to render in-frame text "
            f"({match.group(0)!r}). Diffusion video models paint shapes that resemble text rather "
            "than typesetting it, which is where the garbled frames came from — this is a model "
            "limitation, not a prompt-quality problem. Describe the scene without the text and let "
            "gtm_core.captions burn it in at finish time."
        )


def _lint_presenter_engine(
    shot: dict, prefix: str, errors: list[str], *, disclosed: bool = False
) -> None:
    """A shot of a real person SPEAKING must resolve an engine that can actually do that.

    The check that makes the August 2026 failure unrepresentable, run before any spend. A talking
    head on a general image-to-video model drifts the face (identity cannot cross the image→video
    boundary) and cannot lip-sync (mouth motion comes from prose, not from the audio).

    ``disclosed`` comes from the shot list's top-level ``synthetic_disclosure`` and reaches the
    registry unchanged. It defaults to ``False``, so a shot list that never mentions disclosure is
    linted against the closed answer — the engine that can serve this role today is available for
    DISCLOSED renders only (gtm_core/render_engines.toml, [engines.heygen_avatar]).
    """
    if str(shot.get("role", "") or "").strip() != "presenter":
        return
    speaks = bool(str(shot.get("spoken", "") or "").strip())
    if not speaks:
        return
    try:
        render_engines.engine_for_shot_role("presenter", speaks=True, disclosed=disclosed)
    except render_engines.EngineError as exc:
        errors.append(f"{prefix} is a speaking presenter shot and no engine can serve it. {exc}")


def _lint_voice_grade(shot: dict, prefix: str, errors: list[str], *, voice_grade: str) -> None:
    """A shot with spoken lines may not ship on an INSTANT voice clone.

    An instant clone is produced from a short sample and the provider does not train a custom
    model for it — it makes an educated guess from prior training data. Flat, not-quite-right
    output is that grade's DOCUMENTED behaviour, not a tuning failure, and it is what the operator
    heard on 2026-08-19 ("voice doesn't really sound like me"). A professional clone fine-tunes a
    dedicated model on 30 min-3 h of clean audio.

    Fail-closed on an unrecorded grade: a voice whose provenance nobody wrote down is not evidence
    of a good one.
    """
    if not str(shot.get("spoken", "") or "").strip():
        return
    if voice_grade == "professional":
        return
    detail = (
        f"voice_grade={voice_grade!r}"
        if voice_grade
        else "no voice_grade recorded in the brand kit"
    )
    errors.append(
        f"{prefix} has a spoken line but {detail} — a shipped VO requires a PROFESSIONAL voice "
        "clone (30 min-3 h of clean audio). An instant clone does not train a custom model at "
        "all; it guesses from prior training data, which is why it does not sound like the "
        "speaker. Record the grade with: python -m gtm_core.brandkit --profile <p> "
        "--set identity.voice_grade --value professional --note '<when/how cloned>'"
    )


#: What a shot may declare as its audio bed when nobody speaks in it. Free-text, because the
#: point is that a human named something — room tone, a music cue, a diegetic SFX — rather than
#: that they picked from a menu. ``"silent"`` is accepted and is the ONE value that must be
#: argued for in the same string, because deliberate silence is a real editorial device and
#: accidental silence is the defect this rule exists to stop.
_AUDIO_BED_FIELD = "audio_bed"
#: A bare "silent" with no reason is the accidental case wearing the deliberate case's clothes.
_MIN_SILENCE_REASON_CHARS = 12

#: Orientation a look's native pixels imply, vs the orientation each delivery ratio needs.
_RATIO_ORIENTATION = {"9:16": "portrait", "4:5": "portrait", "1:1": "square", "16:9": "landscape"}


def _orientation_of(width: int, height: int) -> str:
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def _lint_audio_bed(shot: dict, prefix: str, errors: list[str]) -> None:
    """Every shot carries sound, or says out loud why it does not.

    2026-08-28: ten of eighteen shots in `three-questions-p1` were concatenated with no audio
    stream at all, so 39% of the finished film — including the entire social cut — played in
    digital silence. `video_lint`'s V10 catches that on the finished asset; this catches it while
    it is still free to fix, and it is the only layer that can name WHICH shot is silent."""
    spoken = str(shot.get("spoken", "") or "").strip()
    if spoken:
        return
    bed = str(shot.get(_AUDIO_BED_FIELD, "") or "").strip()
    if not bed:
        errors.append(
            f"{prefix} has no `spoken` line and no `{_AUDIO_BED_FIELD}` — a shot with neither is "
            "silent, and a run of them is what makes a finished film read as broken. Name the "
            "bed (room tone, a music cue, an SFX), or declare "
            f'`{_AUDIO_BED_FIELD}: "silent — <why>"` if the silence is the point.'
        )
        return
    if bed.lower().startswith("silent") and len(bed) - len("silent") < _MIN_SILENCE_REASON_CHARS:
        errors.append(
            f"{prefix} declares `{_AUDIO_BED_FIELD}: {bed!r}` with no reason. Deliberate silence "
            "is a real device and accidental silence is a defect; only the reason tells them "
            "apart. Say what the beat is doing."
        )


def _lint_look_ratio(doc: dict, errors: list[str], warnings: list[str]) -> None:
    """A look whose native pixels contradict the delivery ratio cannot fill the frame.

    2026-08-28: a 608x1080 portrait avatar look was rendered for a 16:9 master, leaving 608px of
    1920 as real picture and 68% of the frame as a blurred copy of itself. Six native 1920x1080
    landscape looks existed in the same avatar group at identical cost. The shot list already
    records the look's source pixels, so this is arithmetic, not a judgement call."""
    ratios = doc.get("deliverable_ratios") or doc.get("ratios")
    bindings = doc.get("identity_bindings")
    if not isinstance(bindings, dict) or not isinstance(ratios, list) or not ratios:
        return
    for name, binding in bindings.items():
        if not isinstance(binding, dict):
            continue
        px = str(binding.get("source_px", "") or "")
        m = re.search(r"(\d+)\s*[x×]\s*(\d+)", px)
        if not m:
            continue
        have = _orientation_of(int(m.group(1)), int(m.group(2)))
        for ratio in ratios:
            need = _RATIO_ORIENTATION.get(str(ratio))
            if need and need != have and "square" not in (need, have):
                errors.append(
                    f"identity_bindings.{name} source is {m.group(1)}x{m.group(2)} ({have}) but "
                    f"{ratio} needs {need}. Rendering it anyway fills the frame with a scaled "
                    f"copy of itself — a portrait look in a 16:9 frame leaves ~32% real picture. "
                    "Pick a look whose native orientation matches (list_avatar_looks reports "
                    "`preferred_orientation` and `image_width`/`image_height` per look), or "
                    "compose that ratio as a deliberate layout and say so here."
                )


def lint_shotlist(doc: object, *, voice_grade: str | None = None) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for a parsed shot-list document.

    ``voice_grade`` is the brand kit's ``identity.voice_grade``. ``None`` means "not supplied by
    this caller" and skips the check entirely — a shot list linted with no kit in hand should not
    fail on a fact the caller never had. Passing ``""`` is different: it means the kit WAS read and
    records no grade, which fails closed.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(doc, dict):
        return (["shot list is not a JSON object"], warnings)

    for key in ("source_item", "total_duration_s", "style_scaffold", "shots"):
        if key not in doc:
            errors.append(f"missing required top-level field `{key}`")

    scaffold = doc.get("style_scaffold")
    if isinstance(scaffold, dict):
        for key in ("look", "provider_model"):
            if not str(scaffold.get(key, "")).strip():
                errors.append(f"style_scaffold.{key} is missing or empty")
        look = scaffold.get("look", "")
        if isinstance(look, str) and look:
            _lint_negation(look, "style_scaffold.look", errors)
    elif scaffold is not None:
        errors.append("style_scaffold is not an object")

    # A shot list may DECLARE that the finished render will carry the tenant's synthetic-media
    # disclosure line. It is only a declaration — the binding check that the text is actually
    # present, and matches [disclosure].line, is owned downstream (gtm_core.video_finish at plan
    # time, agent/publish.py at the gate). What it buys here is that the presenter engine resolves
    # before any spend instead of failing after a script is written.
    disclosed = False
    if "synthetic_disclosure" in doc:
        declared = doc.get("synthetic_disclosure")
        if not isinstance(declared, str) or not declared.strip():
            errors.append(
                "synthetic_disclosure is present but empty — declare the disclosure line the "
                "finished render will carry, or omit the key entirely. A present-but-blank "
                "declaration reads as an attempt to open the presenter engine without accepting "
                "the Art. 50 duty that opens it."
            )
        else:
            disclosed = True

    shots = doc.get("shots")
    if not isinstance(shots, list) or not shots:
        errors.append("shots must be a non-empty array")
        return (errors, warnings)

    duration_total = 0.0
    for i, shot in enumerate(shots, 1):
        prefix = f"shot[{i}]"
        if not isinstance(shot, dict):
            errors.append(f"{prefix} is not an object")
            continue

        camera = str(shot.get("camera", "") or "")
        visual = str(shot.get("visual", "") or "")
        if not camera.strip():
            errors.append(f"{prefix} missing required field `camera`")
        if not visual.strip():
            errors.append(f"{prefix} missing required field `visual`")

        duration = shot.get("duration_s")
        if not isinstance(duration, (int, float)) or duration <= 0:
            errors.append(f"{prefix} duration_s must be a number > 0")
        else:
            duration_total += float(duration)
            if duration > _MAX_CONFIRMED_CEILING_S:
                warnings.append(
                    f"{prefix} duration_s={duration} exceeds every confirmed provider "
                    f"ceiling ({_MAX_CONFIRMED_CEILING_S}s — provider-duration-ceilings.md); "
                    "the provider will clamp it and desync this file's stated timing"
                )

        role = shot.get("role", "presenter")
        if role not in _VALID_ROLES:
            errors.append(f"{prefix} role {role!r} is not one of {sorted(_VALID_ROLES)}")

        if camera.strip():
            _lint_camera(camera, prefix, errors, warnings)

        _lint_motion_prompt(shot, prefix, errors, warnings)
        _lint_vo_duration_parity(shot, prefix, errors, warnings)
        _lint_speech_cue(shot, prefix, errors)
        _lint_no_in_frame_text(shot, prefix, errors)
        _lint_presenter_engine(shot, prefix, errors, disclosed=disclosed)
        _lint_audio_bed(shot, prefix, errors)
        if voice_grade is not None:
            _lint_voice_grade(shot, prefix, errors, voice_grade=voice_grade)

        for field_name in _NEGATION_SCANNED_SHOT_FIELDS:
            value = shot.get(field_name)
            if isinstance(value, str) and value:
                _lint_negation(value, f"{prefix}.{field_name}", errors)

    _lint_shot_mix(shots, errors, warnings)
    _lint_look_ratio(doc, errors, warnings)

    total = doc.get("total_duration_s")
    if isinstance(total, (int, float)) and duration_total > 0:
        drift = abs(float(total) - duration_total)
        if drift > _DURATION_DRIFT_TOLERANCE_S:
            warnings.append(
                f"total_duration_s={total} but the shots sum to {duration_total:g}s "
                f"(drift {drift:g}s) — the stated timing and what renders will not match"
            )

    return (errors, warnings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.shots_lint",
        description="Deterministic craft lint for a <slug>.shots.json (free, pre-render).",
    )
    parser.add_argument("path", type=Path, help="Path to the shot list JSON")
    parser.add_argument(
        "--voice-grade",
        default=None,
        help=(
            "The brand kit's recorded clone grade for the engine that will actually render the "
            "spoken shots — identity.heygen_voice_grade for a HeyGen presenter, "
            "identity.voice_grade for a Higgsfield one. Two providers means two grades, and "
            "checking the wrong one is checking nothing. Omit to skip the check; pass an empty "
            "string to mean 'the kit was read and records no grade', which fails closed."
        ),
    )
    args = parser.parse_args(argv)

    try:
        doc = json.loads(args.path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(json.dumps({"path": str(args.path), "error": f"unreadable: {exc}"}))
        return 2
    except json.JSONDecodeError as exc:
        print(json.dumps({"path": str(args.path), "error": f"invalid JSON: {exc}"}))
        return 2

    errors, warnings = lint_shotlist(doc, voice_grade=args.voice_grade)
    print(
        json.dumps(
            {
                "path": str(args.path),
                "ok": not errors,
                "errors": errors,
                "warnings": warnings,
            },
            indent=2,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
