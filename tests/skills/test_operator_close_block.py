"""Test that every skill carries the operator close block and no body orders unedited output."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.skills.codegen import render
from gtm_core.skills.registry import all_skills

CLOSE = "## How to close this run (every surface)"


def test_every_generated_skill_carries_the_operator_close():
    for s in all_skills():
        text = render(s, "body\n")
        assert CLOSE in text, s.name
        assert "Lead with the outcome" in text and "<details>" in text and "what it cost" in text


def test_no_body_orders_raw_output_pasted_unedited():
    for p in Path("plugin/skills").glob("*/body_template.md"):
        assert "unedited" not in p.read_text(encoding="utf-8"), p


def test_overlay_skills_carry_operator_close():
    if not Path("oss/overlays").is_dir():
        pytest.skip("oss/overlays is not carved into the public export")
    overlay_skills = list(Path("oss/overlays/plugin/skills").glob("*/SKILL.md"))
    assert overlay_skills, "Must find overlay skills"
    for p in overlay_skills:
        text = p.read_text(encoding="utf-8")
        assert CLOSE in text, p
