"""Contract test — the sequence skill routes by PREMISE, and says where premises are declared.

Two failures on 2026-08-29, both from this gap. A seat-only split of a clean, address-verified
list returned 78% SATURATED on its first lint, because seat says nothing about whether a row's
evidence supports the claim the body makes. And a new argument was proposed on
`ships-agent-product` — a premise `premise-vocab.toml` disqualifies in capitals, citing the
56-row test that refuted it — because nothing in the skill's reading list points at that file.

Premise is the axis that actually gates: `premise-unsupported` is a hard ERROR that stops
itemising past half a list and reports one SATURATED aggregate instead.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "plugin" / "skills" / "email-sequence"


def _bodies() -> list[str]:
    return [
        (SKILL_DIR / name).read_text(encoding="utf-8") for name in ("body_template.md", "SKILL.md")
    ]


def test_premise_vocab_is_in_the_reading_list_not_only_a_linter_flag():
    """It must be something you READ before choosing an argument.

    It was previously mentioned only as a side effect of the merge-render linter's
    `--profile` flag, i.e. discoverable after the gate rejects you, not before you draft.
    """
    for body in _bodies():
        load_section = body.split("## Inputs to gather")[0]
        assert "premise-vocab.toml" in load_section, (
            "premise-vocab.toml must appear in 'Load context first', not only downstream"
        )
        assert "disqualified" in load_section


def test_the_split_is_signal_then_premise_then_seat():
    for body in _bodies():
        assert "Split by PREMISE and SEAT" in body
        assert "signal → premise → seat" in body
        assert "premise_unsupported" in body, "name the check that enforces it"
        assert "SATURATED" in body


def test_premise_is_introduced_before_seat_in_the_split_section():
    """Ordering is the whole point: seat-first is what produced the 78% saturated run."""
    for body in _bodies():
        section = body.split("Split by PREMISE and SEAT")[1].split("## ")[0]
        assert section.index("Premise decides") < section.index("Seat then decides")
