"""Property-based fuzz testing for natural language, title, and ladder parsers.

Phase 1 of Test Suite Hardening roadmap.
Uses Hypothesis to verify generative properties, Unicode boundary cases, and
prevent recurring regression classes (e.g. whitespace-free name regexes,
CTO/director substring collisions, and dash/paren ladder header mismatches).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("hypothesis")

# The SAME prologue every other consumer uses (`gtm_core/cells.py`, `rule_baseline.py`,
# `build_eval_sheet.py`, `hook_coverage/config.py`, `tests/injection/...`), and not
# `from tests.linter.outreach import ...`. There is no `__init__.py` under `tests/`, so both
# spellings resolve under pytest — into TWO distinct `sys.modules` entries, each with its own
# `RULES_VERSION`, `_SEAT_RULES` and `HEDGE_CUES`. The one-implementation contract in
# `tests/linter/test_outreach_linter.py` compares `config.seat_of is outreach.seat_of` and
# cannot see a second copy imported under a different name, so this file was the one place the
# package could silently fork.
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests" / "linter"))

from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from outreach import (  # noqa: E402
    _NON_NAMES,
    _is_person_name,
    non_buyer_of,
    parse_ladder,
    persona_of,
)

# ── 1. _is_person_name property tests ──────────────────────────────────────────

VALID_NAME_PARTS = st.sampled_from(
    [
        "Alice",
        "Bob",
        "Charlie",
        "Dana",
        "Josué",
        "Joaquín",
        "Jean-Luc",
        "O'Brien",
        "Hui",
        "Jie",
        "Wei",
        "Ming",
        "Siti",
        "Nurhaliza",
        "Mary",
        "Anne",
        "Søren",
        "Müller",
    ]
)

NON_NAME_TOKENS = st.sampled_from(
    sorted(_NON_NAMES)
    + [
        "[INSERT",
        "TODO",
        "TBD",
        "PLACEHOLDER",
        "123",
        "User#1",
        "Dana!",
    ]
)


@given(st.lists(VALID_NAME_PARTS, min_size=1, max_size=3))
def test_valid_human_names_are_accepted(parts: list[str]) -> None:
    """Proves 1-3 token names (including accents, hyphens, and Asian names) pass."""
    name = " ".join(parts)
    assert _is_person_name(name) is True, f"Expected valid name for: {name!r}"


@given(st.lists(VALID_NAME_PARTS, min_size=4, max_size=10))
def test_names_exceeding_three_tokens_are_rejected(parts: list[str]) -> None:
    """Beyond 3 tokens is considered a title or note, not a given name."""
    name = " ".join(parts)
    assert _is_person_name(name) is False


@given(
    st.lists(VALID_NAME_PARTS, min_size=0, max_size=2),
    NON_NAME_TOKENS,
    st.lists(VALID_NAME_PARTS, min_size=0, max_size=2),
)
def test_names_containing_placeholder_or_blacklisted_tokens_are_rejected(
    prefix: list[str], bad: str, suffix: list[str]
) -> None:
    """A placeholder or non-name token in any position must invalidate the name."""
    tokens = prefix + [bad] + suffix
    name = " ".join(tokens)
    assert _is_person_name(name) is False


@given(st.text(alphabet=st.characters(whitelist_categories=("Zs", "Cc", "Cf")), max_size=10))
def test_empty_or_whitespace_names_are_rejected(ws: str) -> None:
    assert _is_person_name(ws) is False


# ── 2. persona_of title and boundary invariants ────────────────────────────────

EXECUTIVE_CUES = st.sampled_from(["CEO", "Chief Executive Officer", "Chief Operating Officer"])
ANTI_CUES = st.sampled_from(["Vice President", "vice-president", "EVP", "SVP", "AVP"])
TECHNICAL_CUES = st.sampled_from(
    ["CTO", "Chief Technology Officer", "VP of Engineering", "Head of AI"]
)
NON_BUYER_ROLES = st.sampled_from(
    ["Executive Assistant", "Administrative Assistant", "Recruiter", "Intern"]
)


@given(EXECUTIVE_CUES)
def test_pure_executive_titles_resolve_to_ceo(title: str) -> None:
    assert persona_of(title) == "ceo"


@given(EXECUTIVE_CUES, ANTI_CUES)
def test_executive_titles_with_anti_cues_never_resolve_to_ceo(exec_cue: str, anti_cue: str) -> None:
    """Anti-cue invariant: EVP or Vice President must disqualify the ceo seat."""
    compound_title = f"{anti_cue}, {exec_cue}"
    res = persona_of(compound_title)
    assert res != "ceo", f"Compound title {compound_title!r} unexpectedly resolved to 'ceo'"


@given(
    st.sampled_from(
        [
            "Director of Information Security",
            "Director of Platform",
            "Director of Talent Acquisition",
            "Managing Director",
        ]
    )
)
def test_director_substring_collision_never_resolves_to_cto(director_title: str) -> None:
    """Historical bug invariant: 'cto' must not match inside 'director'."""
    res = persona_of(director_title)
    assert res != "cto", f"Title {director_title!r} matched substring 'cto'"


@given(NON_BUYER_ROLES)
def test_non_buyer_roles_are_detected(role: str) -> None:
    res = non_buyer_of(role)
    assert res is not None, f"Expected non-buyer role for {role!r}"


# ── 3. parse_ladder sequence header formats ───────────────────────────────────

CHANNELS = st.sampled_from(["email", "linkedin", "phone", "loom"])


@given(
    st.integers(min_value=1, max_value=10),
    st.integers(min_value=1, max_value=30),
    CHANNELS,
)
def test_parse_ladder_accepts_dash_format(touch: int, day: int, channel: str) -> None:
    """Tests the canonical dash-separated touch format: **Touch N - Day D - Channel:**"""
    text = f"Some intro\n**Touch {touch} - Day {day} - {channel}:**\nDraft body here\n"
    touches = parse_ladder(text)
    assert len(touches) == 1
    t_num, t_day, t_chan = touches[0]
    assert t_num == touch
    assert t_day == day
    assert t_chan.lower() == channel.lower()


@given(
    st.integers(min_value=1, max_value=10),
    st.integers(min_value=1, max_value=30),
    CHANNELS,
)
def test_parse_ladder_accepts_parenthesized_format(touch: int, day: int, channel: str) -> None:
    """Tests the alternate parenthesized format: **Touch N (Day D, Channel):**"""
    text = f"Header\n**Touch {touch} (Day {day}, {channel}):**\nBody text\n"
    touches = parse_ladder(text)
    assert len(touches) == 1
    t_num, t_day, t_chan = touches[0]
    assert t_num == touch
    assert t_day == day
    assert t_chan.lower() == channel.lower()
