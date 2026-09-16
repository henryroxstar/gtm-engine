"""Invariant tests enforcing cross-PRD architectural constraints (10x Video PRD)."""

from __future__ import annotations

import re
from pathlib import Path

from gtm_core.craft_lint import _lint_element_ref
from gtm_core.preview_card import PreviewCard
from gtm_core.video_presets import CORE_PRESET_KEYS, load_presets

REPO = Path(__file__).resolve().parents[2]


def test_gate_count_is_strictly_bounded():
    """I1: The review gates must remain bounded and not proliferate new blocking gates."""
    skills_dir = REPO / "plugin" / "skills"
    tokens = set()
    gate_pattern = re.compile(r"⟦GATE:([a-zA-Z0-9_-]+)⟧")
    for f in skills_dir.glob("**/SKILL.md"):
        content = f.read_text(encoding="utf-8")
        for match in gate_pattern.finditer(content):
            tokens.add(match.group(1))

    # Permitted gates in skills: plan, publish, reply, capture
    allowed_gates = {"plan", "publish", "reply", "capture"}
    unexpected = tokens - allowed_gates
    assert not unexpected, f"Unexpected review gates found: {unexpected}"


def test_vlm_outputs_are_advisory_never_blocking():
    """I2: VLM inspection outputs (pixel_gate, review.json) must RANK, never GATE.

    No gate-blocking logic in agent/pipeline.py or agent/publish.py may refuse based on VLM.
    """
    for file_path in (
        REPO / "agent" / "pipeline.py",
        REPO / "agent" / "publish.py",
    ):
        if not file_path.is_file():
            continue
        content = file_path.read_text(encoding="utf-8")
        assert "pixel_gate" not in content, f"pixel_gate referenced in blocking path: {file_path}"
        assert "review.json" not in content, f"review.json referenced in blocking path: {file_path}"


def test_new_terms_do_not_leak_to_operator_in_preview_card():
    """I4: Internal developer terms (element_ref, visual_invariant, skin_scaffold, V14, review.json, render_queue)
    must not appear in rendered operator-facing Preview Card markdown."""
    card = PreviewCard(
        script_slug="test-slug",
        hero_stills=["hero1.png", "hero2.png", "hero3.png"],
        animatic_path="animatic.mp4",
        total_duration_s=15.0,
        total_words=30,
        pacing_verdict="Pacing clean",
        estimated_credits=23,
        estimated_credits_min=11,
        budget_credits=50,
    )
    md = card.to_markdown()

    internal_terms = (
        "element_ref",
        "visual_invariant",
        "skin_scaffold",
        "V14",
        "review.json",
        "render_queue",
    )
    for term in internal_terms:
        assert term not in md, f"Internal term {term!r} leaked into preview card markdown"


def test_element_ref_scoped_to_character_shots_only():
    """I5: B-roll, screen capture, and faceless shots must never require element_ref,
    preventing the pipeline from blocking legitimate face-free content."""
    broll_shot = {"role": "broll", "visual": "server rack with flashing lights", "duration_s": 3.0}
    screen_shot = {"role": "screen", "visual": "terminal command execution diff", "duration_s": 4.0}

    errors: list[str] = []
    _lint_element_ref(broll_shot, "shot[1]", errors)
    _lint_element_ref(screen_shot, "shot[2]", errors)
    assert errors == []


def test_preset_spend_ceiling_defaults_conservative():
    """I6: Every core preset in video-presets.toml must ship a max_spend_credits default > 0."""
    presets = load_presets("_template", profiles_root=REPO / "profiles")
    for key in CORE_PRESET_KEYS:
        assert key in presets
        preset = presets[key]
        assert hasattr(preset, "max_spend_credits")
        assert preset.max_spend_credits > 0, f"Preset {key} max_spend_credits must be > 0"
        assert preset.max_spend_credits <= 100, (
            f"Preset {key} max_spend_credits should be conservatively bounded"
        )
