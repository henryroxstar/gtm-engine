"""C10 — the keyframe request builder (`gtm_core.keyframe_request`).

Covers PRD test ids C10-T1 (a keyframe shot sends both frames, in order), C10-T2 (a shot without
one is byte-identical to the request shape that shipped before this module existed) and C10-T6
(the audio toggle is sent explicitly, never inherited).

The load-bearing assertion here is dict EQUALITY against a literal, not a field-by-field probe: a
builder that quietly grows a key is the way a "no behaviour change" claim goes wrong.
"""

from __future__ import annotations

import dataclasses

import pytest

from gtm_core.keyframe_request import KeyframeRequestError, build_request
from gtm_core.render_engines import EngineSpec, resolve_engine

KEYFRAME_SHOT = {"role": "broll", "end_frame": "refs/shot-03-end.png", "motion_prompt": "slow push"}
PLAIN_SHOT = {"role": "broll", "motion_prompt": "slow push"}


def _engine(**overrides) -> EngineSpec:
    base = resolve_engine("keyframe_broll")
    return dataclasses.replace(base, **overrides) if overrides else base


# ── C10-T1 / C10-T2: the two request shapes ───────────────────────────────────────────────────


def test_a_keyframe_shot_sends_both_frames_in_start_then_end_order():
    """Position is the contract — the model is told where to begin before where to arrive."""
    request = build_request(
        KEYFRAME_SHOT, engine=_engine(), start_media_id="media-start", end_media_id="media-end"
    )
    assert request == {
        "model": "wan2_7",
        "medias": [
            {"role": "start_image", "value": "media-start"},
            {"role": "end_image", "value": "media-end"},
        ],
    }, "a keyframe request must carry exactly the two frames, start first"


def test_a_shot_with_no_end_frame_builds_the_request_that_shipped_before_this_module():
    """C10 must be additive. A single-frame render is not allowed to change shape."""
    assert build_request(PLAIN_SHOT, engine=_engine(), start_media_id="media-start") == {
        "model": "wan2_7",
        "medias": [{"role": "start_image", "value": "media-start"}],
    }, "the single-frame request drifted — every b-roll render in the repo just changed"


# ── the two halves must agree, or nothing is sent ─────────────────────────────────────────────


def test_a_declared_end_frame_with_no_uploaded_media_is_refused():
    """The silent twin: build the single-frame request instead and the clip looks fine."""
    with pytest.raises(KeyframeRequestError, match="end_media_id"):
        build_request(KEYFRAME_SHOT, engine=_engine(), start_media_id="s")


def test_an_uploaded_end_media_for_a_shot_that_declares_none_is_refused():
    """A frame that reached the request without reaching the shot list is an unreviewed edit."""
    with pytest.raises(KeyframeRequestError, match="declares no `end_frame`"):
        build_request(PLAIN_SHOT, engine=_engine(), start_media_id="s", end_media_id="e")


def test_an_engine_that_cannot_take_an_end_frame_refuses_the_shot_that_declares_one():
    """An unknown role is DROPPED server-side, so the refusal has to happen on our side."""
    with pytest.raises(KeyframeRequestError, match="does not accept an end frame"):
        build_request(
            KEYFRAME_SHOT,
            engine=_engine(accepts_end_frame=False),
            start_media_id="s",
            end_media_id="e",
        )


def test_a_missing_start_media_id_is_refused():
    """Positive control lives in the two shape tests above; every shot begins somewhere."""
    with pytest.raises(KeyframeRequestError, match="start_media_id"):
        build_request(PLAIN_SHOT, engine=_engine(), start_media_id="")


# ── C10-T6: the audio toggle is explicit, never inherited ─────────────────────────────────────


def test_an_engine_with_an_audio_toggle_always_sends_it_even_when_its_default_is_off():
    """Omitting the key inherits the PROVIDER's default, which is `true` on every model with one."""
    request = build_request(
        PLAIN_SHOT,
        engine=_engine(audio_default=False, audio_toggle="generate_audio"),
        start_media_id="s",
    )
    assert request["generate_audio"] is False, "an off-by-default toggle is still sent explicitly"


def test_a_shot_with_sfx_turns_the_toggle_on_and_a_shot_without_leaves_it_off():
    """The one case native audio is wanted, matching video-render's documented posture."""
    engine = _engine(audio_default=True, audio_toggle="generate_audio")
    with_sfx = build_request(
        {**PLAIN_SHOT, "sfx": "a low mechanical hum"}, engine=engine, start_media_id="s"
    )
    without = build_request(PLAIN_SHOT, engine=engine, start_media_id="s")
    assert with_sfx["generate_audio"] is True, "a b-roll shot with sfx is the one native-audio case"
    assert without["generate_audio"] is False, "an empty sfx field must not score the clip"


def test_an_engine_with_no_toggle_at_all_emits_no_audio_key():
    """ "No toggle" and "a toggle that defaults off" are different states; wan2_7 is the former."""
    request = build_request(PLAIN_SHOT, engine=_engine(), start_media_id="s")
    assert "generate_audio" not in request and "sound" not in request, (
        "an engine that exposes no audio parameter must not be sent an invented one"
    )


# ── the model always comes from the registry ──────────────────────────────────────────────────


def test_the_model_is_read_off_the_engine_and_never_hardcoded():
    """A body that remembers which model is cheap this month is a body that goes stale."""
    request = build_request(
        PLAIN_SHOT, engine=_engine(model="some_future_model"), start_media_id="s"
    )
    assert request["model"] == "some_future_model", "the request must follow the registry"


# ── the CLI's shot index (review finding) ─────────────────────────────────────────────────────


def _shots_file(tmp_path):
    import json

    f = tmp_path / "x.shots.json"
    f.write_text(
        json.dumps(
            {"shots": [{"n": 1, "role": "broll"}, {"n": 2, "role": "broll", "end_frame": "e.png"}]}
        )
    )
    return f


@pytest.mark.parametrize("shot", ["0", "-1"])
def test_a_shot_number_below_one_is_refused_instead_of_negative_indexing(tmp_path, capsys, shot):
    """`--shot 0` used to build the LAST shot's request and exit 0 — credits on the wrong shot."""
    from gtm_core.keyframe_request import main

    code = main(
        [
            "--shots",
            str(_shots_file(tmp_path)),
            "--shot",
            shot,
            "--start-media",
            "S",
            "--end-media",
            "E",
        ]
    )
    assert code == 1
    assert "1-based" in capsys.readouterr().out


def test_a_shot_that_is_not_an_object_exits_cleanly(tmp_path, capsys):
    import json

    from gtm_core.keyframe_request import main

    f = tmp_path / "bad.shots.json"
    f.write_text(json.dumps({"shots": ["not a shot"]}))
    assert main(["--shots", str(f), "--shot", "1", "--start-media", "S"]) == 1
    assert "not an object" in capsys.readouterr().out
