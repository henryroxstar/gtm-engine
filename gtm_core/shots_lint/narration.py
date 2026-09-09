"""The NARRATION lane — a voice-over track laid over an already-cut timeline.

The per-shot fields model one case and model it correctly: a shot IS the render request, so
``spoken`` marks it as one an audio-capable video model will render and ``vo_seconds`` binds its
``duration_s`` to the truncate/atempo ceilings :func:`gtm_core.video_finish.mux` enforces. Nothing
in this module changes either.

Narration is the other case, and until 2026-09-05 it had nowhere to live. On the 2026-09-04
launch film (30.0s, 12 beats, 10 lines) the real read carried on the shots fired 7 vo-parity
errors; keeping ``spoken`` and dropping ``vo_seconds`` fired 16 duration errors instead. Both rule
sets were right about the lane they price and wrong about this one — a narration line's
``duration_s`` is FINAL-CUT length, not a render request, lines lead their picture cuts on
purpose, and the gaps between them are audio design. So the read was filed in the free-form
``production`` namespace to keep the gate green, which is the same as not having a gate: the
film's actual audio script was invisible to every check in this package.

What CAN go wrong on a track is different from what goes wrong on a shot. Three things, and the
set is deliberately SMALL: this lane is post-render by construction, so unlike every other rule in
this package these save no spend — the audio already exists and playback is free. They earn their
place by being about the finished AUDIO: two lines talking over each other, a line running past
the end of the cut, and a speech duty cycle high enough that the mix's default duck depth removes
the music bed from the whole asset rather than from under the words. That last one is the only
rule here with a measured cost behind it, and it is a warning.

A fourth rule was written and then removed: onsets increasing monotonically. It was a claim about
this FILE's ordering convention rather than about the film — lines listed out of order with
correct onsets still mux into a correct read — and the overlap rule below already catches the
cases where a backwards onset means something audible.
"""

from __future__ import annotations

#: The lane's top-level key. One name, read off this constant by the linter, the published
#: constants, and the tests — never spelled twice.
_NARRATION_FIELD = "narration"

#: Slack allowed before two lines are called overlapping, or one is called past the end of the
#: cut. VENDOR ROUNDING, not a licence for a different edit: onsets come from TTS word timestamps
#: and lengths from a measured file, and both are rounded to the millisecond by the time they
#: reach this file. Same magnitude, and the same reason, as ``gtm_core.vo_timings._END_TOLERANCE_S``.
_NARRATION_TOLERANCE_S = 0.05

#: Speech duty cycle (share of the cut the read occupies) past which the mix's DEFAULT duck depth
#: is the wrong depth. Two measured points, both from the same audio pipeline:
#:
#:   * 2026-09-03, a single narration track over the whole cut (~100% duty) mixed at
#:     ``duck_music_bed``'s defaults of the day (-15 dB, 8:1) produced a bed the operator
#:     described as "no music" on first watch. The sidechain is active almost all of the time, so
#:     there was no clear section left for that depth to recover into. Those two figures are a
#:     dated OBSERVATION, not a live claim about the function: the rule below reads the current
#:     defaults off its signature.
#:   * 2026-09-04, the launch film at 63% duty was deliberately mixed at -6 dB for the same
#:     reason, recorded in its own production note.
#:
#: 60 sits just under the lower of the two, so it is a threshold with an observed case behind it
#: rather than a round number. It is a WARNING and stays one: duck depth is a mixing decision an
#: operator makes by ear, and this rule's whole job is to put the number in front of them before
#: they reach for the defaults. It is not a ceiling on how much a film may talk.
_NARRATION_DUTY_WARN_PCT = 60.0


