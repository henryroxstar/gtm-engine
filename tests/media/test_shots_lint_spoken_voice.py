"""gtm_core.shots_lint — a script may not carry `[SPOKEN]` lines that nothing will voice.

The defect this closes, traced end to end on 2026-08-29:

1. ``_lint_audio_bed`` requires an ``audio_bed`` only on a shot whose ``spoken`` field is EMPTY —
   a shot *with* a spoken line is assumed to carry audio.
2. ``video-render`` Step 4a generates the VO only if the merged kit carries a non-empty
   ``identity.voice_id`` **and** the script has ``[SPOKEN]`` lines. There is no ``else``.
3. No ``voiceover`` role exists in ``render_engines.toml``, so nothing resolves an engine for the
   missing case either.

So a profile with an empty ``voice_id`` writes a script full of spoken lines, passes this linter
clean (every shot HAS a spoken line, so the audio-bed rule never fires), renders every shot, and
the defect surfaces at ``video_lint`` V10 — dead air on the finished asset — **after the entire
render spend**. That is the 39%-silence failure reachable a second way, and it is exactly the
class the preflight was built to make unreachable.

It bites hardest on the lane the router recommends most confidently: a bare profile has no
``voice_id`` by definition, and the Step 1 menu advertises the faceless lane as ready now.

This is the layer that makes the class *unrepresentable*, in the same shape as the audio-bed rule:
the shot list is the only artefact that can name WHICH line has no voice.

Design: the 2026-08-29 video-router hardening note, item C1a.
"""

from __future__ import annotations

from gtm_core import shots_lint as sl


def _spoken_shot(**overrides) -> dict:
    """A well-formed shot that SPEAKS. Every unrelated rule is satisfied in the baseline so these
    tests exercise the voice rule and not an incidental neighbour."""
    shot = {
        "n": 1,
        "duration_s": 5,
        "camera": "static, medium",
        "motion_prompt": "she speaks to camera, articulating each word, hands resting still",
        "visual": "a plain studio wall",
        "role": "presenter",
        "spoken": "The gate refuses the render before it costs you anything.",
    }
    shot.update(overrides)
    return shot


def _silent_shot(**overrides) -> dict:
    shot = {
        "n": 1,
        "duration_s": 5,
        "camera": "static, wide",
        "motion_prompt": "the dashboard redraws as new rows arrive",
        "visual": "a dashboard on a monitor",
        "role": "screen",
        "spoken": "",
        "audio_bed": "low synth pad under the whole beat",
    }
    shot.update(overrides)
    return shot


def _doc(shots: list[dict], **extra) -> dict:
    doc = {
        "source_item": "item-1",
        "total_duration_s": sum(s["duration_s"] for s in shots),
        "style_scaffold": {"look": "editorial monochrome", "provider_model": "wan2_7"},
        "shots": shots,
    }
    doc.update(extra)
    return doc


def _voice_errors(errors: list[str]) -> list[str]:
    return [e for e in errors if "voice" in e.lower() and "spoken" in e.lower()]


# --- the rule ----------------------------------------------------------------------------------


def test_a_spoken_line_with_no_voice_id_is_refused():
    """The regression test for the whole class.

    ``voice_id=""`` means the kit WAS read and records no voice — distinct from ``None``, which
    means the caller never had a kit in hand. Empty fails closed.
    """
    errors, _ = sl.lint_shotlist(_doc([_spoken_shot()]), voice_id="")
    assert _voice_errors(errors), f"a spoken line with no voice_id must ERROR; got {errors}"


def test_a_spoken_line_with_a_voice_id_passes():
    """Positive control. Without it the rule could pass this file by always failing."""
    errors, _ = sl.lint_shotlist(_doc([_spoken_shot()]), voice_id="voice-abc-123")
    assert not _voice_errors(errors), errors


def test_a_captions_only_list_passes_with_no_voice_id():
    """The legitimate faceless cut is not collateral damage.

    Captions-and-music with no voice-over is a real deliverable, not a defect — which is why C1b
    reports the faceless lane as ready-with-a-caveat rather than blocked.
    """
    errors, _ = sl.lint_shotlist(_doc([_silent_shot()]), voice_id="")
    assert not _voice_errors(errors), errors


def test_a_declared_vo_source_satisfies_the_rule():
    """The documented escape hatch. A list that names where its audio comes from has answered
    the question the rule asks, even with no voice_id in the kit."""
    doc = _doc([_spoken_shot()], vo_source="operator records the VO locally and supplies a WAV")
    errors, _ = sl.lint_shotlist(doc, voice_id="")
    assert not _voice_errors(errors), errors


def test_an_empty_vo_source_does_not_satisfy_the_rule():
    """A blank declaration is the missing case wearing the declared case's clothes."""
    doc = _doc([_spoken_shot()], vo_source="   ")
    errors, _ = sl.lint_shotlist(doc, voice_id="")
    assert _voice_errors(errors), errors


def test_the_error_names_the_offending_shot_index():
    """The property that makes it actionable: WHICH line has no voice. A list-level complaint
    sends the operator hunting through eighteen shots."""
    shots = [_silent_shot(n=1), _spoken_shot(n=2), _silent_shot(n=3)]
    errors, _ = sl.lint_shotlist(_doc(shots), voice_id="")
    voiced = _voice_errors(errors)
    assert len(voiced) == 1, voiced
    assert "shot[2]" in voiced[0], voiced[0]


def test_the_error_names_both_remedies():
    """Two ways out, and the message must carry both — clone a voice, or make the cut
    captions-only. A refusal that names no remedy is a dead end."""
    errors, _ = sl.lint_shotlist(_doc([_spoken_shot()]), voice_id="")
    message = "\n".join(_voice_errors(errors))
    assert "identity-kit" in message, message
    assert "caption" in message.lower(), message


def test_a_caller_with_no_kit_in_hand_skips_the_check():
    """``None`` means the caller never read a kit. A shot list linted without one should not fail
    on a fact the caller never had — the same contract ``voice_grade`` already uses."""
    errors, _ = sl.lint_shotlist(_doc([_spoken_shot()]), voice_id=None)
    assert not _voice_errors(errors), errors


def test_the_default_caller_skips_the_check():
    """Omitting the argument entirely is the same as passing None — every existing caller keeps
    working unchanged."""
    errors, _ = sl.lint_shotlist(_doc([_spoken_shot()]))
    assert not _voice_errors(errors), errors
