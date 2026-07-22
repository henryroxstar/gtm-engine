# tests/agent/test_onboard_settings.py
"""Tests that _render_profile_md derives PROFILE.md config from draft["settings"].

Covers Task 2 of docs/prds/2026-07-19-onboarding-front-door.md: settings-driven
rendering (title, segment_mix, budget, per_run_cap, language) instead of the old
hardcoded Founder / 100% startup / $100 literals.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from agent.config import Config
from agent.onboard import ingest, render, stage


def _base_draft(settings=None):
    d = {
        "source": {"type": "url", "value": "https://acme.example"},
        "confidence": "high",
        "company": {
            "name": "Acme Inc",
            "slug": "acme",
            "brand_name": "Acme",
            "description": "We build widgets for teams.",
            "markets": ["United States"],
            "social_handle": "@acme",
        },
        "voice": {
            "tone": "Direct and concrete.",
            "principles": ["a", "b", "c"],
            "ban_list": ["synergy"],
            "examples": ["We ship."],
        },
        "icp": {
            "personas": [{"title": "Head of Ops", "pain_points": ["slow"], "goals": ["fast"]}],
            "verticals": ["SaaS"],
            "company_size": "50-500",
        },
        "competitors": [],
        "pillars": ["p1", "p2"],
        "products": [
            {
                "name": "Widget",
                "slug": "widget",
                "description": "A great widget tool.",
                "capabilities": ["prospect"],
                "use_cases": ["u1"],
                "references": [{"url": "https://x", "title": "X"}],
                "flagship": True,
            }
        ],
        "brand": {"palette": ["#111111"]},
        "gaps": [],
    }
    if settings is not None:
        d["settings"] = settings
    return d


def test_settings_drive_profile_md():
    files = render(
        _base_draft(
            {
                "title": "CEO",
                "email_signature": "Dana",
                "segment_mix": "50% startup / 50% enterprise",
                "monthly_tool_budget_usd": 250,
                "per_run_cap_usd": 25,
                "language": "English",
            }
        )
    )
    p = files["PROFILE.md"]
    assert "title:           CEO" in p
    assert "50% startup / 50% enterprise" in p
    assert "monthly_tool_budget_usd: 250" in p
    assert "per_run_cap_usd:         25" in p
    assert "100% startup" not in p  # old hardcode is gone
    assert "title:           Founder" not in p


def test_settings_defaults_when_absent():
    p = render(_base_draft())["PROFILE.md"]  # no settings key at all
    assert "title:           Founder" in p  # sensible default, not a crash
    assert "70% startup / 30% enterprise" in p  # matches setup skill default
    assert "monthly_tool_budget_usd: 50" in p


def test_connectors_render_when_present():
    p = render(
        _base_draft(
            {
                "connectors": [
                    {
                        "name": "rocketreach",
                        "plan": "Ultimate + Phone",
                        "billing": "subscription",
                        "monthly_allowance": {"unit": "premium_lookups", "limit": 1000},
                        "features": ["intent", "phone", "signal_search"],
                        "notes": "CONFIRM exact allowance against live account.",
                    }
                ]
            }
        )
    )["PROFILE.md"]
    assert "Connector plans & entitlements" in p
    assert "rocketreach:" in p
    assert "plan:              Ultimate + Phone" in p
    assert "billing:           subscription" in p
    assert "monthly_allowance: 1000 premium_lookups/mo" in p
    assert "features:          [intent, phone, signal_search]" in p
    assert "CONFIRM exact allowance" in p


def test_connectors_absent_renders_no_placeholder():
    """No settings.connectors at all -> plain-English guidance, never a <...> placeholder
    (the global guard in test_onboard_render_full.py fails on any literal <...> in output)."""
    p = render(_base_draft())["PROFILE.md"]
    assert "Connector plans & entitlements" in p
    assert "No connector plans captured yet" in p
    assert "<" not in p.split("## Connector plans")[1].split("## Output location")[0]


def test_stage_meta_has_timestamps(cfg_isolated):
    files = render(_base_draft())
    draft_id, staged = stage("acme", files, cfg_isolated, company_name="Acme Inc")
    meta = json.loads((staged / ".onboard-meta.json").read_text())
    assert "created_at" in meta and "updated_at" in meta
    datetime.fromisoformat(meta["created_at"])  # parses as ISO-8601
    assert meta["step"] == "staged"


def test_ingest_empty_text_is_rejected():
    with pytest.raises(ValueError, match="no readable text"):
        ingest("   ", "text", Config.from_env())
