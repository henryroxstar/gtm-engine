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


# ── competitor bucket (tiering) ────────────────────────────────────────────────


def test_competitor_bucket_valid_passes(schema):
    from tests.contracts.minijsonschema import validate

    doc = {
        **MINIMAL_VALID,
        "competitors": [
            {"name": "RivalCo", "differentiator": "We are faster.", "bucket": "Direct rivals"}
        ],
    }
    assert validate(doc, schema) == []


def test_competitor_bucket_optional(schema):
    """bucket is optional — a competitor with only name+differentiator still validates."""
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "competitors": [{"name": "RivalCo", "differentiator": "We differ."}]}
    assert validate(doc, schema) == []


# ── buyer_journey ──────────────────────────────────────────────────────────────

_BUYER_JOURNEY = {
    "primary_persona": "VP Engineering",
    "triggers": [
        {
            "event": "New funding round",
            "activates": "VP Engineering",
            "predicts": "budget to modernize",
            "detect_via": "press → RocketReach",
        }
    ],
    "stages": [
        {
            "stage": "Problem-aware",
            "buyer_question": "Could deploys be faster?",
            "angle": "reframe the cost",
            "proof": "benchmark",
            "objection": "it's just how it is",
            "pillar": "DevOps",
        }
    ],
    "persona_deltas": [
        {"persona": "Head of Ops", "notes": "Enters later; leads with reliability."}
    ],
    "operator_confirm": ["Real sales-cycle length?", "Who signs?"],
}


def test_buyer_journey_valid_passes(schema):
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "buyer_journey": _BUYER_JOURNEY}
    assert validate(doc, schema) == []


def test_buyer_journey_optional(schema):
    """buyer_journey is optional — the minimal draft (without it) still validates."""
    from tests.contracts.minijsonschema import validate

    assert validate(MINIMAL_VALID, schema) == []


def test_buyer_journey_trigger_missing_required_fails(schema):
    from tests.contracts.minijsonschema import validate

    bad = {**_BUYER_JOURNEY, "triggers": [{"activates": "VP Engineering"}]}  # no 'event'
    doc = {**MINIMAL_VALID, "buyer_journey": bad}
    assert validate(doc, schema), "Expected errors for a trigger missing required 'event'"


def test_buyer_journey_additional_property_fails(schema):
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "buyer_journey": {**_BUYER_JOURNEY, "unexpected": "x"}}
    assert validate(doc, schema), "Expected errors for an unknown buyer_journey key"


# ── the schema reaches the code path that renders (issue #267) ────────────────
#
# Every test above validates a hand-written fixture against the schema file. That is exactly the
# coverage staging had on 2026-09-17 when every real onboarding 500'd: `_parse_and_validate_draft`
# never read this schema, so a model that returned `pillars` as a list of objects sailed through
# to `render_profile.py`, where `", ".join(pillars)` raised `TypeError`. A schema only the test
# tree checks is not enforcement. These tests pin the wiring, not the schema.


def _draft_json(**overrides) -> str:
    return json.dumps({**MINIMAL_VALID, **overrides})


def test_the_extractor_coerces_the_draft_shape_that_crashed_staging():
    """`pillars` as objects — the shape live models emit — is coerced to strings and accepted."""
    from agent.onboard.extract import _parse_and_validate_draft

    crashing = [
        {"name": "DevOps", "description": "Shipping faster."},
        {"name": "Platform engineering", "description": "Paved roads."},
    ]
    draft = _parse_and_validate_draft(_draft_json(pillars=crashing))
    assert draft["pillars"] == ["DevOps", "Platform engineering"]


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("pillars", "not-a-list"),
        ("gaps", [{"field": "pricing", "why": "no pricing page"}]),
        ("company", {**MINIMAL_VALID["company"], "markets": [{"region": "United States"}]}),
        (
            "voice",
            {**MINIMAL_VALID["voice"], "principles": [{"p": "Be brief."}, {"p": "Use data."}]},
        ),
        ("voice", {**MINIMAL_VALID["voice"], "ban_list": [{"word": "synergy"}]}),
        ("voice", {**MINIMAL_VALID["voice"], "examples": [{"text": "A sample post."}]}),
        (
            "products",
            [{**MINIMAL_VALID["products"][0], "capabilities": [{"name": "prospect"}]}],
        ),
    ],
)
def test_the_extractor_refuses_objects_in_every_list_the_renderer_joins(field, bad):
    """`pillars` is coerced; these are the rest of `", ".join(...)`'s inputs in render_profile.py.
    The schema check covers all of them."""
    from agent.onboard.errors import OnboardingExtractError
    from agent.onboard.extract import _parse_and_validate_draft

    with pytest.raises(OnboardingExtractError):
        _parse_and_validate_draft(_draft_json(**{field: bad}))


