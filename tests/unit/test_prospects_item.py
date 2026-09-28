"""Unit tests for gtm_core.prospects_item.check_vocabulary — the public wrapper Phase 2 of
PRD-2026-09-28 exposes so prospects_state.py / signal_freshness.py can validate a closed
vocabulary field without their own copy of the private _word() helper."""

from __future__ import annotations

import pytest

from gtm_core.prospects_item import VocabularyRefusal, check_vocabulary


@pytest.mark.parametrize("field", ["verdict", "lane", "signal_agent_kind", "category_relation"])
def test_check_vocabulary_accepts_blank(field: str) -> None:
    """A brand-new, not-yet-scored account legitimately has no verdict/lane yet — blank
    must stay legal, matching _word()/new_account_defaults()'s existing semantics."""
    check_vocabulary(field, "", where="test")
    check_vocabulary(field, "   ", where="test")


def test_check_vocabulary_accepts_a_real_verdict() -> None:
    check_vocabulary("verdict", "send", where="test")
    check_vocabulary("verdict", "Send", where="test")  # case-insensitive, matches _word()


def test_check_vocabulary_refuses_an_unrecognised_word() -> None:
    with pytest.raises(VocabularyRefusal, match="not in the allowed set for verdict"):
        check_vocabulary("verdict", "prospect", where="test")


def test_check_vocabulary_error_copy_matches_the_prd_contract() -> None:
    with pytest.raises(VocabularyRefusal) as exc_info:
        check_vocabulary("verdict", "prospect", where="test")
    message = str(exc_info.value)
    assert message.startswith('refused: verdict="prospect" is not in the allowed set for verdict (')
    assert message.endswith(
        "To add a new legitimate value, edit gtm_core/prospects_item.py::_VOCABULARIES and redeploy."
    )


def test_check_vocabulary_is_a_no_op_for_a_field_outside_the_closed_vocabularies() -> None:
    """Only the four closed-vocabulary fields are validated — every other mutable field
    (notes, status, owner, ...) must pass through untouched."""
    check_vocabulary("notes", "anything at all, even garbage words", where="test")
    check_vocabulary("status", "", where="test")


def test_check_vocabulary_refuses_lane_lane_verdict_and_agent_kind_too() -> None:
    with pytest.raises(VocabularyRefusal, match="not in the allowed set for lane"):
        check_vocabulary("lane", "not-a-real-lane", where="test")
    with pytest.raises(VocabularyRefusal, match="not in the allowed set for signal_agent_kind"):
        check_vocabulary("signal_agent_kind", "not-a-real-agent", where="test")
    with pytest.raises(VocabularyRefusal, match="not in the allowed set for category_relation"):
        check_vocabulary("category_relation", "not-a-real-relation", where="test")


@pytest.mark.parametrize("value", [1, 12.5, True, ["send"], {"x": 1}])
def test_check_vocabulary_refuses_a_non_string_value_cleanly(value: object) -> None:
    """A verification-audit red-team finding (2026-09-28): `(value or "").strip()` crashed
    with a raw AttributeError on any non-string truthy value, bypassing the PRD's error-copy
    contract and the CLI's `except ValueError` handler. Must refuse, never crash."""
    with pytest.raises(VocabularyRefusal, match="not in the allowed set for verdict"):
        check_vocabulary("verdict", value, where="test")


def test_check_vocabulary_still_accepts_none_as_blank() -> None:
    """`None` (a field genuinely absent from a dict.get() default) stays a safe no-op —
    only a non-blank NON-STRING value is refused, not the absence sentinel itself."""
    check_vocabulary("verdict", None, where="test")
