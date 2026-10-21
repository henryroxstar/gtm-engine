"""Unit tests for onboarding render() with offer_ladder in agent/onboard/render.py."""

from __future__ import annotations

from agent.onboard.render import render

SAMPLE_DRAFT_WITH_OFFER = {
    "source": {"type": "url", "value": "https://example.com", "crawled_pages": 4},
    "confidence": "high",
    "company": {
        "name": "Widget AI",
        "slug": "widget-ai",
        "brand_name": "Widget",
        "description": "AI agent tools for enterprise teams.",
        "markets": ["United States"],
        "social_handle": "https://linkedin.com/company/widget",
    },
    "voice": {
        "tone": "Direct and crisp.",
        "principles": ["Be concise", "No fluff", "Show data"],
        "ban_list": ["synergy", "game-changing"],
        "examples": ["We make agents safe."],
    },
    "icp": {
        "personas": [
            {
                "title": "Head of AI",
                "pain_points": ["uncontrolled agents"],
                "goals": ["safe production rollout"],
            }
        ],
        "verticals": ["Enterprise SaaS"],
        "company_size": "100-1000",
    },
    "competitors": [{"name": "Rival", "differentiator": "We are faster."}],
    "pillars": ["Agent Security", "Enterprise Governance"],
    "products": [
        {
            "name": "Widget Gateway",
            "slug": "widget-gateway",
            "flagship": True,
            "description": "Inline security gateway.",
            "technical_notes": "Proxy architecture.",
            "capabilities": ["prospect"],
            "use_cases": ["Agent traffic inspection"],
            "references": [
                {
                    "url": "https://example.com/gw",
                    "title": "GW",
                }
            ],
        }
    ],
    "brand": {"palette": ["#111111", "#FFFFFF"]},
    "gaps": [],
    "offer_ladder": {
        "dream_outcome": "Make your agents enterprise-ready in an afternoon—with zero code changes.",
        "scope_boundaries": ["Never promise 100% guarantee", "Proxy only"],
        "touch1_offers": [
            {
                "name": "1:1 Indicative Security Scorecard",
                "deliverable": "1-page scorecard",
                "target_persona": "Head of AI",
            },
            {
                "name": "Blueprint Alliance Readiness Brief",
                "deliverable": "Readiness brief PDF",
                "target_persona": "Head of AI",
            },
        ],
        "touch2_stepdown": "60-day free trial of hosted gateway",
        "internal_quota_bans": ["5,000 MAUs", "50 agents"],
    },
}


def test_render_with_offer_ladder_produces_custom_gift_artifacts() -> None:
    files = render(SAMPLE_DRAFT_WITH_OFFER)

    assert "knowledge/gift-artifacts.txt" in files
    gift_content = files["knowledge/gift-artifacts.txt"]

    # Verify custom offer artifacts are rendered
    assert "1:1 Indicative Security Scorecard" in gift_content
    assert "Blueprint Alliance Readiness Brief" in gift_content
    assert "60-day free trial of hosted gateway" in gift_content

    # Verify generic template nouns are NOT present when offer_ladder is supplied
    assert "one-pager\n" not in gift_content
    assert "teardown\n" not in gift_content
    assert "short read\n" not in gift_content

    # Verify voice-bans.txt includes internal_quota_bans
    assert "knowledge/voice-bans.txt" in files
    bans_content = files["knowledge/voice-bans.txt"]
    assert "synergy" in bans_content
    assert "5,000 MAUs" in bans_content
    assert "50 agents" in bans_content
