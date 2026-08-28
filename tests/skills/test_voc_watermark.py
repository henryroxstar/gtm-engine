"""Guards for the pull-window policy and the watermark state machine.

Three properties carry the honesty of the harvest and are pinned here:
a failed pull must not advance the watermark, a slow source must not be asked for a
meaninglessly short window, and an unservable gap must be reported rather than clipped.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from gtm_core.voc import watermark as wm

TODAY = date(2026, 7, 29)


def _state(lane: str, pulled: str, status: str = wm.STATUS_OK) -> dict:
    return {
        "kind": "voc-watermarks",
        "lanes": {lane: {"last_pulled_at": pulled, "last_status": status}},
    }


# --- the watermark state machine ----------------------------------------------------- #


def test_failed_pull_does_not_advance_the_watermark():
    """The load-bearing rule. Advancing on failure would silently drop the window the failed
    run should have covered — nothing would ever request it again."""
    state = _state("vendor_watch", "2026-06-01")
    wm.record(state, "vendor_watch", status=wm.STATUS_FAILED, today=TODAY)

    entry = state["lanes"]["vendor_watch"]
    assert entry["last_pulled_at"] == "2026-06-01"  # unmoved
    assert entry["last_attempt_at"] == "2026-07-29"  # but the attempt is recorded
    assert wm.coverage_state(state, "vendor_watch") == "pull-failed"

    win = wm.window_for(state, "vendor_watch", TODAY)
    assert win["since"] == "2026-06-01"  # the failed window is re-requested
    assert any("FAILED" in w for w in win["warnings"])


def test_empty_pull_advances_the_watermark():
    """'Nothing new' means the window WAS covered — unlike a failure. Not advancing here
    would make every subsequent run re-request a window already known to be empty."""
    state = _state("vendor_watch", "2026-06-01")
    wm.record(state, "vendor_watch", status=wm.STATUS_EMPTY, today=TODAY, items=0)
    assert state["lanes"]["vendor_watch"]["last_pulled_at"] == "2026-07-29"
    assert wm.coverage_state(state, "vendor_watch") == "nothing-new-since"


def test_the_four_coverage_states_are_distinguishable():
    """A blank renders 'never pulled', 'pulled and empty' and 'pull failed' identically —
    which is how a brief quietly overstates its own coverage."""
    empty: dict = {"lanes": {}}
    assert wm.coverage_state(empty, "standards_watch") == "not-pulled"

    for status, expected in (
        (wm.STATUS_OK, "current"),
        (wm.STATUS_EMPTY, "nothing-new-since"),
        (wm.STATUS_FAILED, "pull-failed"),
    ):
        state = _state("standards_watch", "2026-07-01", status)
        assert wm.coverage_state(state, "standards_watch") == expected


def test_unknown_lane_and_status_are_rejected():
    """A typo must fail loudly — silently accepting it would mean an unbounded pull window."""
    with pytest.raises(KeyError):
        wm.window_for({"lanes": {}}, "not_a_lane", TODAY)
    with pytest.raises(KeyError):
        wm.record({"lanes": {}}, "not_a_lane", status=wm.STATUS_OK, today=TODAY)
    with pytest.raises(ValueError):
        wm.record({"lanes": {}}, "vendor_watch", status="probably-fine", today=TODAY)


# --- window policy -------------------------------------------------------------------- #


def test_slow_source_gets_its_minimum_window_even_when_just_pulled():
    """A 1-day window on SEC EDGAR returns nothing, which reads as 'no enterprise signal'
    when it means 'wrong window'. The floor is what prevents that misreading."""
    state = _state("enterprise_filings", "2026-07-28")
    win = wm.window_for(state, "enterprise_filings", TODAY)
    assert win["days"] == 90
    assert win["floor_applied"] is True


def test_a_lane_without_a_floor_uses_the_raw_gap():
    state = _state("vendor_watch", "2026-07-27")
    win = wm.window_for(state, "vendor_watch", TODAY)
    assert win["days"] == 2
    assert win["floor_applied"] is False


def test_first_run_uses_the_cold_start_window():
    win = wm.window_for({"lanes": {}}, "standards_watch", TODAY)
    assert win["basis"] == "first-run"
    assert win["days"] == 30
    assert win["coverage_state"] == "not-pulled"


def test_gap_beyond_the_archive_is_reported_as_loss_not_clipped_silently():
    """Syften's Standard plan keeps 30 days. A 90-day gap means 60 days are gone for good —
    the plan has to SAY that, because a clipped window looks identical to a quiet market."""
    state = _state("syften_market_signals", "2026-04-30")
    win = wm.window_for(state, "syften_market_signals", TODAY)
    assert win["days"] == 30
    assert win["capped"] is True
    assert win["data_loss"] is True
    assert win["data_loss_days"] == 60
    assert any("lost, not empty" in w for w in win["warnings"])


def test_every_lane_maps_to_a_real_collector_source():
    """A lane whose source_id no collector emits would produce a window nothing consumes."""
    from gtm_core.voc import collect

    manifest = collect.collect(Path("/nonexistent"), Path("/nonexistent"), "acme", TODAY)
    known = {s["id"] for s in manifest["sources"]}
    for pol in wm.POLICIES:
        assert pol.source_id in known, pol.lane


def test_phase_three_lanes_have_policies():
    """The four new Phase-3 lanes must be known to the watermark state machine."""
    lanes = {p.lane for p in wm.POLICIES}
    for lane in (
        "own_product_watch",
        "funding_and_ma",
        "regulatory_enforcement",
        "incidents_benchmarks",
    ):
        assert lane in lanes


def test_phase_three_slow_lanes_get_minimum_windows():
    """Funding, regulatory, and incident lanes are sparse; a too-recent watermark must be
    widened to the floor so a quiet week is not misread as an empty landscape."""
    for lane in ("funding_and_ma", "regulatory_enforcement", "incidents_benchmarks"):
        state = _state(lane, "2026-07-28")
        win = wm.window_for(state, lane, TODAY)
        assert win["floor_applied"] is True, lane
        assert win["days"] == 30, lane


def test_failed_pull_does_not_advance_for_phase_three_lane():
    state = _state("own_product_watch", "2026-06-01")
    wm.record(state, "own_product_watch", status=wm.STATUS_FAILED, today=TODAY)
    entry = state["lanes"]["own_product_watch"]
    assert entry["last_pulled_at"] == "2026-06-01"
    assert wm.coverage_state(state, "own_product_watch") == "pull-failed"


# --- store round-trip ------------------------------------------------------------------ #


def test_state_round_trips_and_a_corrupt_file_is_not_fatal(tmp_path: Path):
    path = tmp_path / "watermarks.json"
    assert wm.load(path)["lanes"] == {}  # missing file is a valid starting point

    state = wm.record({"lanes": {}}, "standards_watch", status=wm.STATUS_OK, today=TODAY, items=7)
    wm.save(path, state)
    assert wm.load(path)["lanes"]["standards_watch"]["last_items"] == 7

    path.write_text("{ not json", encoding="utf-8")
    assert wm.load(path)["lanes"] == {}


def test_plan_flags_the_lanes_needing_attention():
    state = {
        "lanes": {
            "syften_market_signals": {"last_pulled_at": "2026-01-01", "last_status": "ok"},
            "vendor_watch": {"last_pulled_at": "2026-07-01", "last_status": "failed"},
        }
    }
    out = wm.plan(state, TODAY)
    assert out["summary"]["data_loss"] == ["syften_market_signals"]
    assert out["summary"]["failed"] == ["vendor_watch"]
    # Never pulled at all — distinct from both of the above.
    assert "enterprise_filings" in out["summary"]["never_pulled"]


def test_store_path_rejects_an_unsafe_profile(tmp_path: Path):
    with pytest.raises(ValueError):
        wm.state_path(tmp_path, "../evil")
