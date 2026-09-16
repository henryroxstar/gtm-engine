"""Tests for gtm_core.reception (Q3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import reception as rc


def _responses(
    synthetic_n: int = 25,
    human_n: int = 25,
    synthetic_act: int = 10,
    human_act: int = 12,
    synthetic_recall: int = 20,
    human_recall: int = 22,
) -> list[rc.AudienceResponse]:
    out = []
    for i in range(synthetic_n):
        out.append(
            rc.AudienceResponse(
                audience_id=f"syn_{i}",
                condition="synthetic",
                recalled_message=(i < synthetic_recall),
                took_action=(i < synthetic_act),
            )
        )
    for i in range(human_n):
        out.append(
            rc.AudienceResponse(
                audience_id=f"hum_{i}",
                condition="human",
                recalled_message=(i < human_recall),
                took_action=(i < human_act),
            )
        )
    return out


def test_reception_eval_passes_within_gap():
    # 10/25 = 40% synthetic, 12/25 = 48% human -> gap = 8% <= 15% ceiling
    responses = _responses(synthetic_act=10, human_act=12)
    verdict = rc.evaluate_reception(responses)
    assert verdict.passed
    assert verdict.control_act_gap == pytest.approx(0.08, abs=1e-4)


def test_reception_eval_fails_when_gap_exceeds_ceiling():
    # 5/25 = 20% synthetic, 15/25 = 60% human -> gap = 40% > 15% ceiling
    responses = _responses(synthetic_act=5, human_act=15)
    verdict = rc.evaluate_reception(responses)
    assert not verdict.passed
    assert verdict.control_act_gap == pytest.approx(0.40, abs=1e-4)
    assert any("exceeds" in r for r in verdict.reasons)


def test_reception_eval_fails_on_small_sample():
    responses = _responses(synthetic_n=10, human_n=10)
    verdict = rc.evaluate_reception(responses)
    assert not verdict.passed
    assert any("sample size too small" in r for r in verdict.reasons)


def test_record_reception_outcome(tmp_path: Path):
    from gtm_core.outcomes import read_outcomes

    responses = _responses()
    verdict = rc.evaluate_reception(responses)
    rc.record_reception_outcome(
        content_root=tmp_path,
        profile="test-prof",
        verdict=verdict,
        ref="film_01",
    )
    rows = read_outcomes(tmp_path, "test-prof")
    assert len(rows) == 1
    assert rows[0]["channel"] == "reception"
    assert "q3_reception" in rows[0]["tags"]
    assert rows[0]["meta"]["passed"] is True


def test_reception_cli_end_to_end(tmp_path: Path):
    responses = _responses(synthetic_act=10, human_act=12)
    in_file = tmp_path / "responses.json"
    in_file.write_text(json.dumps([rc.asdict(r) for r in responses]), encoding="utf-8")

    code = rc.main(["--input", str(in_file), "--json"])
    assert code == 0
