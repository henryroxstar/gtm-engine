"""Tests for the competitor registry (gtm_core.voc.registry).

The registry is a per-profile TOML config. Fetched content must never write it;
updates are operator-gated via ``propose`` + ``apply``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.voc import registry as reg


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_load_missing_registry_returns_empty(tmp_path):
    registry = reg.load(profile="acme", profiles_root=tmp_path / "profiles")
    assert registry["schema"] == reg.SCHEMA_VERSION
    assert registry["competitor"] == []


def test_validate_rejects_unknown_tier(tmp_path):
    data = {"schema": 1, "competitor": [{"name": "X", "tier": "not-a-tier"}]}
    with pytest.raises(ValueError, match="unknown tier"):
        reg.validate(data)


def test_validate_rejects_duplicate_name(tmp_path):
    data = {
        "schema": 1,
        "competitor": [
            {"name": "X", "tier": "direct"},
            {"name": "X", "tier": "adjacent"},
        ],
    }
    with pytest.raises(ValueError, match="duplicate"):
        reg.validate(data)


def test_validate_rejects_non_https_watch_url(tmp_path):
    data = {
        "schema": 1,
        "competitor": [
            {
                "name": "X",
                "tier": "direct",
                "watch_urls": ["http://x.example/blog"],
                "domains": ["x.example"],
            }
        ],
    }
    with pytest.raises(ValueError, match="https"):
        reg.validate(data)


def test_propose_writes_data_not_config(tmp_path):
    content = tmp_path / "content"
    candidates = [
        {
            "name": "NewCo",
            "tier": "direct",
            "aliases": ["NewCo"],
            "watch_urls": ["https://newco.example/blog/"],
            "domains": ["newco.example"],
            "syften_filter": "",
            "note": "found in funding lane",
        }
    ]
    path = reg.propose(content_root=content, profile="acme", candidates=candidates)
    assert path.exists()
    assert path.name.startswith("registry-proposals-")
    # The registry config file must remain untouched.
    assert not (content / "acme" / "knowledge" / "competitors.toml").exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["candidates"][0]["name"] == "NewCo"


def test_apply_refuses_watch_url_on_wrong_domain(tmp_path):
    profiles = tmp_path / "profiles"
    proposals = tmp_path / "proposals.json"
    candidates = [
        {
            "name": "NewCo",
            "tier": "direct",
            "aliases": ["NewCo"],
            "watch_urls": ["https://attacker.example/blog/"],
            "domains": ["newco.example"],
            "syften_filter": "",
        }
    ]
    proposals.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")

    with pytest.raises(ValueError, match="not under allowed domains"):
        reg.apply(
            profiles_root=profiles,
            profile="acme",
            proposals_path=proposals,
            accept=["NewCo"],
        )


def test_apply_appends_and_is_idempotent(tmp_path):
    profiles = tmp_path / "profiles"
    registry_path = profiles / "acme" / "knowledge" / "competitors.toml"
    _write(
        registry_path,
        'schema = 1\nreviewed = "2026-07-01"\n[[competitor]]\nname = "OldCo"\ntier = "direct"\naliases = []\nwatch_urls = []\ndomains = []\nsyften_filter = ""\nfirst_seen = "2026-07-01"\nlast_reviewed = "2026-07-01"\nstatus = "active"\nnote = ""\n',
    )
    proposals = tmp_path / "proposals.json"
    candidates = [
        {
            "name": "NewCo",
            "tier": "direct",
            "aliases": ["NewCo"],
            "watch_urls": ["https://newco.example/blog/"],
            "domains": ["newco.example"],
            "syften_filter": "",
            "status": "active",
        }
    ]
    proposals.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")

    appended = reg.apply(
        profiles_root=profiles,
        profile="acme",
        proposals_path=proposals,
        accept=["NewCo"],
    )
    assert appended == ["NewCo"]

    registry = reg.load(profile="acme", profiles_root=profiles)
    assert len(registry["competitor"]) == 2
    assert registry["competitor"][1]["name"] == "NewCo"
    assert registry["competitor"][1]["first_seen"] == reg._today()

    # Second apply of the same proposal is a no-op.
    appended2 = reg.apply(
        profiles_root=profiles,
        profile="acme",
        proposals_path=proposals,
        accept=["NewCo"],
    )
    assert appended2 == []


def test_filter_budget_report_matches_tags():
    registry = {
        "competitor": [
            {"name": "CoveredDirect", "tier": "direct", "syften_filter": "competitor-direct"},
            {"name": "Uncovered", "tier": "direct", "syften_filter": ""},
        ]
    }
    syften = {
        "filters": {
            "something $tag:competitor-direct lang:en": {"category": "competitor"},
        }
    }
    report = reg.filter_budget_report(registry=registry, syften_filters=syften)
    assert report["total"] == 2
    assert len(report["covered"]) == 1
    assert len(report["uncovered_direct"]) == 1
    assert report["uncovered_direct"][0]["name"] == "Uncovered"
