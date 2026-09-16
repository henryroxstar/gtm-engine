"""The caption craft reference: its standing, its scope, and its examples against the real linter.

Sister of test_performance_lexicon_reference.py. Validates that caption-craft.md exists,
is cited by creator-brief, is de-branded, contains all required sections, covers all 12 caption
functions, and respects carve boundaries.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REF = REPO / "plugin" / "skills" / "creator-brief" / "references" / "caption-craft.md"
BODY = REPO / "plugin" / "skills" / "creator-brief" / "body_template.md"
DENYLIST = REPO / "tests" / "linter" / "safe_share_denylist.txt"


def _tenant_tokens() -> list[str]:
    """Tenant tokens from the ONE denylist (tests/linter/safe_share_denylist.txt), never a
    literal roster here. A hardcoded list put the tenant's own product names *into* this file,
    so the marker sweep that guards the shipped surface flagged the guard itself — and it went
    stale besides: the roster it carried predated two product renames. Absent file (the public
    cut excludes it) → empty, same fail-open-to-nothing posture as debrand_check.sh."""
    if not DENYLIST.is_file():
        return []
    return [
        value.strip().lower()
        for line in DENYLIST.read_text(encoding="utf-8").splitlines()
        if (value := line.partition(":")[2])
        and line.split(":", 1)[0].strip() in ("token", "rtoken")
    ]


if not REF.is_file():
    pytest.skip(
        "creator-brief references not present in this distribution (paid-tier stub)",
        allow_module_level=True,
    )

CAPTION_FUNCTIONS = (
    "premise",
    "want",
    "cost",
    "stakes",
    "time",
    "interior",
    "irony",
    "setup",
    "callback",
    "turn",
    "describe",
    "message",
)


@pytest.fixture(scope="module")
def text() -> str:
    return REF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# ── T1 · existence and citation ────────────────────────────────────────────────────────


def test_the_reference_exists_and_is_cited_by_its_skill():
    assert REF.is_file()
    assert "caption-craft.md" in BODY.read_text(encoding="utf-8")


def test_it_declares_itself_company_neutral(flat: str):
    assert "**Company-neutral.**" in flat


# ── T2 · all required sections present ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "heading",
    [
        "## The defect",
        "## 1. Two kinds of on-screen words",
        "## 2. The functions vocabulary",
        "## 3. Anchorage vs relay",
        "## 4. Voice",
        "## 5. Threads",
        "## 6. Withholding (2 + 2",
        "## 7. Rhythm and punctuation",
        "## 8. Worked example on a fictional film",
        "## 9. What this file does not do",
        "## Claim strength and provenance",
    ],
)
def test_all_required_sections_present(text: str, heading: str):
    assert heading in text


# ── T3 · the 12 functions are named ───────────────────────────────────────────────────


@pytest.mark.parametrize("fn", CAPTION_FUNCTIONS)
def test_every_caption_function_is_defined(flat: str, fn: str):
    assert f"`{fn}`" in flat


# ── T4 · key craft concepts present ───────────────────────────────────────────────────


def test_barthes_anchorage_and_relay_explained(flat: str):
    assert "Anchorage" in flat or "anchorage" in flat
    assert "Relay" in flat or "relay" in flat
    assert "Barthes" in flat


def test_withholding_2_plus_2_principle(flat: str):
    assert "2 + 2" in flat
    assert "Stanton" in flat or "Andrew Stanton" in flat


def test_punctuation_as_timing_instruction(flat: str):
    assert "Punctuation as timing instruction" in flat
    assert "Em-dash" in flat or "em-dash" in flat
    assert "Period" in flat or "period" in flat


def test_threads_setup_and_callback(flat: str):
    assert "setup" in flat and "callback" in flat
    assert "caption_pairs_with" in flat


# ── T5 · carve standing and de-branding ─────────────────────────────────────────────────


def test_it_names_no_person_channel_or_company(text: str):
    assert "@" not in text
    for scheme in ("http://", "https://"):
        assert scheme not in text, f"{scheme!r} points at a specific external source"
    for banned in _tenant_tokens():
        assert banned not in text.lower(), f"tenant token {banned!r} in the caption-craft reference"


def test_it_cites_no_withheld_document(text: str):
    assert "docs/prds/" not in text
    assert "docs/archive/" not in text


def test_it_emits_no_gate_marker(text: str):
    assert "⟦GATE" not in text


def test_it_says_what_it_does_not_do(flat: str):
    section = flat[flat.index("## 9. What this file does not do") :]
    assert "transcription guide" in section
    assert "score" in section


# ── K8 · attribution recording into score.json and outcome row ─────────────────────────


def test_video_score_body_documents_caption_craft_at_top_level():
    """K8. score.json carries caption_mode, caption_voice, describe_share at top level."""
    score_body = (REPO / "plugin" / "skills" / "video-score" / "body_template.md").read_text(
        encoding="utf-8"
    )
    assert "caption_mode" in score_body
    assert "caption_voice" in score_body
    assert "describe_share" in score_body
    assert "top level" in score_body
    assert "never inside `recommended`" in score_body


def test_content_outcomes_sync_lists_caption_craft_prefixes():
    """K8. content-outcomes-sync includes caption_mode:, caption_voice:, describe_share:."""
    sync_body = (
        REPO / "plugin" / "skills" / "content-outcomes-sync" / "body_template.md"
    ).read_text(encoding="utf-8")
    assert "`caption_mode:`" in sync_body
    assert "`caption_voice:`" in sync_body
    assert "`describe_share:`" in sync_body
