"""The render-engine registry, and the pre-spend rules that consume it.

Two finished videos were rejected in August 2026 for face distortion, absent lip sync, and a voice
that did not sound like the operator. None was a craft defect: ``soul_id`` is accepted only by
*image* models, so identity cannot cross the image→video boundary, and general image-to-video
models approximate mouth motion from prose rather than generating it from audio.

Prose already forbade the two specific mistakes this replaces — the ``sync_so`` prohibition and the
storyboard gate — and both were bypassed anyway. Hence a capability requirement checked in code.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from gtm_core import render_engines as re_mod
from gtm_core import shots_lint

REPO = Path(__file__).resolve().parents[2]


# --- the registry ------------------------------------------------------------------------------


def test_broll_resolves_because_higgsfield_is_genuinely_good_at_it():
    """The ban is narrow on purpose: no real person in frame is Higgsfield's reviewed strength."""
    spec = re_mod.resolve_engine("broll")
    assert spec.model == "wan2_7"


def test_an_identity_still_resolves_to_the_soul():
    spec = re_mod.resolve_engine("identity_still")
    assert spec.identity_faithful and spec.output == "image"


def test_an_undisclosed_presenter_is_refused_and_says_what_to_do_instead():
    """The closed DEFAULT — this must fail EARLY and actionably, not at render time.

    `disclosed` is keyword-only and defaults to False, so a caller that has not thought about
    Art. 50 gets the refusal. That is the whole reason the parameter has a default at all.
    """
    with pytest.raises(re_mod.EngineUnavailable) as exc:
        re_mod.resolve_engine("presenter")
    message = str(exc.value)
    assert "REAL FOOTAGE" in message and "FACELESS" in message, (
        "refusing without naming the two lanes that work turns a routing decision into a dead end"
    )
    assert "panel_eval" in message, (
        "an undisclosed synthetic presenter is exactly what the W4 panel was pre-registered to "
        "decide; the refusal has to say so, or the gate looks arbitrary and gets edited away"
    )


def test_a_disclosed_presenter_resolves_the_avatar_engine():
    """The positive control.

    Every mistake in this module looks like a deny, so a refusal test proves nothing on its own —
    a typo in the role name passes it. This is the paired assertion that the lane is actually
    reachable when the disclosure duty is accepted.
    """
    spec = re_mod.resolve_engine("presenter", disclosed=True)
    assert spec.name == "heygen_avatar"
    assert spec.lip_sync == "native" and spec.identity_faithful


def test_disclosure_is_not_a_property_engines_have_by_default():
    """Fail-closed: an engine that never mentions disclosure does not silently acquire the flag."""
    assert re_mod.resolve_engine("broll").requires_disclosure is False
    assert re_mod.resolve_engine("identity_still").requires_disclosure is False


def test_a_silent_presenter_shot_is_a_still_not_a_banned_render():
    """A presenter who is not speaking is a held identity still — cheap, and allowed."""
    assert re_mod.engine_for_shot_role("presenter", speaks=False).output == "image"


def test_a_speaking_presenter_shot_is_refused_when_undisclosed():
    with pytest.raises(re_mod.EngineUnavailable):
        re_mod.engine_for_shot_role("presenter", speaks=True)


def test_a_speaking_presenter_shot_resolves_when_disclosed():
    assert re_mod.engine_for_shot_role("presenter", speaks=True, disclosed=True).lip_sync == (
        "native"
    )


def test_disclosure_does_not_leak_into_the_silent_presenter_path():
    """A held still is not synthetic *speech*; disclosing changes nothing about which engine serves
    it. Asserted because `disclosed` is threaded through `engine_for_shot_role` for every role."""
    for disclosed in (False, True):
        spec = re_mod.engine_for_shot_role("presenter", speaks=False, disclosed=disclosed)
        assert spec.output == "image"


