from __future__ import annotations

from .audio import _AUDIO_BED_FIELD, _lint_audio_bed
from .camera import _NEGATION_SCANNED_SHOT_FIELDS, _lint_camera, _lint_motion_prompt, _lint_negation
from .identity import (
    _lint_identity_bindings,
    _lint_look_ratio,
    _lint_presenter_engine,
    _lint_synthetic_disclosure,
    _lint_voice_grade,
)
from .mix import (
    _PRESENTER_QUOTA_MIN_SHOTS,
    MAX_PRESENTER_SHARE,
    _lint_no_in_frame_text,
    _lint_shot_mix,
)
from .narration import (
    _NARRATION_DUTY_WARN_PCT,
    _NARRATION_FIELD,
    _lint_narration_duty_cycle,
    _lint_narration_lane_exclusive,
    _lint_narration_timeline,
)
from .timing import (
    _CAPTURE_MODES,
    _DEFAULT_CAPTURE_MODE,
    _DURATION_DRIFT_TOLERANCE_S,
    _MAX_CONFIRMED_CEILING_S,
    _VALID_ROLES,
    _VO_SOURCE_FIELD,
    VO_WORDS_PER_SEC,
    _lint_speech_cue,
    _lint_spoken_has_a_voice,
    _lint_vo_duration_parity,
)


def lint_shotlist(
    doc: object,
    *,
    voice_grade: str | None = None,
    voice_id: str | None = None,
    disclosure_line: str | None = None,
) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for a parsed shot-list document.

    ``voice_grade`` is the brand kit's ``identity.voice_grade``. ``None`` means "not supplied by
    this caller" and skips the check entirely — a shot list linted with no kit in hand should not
    fail on a fact the caller never had. Passing ``""`` is different: it means the kit WAS read and
    records no grade, which fails closed.

    ``voice_id`` is the kit's ``identity.voice_id`` and carries the identical contract: ``None``
    skips, ``""`` means the kit was read and holds no voice — which refuses any shot carrying a
    spoken line, unless the list declares its own ``vo_source``.

    ``disclosure_line`` is the kit's ``[disclosure].line`` and carries the same contract again:
    ``None`` skips the match, ``""`` means the kit was read and configures no line — which fails
    closed against any declared ``synthetic_disclosure``.
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
    # disclosure line. What it buys here is that the presenter engine resolves before any spend
    # instead of failing after a script is written — and, when the caller supplies the kit's line,
    # that the declared TEXT is the line rather than something that merely fills the field.
    capture_mode = str(doc.get("capture_mode", _DEFAULT_CAPTURE_MODE) or "").strip()
    if capture_mode not in _CAPTURE_MODES:
        errors.append(
            f"capture_mode {capture_mode!r} is not one of {sorted(_CAPTURE_MODES)}. An unknown "
            "value is refused rather than assumed in either direction — a typo must neither open "
            "the live-action exemptions nor silently re-impose the rendered rules."
        )
        capture_mode = _DEFAULT_CAPTURE_MODE
    live_action = capture_mode == "live_action"

    disclosed = _lint_synthetic_disclosure(
        doc, errors, live_action=live_action, disclosure_line=disclosure_line
    )

    # Where the voice-over comes from when the kit holds no voice_id. Read once, at list level:
    # it answers the question for the whole cut, not shot by shot.
    vo_source = str(doc.get(_VO_SOURCE_FIELD, "") or "")

    shots = doc.get("shots")

    # The narration lane: a read laid over an already-cut timeline, checked as a TRACK rather
    # than shot by shot. Run BEFORE the shots array is adjudicated, because only the exclusivity
    # rule reads the shots at all — a file with a broken `shots` still gets its read checked,
    # rather than returning early with the audio script unexamined, which is the state this lane
    # was invented to end.
    _lint_narration_lane_exclusive(doc, shots if isinstance(shots, list) else [], errors)
    _lint_narration_timeline(doc, errors)
    _lint_narration_duty_cycle(doc, warnings)

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
        _lint_presenter_engine(shot, prefix, errors, disclosed=disclosed, live_action=live_action)
        _lint_audio_bed(shot, prefix, errors)
        # Both voice rules govern a SYNTHESISED voice: one refuses an instant clone, the other
        # refuses a spoken line nothing will say. On this lane the operator is the voice — there
        # is no clone to grade and no TTS to be missing — so applying them would refuse every
        # valid live-action script on a profile with no clone, which is most of them.
        if not live_action:
            if voice_grade is not None:
                _lint_voice_grade(shot, prefix, errors, voice_grade=voice_grade)
            if voice_id is not None:
                _lint_spoken_has_a_voice(
                    shot, prefix, errors, voice_id=voice_id, vo_source=vo_source
                )

        for field_name in _NEGATION_SCANNED_SHOT_FIELDS:
            value = shot.get(field_name)
            if isinstance(value, str) and value:
                _lint_negation(value, f"{prefix}.{field_name}", errors)

    _lint_shot_mix(shots, errors, warnings, live_action=live_action)
    _lint_identity_bindings(doc, errors, warnings)
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


#: Constants a caller writing TO these rules needs, named once. Values are read off module state
#: at call time — never restated — so `--rules` cannot disagree with what the linter enforces.
def _active_constants() -> dict[str, object]:
    return {
        "MAX_PRESENTER_SHARE": MAX_PRESENTER_SHARE,
        "presenter_quota_min_shots": _PRESENTER_QUOTA_MIN_SHOTS,
        "required_shot_role": "screen",
        "valid_roles": sorted(_VALID_ROLES),
        "audio_bed_field": _AUDIO_BED_FIELD,
        "vo_source_field": _VO_SOURCE_FIELD,
        "narration_field": _NARRATION_FIELD,
        "narration_duty_warn_pct": _NARRATION_DUTY_WARN_PCT,
        "max_confirmed_duration_s": _MAX_CONFIRMED_CEILING_S,
        "vo_words_per_sec": VO_WORDS_PER_SEC,
    }


def active_rules() -> list[dict[str, str]]:
    """The plan-time rules :func:`lint_shotlist` actually runs, with their own summaries.

    Derived by reading which ``_lint_*`` helpers the entry point invokes and taking each one's
    docstring first line — NOT from a hand-maintained list, because a hand-maintained list is the
    thing that went stale. Add a rule and it publishes itself; delete one and it disappears.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(lint_shotlist))
    names = sorted(
        {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.startswith("_lint_")
        }
    )

    rules = []
    for name in names:
        fn = globals().get(name)
        doc = inspect.getdoc(fn) or ""
        summary = doc.split("\n", 1)[0].strip() if doc else ""
        rules.append({"name": name, "summary": summary or "(no docstring)"})
    return rules
