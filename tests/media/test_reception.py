"""Tests for gtm_core.reception (Q3) aligned with PRD §2.3."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import reception as rc


def _passing_responses(judges: int = 5) -> list[rc.AudienceResponse]:
    """Build a valid passing response dataset with 5 judges across control and our film."""
    responses: list[rc.AudienceResponse] = []
    for i in range(1, judges + 1):
        jid = f"j{i}"
        # Human control response
        responses.append(
            rc.AudienceResponse(
                judge_id=jid,
                condition="control_human",
                took_action=True,
                is_icp=True,
                moved_score=4.0,
                defect="",
                unprompted_recall_24h=True,
            )
        )
        # Our film response
        responses.append(
            rc.AudienceResponse(
                judge_id=jid,
                condition="ours",
                took_action=True,
                is_icp=True,
                moved_score=4.0,
                defect="",
                unprompted_recall_24h=True,
            )
        )
    return responses


def test_reception_eval_passes_cleanly():
    responses = _passing_responses()
    verdict = rc.evaluate_reception(responses)
    assert verdict.passed is True
    assert verdict.control_act_gap == 0.0
    assert verdict.mean_moved == 4.0
    assert verdict.judges == 5
    assert verdict.icp_share == 1.0
    assert len(verdict.reasons) == 0


def test_fail_closed_missing_control_human():
    """§R12 positive control: missing control_human must fail closed with ReceptionError."""
    responses = [
        rc.AudienceResponse(
            judge_id=f"j{i}",
            condition="ours",
            took_action=True,
            is_icp=True,
            moved_score=4.5,
        )
        for i in range(5)
    ]
    with pytest.raises(rc.ReceptionError, match="control_human"):
        rc.evaluate_reception(responses)


def test_fail_closed_missing_ours_condition():
    responses = [
        rc.AudienceResponse(
            judge_id=f"j{i}",
            condition="control_human",
            took_action=True,
            is_icp=True,
            moved_score=4.5,
        )
        for i in range(5)
    ]
    with pytest.raises(rc.ReceptionError, match="ours"):
        rc.evaluate_reception(responses)


def test_refusal_on_too_few_judges():
    """Below MIN_JUDGES (5), one person's taste dominates."""
    responses = _passing_responses(judges=3)
    verdict = rc.evaluate_reception(responses)
    assert verdict.passed is False
    assert any("only 3 judge(s)" in r for r in verdict.reasons)


def test_refusal_on_low_icp_share():
    """At least 60% of judges must be in ICP."""
    responses = _passing_responses(judges=5)
    # Mark 3 out of 5 judges as NOT in ICP -> ICP share = 40% < 60%
    for r in responses:
        if r.judge_id in ("j1", "j2", "j3"):
            r.is_icp = False
    verdict = rc.evaluate_reception(responses)
    assert verdict.passed is False
    assert any("ICP share 40.0% is below" in r for r in verdict.reasons)


def test_refusal_on_control_act_gap_exceeded():
    """Losing to control by > 15% fails on production follow-through."""
    responses: list[rc.AudienceResponse] = []
    # Control has 5/5 = 100% action
    for i in range(1, 6):
        responses.append(
            rc.AudienceResponse(
                judge_id=f"j{i}",
                condition="control_human",
                took_action=True,
                is_icp=True,
                moved_score=4.0,
            )
        )
    # Our film has only 3/5 = 60% action -> gap = 40% > 15%
    for i in range(1, 6):
        responses.append(
            rc.AudienceResponse(
                judge_id=f"j{i}",
                condition="ours",
                took_action=(i <= 3),
                is_icp=True,
                moved_score=4.0,
            )
        )
    verdict = rc.evaluate_reception(responses)
    assert verdict.passed is False
    assert verdict.control_act_gap == pytest.approx(0.40, abs=1e-4)
    assert any("control act gap 40.0% exceeds 15.0% ceiling" in r for r in verdict.reasons)


