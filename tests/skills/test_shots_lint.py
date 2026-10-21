"""gtm_core.shots_lint — the deterministic craft gate video-script runs before hand-off
(Phase 18, R5/R6).

Errors block: stacked camera moves, negation phrasing in prompt fields, structural violations.
Warnings advise: unknown camera terms, durations past every confirmed ceiling, beat-total drift.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gtm_core import shots_lint as sl

REPO = Path(__file__).resolve().parents[2]
SCRIPT_BODY = REPO / "plugin" / "skills" / "video-script" / "body_template.md"


def _doc(**overrides) -> dict:
    base = {
        "source_item": "item-1",
        "total_duration_s": 8,
        "style_scaffold": {
            "look": "editorial monochrome, fine grain, shallow depth of field",
            "provider_model": "m",
            "negative": "text artifacts, logos",
        },
        "shots": [
            {
                "n": 1,
                "duration_s": 8,
                "camera": "static, slow push-in",
                "motion_prompt": "she speaks to camera, articulating each word, and holds the gaze",
                "visual": "office at dusk, laptop glow on her face",
                "role": "presenter",
                # audio_bed required since 2026-08-28 — see sl._lint_audio_bed.
                "audio_bed": "room tone under the whole beat",
            }
        ],
    }
    base.update(overrides)
    return base


def _shot(**overrides) -> dict:
    shot = {
        "n": 1,
        "duration_s": 8,
        "camera": "static, slow push-in",
        # Required since 2026-08-19 — see sl._lint_motion_prompt. Present in the baseline so the
        # camera/negation/duration tests below exercise the rule they name, not this one.
        "motion_prompt": "she speaks to camera, articulating each word, turning a page",
        "visual": "office",
        # audio_bed required since 2026-08-28 — see sl._lint_audio_bed. Present in the
        # baseline so the tests below exercise the rule they name, not this one.
        "audio_bed": "room tone under the whole beat",
    }
    shot.update(overrides)
    return shot


# --- clean paths ---------------------------------------------------------------------------


def test_a_clean_shotlist_has_no_errors_or_warnings():
    errors, warnings = sl.lint_shotlist(_doc())
    assert errors == []
    assert warnings == []


def test_the_canonical_single_move_examples_are_not_stacked():
    for camera in (
        "static, slow push-in",
        "handheld pan left to right",
        "locked wide, camera remains still",
        "static, screen capture",
    ):
        errors, _ = sl.lint_shotlist(_doc(shots=[_shot(camera=camera)]))
        assert errors == [], f"{camera!r} should lint clean, got {errors}"


def test_coverage_framing_terms_are_recognised():
    """Reverse coverage and comparative framing are vocabulary, not unknown terms.

    Both were absent: the list had `over-the-shoulder` and `two-shot` but no reverse angle, and
    `\\bwide\\b` did not match the natural comparative "wider". A script asking for either got a
    spurious unknown-term warning on phrasing the models do understand.
    """
    # Each string carries ONLY the term under test — no "static"/"locked" prefix, which is itself
    # a recognised term and would satisfy the check on its own, hiding whether the new one works.
    for camera in (
        "reverse angle on the subject",
        "from behind the subject",
        "wider angle of the same scene",
    ):
        errors, warnings = sl.lint_shotlist(_doc(shots=[_shot(camera=camera)]))
        assert errors == [], f"{camera!r} should lint clean, got {errors}"
        assert not any("camera" in w for w in warnings), f"{camera!r} warned: {warnings}"


def test_named_compound_moves_count_as_one_move():
    for camera in ("dolly zoom in", "crash zoom", "whip pan to the window", "slow pull-back"):
        errors, _ = sl.lint_shotlist(_doc(shots=[_shot(camera=camera)]))
        assert errors == [], f"{camera!r} is one named move, got {errors}"


# --- stacked camera moves (error) ----------------------------------------------------------


def test_stacked_moves_are_an_error():
    errors, _ = sl.lint_shotlist(
        _doc(shots=[_shot(camera="dolly in while zooming and panning left")])
    )
    assert len(errors) == 1
    assert "stacks 3 camera moves" in errors[0]


def test_two_moves_are_an_error():
    errors, _ = sl.lint_shotlist(_doc(shots=[_shot(camera="pan left then tilt up")]))
    assert len(errors) == 1
    assert "one move per shot" in errors[0]


# --- unknown camera vocabulary (warning) ---------------------------------------------------


def test_an_unrecognized_camera_term_is_a_warning_not_an_error():
    errors, warnings = sl.lint_shotlist(_doc(shots=[_shot(camera="vibes-forward angle")]))
    assert errors == []
    assert len(warnings) == 1
    assert "no known move or framing term" in warnings[0]


# --- negation phrasing (error) -------------------------------------------------------------


def test_negation_in_visual_is_an_error_pointing_at_the_negative_clause():
    errors, _ = sl.lint_shotlist(_doc(shots=[_shot(visual="empty office, no people anywhere")]))
    assert len(errors) == 1
    assert "shot[1].visual" in errors[0]
    assert "style_scaffold.negative" in errors[0]


def test_negation_in_camera_is_an_error():
    errors, _ = sl.lint_shotlist(_doc(shots=[_shot(camera="locked wide, no movement")]))
    assert any("shot[1].camera" in e and "negation" in e for e in errors)


def test_negation_in_motion_prompt_is_an_error():
    errors, _ = sl.lint_shotlist(
        _doc(shots=[_shot(motion_prompt="he stands still, don't move the hands")])
    )
    assert any("shot[1].motion_prompt" in e for e in errors)


def test_negation_in_the_scaffold_look_is_an_error():
    doc = _doc()
    doc["style_scaffold"]["look"] = "editorial monochrome, no on-screen graphics"
    errors, _ = sl.lint_shotlist(doc)
    assert any("style_scaffold.look" in e for e in errors)


def test_spoken_dialogue_is_exempt_from_the_negation_rule():
    errors, _ = sl.lint_shotlist(_doc(shots=[_shot(spoken="This is not what anyone tells you.")]))
    assert errors == []


def test_the_negative_field_is_exempt_by_design():
    doc = _doc()
    doc["style_scaffold"]["negative"] = "text artifacts, logos, watermarks"
    errors, _ = sl.lint_shotlist(doc)
    assert errors == []


# --- durations (warnings) ------------------------------------------------------------------


def test_a_duration_past_every_confirmed_ceiling_warns():
    errors, warnings = sl.lint_shotlist(_doc(total_duration_s=20, shots=[_shot(duration_s=20)]))
    assert errors == []
    assert any("confirmed provider" in w for w in warnings)


def test_beat_total_drift_from_total_duration_warns():
    errors, warnings = sl.lint_shotlist(_doc(total_duration_s=30))
    assert errors == []
    assert any("drift" in w for w in warnings)


# --- structure (errors) --------------------------------------------------------------------


def test_missing_camera_is_an_error():
    errors, _ = sl.lint_shotlist(_doc(shots=[{"n": 1, "duration_s": 8, "visual": "office"}]))
    assert any("missing required field `camera`" in e for e in errors)


def test_an_unknown_role_is_an_error():
    errors, _ = sl.lint_shotlist(_doc(shots=[_shot(role="narrator")]))
    assert any("role" in e for e in errors)


def test_missing_top_level_fields_are_errors():
    errors, _ = sl.lint_shotlist({"shots": [_shot()]})
    assert any("`source_item`" in e for e in errors)
    assert any("`style_scaffold`" in e for e in errors)


def test_empty_shots_is_an_error():
    errors, _ = sl.lint_shotlist(_doc(shots=[]))
    assert any("non-empty" in e for e in errors)


# --- the skill body's own worked example must lint error-free ------------------------------


def test_video_script_worked_example_has_no_lint_errors():
    """The canonical Step 1.5 example is what operators copy — it must never teach a pattern
    the linter then rejects. Warnings are tolerated (the example is an excerpt, so its beat
    total legitimately drifts from total_duration_s)."""
    if not SCRIPT_BODY.is_file():
        pytest.skip(
            "video-script/body_template.md not present in this distribution (paid-tier stub)"
        )
    body = SCRIPT_BODY.read_text(encoding="utf-8")
    idx = body.index("Step 1.5")
    m = re.search(r"```json\n(.*?)```", body[idx:], re.DOTALL)
    assert m, "no worked example found after Step 1.5"
    errors, _ = sl.lint_shotlist(json.loads(m.group(1)))
    assert errors == []


def test_presenter_refused_on_high_emotional_load():
    """Q5: High emotional load beats cannot route to prompt-only synthetic presenter."""
    doc = _doc(shots=[_shot(role="presenter", emotional_load="high")])
    errors, _ = sl.lint_shotlist(doc)
    assert any("emotional_load='high'" in e for e in errors)
    assert any("Choose one of three routes: (1) real footage" in e for e in errors)
    assert any("(2) performance transfer from a driving clip" in e for e in errors)
    assert any("(3) the faceless format" in e for e in errors)

    # Route 1: live action (real footage) with emotional_load="high" passes
    doc_live = _doc(
        capture_mode="live_action", shots=[_shot(role="presenter", emotional_load="high")]
    )
    errors_live, _ = sl.lint_shotlist(doc_live)
    assert errors_live == []

    # Route 3: faceless format (broll / held stills) with emotional_load="high" passes
    doc_broll = _doc(shots=[_shot(role="broll", emotional_load="high")])
    errors_broll, _ = sl.lint_shotlist(doc_broll)
    assert errors_broll == []

    # Clean shot list with emotional_load="low" must not over-fire
    doc_clean = _doc(shots=[_shot(role="presenter", emotional_load="low")])
    errors_clean, _ = sl.lint_shotlist(doc_clean)
    assert errors_clean == []


# --- CLI -----------------------------------------------------------------------------------


def test_cli_exit_1_on_errors_and_json_output(tmp_path, capsys):
    p = tmp_path / "bad.shots.json"
    p.write_text(json.dumps(_doc(shots=[_shot(camera="pan left then tilt up")])))

    rc = sl.main([str(p)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False
    assert out["errors"]


def test_cli_exit_0_with_warnings_only(tmp_path, capsys):
    p = tmp_path / "warn.shots.json"
    p.write_text(json.dumps(_doc(shots=[_shot(camera="vibes-forward angle")])))
    rc = sl.main([str(p)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["warnings"]


def test_cli_exit_2_on_a_missing_file(tmp_path, capsys):
    rc = sl.main([str(tmp_path / "absent.shots.json")])
    assert rc == 2
    assert "unreadable" in capsys.readouterr().out


def test_cli_exit_2_on_invalid_json(tmp_path, capsys):
    p = tmp_path / "broken.shots.json"
    p.write_text("{not json")
    rc = sl.main([str(p)])
    assert rc == 2
    assert "invalid JSON" in capsys.readouterr().out


# --- production.screen_ui tests ------------------------------------------------------------


def test_screen_ui_exempt_from_in_frame_text_lint():
    """A screen_ui shot is rendered deterministically, so text prompts are not diffusion noise."""
    shot_with_text = _shot(
        role="screen",
        visual="a dashboard screen that reads 'System Healthy' in bold letters",
    )
    # Without production.screen_ui, in-frame text is an error
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_with_text]))
    assert any("asks the video model to render in-frame text" in e for e in errors)

    # With production.screen_ui, the prompt is exempt
    shot_with_text["production"] = {
        "screen_ui": {"scene": "kinetic-type", "headline": "System Healthy"}
    }
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_with_text]))
    assert not any("asks the video model to render in-frame text" in e for e in errors)


def test_screen_ui_mutual_exclusivity_scene_and_composition():
    """Exactly one of scene or composition is required."""
    # Both scene and composition
    shot_both = _shot(
        role="screen",
        production={
            "screen_ui": {
                "scene": "hero-reveal",
                "composition": "compositions/card.html",
            }
        },
    )
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_both]))
    assert any("specifies both 'scene' and 'composition'" in e for e in errors)

    # Neither scene nor composition
    shot_neither = _shot(role="screen", production={"screen_ui": {"backend": "html"}})
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_neither]))
    assert any("specifies neither 'scene' nor 'composition'" in e for e in errors)


def test_screen_ui_backend_validation():
    """Backend must be 'pillow' or 'html' if specified."""
    shot_bad_backend = _shot(
        role="screen",
        production={"screen_ui": {"scene": "hero-reveal", "backend": "blender"}},
    )
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_bad_backend]))
    assert any("backend must be 'pillow' or 'html'" in e for e in errors)

    shot_good_backend = _shot(
        role="screen",
        production={"screen_ui": {"scene": "hero-reveal", "backend": "html"}},
    )
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_good_backend]))
    assert not any("backend must be" in e for e in errors)


def test_screen_ui_composition_confinement_and_extension():
    """Compositions must be relative .html files without traversal."""
    # Not an .html file
    shot_txt = _shot(
        role="screen",
        production={"screen_ui": {"composition": "compositions/card.txt"}},
    )
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_txt]))
    assert any("must be an .html file" in e for e in errors)

    # Path traversal
    shot_traversal = _shot(
        role="screen",
        production={"screen_ui": {"composition": "../other-tenant/card.html"}},
    )
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_traversal]))
    assert any("path traversal ('..') is refused" in e for e in errors)

    # Absolute path
    shot_abs = _shot(
        role="screen",
        production={"screen_ui": {"composition": "/etc/card.html"}},
    )
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_abs]))
    assert any("must be a relative path" in e for e in errors)

    # Pillow backend with composition
    shot_pillow_comp = _shot(
        role="screen",
        production={
            "screen_ui": {
                "composition": "compositions/card.html",
                "backend": "pillow",
            }
        },
    )
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_pillow_comp]))
    assert any("composition requires backend: 'html'" in e for e in errors)


def test_screen_ui_string_extras_ceilings():
    """String extras must respect character ceilings."""
    shot_long_headline = _shot(
        role="screen",
        production={
            "screen_ui": {
                "scene": "kinetic-type",
                "headline": "A" * 121,
            }
        },
    )
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_long_headline]))
    assert any("headline length (121) exceeds ceiling of 120 chars" in e for e in errors)

    shot_good_headline = _shot(
        role="screen",
        production={
            "screen_ui": {
                "scene": "kinetic-type",
                "headline": "A" * 120,
            }
        },
    )
    errors, _ = sl.lint_shotlist(_doc(shots=[shot_good_headline]))
    assert not any("headline length" in e for e in errors)
