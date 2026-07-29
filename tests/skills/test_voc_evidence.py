"""Guards for the evidence store and the breadth rule.

The brief's credibility rests on one claim: "N independent sources back this demand."
These tests pin the two filters that make N trustworthy — speaker eligibility and
verification — plus distinctness, so a single loud filer can never read as consensus.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.voc import evidence as ev


def _rec(**kw) -> ev.EvidenceRecord:
    base = {
        "claim_id": "agent-identity-demand",
        "verbatim": "AI agents require authenticated access at a scale exceeding human models.",
        "url": "https://sec.gov/example",
        "date": "2026-03-05",
        "entity": "Lumen Cloud, Inc.",
        "speaker": "customer-voice",
        "source_id": "enterprise_filings",
        "verified": True,
    }
    base.update(kw)
    return ev.EvidenceRecord(**base)


# --- the two filters ---------------------------------------------------------------- #


def test_unverified_record_is_never_citable():
    """A full-text-search hit means the phrase appears — not that the company said it.
    169 filers, a handful read: the unread ones must be structurally uncitable."""
    rec = _rec(verified=False)
    assert rec.is_citable() is False
    assert ev.breadth([rec], "agent-identity-demand") == 0


def test_wrong_speaker_is_never_citable_even_when_verified():
    for speaker in ("standards-voice", "vendor-voice", "bd-focus", "expert-lens", "mixed"):
        rec = _rec(speaker=speaker, verified=True)
        assert rec.is_citable() is False, speaker
        assert ev.breadth([rec], "agent-identity-demand") == 0, speaker


def test_verified_customer_voice_is_citable():
    assert _rec().is_citable() is True
    assert ev.breadth([_rec()], "agent-identity-demand") == 1


# --- distinctness ------------------------------------------------------------------- #


def test_breadth_counts_distinct_sources_not_records():
    """Ten quotes from one 10-K are one source. A single loud filer is not consensus."""
    same = [_rec(verbatim=f"quote {i}") for i in range(10)]
    assert ev.breadth(same, "agent-identity-demand") == 1

    varied = [
        _rec(entity="Lumen Cloud, Inc."),
        _rec(entity="Northbridge Bank Corp"),
        _rec(entity="Ashford Capital Corp"),
    ]
    assert ev.breadth(varied, "agent-identity-demand") == 3


def test_same_entity_via_different_sources_counts_twice():
    # A company saying it in a filing AND in a conference talk is genuine corroboration
    # across channels, so it is two sources.
    recs = [
        _rec(source_id="enterprise_filings"),
        _rec(source_id="syften_market_signals"),
    ]
    assert ev.breadth(recs, "agent-identity-demand") == 2


def test_breadth_is_scoped_to_the_claim():
    recs = [_rec(claim_id="claim-a"), _rec(claim_id="claim-b", entity="Meridian Payments Inc.")]
    assert ev.breadth(recs, "claim-a") == 1
    assert ev.breadth(recs, "claim-b") == 1
    assert ev.breadth(recs, "claim-c") == 0


# --- confidence + assessment -------------------------------------------------------- #


def test_confidence_bands_are_conservative():
    assert ev.confidence(0) == "unsupported"
    assert ev.confidence(1) == "weak"  # one source is never better than weak
    assert ev.confidence(2) == "moderate"
    assert ev.confidence(3) == "strong"
    assert ev.confidence(9) == "strong"


def test_assess_explains_why_a_claim_is_weak():
    """'Three sources but all unverified' must be distinguishable from 'no sources'."""
    recs = [
        _rec(entity="A", verified=False),
        _rec(entity="B", verified=False),
        _rec(entity="C", speaker="vendor-voice", verified=True),
    ]
    out = ev.assess(recs, "agent-identity-demand")
    assert out["breadth"] == 0
    assert out["confidence"] == "unsupported"
    assert out["records_total"] == 3
    assert out["excluded_unverified"] == 2
    assert out["excluded_wrong_speaker"] == 1
    assert out["citable_entities"] == []


def test_assess_lists_citable_entities():
    recs = [
        _rec(entity="Northbridge Bank Corp"),
        _rec(entity="Meridian Payments Inc."),
        _rec(entity="A", verified=False),
    ]
    out = ev.assess(recs, "agent-identity-demand")
    assert out["breadth"] == 2
    assert out["confidence"] == "moderate"
    assert out["citable_entities"] == ["Meridian Payments Inc.", "Northbridge Bank Corp"]


# --- store round-trip --------------------------------------------------------------- #


def test_append_then_load_round_trips(tmp_path: Path):
    path = tmp_path / "evidence.jsonl"
    assert ev.load(path) == []  # missing file is not an error
    assert ev.append(path, [_rec(entity="A"), _rec(entity="B", verified=False)]) == 2

    loaded = ev.load(path)
    assert len(loaded) == 2
    assert {r.entity for r in loaded} == {"A", "B"}
    assert [r.verified for r in loaded] == [True, False]
    # Appending is additive, never a rewrite.
    ev.append(path, [_rec(entity="C")])
    assert len(ev.load(path)) == 3


def test_malformed_lines_are_skipped_not_fatal(tmp_path: Path):
    """A corrupt line must never silently become evidence, and must not kill the run."""
    path = tmp_path / "evidence.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"not": "a record"}),  # missing required fields
                "{ broken json",
                "",
                json.dumps(
                    {
                        "claim_id": "c",
                        "verbatim": "v",
                        "url": "u",
                        "date": "2026-01-01",
                        "entity": "E",
                        "speaker": "customer-voice",
                        "source_id": "enterprise_filings",
                        "verified": True,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    loaded = ev.load(path)
    assert len(loaded) == 1
    assert loaded[0].entity == "E"


def test_summarize_reports_the_verification_backlog():
    recs = [
        _rec(entity="A"),
        _rec(entity="B", verified=False),
        _rec(entity="C", verified=False),
        _rec(entity="D", speaker="standards-voice", claim_id="spec-watch"),
    ]
    out = ev.summarize(recs)
    assert out["records"] == 4
    assert out["verified"] == 2
    assert out["unverified"] == 2
    assert out["by_speaker"] == {"customer-voice": 3, "standards-voice": 1}
    assert out["claims"]["agent-identity-demand"]["breadth"] == 1
    assert out["claims"]["spec-watch"]["breadth"] == 0


def test_store_path_rejects_unsafe_profile(tmp_path: Path):
    with pytest.raises(ValueError):
        ev.store_path(tmp_path, "../evil")


def test_store_path_lands_under_market_intelligence(tmp_path: Path):
    p = ev.store_path(tmp_path, "acme")
    assert p == tmp_path / "acme" / "plans" / "market-intelligence" / "evidence.jsonl"