def _well_formed_lines(lines: list, errors: list[str]) -> list[tuple[int, float, float]]:
    """Type-check each entry, reporting its own violation, and return the usable ones.

    The arithmetic rules below need three numbers per line. A malformed entry is named once here
    and then skipped, rather than being allowed to poison an overlap or duty-cycle figure with a
    ``None`` — a wrong number reported confidently is worse than a missing one.
    """
    usable: list[tuple[int, float, float]] = []
    for i, entry in enumerate(lines, 1):
        prefix = f"{_NARRATION_FIELD}.lines[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} is not an object")
            continue
        line = str(entry.get("line", "") or "").strip()
        onset = entry.get("voice_onset_s")
        length = entry.get("len_s")
        ok = True
        if not line:
            errors.append(f"{prefix} has no `line` — a narration entry with no words is not a read")
            ok = False
        if not isinstance(onset, (int, float)) or isinstance(onset, bool) or onset < 0:
            errors.append(f"{prefix} voice_onset_s must be a number >= 0")
            ok = False
        if not isinstance(length, (int, float)) or isinstance(length, bool) or length <= 0:
            errors.append(f"{prefix} len_s must be a number > 0")
            ok = False
        if ok:
            usable.append((i, float(onset), float(length)))
    return usable


def _lint_narration_timeline(doc: dict, errors: list[str]) -> None:
    """A narration track's lines never play over each other, and end inside the cut.

    Two failures, both about what the finished mix SOUNDS like: two lines whose audio overlaps
    (both play, and the read is unintelligible for the overlap) and a line running past
    ``total_duration_s`` (the mux truncates it, and the film ends mid-sentence — the sneakier of
    the two, because a cut tail is silent rather than wrong).

    Neither is a shot-level rule, because neither is about a shot: a narration line's placement is
    independent of the cut points by design.
    """
    narration = doc.get(_NARRATION_FIELD)
    if narration is None:
        return
    lines = narration.get("lines") if isinstance(narration, dict) else None
    # ONE message for both structural shapes. A `narration` that is not an object and a
    # `narration` whose `lines` are missing or empty are the same defect from the reader's side:
    # the key is present, so the file claims a read, and there is no track to check.
    if not isinstance(lines, list) or not lines:
        errors.append(
            f"{_NARRATION_FIELD} must be an object with a non-empty `lines` array — the key is "
            "present, so this file claims a voice-over track, and there is none to check. Give "
            "it the read, or drop the key."
        )
        return

    usable = _well_formed_lines(lines, errors)

    total = doc.get("total_duration_s")
    has_total = isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0

    # Scanned in ONSET order, not file order. With no ordering rule the file's sequence carries no
    # meaning, so overlap has to be a claim about the intervals themselves — otherwise a line
    # listed out of order but sitting in a clear gap reads as a collision with a line that ends
    # after it starts. Findings still name each line by its position in the FILE, which is where
    # the reader has to go to fix it. Within that pass the cursor tracks the furthest END reached,
    # not the previous line: a short line nested inside a long one must not reset the running end
    # and clear the next line that still collides with the long one.
    usable.sort(key=lambda entry: entry[1])
    prev_end: float | None = None
    prev_end_i: int | None = None
    for i, onset, length in usable:
        prefix = f"{_NARRATION_FIELD}.lines[{i}]"
        if prev_end is not None and onset + _NARRATION_TOLERANCE_S < prev_end:
            errors.append(
                f"{prefix} starts at {onset:g}s but lines[{prev_end_i}] is still speaking until "
                f"{prev_end:g}s — {prev_end - onset:.2f}s of the two reads overlap and both will "
                "play. Move this onset to at least the earlier line's end, or shorten that line."
            )
        if has_total and onset + length > float(total) + _NARRATION_TOLERANCE_S:
            errors.append(
                f"{prefix} ends at {onset + length:.2f}s, past total_duration_s={total:g} — the "
                f"last {onset + length - float(total):.2f}s of this line plays over no picture "
                "and the mux cuts it, so the film ends mid-sentence. Move the line earlier or "
                "extend the cut."
            )
        if prev_end is None or onset + length > prev_end:
            prev_end, prev_end_i = onset + length, i


