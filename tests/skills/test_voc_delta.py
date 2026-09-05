"""Tests for the signal record schema and issue-to-issue delta."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from gtm_core.voc import delta as d
from gtm_core.voc import signals as sig

TODAY = date(2026, 7, 29)


def _signal(
    id: str,
    direction: str = "neutral",
    material: bool = True,
    triage: str = "watch",
    signal_date: str = "2026-07-28",
    decay_days: int = 30,
    lane: str = "funding_and_ma",
    speaker: str = "vendor-voice",
    direction_basis: str = sig.DIRECTION_BASIS_JUDGED,
) -> sig.SignalRecord:
    return sig.SignalRecord(
        id=id,
        title=f"Title for {id}",
        date=signal_date,
        lane=lane,
        speaker=speaker,
        entity="Example Inc",
        url="https://example.test/news",
        direction=direction,
        direction_basis=direction_basis,
        material=material,
        verified=True,
        evidence_ids=["B-1"],
        functions=["product", "marketing"],
        decay_days=decay_days,
        triage=triage,
    )


def test_signal_validation_requires_known_lane():
    s = _signal(id="x", lane="not-a-lane")
    with pytest.raises(sig.SignalValidationError, match="unknown lane"):
        sig._validate(s)


def test_signal_validation_requires_known_speaker():
    s = _signal(id="x", speaker="not-a-speaker")
    with pytest.raises(sig.SignalValidationError, match="unknown speaker"):
        sig._validate(s)


def test_signal_validation_requires_judged_basis():
    s = _signal(id="x", direction_basis="computed")
    with pytest.raises(sig.SignalValidationError, match="direction_basis"):
        sig._validate(s)


def test_signal_validation_requires_iso_date():
    s = _signal(id="x", signal_date="07-28-2026")
    with pytest.raises(sig.SignalValidationError, match="date must be ISO"):
        sig._validate(s)


def test_signal_from_dict_round_trips():
    s = _signal(id="sig-1", direction="threat")
    raw = s.to_dict()
    restored = sig.from_dict(raw)
    assert restored == s


def test_signal_write_and_load(tmp_path: Path):
    path = tmp_path / "signals-2026-07-29.json"
    signals = [_signal("sig-1"), _signal("sig-2", direction="opportunity")]
    sig.write(path, signals)
    loaded = sig.load(path)
    assert [s.id for s in loaded] == ["sig-1", "sig-2"]


def test_signal_load_skips_malformed_records(tmp_path: Path):
    path = tmp_path / "signals-2026-07-29.json"
    payload = [
        _signal("sig-1").to_dict(),
        {"id": "bad", "direction": "???"},
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = sig.load(path)
    assert [s.id for s in loaded] == ["sig-1"]


def test_list_files_sorts_by_date(tmp_path: Path):
    content = tmp_path / "content"
    store = content / "acme" / "plans" / "market-intelligence"
    store.mkdir(parents=True)
    (store / "signals-2026-07-27.json").write_text("[]", encoding="utf-8")
    (store / "signals-2026-07-29.json").write_text("[]", encoding="utf-8")
    (store / "signals-2026-07-28.json").write_text("[]", encoding="utf-8")
    files = sig.list_files(content, "acme")
    assert [p.name for p in files] == [
        "signals-2026-07-27.json",
        "signals-2026-07-28.json",
        "signals-2026-07-29.json",
    ]


# --- delta classification --------------------------------------------------------- #


def test_first_run_renders_baseline_not_nothing_changed():
    current = [_signal("sig-a")]
    result = d.diff(None, current, today=TODAY)
    assert result["baseline"] is True
    assert result["new"] == ["sig-a"]
    assert result["escalated"] == []
    assert result["decayed"] == []
    rendered = d.render(result)
    assert "No prior issue" in rendered
    assert "nothing changed" not in rendered.lower()


def test_new_signals_detected():
    previous = [_signal("sig-a")]
    current = [_signal("sig-a"), _signal("sig-b"), _signal("sig-c")]
    result = d.diff(previous, current, today=TODAY)
    assert sorted(result["new"]) == ["sig-b", "sig-c"]


def test_escalated_when_direction_moves_toward_threat():
    previous = [_signal("sig-a", direction="validation")]
    current = [_signal("sig-a", direction="threat")]
    result = d.diff(previous, current, today=TODAY)
    assert result["escalated"] == ["sig-a"]


def test_not_escalated_when_direction_moves_away_from_threat():
    previous = [_signal("sig-a", direction="threat")]
    current = [_signal("sig-a", direction="validation")]
    result = d.diff(previous, current, today=TODAY)
    assert result["escalated"] == []


def test_escalated_when_material_becomes_true():
    previous = [_signal("sig-a", material=False)]
    current = [_signal("sig-a", material=True)]
    result = d.diff(previous, current, today=TODAY)
    assert result["escalated"] == ["sig-a"]


def test_resolved_when_triage_changes_to_ignore():
    previous = [_signal("sig-a", triage="watch")]
    current = [_signal("sig-a", triage="ignore")]
    result = d.diff(previous, current, today=TODAY)
    assert result["resolved"] == ["sig-a"]
    assert result["still_ignored"] == []


def test_still_ignored_when_ignore_carried_forward():
    previous = [_signal("sig-a", triage="ignore")]
    current = [_signal("sig-a", triage="ignore")]
    result = d.diff(previous, current, today=TODAY)
    assert result["still_ignored"] == ["sig-a"]
    assert result["resolved"] == []


def test_ignored_signals_carried_forward_when_missing():
    previous = [_signal("sig-a", triage="ignore"), _signal("sig-b", triage="watch")]
    current = [_signal("sig-b", triage="watch")]
    result = d.diff(previous, current, today=TODAY)
    assert result["carry_forward_ignored"] == ["sig-a"]


def test_decayed_signal_flagged_when_past_decay_window_and_not_reobserved():
    previous = [_signal("sig-old", signal_date="2026-07-01", decay_days=7, triage="watch")]
    current = []
    result = d.diff(previous, current, today=TODAY)
    assert result["decayed"] == ["sig-old"]


def test_ignore_signals_not_flagged_as_decayed():
    previous = [_signal("sig-old", signal_date="2026-07-01", decay_days=7, triage="ignore")]
    current = []
    result = d.diff(previous, current, today=TODAY)
    assert result["decayed"] == []


def test_fresh_absent_signal_not_decayed():
    previous = [_signal("sig-recent", signal_date="2026-07-28", decay_days=30, triage="watch")]
    current = []
    result = d.diff(previous, current, today=TODAY)
    assert result["decayed"] == []
