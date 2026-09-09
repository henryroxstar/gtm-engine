from __future__ import annotations

#: What a shot may declare as its audio bed when nobody speaks in it. Free-text, because the
#: point is that a human named something — room tone, a music cue, a diegetic SFX — rather than
#: that they picked from a menu. ``"silent"`` is accepted and is the ONE value that must be
#: argued for in the same string, because deliberate silence is a real editorial device and
#: accidental silence is the defect this rule exists to stop.
_AUDIO_BED_FIELD = "audio_bed"
#: A bare "silent" with no reason is the accidental case wearing the deliberate case's clothes.
_MIN_SILENCE_REASON_CHARS = 12


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