def test_refusal_on_low_mean_moved_score():
    """PASS_MOVED_MIN = 3.5. A mean of 3.2 is 'the shrug' and must fail."""
    responses: list[rc.AudienceResponse] = []
    for i in range(1, 6):
        responses.append(
            rc.AudienceResponse(
                judge_id=f"j{i}",
                condition="control_human",
                took_action=True,
                is_icp=True,
                moved_score=4.0,
            )
        )
        responses.append(
            rc.AudienceResponse(
                judge_id=f"j{i}",
                condition="ours",
                took_action=True,
                is_icp=True,
                moved_score=3.2,
            )
        )
    verdict = rc.evaluate_reception(responses)
    assert verdict.passed is False
    assert verdict.mean_moved == pytest.approx(3.2, abs=1e-4)
    assert any("mean moved score 3.20 is below 3.50 minimum" in r for r in verdict.reasons)


def test_negative_control_kill_defect_share():
    """Negative control: film beats human control on act-rate, but 3 of 5 judges (60%)
    name 'the faces' as defect -> must FAIL on KILL_DEFECT_SHARE."""
    responses: list[rc.AudienceResponse] = []
    # Control act rate: 3/5 = 60%
    for i in range(1, 6):
        responses.append(
            rc.AudienceResponse(
                judge_id=f"j{i}",
                condition="control_human",
                took_action=(i <= 3),
                is_icp=True,
                moved_score=3.8,
            )
        )
    # Our film act rate: 5/5 = 100% (beats control!), moved score 4.2
    # BUT 3 of 5 judges name "the faces"
    defects = ["the faces", "the faces", "the faces", "", ""]
    for i in range(1, 6):
        responses.append(
            rc.AudienceResponse(
                judge_id=f"j{i}",
                condition="ours",
                took_action=True,
                is_icp=True,
                moved_score=4.2,
                defect=defects[i - 1],
            )
        )

    verdict = rc.evaluate_reception(responses)
    assert verdict.control_act_gap == 0.0
    assert verdict.mean_moved == 4.2
    assert verdict.dominant_defect is not None
    assert verdict.dominant_defect[0] == "the faces"
    assert verdict.dominant_defect[1] == pytest.approx(0.60, abs=1e-4)
    # Must fail on KILL_DEFECT_SHARE
    assert verdict.passed is False
    assert any("named the same defect ('the faces')" in r for r in verdict.reasons)


def test_unprompted_recall_collected_as_data_never_thresholded():
    """24-hour unprompted recall is recorded in payload as empirical data,
    but does not fail or pass the film."""
    responses = _passing_responses()
    # Set recall to 0 for all judges
    for r in responses:
        r.unprompted_recall_24h = False

    verdict = rc.evaluate_reception(responses)
    assert verdict.passed is True  # Zero recall does NOT fail the film
    assert verdict.ours_recall_rate == 0.0


def test_write_reception_manifest_contains_individual_judge_rows(tmp_path: Path):
    responses = _passing_responses()
    verdict = rc.evaluate_reception(responses)
    out_file = tmp_path / "content" / "acme" / "video" / "film-01" / "reception.json"

    written = rc.write_reception_manifest(out_file, verdict, responses)
    assert written.is_file()

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert "verdict" in data
    assert "responses" in data
    assert data["verdict"]["passed"] is True
    assert len(data["responses"]) == 10  # 5 judges * 2 conditions
    assert data["responses"][0]["judge_id"] == "j1"
    assert data["responses"][0]["condition"] in rc.CONDITIONS


def test_record_reception_outcome(tmp_path: Path):
    from gtm_core.outcomes import read_outcomes

    responses = _passing_responses()
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
    assert "reception_pass" in rows[0]["tags"]
    assert rows[0]["meta"]["passed"] is True
    assert rows[0]["meta"]["mean_moved"] == 4.0


def test_reception_cli_end_to_end(tmp_path: Path):
    responses = _passing_responses()
    in_file = tmp_path / "responses.json"
    in_file.write_text(json.dumps([r.to_dict() for r in responses]), encoding="utf-8")

    code = rc.main(["--input", str(in_file), "--json"])
    assert code == 0


def test_reception_cli_accepts_manifest_file(tmp_path: Path):
    """The CLI must accept a reception.json manifest object with {'verdict': ..., 'responses': ...}."""
    responses = _passing_responses()
    verdict = rc.evaluate_reception(responses)
    manifest_file = tmp_path / "reception.json"
    rc.write_reception_manifest(manifest_file, verdict, responses)

    code = rc.main(["--input", str(manifest_file), "--json"])
    assert code == 0