def test_a_well_formed_draft_still_passes_the_extractor_unchanged():
    """Negative control: the check discriminates. Without this, a validator that refused
    everything would pass every test above (§R18)."""
    from agent.onboard.extract import _parse_and_validate_draft

    assert _parse_and_validate_draft(_draft_json()) == MINIMAL_VALID


def test_a_well_formed_draft_survives_a_markdown_fence():
    from agent.onboard.extract import _parse_and_validate_draft

    assert _parse_and_validate_draft(f"```json\n{_draft_json()}\n```") == MINIMAL_VALID


# ── the contract admits what the prompt asks for (issue #267) ─────────────────
#
# body_template.md's `### brand` section (commit c14698de, 2026-09-03) asks for ten fields and
# defines `palette` as colour roles. That commit never touched this schema, so for two weeks the
# contract refused the data the prompt requested — invisible while nothing validated, and a hard
# 502 the moment something did. These tests are the pairing the drift got past.


def test_the_brand_block_the_prompt_asks_for_is_accepted(schema):
    from tests.contracts.minijsonschema import validate

    doc = {
        **MINIMAL_VALID,
        "brand": {
            "brand_document": None,
            "palette": {"canvas": "#0B0B0C", "ink": "#F4F4F5", "primary": "#2F6FEB"},
            "gradients": {"dusk": ["#2F6FEB", "#7C3AED"]},
            "sub_brand_map": {"acme-deploy": "none, confirmed"},
            "typography": {"display": "Unset", "body": "Unset"},
            "logo_files": [],
            "mode_default": "dark",
            "imagery": {"house_look": "Plain workshop photography.", "hard_rejects": []},
            "accessibility": {"contrast": "AA"},
            "pronunciation": "AK-mee",
            "assets_note": "No asset pack on the site.",
        },
    }
    assert validate(doc, schema) == [], validate(doc, schema)


def test_a_palette_of_hex_codes_is_still_accepted(schema):
    """The older sampled-from-the-site form. Both shapes render; neither may be refused."""
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "brand": {"palette": ["#000000", "#FFFFFF"]}}
    assert validate(doc, schema) == []


def test_a_brand_with_nothing_obtainable_is_accepted(schema):
    """The prompt's standing rule is to leave what could not be obtained UNSET, not to invent it."""
    from tests.contracts.minijsonschema import validate

    assert validate({**MINIMAL_VALID, "brand": {}}, schema) == []
    assert validate({**MINIMAL_VALID, "brand": {"palette": None}}, schema) == []


def test_an_unrequested_brand_key_is_still_refused(schema):
    """Negative control: the brand block was widened to what the prompt asks for, not opened."""
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "brand": {"palette": [], "api_key": "should-never-be-here"}}
    assert validate(doc, schema), "Expected an error for a brand key nobody asked for"


@pytest.mark.parametrize(
    "delta",
    [
        {"persona": "VP Eng", "notes": "Enters at Solution-aware."},
        {"persona": "VP Eng", "entry_stage": "Solution-aware", "what_leads": "Proof."},
        {"title": "VP Eng", "enters_at": "Solution-aware", "lead": "Proof."},
    ],
)
def test_persona_deltas_accept_the_field_names_a_model_actually_picks(schema, delta):
    """The prompt names four concepts and no field names, so live runs invented two different
    sets. A schema cannot enforce names the prompt never gave; render_icp.py reads `persona` and
    `notes` with defaults and degrades rather than crashing on the rest."""
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "buyer_journey": {"persona_deltas": [delta]}}
    assert validate(doc, schema) == [], validate(doc, schema)


def test_a_market_the_source_never_stated_is_not_forced(schema):
    from tests.contracts.minijsonschema import validate

    doc = {
        **MINIMAL_VALID,
        "company": {**MINIMAL_VALID["company"], "markets": [], "social_handle": None},
    }
    assert validate(doc, schema) == []


def test_pasted_text_is_a_source_type(schema):
    """Both live runs returned 'paste'; `_source_label` already treats it as `text` does."""
    from tests.contracts.minijsonschema import validate

    doc = {**MINIMAL_VALID, "source": {"type": "paste", "value": "About us..."}}
    assert validate(doc, schema) == []
