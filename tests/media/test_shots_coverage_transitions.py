"""`gtm_core.shots_coverage` — a declared `production.transition_in` must reach the master.

Sibling of test_shots_coverage.py, kept in its own file because the whole point of this row is a
different kind of evidence: not a sidecar in the run folder but the finish manifest's record of
the joins `video_finish stitch` actually applied. Fixtures are fictional.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.shots_coverage import check_coverage


def _doc(*transitions: dict | None) -> dict:
    """A shot list whose first shot is a hard cut and whose rest carry the given declarations."""
    shots: list[dict] = [{"id": "s00", "n": 1, "duration_s": 3.0}]
    for k, entry in enumerate(transitions, 1):
        shot: dict = {"id": f"s{k:02d}", "n": k + 1, "duration_s": 3.0}
        if entry is not None:
            shot["production"] = {"transition_in": entry}
        shots.append(shot)
    return {"source_item": "ci-fixture-02", "total_duration_s": 9.0, "shots": shots}


DISSOLVE = {"kind": "dissolve", "duration_s": 0.4, "why": "months later"}
FADE = {"kind": "fade", "duration_s": 0.5}


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run"
    d.mkdir()
    return d


def _finish(run_dir: Path, joins: list[dict] | None) -> None:
    payload: dict = {"stages": ["normalize"]}
    if joins is not None:
        payload["transitions"] = joins
    (run_dir / "finish-9x16.json").write_text(json.dumps(payload))


def _fields(report, *, missing: bool = True) -> set[tuple[str, str]]:
    rows = report.missing if missing else report.ok
    return {(f.shot_id, f.field) for f in rows}


def test_plan_stage_passes_a_well_formed_declaration_and_asks_nothing_of_disk(run_dir):
    report = check_coverage(_doc(None, DISSOLVE), run_dir=run_dir, stage="plan")
    assert report.missing == ()
    assert ("s02", "production.transition_in") in _fields(report, missing=False)


def test_plan_stage_refuses_a_malformed_declaration(run_dir):
    report = check_coverage(_doc(None, {"kind": "wipe"}), run_dir=run_dir, stage="plan")
    assert _fields(report) == {("s02", "production.transition_in")}


def test_finish_stage_reports_a_declared_transition_with_no_join_behind_it(run_dir):
    """The state this row exists for: the field is declared, the film was stitched with hard cuts,
    and nothing anywhere said so."""
    _finish(run_dir, None)
    report = check_coverage(_doc(DISSOLVE, FADE), run_dir=run_dir, stage="finish")
    assert _fields(report) == {
        ("s01", "production.transition_in"),
        ("s02", "production.transition_in"),
    }


def test_finish_stage_is_clean_when_the_manifest_records_each_join_as_declared(run_dir):
    _finish(
        run_dir,
        [
            {"join": 0, "kind": "dissolve", "duration_s": 0.4},
            {"join": 1, "kind": "fade", "duration_s": 0.5},
        ],
    )
    report = check_coverage(_doc(DISSOLVE, FADE), run_dir=run_dir, stage="finish")
    assert report.missing == ()


def test_a_join_of_the_wrong_kind_or_length_does_not_count(run_dir):
    """Presence alone is not evidence — the recorded join must be the one the shot asked for."""
    for join in (
        {"join": 0, "kind": "fade", "duration_s": 0.4},
        {"join": 0, "kind": "dissolve", "duration_s": 0.8},
        {"join": 1, "kind": "dissolve", "duration_s": 0.4},
    ):
        _finish(run_dir, [join])
        report = check_coverage(_doc(DISSOLVE), run_dir=run_dir, stage="finish")
        assert _fields(report) == {("s01", "production.transition_in")}, join


def test_a_transition_on_the_first_shot_is_reported_as_unconsumable(run_dir):
    doc = _doc(None)
    doc["shots"][0]["production"] = {"transition_in": FADE}
    _finish(run_dir, [{"join": 0, "kind": "fade", "duration_s": 0.5}])
    report = check_coverage(doc, run_dir=run_dir, stage="finish")
    assert _fields(report) == {("s00", "production.transition_in")}


def test_a_list_that_declares_nothing_produces_no_row(run_dir):
    _finish(run_dir, None)
    for stage in ("plan", "render", "finish"):
        report = check_coverage(_doc(None, None), run_dir=run_dir, stage=stage)
        assert not any(f.field == "production.transition_in" for f in report.missing + report.ok)
