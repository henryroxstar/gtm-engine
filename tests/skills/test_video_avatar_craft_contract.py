"""Contract: `video-avatar`'s body teaches the render-time craft levers HeyGen documents.

These are body-contract tests — the skill is a prompt, so the artefact under test is the prose an
agent will actually follow. Same shape as the other `test_video_*_contract.py` files.

The theme is **fix pacing where it is cheap**. Duration parity between a VO and its shot is
attacked from two ends: `shots_lint` refuses a mismatch before spend, and `video_finish.mux`
time-compresses finished audio with `atempo` after it. The lever in between — HeyGen's own
`voice_settings.speed`, applied at the render — was documented and unused, so every drift landed
on the mux, which degrades audio that has already been paid for and is bounded at
`MUX_ATEMPO_MAX_LIP_SYNCED = 1.02` on a lip-synced shot (past that it desyncs the mouth it was
supposed to fix).

`<break>` is the same story for rhythm: pacing was whatever the engine defaulted to, because
nothing told the script step the tag existed. It carries two documented conditions, and a body
that teaches the feature without them produces audio artefacts instead of pauses.

Design: the 2026-08-29 video-router hardening note, item C14a (from P5, HeyGen's published
voice/speech docs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BODY = REPO / "plugin" / "skills" / "video-avatar" / "body_template.md"

if not BODY.exists():
    # video-avatar is `oss = "private"` (gtm_core/gating.toml) — the OSS carve stubs
    # its body_template.md out, so this drift guard has nothing to check in that distribution.
    pytest.skip(
        "video-avatar body_template.md not present (paid-tier stub)", allow_module_level=True
    )


def _body() -> str:
    return BODY.read_text(encoding="utf-8")


def test_the_body_teaches_break_tags_with_both_conditions():
    """The two documented failure modes, not just the feature.

    HeyGen's docs attach two conditions to `<break time="1s"/>`: the voice must carry
    `support_pause`, and `<break>` must be the ONLY markup in the text — other tags cause audio
    artefacts. A body that teaches the tag without both conditions has taught a way to break the
    audio, which is worse than leaving pacing at the default.
    """
    text = _body()
    assert "<break" in text, "the body does not teach `<break>` pause control at all"
    assert "support_pause" in text, "the `<break>` guidance omits the support_pause precondition"
    lowered = text.lower()
    assert "only markup" in lowered or "sole markup" in lowered, (
        "the `<break>` guidance omits the only-markup-in-the-text condition"
    )


def test_the_body_tries_voice_settings_before_atempo():
    """Order matters and must be stated as an order.

    A VO that runs long is fixable at the render for the price of a parameter, or at the mux by
    time-compressing finished audio. The body has to name the render-time fix as FIRST — an
    agent that reads both levers as equivalent will reach for the one it meets later in the
    pipeline, which is the expensive, quality-degrading one.
    """
    text = _body()
    assert "voice_settings.speed" in text or "voiceSettings.speed" in text
    assert "atempo" in text, "the body never contrasts the render-time fix with the mux fallback"

    speed_at = min(
        (text.index(tok) for tok in ("voice_settings.speed", "voiceSettings.speed") if tok in text),
        default=-1,
    )
    # The contrast has to appear in the passage that teaches the ordering, not merely somewhere
    # in the file — so anchor on the atempo mention that follows the speed guidance.
    assert "atempo" in text[speed_at:], (
        "`atempo` is never named as the fallback after the render-time speed fix"
    )


def test_the_body_keeps_conservative_speed_bounds():
    """0.9–1.1 by default. The API accepts 0.5–1.5, and a body that only quotes the API range
    invites a 1.4× render that sounds rushed — the bound is an editorial judgement layered on
    top of the technical one, so it has to be written down separately."""
    text = _body()
    assert "0.9" in text and "1.1" in text, (
        "the conservative default speed band (0.9-1.1) is not stated"
    )


# --- C14b / C14 decline -------------------------------------------------------------------------


def test_the_panel_plan_renders_both_avatar_engines():
    """The A/B rides on a run that is already happening.

    The registry marks `avatar_v` "worth testing" and P5 established it costs the same per second
    as `avatar_iv`, so the comparison is free at the margin — and the pre-registered panel is the
    only instrument here that can actually score it. Naming it in the body is what stops the panel
    being run on one engine and the question staying open for another quarter.
    """
    text = _body()
    assert "avatar_iv" in text and "avatar_v" in text, (
        "the body does not name both avatar engines, so the panel has no A/B to render"
    )
    lowered = text.lower()
    assert "panel" in lowered
    marker = lowered.find("both engines")
    assert marker != -1, (
        "the body does not tell the panel run to render its avatar clips on BOTH engines — "
        "without that instruction the comparison never happens and `avatar_v` stays untested"
    )
    window = lowered[max(0, marker - 700) : marker + 700]
    assert "panel" in window, "the both-engines instruction is not attached to the panel procedure"
    assert "condition" in window, (
        "the body does not say how the split relates to the panel's pre-registered CONDITIONS. "
        "Adding a fourth condition would invalidate the run; the reader has to be told that the "
        "engine split stratifies the existing `avatar` condition instead."
    )


def test_the_video_agent_decline_is_recorded():
    """C10's rule applied to a capability we chose not to use.

    HeyGen's Video Agent prompting guide is documented and good — for a chat-mode conversation
    with the provider. A deterministic pipeline wants explicit avatar/voice/`voice_settings`
    control, every value of which some linter reads before a render is paid for. Recorded so the
    decline is a decision rather than ignorance, and so the next reader does not "discover" it.
    """
    text = _body()
    assert "create_video_agent" in text, (
        "the chat-mode Video Agent surface is never named, so its decline is invisible — the next "
        "session rediscovers it as an unexplored capability"
    )
    lowered = text.lower()
    idx = lowered.find("create_video_agent")
    window = lowered[idx : idx + 900]
    assert "deterministic" in window or "explicit" in window, (
        "the Video Agent mention carries no REASON for the decline; a bare 'we do not use this' "
        "is indistinguishable from an oversight"
    )


def test_the_body_does_not_offer_the_mux_as_a_fallback_for_this_lane():
    """Regression on the FALSE-CAPABILITY class this PRD exists to close (C3d's shape).

    The pacing escalation used to end with "`atempo` at the mux, last" — describing a step a
    HeyGen render never reaches. `mux` attaches a VO to a SILENT shot, which is what `video-render`
    produces from Higgsfield; a HeyGen clip arrives with speech already married to picture and goes
    to `video-finish` for stitch/grade/captions. Verified 2026-08-29 by forcing a mux on a real
    render: it took the atempo branch at 1.003 on a 35ms audio-over-video mismatch inside HeyGen's
    own output — a call production never makes.

    Offering an escape hatch that does not exist is worse than offering none: it makes the two
    rungs that DO work look optional.
    """
    text = _body()
    idx = text.find("voice_settings.speed` for length")
    assert idx != -1, "the pacing escalation block is gone"
    block = text[idx : idx + 1800].lower()
    assert "never passes through" in block or "no third rung" in block, (
        "the pacing escalation does not state that a HeyGen render never reaches the mux, so "
        "`atempo` still reads as an available fallback on a lane that has none"
    )
    assert "silent" in block, (
        "the block does not say WHY the mux does not apply (it attaches a VO to a silent shot) — "
        "a rule with no reason is one a future session overturns"
    )


# ── The performance lexicon (P8) ──────────────────────────────────────────────────────


def _flat() -> str:
    import re

    return re.sub(r"\s+", " ", _body())


def test_the_body_carries_the_person_not_a_diagram_rule():
    """The gap this closes, found 2026-09-08: the rule was stated in the manifest `description=`
    and enforced in `gtm_core.shots_lint`, and appeared nowhere in the body — the text an agent
    actually follows. The lint guards the SHOT LIST, so a gesture invented at this step, after the
    list has already passed, met no rule at all. Two renders shipped this way: counted fingers as
    a peace sign, and a stacked-layers phrasing as a held wooden board."""
    text = _flat()
    assert "describes a PERSON, never a diagram" in text
    assert "two fingers raised" in text.lower()
    assert "prop" in text


def test_the_body_leaves_expressiveness_at_the_engine_default():
    """Raising it puts a performed feeling on a disclosed synthetic of a real person, which is a
    creative decision with a face attached — the operator's, and recorded."""
    text = _flat()
    assert "Leave `expressiveness` unset" in text
    assert "operator" in text
    assert "render report" in text or "recorded in the render report" in text


def test_the_body_does_not_pin_the_expressiveness_enum_it_cannot_own():
    """The accepted values are the provider's and have changed before. A body that hardcodes them
    is a page that goes quietly wrong; it says to read the tool schema at call time instead."""
    text = _flat()
    assert "from the tool schema at call time" in text


def test_the_body_refuses_expression_text_in_the_motion_prompt():
    """This engine owns the face, so the shot's `expression` never reaches it. Pasting it into
    `motionPrompt` to compensate renders a face direction as a gesture."""
    text = _flat()
    assert "engine owns the face" in text
    assert "performance-lexicon.md" in text


def test_the_body_does_not_claim_a_story_payoff_face():
    """`video_preflight` routes a story's emotional payoff to live action precisely because a
    performed expression on a disclosed synthetic is the opposite of the ingredient. The body must
    agree with the router rather than offer a way around it."""
    text = _flat()
    assert "routes a story's emotional payoff to live action" in text
    assert "opposite of the ingredient" in text
