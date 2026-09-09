"""Contract test — video-render actually documents the Soul→video bridge (Phase 8).

Soul renders only as a still (there is no Soul video model), so the ONLY way a trained
identity reaches video is generating one soul_2 still per batch and feeding it as the
image-to-video start frame. This is the drift guard for that bridge: if a future edit to
body_template.md drops the else-if branch, the 4:5→3:4 reframe note, or the
one-still-per-batch discipline, this fails instead of the gap surfacing only when a Soul
profile's render silently comes back generic.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "plugin" / "skills" / "video-render"

if not (SKILL_DIR / "body_template.md").exists():
    # video-render is `oss = "private"` (gtm_core/gating.toml) — the OSS carve stubs its
    # body_template.md out, so this drift guard has nothing to check in that distribution.
    pytest.skip(
        "video-render body_template.md not present (paid-tier stub)", allow_module_level=True
    )


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_body_template_documents_the_soul_bridge():
    body = _text(SKILL_DIR / "body_template.md")
    assert "soul_id" in body
    assert "soul_2" in body
    assert "start frame" in body
    assert "one identity-faithful still" in body or "one" in body


def test_body_template_documents_the_reframe_coercion_note():
    body = _text(SKILL_DIR / "body_template.md")
    assert "3:4" in body and "4:5" in body


def test_body_template_says_one_still_not_one_per_variant():
    body = _text(SKILL_DIR / "body_template.md")
    assert "do not regenerate it per variant" in body


def test_body_template_reference_elements_still_take_priority():
    """The Soul path is the FALLBACK — an else-if, not a replacement — reference
    elements embed directly and need no bridge."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "**Else if**" in body


def test_body_template_empty_soul_in_product_kit_is_override_not_inherit():
    body = _text(SKILL_DIR / "body_template.md")
    assert "override to render" in body or "override" in body


def test_skill_md_is_in_sync_with_the_soul_bridge():
    skill_md = _text(SKILL_DIR / "SKILL.md")
    assert "soul_2" in skill_md
    assert "start frame" in skill_md


def test_manifest_bumped_past_0_1_0_for_the_bridge():
    from gtm_core.skills.video_render import SKILL

    assert SKILL.version != "0.1.0"


# --- Phase 9 (§5.8): two-stage draft -> predict -> batch spend gate ------------


def test_body_template_documents_the_two_stage_gate():
    body = _text(SKILL_DIR / "body_template.md")
    assert "draft pool of K variants" in body
    assert "virality_predictor" in body
    assert "remaining N" in body


def test_body_template_states_the_bounded_by_construction_constraint():
    """The engine drops revisable_from/max_visits at the adapter — an automated
    re-render loop is not buildable today, and the skill must not pretend it is."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "Bounded by construction" in body
    assert "revisable_from" in body
    assert "no loop" in body


def test_body_template_handles_the_15s_cap_both_ways():
    body = _text(SKILL_DIR / "body_template.md")
    assert "15s" in body
    assert "ffmpeg" in body
    assert "say which input was actually" in body


def test_body_template_never_re_renders_to_manufacture_a_score():
    body = _text(SKILL_DIR / "body_template.md")
    assert "Never re-render to manufacture" in body


def test_body_template_preflights_both_draft_and_full_batch_cost():
    body = _text(SKILL_DIR / "body_template.md")
    # C5 renamed this step: it now prices the WHOLE sampling curve, of which the draft pool
    # is one part. The property is unchanged and is what this asserts — both numbers are
    # preflighted before anything is dispatched.
    assert "Preflight the cost: the WHOLE sampling curve" in body
    assert "sampling_curve preflight" in body, "the curve is not actually priced"


def test_body_template_documents_the_draft_pool():
    """Phase 16: the two-stage gate scores a K-variant pool (default 3, operator-overridable
    via draft_pool_size), not a single draft — a reroll/keeper-rate budget so a weak score isn't
    read off one stochastic sample. The winner is draft_rank 1; the rest still ship."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "draft_pool_size" in body
    assert "draft_rank" in body
    assert "pool_predictor_score" in body
    assert "best of" in body.lower() or "best-of" in body.lower()
    assert "not discarded" in body


# --- Phase 12 (§5.6/§6.2): voice-over mux + identity_used disclosure signal ----


def test_body_template_documents_the_vo_mux():
    body = _text(SKILL_DIR / "body_template.md")
    assert "generate_audio" in body
    assert "[SPOKEN]" in body
    assert "voice_id" in body
    # Updated 2026-08-19: this used to assert a literal `ffmpeg -i` command in the body. Bash
    # ffmpeg/ffprobe are DENIED (settings.json, 2026-08-18) so a hand-rolled filter chain cannot
    # bypass video_finish's grading/caption/loudness invariants — the mux now goes through that
    # module's own verb, and the doc must name it.
    assert "gtm_core.video_finish mux" in body


def test_body_template_routes_every_media_op_through_video_finish_never_raw_ffmpeg():
    """The inverse of the old `ffmpeg -i` assertion, and a strictly stronger guard: the body must
    not hand the reader a raw ffmpeg invocation to paste, because `Bash(ffmpeg:*)` is denied and a
    doc that instructs one sends the caller straight into a permission wall (which is exactly what
    happened on 2026-08-19 — the mux/stitch/grade steps still said to shell out after the denial
    landed the day before). Every media operation has a verb; the doc names verbs."""
    body = _text(SKILL_DIR / "body_template.md")

    for verb in ("video_finish mux", "video_finish stitch", "video_finish predictor-trim"):
        assert verb in body, f"body_template must route through `{verb}`"

    # A raw invocation is `ffmpeg` followed by a flag — this deliberately does NOT match prose
    # mentions ("`ffmpeg` is unavailable", "runs ffmpeg from inside gtm_core"), only commands.
    raw_calls = re.findall(r"ffmpeg\s+-[a-zA-Z]", body)
    assert not raw_calls, (
        f"body_template hands the caller {len(raw_calls)} raw ffmpeg command(s) "
        f"({raw_calls[:3]}) — Bash(ffmpeg:*) is denied; use a gtm_core.video_finish verb"
    )


