import datetime
from unittest.mock import patch

from gtm_core.lanes.context import RouterContext
from gtm_core.lanes.router import route
from gtm_core.role_vocabulary import RoleVocabulary


def _row(email, title, company_domain, segment="enterprise"):
    return {
        "email": email,
        "title": title,
        "company_domain": company_domain,
        "company": company_domain.split(".")[0],
        "segment": segment,
        "verdict": "send",
        "signal_observed": "2026-09-01",
        "why_now": "some reason",
    }


def test_champion_missing_hold_enterprise_only():
    ctx = RouterContext(profile="test", as_of=datetime.date(2026, 9, 3))

    # Mock RoleVocabulary
    vocab = RoleVocabulary(
        anti_cues={},
        ceo_title_cues=(),
        persona_rules=(("security", ("security",)), ("architect", ("architect",))),
        non_buyer_cues=(),
        default_persona="",
        seat_rules=(("security", ("security",), ()), ("architect", ("architect",), ())),
        security_only=(),
        segments=("enterprise", "startup"),
        seat_segments={},
        level_cues={"champion": ("vp",)},
        level_mix={},
        wedge_seats={"enterprise": ("security",)},
    )

    rows = [
        _row("a@ent.example", "Architect", "ent.example", "enterprise"),  # evaluator, no champion
        _row(
            "b@startup.example", "Architect", "startup.example", "startup"
        ),  # startup, should not trigger
    ]

    with patch("gtm_core.role_vocabulary.load", return_value=vocab):
        # We also need to mock `row_signal_freshness` or similar so they route to generic/personalised?
        # A row with just signal_observed and why_now routes to generic if judge is not present.
        result = route(rows, [], ctx)

        ent = next(r for r in result.routed if r.email == "a@ent.example")
        start = next(r for r in result.routed if r.email == "b@startup.example")

        assert ent.lane == "hold"
        assert ent.trigger == "champion-missing"

        assert start.lane == "generic"  # not held because not enterprise


def test_champion_missing_satisfied():
    ctx = RouterContext(profile="test", as_of=datetime.date(2026, 9, 3))

    vocab = RoleVocabulary(
        anti_cues={},
        ceo_title_cues=(),
        persona_rules=(("security", ("security",)), ("architect", ("architect",))),
        non_buyer_cues=(),
        default_persona="",
        seat_rules=(("security", ("security",), ()), ("architect", ("architect",), ())),
        security_only=(),
        segments=("enterprise",),
        seat_segments={},
        level_cues={"champion": ("vp",)},
        level_mix={},
        wedge_seats={"enterprise": ("security",)},
    )

    rows = [
        _row("a@ent.example", "Architect", "ent.example", "enterprise"),
        _row("b@ent.example", "VP Security", "ent.example", "enterprise"),  # Champion!
    ]

    with patch("gtm_core.role_vocabulary.load", return_value=vocab):
        result = route(rows, [], ctx)

        ent_a = next(r for r in result.routed if r.email == "a@ent.example")
        ent_b = next(r for r in result.routed if r.email == "b@ent.example")

        assert ent_a.lane == "generic"  # Or whatever send lane
        assert ent_b.trigger == "duplicate-contact"
