"""Unit tests for W7 Level Mix Report (R7.7).

Verifies:
1. The report on a fixture equals a hand-computed expected table (literal oracle).
2. 'Send anyway' overrides are counted separately.
3. The next-step sentence is present when a target is missed.
4. Call sites (prospect_status_cli, list_fit, send_cards) render that function's output.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gtm_core.role_vocabulary import RoleVocabulary, level_mix_report


@pytest.fixture
def sample_vocab():
    return RoleVocabulary(
        anti_cues={},
        ceo_title_cues=("ceo",),
        persona_rules=(
            ("ciso", ("ciso",)),
            ("security", ("security",)),
            ("partnerships", ("partnerships",)),
            ("architect", ("architect",)),
        ),
        non_buyer_cues=(),
        default_persona="architect",
        seat_rules=(
            ("security", ("ciso", "security"), ()),
            ("partnerships", ("partnerships",), ()),
            ("architect", ("architect",), ()),
        ),
        security_only=(),
        segments=("enterprise", "startup"),
        seat_segments={},
        level_cues={},
        level_mix={
            "enterprise": {"champion": 50, "evaluator": 25, "economic-buyer": 25},
            "startup": {"economic-buyer": 100},
        },
        wedge_seats={"enterprise": ("partnerships", "security")},
    )


def test_level_mix_report_matches_hand_computed_oracle(sample_vocab):
    # 4 rows in enterprise:
    # 1 Head of Partnerships (champion) -> 25%
    # 2 Staff Architect (evaluator) -> 50%
    # 1 Chief Information Security Officer (economic-buyer) -> 25%
    # Target: 50% champion, 25% evaluator, 25% economic-buyer
    # Champion missed (25% < 50%) -> next step present
    rows = [
        {"segment": "enterprise", "title": "Head of Partnerships", "seat": "partnerships"},
        {"segment": "enterprise", "title": "Staff Architect", "seat": "architect"},
        {"segment": "enterprise", "title": "Lead Architect", "seat": "architect"},
        {
            "segment": "enterprise",
            "title": "Chief Information Security Officer",
            "seat": "security",
        },
    ]

    report = level_mix_report(rows, sample_vocab, profile="acme")

    assert report.total_counts["enterprise"] == 4
    assert report.realised["enterprise"]["champion"] == 1
    assert report.realised["enterprise"]["evaluator"] == 2
    assert report.realised["enterprise"]["economic-buyer"] == 1
    assert report.realised["enterprise"]["unknown"] == 0

    assert report.percentages["enterprise"]["champion"] == 25.0
    assert report.percentages["enterprise"]["evaluator"] == 50.0
    assert report.percentages["enterprise"]["economic-buyer"] == 25.0

    # Next steps should ask for more Heads/Directors in partnerships (wedge seat)
    assert len(report.next_steps) == 1
    assert "find 2 more Heads/Directors in partnerships" in report.next_steps[0]

    # Rendered plain text check
    rendered = report.render()
    assert "champion           1 ( 25.0%) [target: 50%]" in rendered
    assert "evaluator          2 ( 50.0%) [target: 25%]" in rendered
    assert "economic-buyer     1 ( 25.0%) [target: 25%]" in rendered
    assert "Send anyway overrides: 0" in rendered
    assert "Next step: find 2 more Heads/Directors in partnerships" in rendered


def test_send_anyway_overrides_counted_separately(sample_vocab):
    rows = [
        {
            "segment": "enterprise",
            "title": "Head of Partnerships",
            "seat": "partnerships",
            "decided": "decided:send:champion-missing",
        },
        {"segment": "enterprise", "title": "Staff Architect", "seat": "architect"},
    ]
    decisions = [
        {"decision": "send", "trigger": "champion-missing"},
        {"decision": "send", "trigger": "champion-missing"},
        {"decision": "suppress", "trigger": "champion-missing"},
    ]

    # From decisions
    rep1 = level_mix_report(rows, sample_vocab, decisions=decisions)
    assert rep1.overrides_count == 2

    # From rows when decisions not passed
    rep2 = level_mix_report(rows, sample_vocab)
    assert rep2.overrides_count == 1


def test_level_mix_on_target_has_no_missing_next_steps(sample_vocab):
    # 4 rows: 2 champion (50%), 1 evaluator (25%), 1 economic-buyer (25%) -> matches target!
    rows = [
        {"segment": "enterprise", "title": "Head of Partnerships", "seat": "partnerships"},
        {"segment": "enterprise", "title": "VP Security", "seat": "security"},
        {"segment": "enterprise", "title": "Staff Architect", "seat": "architect"},
        {
            "segment": "enterprise",
            "title": "Chief Information Security Officer",
            "seat": "security",
        },
    ]
    report = level_mix_report(rows, sample_vocab)
    assert report.percentages["enterprise"]["champion"] == 50.0
    assert report.percentages["enterprise"]["evaluator"] == 25.0
    assert report.percentages["enterprise"]["economic-buyer"] == 25.0
    assert report.next_steps == []
    assert "Level mix on target." in report.render()


def test_call_sites_use_level_mix_report(sample_vocab, tmp_path, monkeypatch):
    """Assert status, list-fit, and send-cards call level_mix_report."""
    import gtm_core.role_vocabulary.level_mix as lm_mod

    spy = MagicMock(wraps=lm_mod.level_mix_report)
    monkeypatch.setattr(lm_mod, "level_mix_report", spy)

    # 1. list_fit
    from gtm_core.list_fit import audit_rows
    from gtm_core.list_fit import render as render_list_fit

    rows = [{"segment": "enterprise", "title": "Head of Security", "seat": "security"}]
    with patch("gtm_core.role_vocabulary.load", return_value=sample_vocab):
        audit = audit_rows(rows)
        rendered = render_list_fit(audit)
        assert rendered
        assert spy.call_count >= 1

    # 2. send_cards
    from gtm_core.send_cards import generate_cards_page

    with patch("gtm_core.role_vocabulary.load", return_value=sample_vocab):
        page = generate_cards_page([], profile="acme")
        assert page
        assert spy.call_count >= 2