def test_body_template_degrades_honestly_when_ffmpeg_is_unavailable():
    body = _text(SKILL_DIR / "body_template.md")
    assert "ffmpeg` is unavailable" in body
    assert "save the VO track beside the video" in body


def test_body_template_defers_voice_change_and_dubbing():
    body = _text(SKILL_DIR / "body_template.md")
    assert "voice_change" in body
    assert "dubbing" in body
    assert "deferred" in body


def test_body_template_records_identity_used_in_the_manifest():
    body = _text(SKILL_DIR / "body_template.md")
    assert "identity_used" in body
    assert '["soul"]' in body
    assert "Article 50" in body


def test_skill_md_documents_identity_used():
    skill_md = _text(SKILL_DIR / "SKILL.md")
    assert "identity_used" in skill_md


def test_body_template_still_says_do_not_regenerate_it_per_variant():
    """Phase 14 briefly reworded this to 'either' — drops the exact phrase Phase 9's
    test asserts. Guard against that regressing again now that Phase 15 extends the
    same sentence to cover shots too."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "do not regenerate it per variant" in body


# --- Phase 15: long-form multi-shot ---------------------------------------------------------


def test_body_template_detects_the_shots_json_switch():
    body = _text(SKILL_DIR / "body_template.md")
    assert "shots.json" in body
    assert "Multi-shot mode" in body


def test_body_template_locks_the_anchor_across_every_shot_not_just_variants():
    body = _text(SKILL_DIR / "body_template.md")
    assert "reused across every shot" in body
    assert "do not regenerate it per shot" in body


def test_body_template_requires_an_element_for_scene_changes():
    """The Soul-still bridge forces every shot to share one start frame — a script
    with scene changes and only a Soul configured must stop, not silently render a
    disjointed video."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "stop and report the mismatch" in body
    assert "single-scene only" in body


def test_body_template_documents_per_shot_vo_and_lip_sync_and_mux():
    body = _text(SKILL_DIR / "body_template.md")
    assert "own VO from its `spoken` field" in body
    assert "own VO as\n  `audio_references`" in body or "own VO as `audio_references`" in body
    assert "Mux shot 1's VO onto shot 1's clip" in body


def test_body_template_documents_the_stitch_and_single_grade():
    body = _text(SKILL_DIR / "body_template.md")
    assert "concat demuxer" in body
    assert "Grade once" in body
    assert "one grading pass" in body.lower() or "Grade once" in body


def test_body_template_resolves_the_batch_heterogeneity_question():
    """Phase 16: settled by the live generate_video_batch tool schema (independent params per
    requests[] item) — no longer an open question requiring a live-spend test. Shots 2..N in
    long-form mode dispatch as one heterogeneous batch call."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "heterogeneous batch call" in body
    assert "independent" in body
    assert "verified 2026-08-15" in body


def test_body_template_states_the_duration_ceiling_and_soft_cap():
    body = _text(SKILL_DIR / "body_template.md")
    assert "architectural ceiling" in body
    assert "90s" in body


def test_body_template_never_reroll_anchor_or_grade_per_shot_guardrail():
    body = _text(SKILL_DIR / "body_template.md")
    assert "Never re-roll the identity anchor per shot, and never grade per shot" in body


def test_skill_md_documents_multishot():
    skill_md = _text(SKILL_DIR / "SKILL.md")
    assert "shots.json" in skill_md


def test_manifest_bumped_for_phase_15():
    from gtm_core.skills.video_render import SKILL

    assert SKILL.version not in ("0.1.0", "0.5.0")


def test_manifest_bumped_for_phase_16():
    from gtm_core.skills.video_render import SKILL

    assert SKILL.version not in ("0.1.0", "0.5.0", "0.7.0")


def test_body_template_disclosure_line_threads_to_finish_spec():
    """Phase 16: the black-tail defect's fix is burning disclosure onto the asset's own last
    seconds, not appending a segment — video-render's job is only to ensure disclosure_line
    reaches the finish-spec, not to render pixels itself."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "disclosure_line" in body
    assert "[disclosure].line" in body


# --- Phase 19 (VPE-10): generate_audio discipline + b-roll SFX routing --------


def test_body_template_documents_generate_audio_defaults_true():
    """The whole point of this phase: generate_audio's API default is true, so an omitted
    value is not silence — it must be sent explicitly on every declaring-model call."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "generate_audio" in body
    assert "defaults to" in body and "`true`" in body
    assert "Independent of" in body


def test_body_template_forces_generate_audio_false_by_default():
    body = _text(SKILL_DIR / "body_template.md")
    assert "generate_audio: false" in body


def test_body_template_routes_sfx_to_generate_audio_true_for_broll_only():
    """generate_audio:true is scoped to b-roll/screen shots with a non-empty sfx field —
    never a presenter shot, which must keep the cloned voice uncontested."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "generate_audio: true" in body
    assert "SFX:" in body
    assert "never sets this true" in body or "A presenter shot never sets this true" in body


def test_prompt_recipes_documents_native_audio_control():
    recipes = _text(SKILL_DIR / "references" / "prompt-recipes.md")
    assert "generate_audio" in recipes
    assert "NO MUSIC / NO SFX" in recipes


def test_manifest_bumped_for_phase_19():
    from gtm_core.skills.video_render import SKILL

    assert SKILL.version not in ("0.1.0", "0.5.0", "0.7.0", "0.10.0")
