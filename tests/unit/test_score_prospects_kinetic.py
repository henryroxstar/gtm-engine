from pathlib import Path

from gtm_core.score_prospects import score_and_rank_prospects, score_candidate


def test_score_candidate_kinetic_chain_heat_boost_and_tier_a():
    candidate = {
        "company": "Acme Corp",
        "domain": "acme.example",
        "segment": "enterprise",
        "base_score": 6,
        "kinetic_chain_completed": True,
        "kinetic_chain_name": "mandate",
        "why_now": "Kinetic Trigger (mandate): Regulatory Action (2026-09-01) + AI Hiring Expansion (2026-09-15)",
    }
    scored = score_candidate(candidate)
    assert scored["heat"] >= 3
    # Enterprise ceiling is 12: base 6 + 3 = 9 >= Tier-A threshold 8
    assert scored["score"] == 9
    assert scored["tier"] == "A"
    assert scored["priority"] == "high"
    assert "Kinetic Trigger" in scored["why_now"]


def test_score_candidate_kinetic_chain_capped_at_ceiling():
    candidate = {
        "company": "Acme High Base",
        "domain": "acmehigh.example",
        "segment": "startup",  # ceiling = 10
        "base_score": 9,
        "kinetic_chain_completed": True,
    }
    scored = score_candidate(candidate)
    # 9 + 3 = 12, but capped at startup ceiling 10
    assert scored["score"] == 10
    assert scored["tier"] == "A"


def test_score_candidate_kinetic_chain_false_or_missing_no_boost():
    candidate = {
        "company": "Acme Low",
        "domain": "acmelow.example",
        "segment": "startup",
        "base_score": 5,
        "kinetic_chain_completed": False,
    }
    scored = score_candidate(candidate)
    assert scored["heat"] == 0
    assert scored["tier"] == "drop"

    candidate_str = {
        "company": "Acme Low",
        "domain": "acmelow.example",
        "segment": "startup",
        "base_score": 5,
        "kinetic_chain_completed": "false",
    }
    scored_str = score_candidate(candidate_str)
    assert scored_str["heat"] == 0
    assert scored_str["tier"] == "drop"


def test_surface_agreement_ranking_and_tier_output():
    """Test Plan §4.5: Ensure heat score injected matches the Tier output in ranked finalists."""
    candidates = [
        {
            "company": "Beta Tech",
            "domain": "betatech.example",
            "segment": "enterprise",
            "base_score": 8,
            "heat": 0,
        },
        {
            "company": "Acme Kinetic",
            "domain": "acmekinetic.example",
            "segment": "enterprise",
            "base_score": 6,
            "kinetic_chain_completed": True,
            "why_now": "Kinetic Trigger (mandate): Regulatory Action (2026-09-01) + AI Hiring Expansion (2026-09-15)",
        },
    ]
    ranked = score_and_rank_prospects(candidates)
    # Both are Tier A. Acme Kinetic has heat 3, Beta Tech has heat 0.
    # Ranking sorts by: (1) Tier A, (2) heat descending.
    # Therefore Acme Kinetic MUST rank first.
    assert ranked[0]["company"] == "Acme Kinetic"
    assert ranked[0]["tier"] == "A"
    assert ranked[0]["heat"] == 3
    assert ranked[1]["company"] == "Beta Tech"
    assert ranked[1]["heat"] == 0


def test_score_candidate_low_fit_kinetic_chain_cannot_bypass_publish_threshold():
    """Critical 2: Assert base_score 0 cannot bypass publish threshold (6) even with kinetic boost."""
    candidate = {
        "company": "Acme Zero Base",
        "domain": "acmezero.example",
        "segment": "enterprise",
        "base_score": 0,
        "kinetic_chain_completed": True,
        "kinetic_chain_name": "mandate",
        "why_now": "Kinetic Trigger (mandate): Regulatory Action (2026-09-01) + AI Hiring Expansion (2026-09-15)",
    }
    scored = score_candidate(candidate)
    assert scored["score"] == 3
    assert scored["tier"] == "drop"
    assert scored["priority"] == "low"


def test_score_candidate_borderline_kinetic_chain_promotes_to_tier_a():
    """Assert base_score 3 + kinetic boost 3 reaches publish threshold 6 and qualifies for Tier A."""
    candidate = {
        "company": "Acme Borderline",
        "domain": "acmeborder.example",
        "segment": "enterprise",
        "base_score": 3,
        "kinetic_chain_completed": True,
        "kinetic_chain_name": "mandate",
    }
    scored = score_candidate(candidate)
    assert scored["score"] == 6
    assert scored["tier"] == "A"
    assert scored["priority"] == "high"


def test_score_prospects_file_line_budget_strictly_guarded():
    """Verify gtm_core.score_prospects stays comfortably under 500 lines (§R10)."""
    p = Path("gtm_core/score_prospects.py")
    lines = len(p.read_text(encoding="utf-8").splitlines())
    assert lines <= 470, f"score_prospects.py line count {lines} exceeds safety target 470"