def _lint_narration_duty_cycle(doc: dict, warnings: list[str]) -> None:
    """How much of the cut the read occupies — the number duck depth scales inversely with.

    Not a defect on its own, which is why it is a warning: a film may talk as much as it likes.
    It fires because the DEFAULT mix is tuned for the opposite case — ``duck_music_bed``'s
    defaults assume the bed spends part of the film clear and can recover into it. At a high duty
    cycle there is no clear part left, and the 2026-09-03 mix at those defaults lost its music
    entirely with every automated gate green: ``video_lint`` V10 asks whether the floor is high
    enough, never whether the bed is audible.

    The defaults are READ OFF ``duck_music_bed``'s own signature rather than written down here.
    A message whose whole job is to say "deviate from these two numbers" must not be quoting a
    copy of them that a later re-tune leaves behind.
    """
    narration = doc.get(_NARRATION_FIELD)
    if not isinstance(narration, dict):
        return
    lines = narration.get("lines")
    total = doc.get("total_duration_s")
    if not isinstance(lines, list) or not lines:
        return
    if not isinstance(total, (int, float)) or isinstance(total, bool) or total <= 0:
        return
    # Same helper the timeline rule uses, so the two can never disagree about which lines count.
    # Its findings are DISCARDED here on purpose: a malformed entry is reported once, by the rule
    # that owns typing, and reporting it twice would just spend the reader's attention.
    spoken_s = sum(length for _, _, length in _well_formed_lines(lines, []))
    duty = 100.0 * spoken_s / float(total)
    if duty < _NARRATION_DUTY_WARN_PCT:
        return

    import inspect

    from gtm_core.video_finish.audio import duck_music_bed

    defaults = inspect.signature(duck_music_bed).parameters
    gain = defaults["music_gain_db"].default
    ratio = defaults["duck_ratio"].default
    warnings.append(
        f"{_NARRATION_FIELD} speech duty cycle is {duty:.1f}% ({spoken_s:.2f}s of read over a "
        f"{float(total):g}s cut), at or past {_NARRATION_DUTY_WARN_PCT:g}% — duck the music bed "
        "SHALLOWER than gtm_core.video_finish.audio.duck_music_bed's defaults "
        f"(music_gain_db={gain:g}, duck_ratio={ratio:g}), which assume the bed has a clear "
        "stretch to recover into. At this duty cycle the sidechain is active almost throughout, "
        "and the default depth removes the bed from the whole asset rather than from under the "
        "words — measured 2026-09-03, when every automated gate stayed green and the operator's "
        "first note was 'no music'. -6 dB / 4:1 is what worked at 63%. Verify by ear, not by "
        "peak level."
    )


def _lint_narration_lane_exclusive(doc: dict, shots: list, errors: list[str]) -> None:
    """One file declares ONE speech lane — narration, or per-shot lip sync, never both.

    They make contradictory claims about the same field. Under ``spoken``/``vo_seconds`` a shot's
    ``duration_s`` is a request to a video model that must fit that shot's own line; under
    ``narration`` it is the length of a cut that has already been made, and the read floats free
    of it. A file carrying both gets both rule sets applied to one number, which is how the
    2026-09-04 film produced 16 duration errors against a finished, correct edit.
    """
    if not isinstance(doc.get(_NARRATION_FIELD), dict):
        return
    conflicts = []
    for i, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            continue
        if str(shot.get("spoken", "") or "").strip():
            conflicts.append(f"shot[{i}].spoken")
        if shot.get("vo_seconds") is not None:
            conflicts.append(f"shot[{i}].vo_seconds")
    if not conflicts:
        return
    errors.append(
        f"{_NARRATION_FIELD} is declared alongside the per-shot lip-sync lane "
        f"({', '.join(conflicts)}) — the two are mutually exclusive, because they mean different "
        "things by `duration_s`: a render request that must fit its own line, versus the length "
        "of a cut the read floats over. Keep the lane this film actually uses and move the other "
        f"lane's text into it; `{_NARRATION_FIELD}.lines[].line` holds a narration read."
    )
