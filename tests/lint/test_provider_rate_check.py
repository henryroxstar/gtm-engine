"""Pytest layer for `tests/lint/provider_rate_check.py` (§R17).

The NEGATIVE controls come first here, deliberately. This rule was added next to an existing one
that explicitly PERMITS a rate card in the same files, and a rule that fires on text another rule
sanctions teaches people the output is noise — after which the real findings go unread too. So
the first thing asserted is what the rule must stay silent about.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


prc = _load("provider_rate_check")
mpc = _load("manifest_prose_check")


# ── negative controls, written before the rule was trusted ─────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # A COST is not a rate — no denominator, so nothing to apply forward.
        "That batch cost 209 credits.",
        "A re-render of a single shot cost 23.",
        # Specs and geometry, which the interface is made of.
        "Captions sit in the centre 60% of frame, ~48-62px at 1080 width.",
        "Emits 7-10 cards, or at most 4 for X.",
        "Floors `<break time=…>` at 0.5s.",
        "Presenter share under 60%, at least one `screen` shot.",
        # Durations and counts near the word credits, but not a rate.
        "Delivered 10.7886s of footage across three beats.",
        "The monthly cap is checked before any paid call.",
    ],
)
def test_does_not_fire_on_non_rates(text):
    assert not prc._RATE.search(text), text


def test_does_not_fire_on_the_owning_modules():
    """`gtm_core/heygen_cost.py` states the rate as its entire job. If the rule fired there it
    would be unsatisfiable — the number has to live somewhere."""
    findings = " ".join(prc.scan(REPO))
    for home in prc.RATE_HOMES:
        assert home not in findings, f"the rule fired on a declared home: {home}"


def test_the_owning_module_actually_contains_a_rate():
    """Guards the reverse failure: if `heygen_cost.py` stopped stating a rate, the home entry
    would be vacuous and the rule would be protecting nothing."""
    text = (REPO / "gtm_core" / "heygen_cost.py").read_text(encoding="utf-8")
    assert prc._RATE.search(text), "the declared HeyGen home no longer states a rate"


def test_the_live_tree_is_clean():
    assert prc.scan(REPO) == []


# ── positive controls ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "measured at 0.865 credits/second against balance deltas",
        "~23 credits per render, per minute rounded up",
        "it costs 1 credit/page",
        "consistent with the 0.83 c/s recorded earlier",
        "roughly ~$4/min of finished video",
        "1 media credit per billed minute",
    ],
)
def test_fires_on_a_restated_rate(text):
    assert prc._RATE.search(text), text


def test_it_catches_the_propagation_that_started_this(tmp_path: Path):
    """POSITIVE CONTROL (§R12). Put the HeyGen rate into a tenant voice note — the shape of one of
    the two real 2026-09-07 instances, and the worst class, because the brain loads tenant
    knowledge and writes copy against it. A fictional tenant slug, not `REPO / "profiles"`: a real
    tenant name here would ship into `tests/`, which the export carves verbatim (§R9) — and only
    `profiles/_template` survives the carve, so a real tenant's directory would not even exist
    there for `voice.read_text()` to open."""
    voice_dir = tmp_path / "profiles" / "acme-robotics" / "knowledge"
    voice_dir.mkdir(parents=True)
    (voice_dir / "voice.md").write_text(
        "# Voice\n\nThe clone bills about 0.865 credits/second.\n", encoding="utf-8"
    )
    findings = prc.scan(tmp_path)
    assert any("voice.md" in f and "0.865" in f for f in findings), findings


# ── partition with §R15: exactly one rule reports a given string ───────────────────


@pytest.mark.parametrize(
    "text",
    [
        "measured 2026-08-30 at 0.865 credits/second",
        "~23 credits per render, per minute rounded up",
    ],
)
def test_a_rate_is_reported_by_r17_and_not_also_by_r15(text):
    """The conflict this partition exists to prevent. §R15 owns dates, trial counts and the
    length budget; §R17 owns rates. A string matched by both would be double-reported, which is
    how a warning column becomes noise."""
    assert prc._RATE.search(text), "R17 must own the rate"
    assert not mpc._MEASUREMENT.search(text), (
        "R15's measurement rule must NOT also match a rate — the two are partitioned"
    )


def test_r15_still_owns_what_it_should():
    """The partition must not have hollowed out §R15: trial counts, balance deltas and one-off
    cost claims are still its business, and dates always were."""
    assert mpc._MEASUREMENT.search("12 of 12 jobs agreed")
    assert mpc._MEASUREMENT.search("the balance delta showed it")
    assert mpc._MEASUREMENT.search("206 credits, which cost more than quoted")
    assert mpc._DATE.search("verified 2026-08-19")


def test_allowlist_entries_name_real_files():
    for entry in prc.load_allowlist():
        assert (REPO / entry).is_file(), f"{entry}: allowlisted path does not exist"