def _registry(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "render_engines.toml"
    path.write_text(textwrap.dedent(body))
    return path


def test_an_engine_failing_a_role_requirement_is_refused_by_capability_not_by_name(tmp_path):
    """The rule is vendor-agnostic: ANY engine lacking native lip sync fails, HeyGen included."""
    reg = _registry(
        tmp_path,
        """
        [engines.plausible_but_wrong]
        provider = "someone"
        model = "shiny_new_v9"
        output = "video"
        identity_faithful = true
        lip_sync = "none"

        [roles.presenter]
        engine = "plausible_but_wrong"
        requires = { identity_faithful = true, lip_sync = "native", output = "video" }
        """,
    )
    with pytest.raises(re_mod.EngineUnavailable, match="lip_sync"):
        re_mod.resolve_engine("presenter", registry_path=reg)


def test_a_retired_engine_can_never_be_selected(tmp_path):
    """sync_so is architecturally wrong, not version-wrong — a v4 must not silently qualify."""
    reg = _registry(
        tmp_path,
        """
        [engines.posthoc]
        provider = "higgsfield"
        model = "sync_so"
        output = "video"
        identity_faithful = false
        lip_sync = "post_hoc"
        retired = true
        notes = "re-renders the mouth at lower fidelity and composites it back"

        [roles.presenter]
        engine = "posthoc"
        requires = { }
        """,
    )
    with pytest.raises(re_mod.EngineUnavailable, match="retired"):
        re_mod.resolve_engine("presenter", registry_path=reg)


def test_the_registry_refuses_to_carry_a_secret(tmp_path):
    reg = _registry(
        tmp_path,
        """
        [engines.leaky]
        provider = "someone"
        model = "m"
        output = "video"
        api_key = "sk-do-not-do-this"

        [roles.broll]
        engine = "leaky"
        requires = { }
        """,
    )
    with pytest.raises(re_mod.EngineError, match="secret-shaped"):
        re_mod.resolve_engine("broll", registry_path=reg)


def test_the_shipped_registry_never_serves_a_presenter_without_native_lip_sync():
    """A guard against 'temporarily' binding presenter to an i2v model to unblock a render.

    Expressed as a capability invariant rather than `engine == ""`, because the empty string was
    only ever a proxy for it — and a proxy is what gets edited when someone is in a hurry. Whatever
    is bound here must hold a real likeness AND generate the mouth from the audio.
    """
    data = re_mod.load_registry()
    bound = data["roles"]["presenter"]["engine"]
    if bound:
        engine = data["engines"][bound]
        assert engine["lip_sync"] == "native", (
            f"{bound!r} serves the presenter role without native lip sync — this is the exact "
            "August 2026 defect the registry exists to make unrepresentable"
        )
        assert engine["identity_faithful"] is True
        assert engine.get("requires_disclosure") is True, (
            "a synthetic talking head of a real person may only ship disclosed; an engine bound "
            "here without that flag reopens the undisclosed lane the W4 panel gates"
        )
    assert data["engines"]["higgsfield_lipsync_posthoc"]["retired"] is True


# --- the pre-spend shot-list rules ---------------------------------------------------------------


def _shot(**kw) -> dict:
    base = {
        "n": 1,
        "duration_s": 4,
        "camera": "static",
        "visual": "A quiet desk at dusk.",
        "motion_prompt": "He speaks directly to camera, articulating each word.",
        "role": "presenter",
        "spoken": "Authorization is the hard part.",
    }
    base.update(kw)
    return base


def _doc_with(shots: list[dict]) -> dict:
    """The smallest shot-list document `lint_shotlist` accepts, so a test can vary one key."""
    return {
        "source_item": "probe-item",
        "total_duration_s": sum(float(s["duration_s"]) for s in shots),
        "style_scaffold": {
            "look": "A quiet editorial grade, shallow depth of field.",
            "provider_model": "wan2_7",
        },
        "shots": shots,
    }


def test_a_speaking_presenter_shot_fails_the_lint_before_any_spend():
    errors: list[str] = []
    shots_lint._lint_presenter_engine(_shot(), "shot[1]", errors)
    assert errors and "no engine can serve it" in errors[0]


def test_a_disclosed_speaking_presenter_shot_passes_the_lint():
    errors: list[str] = []
    shots_lint._lint_presenter_engine(_shot(), "shot[1]", errors, disclosed=True)
    assert errors == []


def test_the_disclosure_declaration_is_read_from_the_document_not_guessed():
    """End-to-end through `lint_shotlist`: the top-level key is what opens the engine."""
    doc = _doc_with([_shot()])
    assert [e for e in shots_lint.lint_shotlist(doc)[0] if "no engine" in e]
    doc["synthetic_disclosure"] = "Made with AI. Reviewed and posted by a human."
    assert [e for e in shots_lint.lint_shotlist(doc)[0] if "no engine" in e] == []


def test_a_blank_disclosure_declaration_fails_closed():
    """Present-but-empty is an attempt to open the engine without accepting the duty."""
    doc = _doc_with([_shot()])
    doc["synthetic_disclosure"] = "   "
    errors = shots_lint.lint_shotlist(doc)[0]
    assert any("synthetic_disclosure is present but empty" in e for e in errors)
    assert any("no engine can serve it" in e for e in errors), (
        "a blank declaration must not half-open the gate"
    )


def test_a_presenter_shot_with_no_spoken_line_passes():
    errors: list[str] = []
    shots_lint._lint_presenter_engine(_shot(spoken=""), "shot[1]", errors)
    assert errors == []


def test_a_broll_shot_passes():
    errors: list[str] = []
    shots_lint._lint_presenter_engine(_shot(role="broll"), "shot[1]", errors)
    assert errors == []


@pytest.mark.parametrize(
    "text",
    [
        "A sign reading 'ACCESS DENIED' above the door.",
        "Text overlay appears over the server rack.",
        'A banner that says "Trust nothing".',
        "The screen displays a dashboard headline.",
    ],
)
def test_in_frame_text_requests_are_refused(text):
    errors: list[str] = []
    shots_lint._lint_no_in_frame_text(_shot(visual=text), "shot[1]", errors)
    assert errors, f"expected a finding for {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "A quiet desk at dusk, monitors dark.",
        "He signs the document and slides it across.",  # 'sign' as a verb, not signage
        "Wide shot of a server room, cool blue light.",
    ],
)
def test_ordinary_scene_prose_is_not_flagged(text):
    """A rule that fires on prose nobody sends is a rule people learn to ignore."""
    errors: list[str] = []
    shots_lint._lint_no_in_frame_text(_shot(visual=text), "shot[1]", errors)
    assert errors == [], f"false positive on {text!r}"


