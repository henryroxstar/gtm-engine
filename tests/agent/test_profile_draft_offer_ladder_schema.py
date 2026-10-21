"""Contract tests for offer_ladder in schemas/profile-draft.schema.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.contracts.minijsonschema import validate

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "profile-draft.schema.json"


@pytest.fixture()
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def base_valid() -> dict:
    return {
        "source": {"type": "url", "value": "https://example.com", "crawled_pages": 8},
        "confidence": "medium",
        "company": {
            "name": "Acme Corp",
            "slug": "acme-corp",
            "brand_name": "Acme",
            "description": "We make enterprise widgets.",
            "markets": ["United States"],
            "social_handle": "https://linkedin.com/company/acme",
        },
        "voice": {
            "tone": "Direct and technical.",
            "principles": ["Be brief.", "Use data.", "No jargon."],
            "ban_list": ["synergy"],
            "examples": [],
        },
        "icp": {
            "personas": [
                {
                    "title": "VP Engineering",
                    "pain_points": ["slow deploys"],
                    "goals": ["fast releases"],
                }
            ],
            "verticals": ["SaaS"],
            "company_size": "50-500",
        },
        "competitors": [{"name": "RivalCo", "differentiator": "We are faster."}],
        "pillars": ["DevOps", "Platform engineering"],
        "products": [
            {
                "name": "AcmeDeploy",
                "slug": "acme-deploy",
                "flagship": True,
                "description": "CI/CD platform for fast-moving teams.",
                "technical_notes": "Kubernetes-native.",
                "capabilities": ["prospect"],
                "use_cases": ["Zero-downtime deploys"],
                "references": [
                    {
                        "url": "https://example.com/deploy",
                        "title": "Docs",
                    }
                ],
            }
        ],
        "brand": {"palette": ["#000000", "#FFFFFF"]},
        "gaps": [],
    }


def test_draft_without_offer_ladder_passes_backwards_compatibility(schema, base_valid) -> None:
    # Assert omission is allowed
    assert "offer_ladder" not in base_valid
    errs = validate(base_valid, schema)
    assert errs == [], f"Unexpected schema errors: {errs}"


def test_draft_with_valid_offer_ladder_passes(schema, base_valid) -> None:
    doc = dict(base_valid)
    doc["offer_ladder"] = {
        "dream_outcome": "Make your agents enterprise-ready in an afternoon—with zero code changes.",
        "scope_boundaries": ["Never promise 100% guarantee", "Proxy-only boundary"],
        "touch1_offers": [
            {
                "name": "1:1 Indicative Security Scorecard",
                "deliverable": "1-page scorecard",
                "target_persona": "VP Engineering",
            },
            {
                "name": "Blueprint Alliance Readiness Brief",
                "deliverable": "Readiness brief PDF",
                "target_persona": "CTO",
            },
        ],
        "touch2_stepdown": "60-day free trial of hosted gateway",
        "internal_quota_bans": ["5,000 MAUs", "50 agents", "8 agents per user"],
    }
    errs = validate(doc, schema)
    assert errs == [], f"Unexpected schema errors: {errs}"


def test_draft_with_invalid_offer_ladder_fails(schema, base_valid) -> None:
    doc = dict(base_valid)
    # touch1_offers must be an array of objects
    doc["offer_ladder"] = {
        "dream_outcome": 12345,  # must be string
        "touch1_offers": "not an array",
    }
    errs = validate(doc, schema)
    assert errs, "Expected validation errors for malformed offer_ladder"
