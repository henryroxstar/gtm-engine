"""`gtm_core.render_prompt` — shape A assembled in order, holds and mid-body negations refused.

Fictional shot list. The two refusals under test are the two defects that produced six motionless
shots from eight hand-typed prompts: an `expression` with no timing clause, and "no teeth" in the
body of the prompt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.render_prompt import (
    LEXICON_RULE,
    REFERENCE_STANDARD,
    RenderPromptError,
    build_prompt,
    main,
)


def _shot(**overrides) -> dict:
    base = {
        "role": "broll",
        "duration_s": 3.0,
        "camera": "static camera, slow push-in",
        "motion_prompt": "he looks up and holds the gaze for half a second",
        "visual": "a kitchen at dawn",
        "expression": "at 1.5s the inner brows lift a fraction; the mouth stays closed",
        "stability": "wardrobe, framing and background stay as the start frame",
        "sfx": "a kettle ticking",
    }
    base.update(overrides)
    return base


DOC = {
    "source_item": "ci-fixture-02",
    "total_duration_s": 12.0,
    "style_scaffold": {
        "look": "warm editorial",
        "provider_model": "fixture_v1",
        "negative": ["text artifacts", "logos", "no watermarks"],
    },
    "shots": [
        _shot(id="s01", environment_motion="steam drifts through the key light"),
        _shot(id="s02", expression="his lips stay pressed together; the gaze steady"),
        _shot(id="s03", expression="at 1s the mouth closes. No teeth"),
        _shot(
            id="s04",
            end_frame="stills/s04-end.png",
            motion_prompt="a slow push that settles gently on the mug as the light comes up",
            negative="extra fingers",
        ),
        _shot(id="s05", role="presenter", expression="", sfx=""),
    ],
}


def test_sections_come_out_in_the_recipe_order():
    prompt = build_prompt(DOC, 1, seed=7)["prompt"]
    expected = [
        "Static camera, slow push-in.",
        "He looks up and holds the gaze for half a second.",
        "At 1.5s the inner brows lift a fraction; the mouth stays closed.",
        "Steam drifts through the key light.",
        "Wardrobe, framing and background stay as the start frame.",
        "No text artifacts.",
        "No logos.",
        "No watermarks.",
        REFERENCE_STANDARD,
    ]
    assert prompt == " ".join(expected)


def test_exclusions_appear_only_as_the_trailing_block():
    prompt = build_prompt(DOC, 4, seed=1)["prompt"]
    body, _, tail = prompt.partition(" No ")
    assert "No " not in body
    assert tail.endswith(REFERENCE_STANDARD)
    assert "No extra fingers." in tail, "a per-shot `negative` joins the scaffold's nouns"


def test_a_hold_only_expression_is_refused_with_the_lexicon_rule():
    with pytest.raises(RenderPromptError) as exc:
        build_prompt(DOC, 2, seed=1)
    msg = str(exc.value)
    assert "shot 2 (s02)" in msg and LEXICON_RULE in msg and "hold" in msg


def test_an_untimed_expression_without_a_hold_verb_is_still_refused():
    doc = json.loads(json.dumps(DOC))
    doc["shots"][0]["expression"] = "one brow lifts a few millimetres; the gaze drops"
    with pytest.raises(RenderPromptError, match=LEXICON_RULE):
        build_prompt(doc, 1, seed=1)


def test_a_mid_body_negation_is_refused_citing_veo():
    with pytest.raises(RenderPromptError) as exc:
        build_prompt(DOC, 3, seed=1)
    assert "'No'" in str(exc.value) and "not recommended" in str(exc.value)


@pytest.mark.parametrize("field", ["camera", "motion_prompt", "stability"])
def test_negation_is_refused_in_every_body_field(field):
    doc = json.loads(json.dumps(DOC))
    doc["shots"][0][field] = "the camera doesn't move"
    with pytest.raises(RenderPromptError, match=field):
        build_prompt(doc, 1, seed=1)


def test_a_timed_expression_passes_even_with_a_still_state_verb():
    record = build_prompt(DOC, 1, seed=3)
    assert "stays closed" in record["prompt"]


def test_an_empty_expression_is_not_a_hold():
    record = build_prompt(DOC, 5, seed=3)
    assert record["generate_audio"] is False, "a presenter shot never generates native audio"


def test_output_is_deterministic_and_recording_clean():
    a = build_prompt(DOC, 1, seed=42)
    b = build_prompt(DOC, 1, seed=42)
    assert a == b
    assert a["prompt"] == a["prompt"].strip() and "  " not in a["prompt"]
    assert a == {
        "shot": 1,
        "prompt": a["prompt"],
        "seed": 42,
        "model": "fixture_v1",
        "keyframe": False,
        "generate_audio": True,
    }


def test_a_keyframe_shot_warns_but_is_not_refused(capsys):
    record = build_prompt(DOC, 4, seed=1)
    assert record["keyframe"] is True
    assert "over 12" in capsys.readouterr().err


def test_cli_prints_the_bare_prompt_and_json(tmp_path: Path, capsys):
    p = tmp_path / "f.shots.json"
    p.write_text(json.dumps(DOC))
    assert main(["--shots", str(p), "--shot", "1", "--seed", "9"]) == 0
    bare = capsys.readouterr().out
    assert main(["--shots", str(p), "--shot", "1", "--seed", "9", "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert bare == record["prompt"] + "\n"
    assert record["seed"] == 9


def test_cli_exit_codes(tmp_path: Path, capsys):
    p = tmp_path / "f.shots.json"
    p.write_text(json.dumps(DOC))
    assert main(["--shots", str(p), "--shot", "2", "--seed", "1"]) == 2
    assert LEXICON_RULE in capsys.readouterr().err
    assert main(["--shots", str(p), "--shot", "99", "--seed", "1"]) == 2
    with pytest.raises(SystemExit):
        main(["--shots", str(p), "--shot", "1"])  # --seed is required