def test_the_text_ban_does_not_reach_the_carousel_lane():
    """carousel-visuals Mode V5 renders full-text cards in-image ON PURPOSE, and still ships.

    The ban is video-only. This asserts the rule lives in the video linter and nothing in the
    carousel path imports it — the collision the PRD's first draft would have shipped.
    """
    carousel = REPO / "gtm_core" / "skills" / "carousel_visuals.py"
    if carousel.is_file():
        assert "_lint_no_in_frame_text" not in carousel.read_text(encoding="utf-8")
    assert hasattr(shots_lint, "_lint_no_in_frame_text"), "the rule belongs to the video linter"


# --- the positive control ------------------------------------------------------------------------


def test_the_historical_rejected_shot_list_now_fails_pre_spend():
    """THE acceptance criterion for P1.

    A gate that cannot be shown failing on the real defect it was built for is not a gate. This is
    the actual 2026-08-18 shot list the operator rejected, which previously linted clean.
    """
    # Resolved by glob, never by profile name: no tenant may be named in tests (§R9). The slug
    # is not a tenant token, so it stays.
    matches = sorted(REPO.glob("content/*/scripts/2026-08-18-gym-incident-ciso-authz.shots.json"))
    if not matches:
        pytest.skip("content/ is not present (excluded from the OSS carve)")
    doc = json.loads(matches[0].read_text(encoding="utf-8"))

    # The historical defect is the UNDISCLOSED render — a synthetic talking head shipped with
    # nothing telling the viewer. Strip any later-added declaration so this keeps testing the
    # 2026-08-18 file as it was, rather than silently passing once the rebuild declares one.
    undisclosed = {k: v for k, v in doc.items() if k != "synthetic_disclosure"}
    errors, _ = shots_lint.lint_shotlist(undisclosed)
    presenter_errors = [e for e in errors if "no engine can serve it" in e]
    assert len(presenter_errors) == 3, (
        f"expected all 3 speaking presenter shots refused, got {len(presenter_errors)}: {errors}"
    )

    # The paired positive control: the same three shots are renderable once the render accepts the
    # Art. 50 duty. Without this, a typo in the role name would satisfy the assertion above.
    disclosed = dict(undisclosed)
    disclosed["synthetic_disclosure"] = "Made with AI. Reviewed and posted by a human."
    errors, _ = shots_lint.lint_shotlist(disclosed)
    assert [e for e in errors if "no engine can serve it" in e] == []
