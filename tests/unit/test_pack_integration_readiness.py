"""Unit tests for integration readiness checking (agent.readiness).

Validates that check_readiness evaluates required/optional integrations against
configured_integrations, emitting appropriate GREEN, RED, or YELLOW statuses and
formatting human-actionable guidance using display names.
"""

from __future__ import annotations

from pathlib import Path

from agent.readiness import GREEN, RED, YELLOW, check_readiness
from gtm_core.packs.loader import (
    PackInputIntegration,
    PackInputs,
)


def _write_profile(
    root: Path,
    name: str,
    profile_md: str = "brand_name: Acme\n",
    knowledge: dict[str, str] | None = None,
) -> None:
    pdir = root / name
    (pdir / "knowledge").mkdir(parents=True, exist_ok=True)
    (pdir / "PROFILE.md").write_text(profile_md, encoding="utf-8")
    for topic, content in (knowledge or {}).items():
        (pdir / "knowledge" / f"{topic}.md").write_text(content, encoding="utf-8")


def test_readiness_green_when_required_integration_is_configured(tmp_path):
    _write_profile(tmp_path, "acme")
    inputs = PackInputs(
        integrations=(PackInputIntegration(provider="saleshandy", required=True),),
    )
    report = check_readiness(
        tmp_path,
        "acme",
        inputs,
        configured_integrations={"saleshandy"},
    )
    assert not report.blocked
    assert not report.degraded
    assert len(report.items) == 1
    item = report.items[0]
    assert item.kind == "integration"
    assert item.name == "saleshandy"
    assert item.status == GREEN
    assert item.detail == ""


def test_readiness_blocks_when_required_integration_is_missing(tmp_path):
    _write_profile(tmp_path, "acme")
    inputs = PackInputs(
        integrations=(PackInputIntegration(provider="saleshandy", required=True),),
    )
    report = check_readiness(
        tmp_path,
        "acme",
        inputs,
        configured_integrations=set(),
    )
    assert report.blocked
    assert len(report.items) == 1
    item = report.items[0]
    assert item.kind == "integration"
    assert item.name == "saleshandy"
    assert item.status == RED
    assert item.required is True
    assert "Saleshandy API key is not configured" in item.detail
    assert "Settings > Integrations" in item.detail


def test_readiness_blocks_when_configured_integrations_is_none(tmp_path):
    _write_profile(tmp_path, "acme")
    inputs = PackInputs(
        integrations=(PackInputIntegration(provider="saleshandy", required=True),),
    )
    report = check_readiness(
        tmp_path,
        "acme",
        inputs,
        configured_integrations=None,
    )
    assert report.blocked
    assert report.items[0].status == RED


def test_readiness_degrades_when_optional_integration_is_missing(tmp_path):
    _write_profile(tmp_path, "acme")
    inputs = PackInputs(
        integrations=(PackInputIntegration(provider="apollo", required=False),),
    )
    report = check_readiness(
        tmp_path,
        "acme",
        inputs,
        configured_integrations=set(),
    )
    assert not report.blocked
    assert report.degraded
    assert len(report.items) == 1
    item = report.items[0]
    assert item.kind == "integration"
    assert item.name == "apollo"
    assert item.status == YELLOW
    assert item.required is False
    assert "Apollo API key is not configured" in item.detail


def test_readiness_supports_dict_for_configured_integrations(tmp_path):
    _write_profile(tmp_path, "acme")
    inputs = PackInputs(
        integrations=(
            PackInputIntegration(provider="saleshandy", required=True),
            PackInputIntegration(provider="rocketreach", required=False),
        ),
    )
    report = check_readiness(
        tmp_path,
        "acme",
        inputs,
        configured_integrations={"saleshandy": {"status": "configured"}},
    )
    assert not report.blocked
    assert report.degraded  # rocketreach is optional and missing
    items_by_name = {i.name: i for i in report.items}
    assert items_by_name["saleshandy"].status == GREEN
    assert items_by_name["rocketreach"].status == YELLOW


def test_readiness_preserves_display_names_and_falls_back_cleanly(tmp_path):
    _write_profile(tmp_path, "acme")
    inputs = PackInputs(
        integrations=(
            PackInputIntegration(provider="syften", required=True),
            PackInputIntegration(provider="custom_service", required=True),
        ),
    )
    report = check_readiness(tmp_path, "acme", inputs, configured_integrations=set())
    items_by_name = {i.name: i for i in report.items}
    assert "Syften API key is not configured" in items_by_name["syften"].detail
    assert "Custom_service API key is not configured" in items_by_name["custom_service"].detail
