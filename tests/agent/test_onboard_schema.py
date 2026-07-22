# tests/agent/test_onboard_schema.py
"""Contract tests for schemas/profile-draft.schema.json.

Uses the vendored stdlib-only minijsonschema validator — no network, no extra deps.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_PATH = REPO_ROOT / "schemas" / "profile-draft.schema.json"

# Minimal valid ProfileDraft — every required field present, simplest possible values.
MINIMAL_VALID = {
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
            "source_pages": ["https://example.com/deploy"],
            "references": [
                {
                    "url": "https://example.com/deploy",
                    "title": "AcmeDeploy docs",
                    "summary": "Overview.",
                }
            ],
        }
    ],
    "brand": {"palette": ["#000000", "#FFFFFF"], "assets_note": "Monochrome."},
    "gaps": ["No pricing page found."],
}


@pytest.fixture()
def schema():
    raw = SCHEMA_PATH.read_text()
    return json.loads(raw)


def test_schema_file_exists():
    assert SCHEMA_PATH.exists(), f"schema not found at {SCHEMA_PATH}"


def test_minimal_valid_passes(schema):
    from tests.contracts.minijsonschema import validate

    errs = validate(MINIMAL_VALID, schema)
    assert errs == [], f"Unexpected errors: {errs}"


def test_missing_required_company_fails(schema):
    from tests.contracts.minijsonschema import validate

    doc = {k: v for k, v in MINIMAL_VALID.items() if k != "company"}
    errs = validate(doc, schema)
    assert errs, "Expected errors for missing 'company'"


def test_invalid_confidence_fails(schema):
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "confidence": "very-high"}
    errs = validate(doc, schema)
    assert errs, "Expected errors for invalid confidence enum"


def test_product_missing_use_cases_fails(schema):
    from tests.contracts.minijsonschema import validate

    bad_product = {k: v for k, v in MINIMAL_VALID["products"][0].items() if k != "use_cases"}
    doc = {**MINIMAL_VALID, "products": [bad_product]}
    errs = validate(doc, schema)
    assert errs, "Expected errors for product missing 'use_cases'"


def test_voice_too_few_principles_fails(schema):
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "voice": {**MINIMAL_VALID["voice"], "principles": ["Just one."]}}
    errs = validate(doc, schema)
    assert errs, "Expected errors for voice with < 3 principles"


def test_products_empty_array_fails(schema):
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "products": []}
    errs = validate(doc, schema)
    assert errs, "Expected errors for empty products array"


def test_invalid_source_type_fails(schema):
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "source": {"type": "ftp", "value": "ftp://example.com"}}
    errs = validate(doc, schema)
    assert errs, "Expected errors for invalid source type"


def test_settings_connectors_valid_passes(schema):
    from tests.contracts.minijsonschema import validate

    doc = {
        **MINIMAL_VALID,
        "settings": {
            "connectors": [
                {
                    "name": "rocketreach",
                    "plan": "Ultimate + Phone",
                    "billing": "subscription",
                    "monthly_allowance": {"unit": "premium_lookups", "limit": 1000},
                    "features": ["intent", "phone", "signal_search", "api_access"],
                    "notes": "CONFIRM exact monthly allowance against live account settings.",
                },
                {"name": "vibe_prospecting", "billing": "credit_pack"},
            ]
        },
    }
    errs = validate(doc, schema)
    assert errs == [], f"Unexpected errors: {errs}"


def test_settings_connectors_missing_name_fails(schema):
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "settings": {"connectors": [{"plan": "Ultimate"}]}}
    errs = validate(doc, schema)
    assert errs, "Expected errors for connector entry missing required 'name'"


def test_settings_connectors_invalid_billing_enum_fails(schema):
    from tests.contracts.minijsonschema import validate

    doc = {
        **MINIMAL_VALID,
        "settings": {"connectors": [{"name": "rocketreach", "billing": "unlimited"}]},
    }
    errs = validate(doc, schema)
    assert errs, "Expected errors for invalid billing enum value"


def test_settings_connectors_additional_property_fails(schema):
    from tests.contracts.minijsonschema import validate

    doc = {
        **MINIMAL_VALID,
        "settings": {"connectors": [{"name": "rocketreach", "api_key": "should-never-be-here"}]},
    }
    errs = validate(doc, schema)
    assert errs, "Expected errors for a connector entry carrying a disallowed key (e.g. a secret)"
