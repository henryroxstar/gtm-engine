"""Absence tests for signal-first sourcing attestation routes (R2.5, Amendment A).

A value that is absent, empty, whitespace, or unknown must never be read as permission.
Checks source_attests and record_attests across all relevant row and model fields.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from gtm_core.hook_coverage.premise import Premise
from gtm_core.hook_coverage.source_attest import (
    AttestContext,
    record_attests,
    source_attests,
)
from gtm_core.signal_obs.registry import Registry, Source

ABSENT: tuple[object, ...] = (None, "", "   ", "wharrgarbl")
TODAY = datetime.date(2026, 10, 1)
SHA = "a" * 64
DOMAIN = "northwind.example.test"


def _source(**kw) -> Source:
    base = {
        "id": "agent-register",
        "title": "Agent pilot register",
        "url": "https://register.example.test/cohort",
        "kind": "regulator_register",
        "premise": "agents-in-operation",
        "claim_gap": "Listing shows a pilot, not production.",
        "precision": "8/10",
        "member_role": "mixed",
        "timing": "listing_date",
        "cadence_days": 90,
        "extractor": "brain_list",
        "extractor_args": {},
        "max_members": 80,
        "expires_on": datetime.date(2027, 3, 20),
        "owner": "ops",
        "notes": "",
        "attestation": "agentic",
        "agentic_basis": "The cohort was selected on agent use cases.",
    }
    return Source(**{**base, **kw})


def _obs(**kw) -> dict:
    base = {
        "schema": 1,
        "kind": "source_member",
        "product": "alpha",
        "source_id": "agent-register",
        "source_url": "https://register.example.test/cohort",
        "capture_sha256": SHA,
        "account_key": DOMAIN,
        "observed": "2026-09-20",
        "observed_basis": "first_seen_in_capture",
        "role": "mixed",
        "premise_at_write": "agents-in-operation",
    }
    return {**base, **kw}


def _ctx(
    *, sources: list[Source] | None = None, obs=None, shas=frozenset({SHA}), product="alpha"
) -> AttestContext:
    srcs = sources if sources is not None else [_source()]
    reg = Registry(
        profile="p",
        product=product,
        path=None,
        origin="profile",
        sources=tuple(srcs),
        active=tuple(srcs),
        by_id={s.id: s for s in srcs},
    )
    return AttestContext(
        product=product,
        registry=reg,
        observations=tuple(obs if obs is not None else [_obs()]),
        capture_shas=shas,
        today=TODAY,
    )


def _premise(**kw) -> Premise:
    base = {
        "key": "agents-in-operation",
        "min_distinct": 1,
        "terms": frozenset(),
        "attested_by_source": True,
    }
    return Premise(**{**base, **kw})


OWN_ROW = {
    "company_name": "Northwind Traders",
    "domain": DOMAIN,
    "category_relation": "prospect",
    "signal_agent_kind": "ai",
    "signal_evidence": "Northwind today announced the rollout of an agentic AI solution",
    "signal_source_url": "https://news.example.test/agents",
    "signal_observed": "2026-08-18",
}


@pytest.mark.parametrize("val", ABSENT)
def test_source_attests_refuses_absent_domain(val: object) -> None:
    row = {"domain": val, "company_domain": val}
    assert source_attests(row, _premise(), _ctx()) is False


@pytest.mark.parametrize("val", ABSENT)
def test_source_attests_refuses_absent_obs_fields(val: object) -> None:
    row = {"domain": DOMAIN}
    for field in (
        "kind",
        "product",
        "account_key",
        "role",
        "capture_sha256",
        "observed",
        "premise_at_write",
        "source_id",
    ):
        obs = _obs(**{field: val})
        assert source_attests(row, _premise(), _ctx(obs=[obs])) is False


@pytest.mark.parametrize("val", ABSENT)
def test_source_attests_refuses_absent_source_attestation(val: object) -> None:
    row = {"domain": DOMAIN}
    src = _source(attestation=str(val) if val is not None else "")
    assert source_attests(row, _premise(), _ctx(sources=[src])) is False


@pytest.mark.parametrize("val", ABSENT)
def test_source_attests_refuses_absent_source_premise(val: object) -> None:
    row = {"domain": DOMAIN}
    src = _source(premise=str(val) if val is not None else "")
    assert source_attests(row, _premise(), _ctx(sources=[src])) is False


@pytest.mark.parametrize("val", (None, False))
def test_source_attests_refuses_premise_without_opt_in(val: object) -> None:
    row = {"domain": DOMAIN}
    p = _premise(attested_by_source=bool(val))
    assert source_attests(row, p, _ctx()) is False


def test_source_attests_refuses_none_context() -> None:
    row = {"domain": DOMAIN}
    assert source_attests(row, _premise(), None) is False


@pytest.mark.parametrize("val", ABSENT)
def test_record_attests_refuses_absent_agent_kind(val: object, tmp_path: Path) -> None:
    row = {**OWN_ROW, "signal_agent_kind": val}
    assert record_attests(row, _premise(), TODAY, sources_dir=tmp_path) is False


@pytest.mark.parametrize("val", ABSENT)
def test_record_attests_refuses_absent_category_relation(val: object, tmp_path: Path) -> None:
    row = {**OWN_ROW, "category_relation": val}
    assert record_attests(row, _premise(), TODAY, sources_dir=tmp_path) is False


@pytest.mark.parametrize("val", ABSENT)
def test_record_attests_refuses_absent_evidence(val: object, tmp_path: Path) -> None:
    row = {**OWN_ROW, "signal_evidence": val}
    assert record_attests(row, _premise(), TODAY, sources_dir=tmp_path) is False


@pytest.mark.parametrize("val", ABSENT)
def test_record_attests_refuses_absent_source_url(val: object, tmp_path: Path) -> None:
    row = {**OWN_ROW, "signal_source_url": val}
    assert record_attests(row, _premise(), TODAY, sources_dir=tmp_path) is False


@pytest.mark.parametrize("val", ABSENT)
def test_record_attests_refuses_absent_observed_date(val: object, tmp_path: Path) -> None:
    row = {**OWN_ROW, "signal_observed": val}
    assert record_attests(row, _premise(), TODAY, sources_dir=tmp_path) is False


@pytest.mark.parametrize("val", (None, False))
def test_record_attests_refuses_premise_without_opt_in(val: object, tmp_path: Path) -> None:
    p = _premise(attested_by_source=bool(val))
    assert record_attests(OWN_ROW, p, TODAY, sources_dir=tmp_path) is False


@pytest.mark.parametrize("val", ABSENT)
def test_record_attests_refuses_absent_profile_and_sources_dir(val: object) -> None:
    row = {**OWN_ROW, "profile": val}
    assert (
        record_attests(
            row, _premise(), TODAY, profile=str(val) if val is not None else None, sources_dir=None
        )
        is False
    )
