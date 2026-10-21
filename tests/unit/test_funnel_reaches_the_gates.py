"""The funnel models the six gates that actually refuse a row — and models none of them at 1.0.

`STAGES` held five DISCOVERY stages and stopped before every gate that decides whether a row
may be enrolled. So the forecaster built for *"blocks occur at the end and I keep fighting
this tool to unlock more emails"* modelled exactly the half of the funnel that does not
block. P5a adds the six, in pipeline order.

**The absence of a `DEFAULT_YIELDS` entry for those six is the phase.** `load_yields` merges
that dict per key, so `1.00` there does not read as "unknown" — it forecasts **zero loss** at
a real gate, on every profile that has never measured it, while looking exactly as
authoritative as a measured number. That is the original surprise, reproduced by the fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.funnel import (
    DEFAULT_YIELDS,
    LATE_GATES,
    STAGES,
    Plan,
    check_stage,
    load_yields,
    record_actuals,
    render_stage_table,
    size,
)

_DISCOVERY = STAGES[:5]


REPO = Path(__file__).resolve().parents[2]


def _seeded_profile() -> Path:
    """The tenant profile P5b seeded, discovered rather than named (de-brand lint).

    Any profile whose committed yields file carries a late-gate entry. There is exactly one
    today; globbing rather than hardcoding also means the next tenant to seed is covered by
    these checks without editing them.
    """
    for path in sorted(REPO.glob("profiles/*/knowledge/funnel-yields.toml")):
        if LATE_GATES & set(load_yields(path.parent.parent)):
            return path.parent.parent
    pytest.skip("no profile in this checkout has seeded the late gates")


# --- the stage set itself ---------------------------------------------------


def test_the_six_late_gates_are_present_in_pipeline_order():
    assert STAGES[:5] == ("scored", "qualified", "seat_found", "contact_usable", "why_now")
    assert STAGES[5:] == (
        "compliant",
        "unsuppressed",
        "list_fit",
        "integrity",
        "lane_enrollable",
        "cell_assignable",
    )
    assert len(set(STAGES)) == len(STAGES), "a duplicated stage would be counted twice"
    assert LATE_GATES == set(STAGES[5:])


def test_the_late_gates_have_no_default_yield():
    """THE control for this phase, stated as directly as it can be.

    Adding `"integrity": 1.00` to DEFAULT_YIELDS is the single change that would quietly
    undo P5a — the renderer would stop saying `unmeasured`, the number would look modelled,
    and the forecast would claim a gate that refuses ~a third of a real list refuses nobody.
    """
    assert not (LATE_GATES & set(DEFAULT_YIELDS)), (
        "a late gate has a default yield. 1.00 at an unmeasured gate is not 'unknown', it "
        "is 'forecast zero loss' — the exact surprise this phase exists to remove"
    )
    assert set(DEFAULT_YIELDS) == set(_DISCOVERY)


def test_the_stage_set_is_closed_and_fails_loudly():
    plan = size(10, pool_available=100_000)
    with pytest.raises(ValueError, match="unknown stage"):
        check_stage("nonsense", 1, 1, plan)


def test_the_receipts_stage_set_stays_a_separate_thing():
    """`prospect_status_receipt._STAGES` is an unrelated status-bucket set with a colliding
    name. Merging them would put a funnel stage in an operator status table and vice versa."""
    from gtm_core.prospect_status_receipt import BUCKETS

    assert not (set(BUCKETS) & set(STAGES))


# --- unmeasured renders as unmeasured, never as 1.0 -------------------------


def _profile(tmp_path, body: str | None = None):
    root = tmp_path / "profiles" / "acme"
    (root / "knowledge").mkdir(parents=True)
    if body is not None:
        (root / "knowledge" / "funnel-yields.toml").write_text(body, encoding="utf-8")
    return root


def test_a_profile_that_has_measured_none_of_them_still_sizes(tmp_path):
    """(a) `size()` does not raise. The forecast may never refuse a run the real gates would
    have passed — that would move the fight earlier instead of removing it."""
    root = _profile(tmp_path, "[yields]\nseat_found = 0.8000\n")
    plan = size(100, profile_root=root, pool_available=100_000)
    assert isinstance(plan, Plan)
    assert plan.discovery_needed > 0


def test_every_unmeasured_gate_is_named_as_unmeasured(tmp_path):
    """(b) Named, not silently dropped. An operator who cannot see the gap cannot ask for it
    to be measured."""
    root = _profile(tmp_path, "[yields]\nseat_found = 0.8000\n")
    plan = size(100, profile_root=root, pool_available=100_000)
    assert set(plan.unmeasured) == LATE_GATES
    table = "\n".join(render_stage_table(plan))
    for gate in LATE_GATES:
        assert gate in table
    assert table.count("unmeasured") == len(LATE_GATES)


def test_an_unmeasured_gate_is_absent_from_the_product_not_multiplied_as_one(tmp_path):
    """(c) The arithmetic, not just the wording.

    A profile that HAS measured `integrity` at 0.50 must need roughly twice the discovery of
    one that has not — because the second is not modelling that gate at all. If an unmeasured
    gate were multiplied in as 1.0, the two would come out identical and the `unmeasured`
    label would be decoration over a forecast that had already assumed the answer.
    """
    none_measured = size(
        100, profile_root=_profile(tmp_path / "a", "[yields]\n"), pool_available=1_000_000
    )
    half_refused = size(
        100,
        profile_root=_profile(tmp_path / "b", "[yields]\nintegrity = 0.5000\n"),
        pool_available=1_000_000,
    )
    assert half_refused.discovery_needed >= none_measured.discovery_needed * 1.9
    assert "integrity" in none_measured.unmeasured
    assert "integrity" not in half_refused.unmeasured


def test_the_discovery_number_says_it_is_a_floor(tmp_path):
    """A number that does not model six gates is a lower bound, and the plan must not offer
    it as an estimate. It can only go up once those gates are measured."""
    root = _profile(tmp_path, "[yields]\n")
    plan = size(100, profile_root=root, pool_available=1_000_000)
    assert plan.is_floor
    assert plan.to_dict()["discovery_is_floor"] is True
    assert any("floor" in w for w in plan.warnings)
    assert any(gate in w for w in plan.warnings for gate in LATE_GATES)


def test_a_fully_measured_profile_is_not_a_floor(tmp_path):
    """Positive control on `is_floor` — it must track the measurement, not be always-on."""
    body = "[yields]\n" + "".join(f"{s} = 0.9000\n" for s in STAGES)
    plan = size(10, profile_root=_profile(tmp_path, body), pool_available=1_000_000)
    assert plan.unmeasured == ()
    assert not plan.is_floor
    assert plan.to_dict()["discovery_is_floor"] is False


def test_the_survivor_chain_stops_at_the_first_unmeasured_gate(tmp_path):
    """A count after an unmeasured gate is the count you get by pretending that gate refuses
    nobody — so it is withheld rather than printed."""
    body = "[yields]\n" + "".join(f"{s} = 0.9000\n" for s in STAGES if s != "compliant")
    plan = size(10, profile_root=_profile(tmp_path, body), pool_available=1_000_000)
    assert "why_now" in plan.stage_counts
    for later in ("compliant", "unsuppressed", "integrity", "cell_assignable"):
        assert later not in plan.stage_counts


# --- serialisation ----------------------------------------------------------


def test_plan_round_trips_every_stage_measured_or_not(tmp_path):
    """`stage_counts` in `to_dict` carries every stage, so a consumer indexing by name
    cannot KeyError on a gate added after it was written."""
    plan = size(100, profile_root=_profile(tmp_path, "[yields]\n"), pool_available=1_000_000)
    counts = plan.to_dict()["stage_counts"]
    for stage in STAGES:
        assert stage in counts
    assert counts["integrity"] is None, "absent must not serialise as 0"
    assert counts["scored"] is not None


# --- the tripwire on an unmeasured stage ------------------------------------


def test_the_tripwire_does_not_fire_on_a_stage_it_never_modelled(tmp_path):
    """This run IS the measurement. `ok=False` here would stop a run on the grounds that we
    had never measured it before — a refusal the forecast is not allowed to make."""
    plan = size(100, profile_root=_profile(tmp_path, "[yields]\n"), pool_available=1_000_000)
    verdict = check_stage("integrity", 100, 30, plan)
    assert verdict["ok"] is True
    assert verdict["modelled"] is None
    assert verdict["actual"] == 0.3
    assert "unmeasured before this run" in verdict["message"]


def test_the_tripwire_does_fire_once_the_stage_is_measured(tmp_path):
    """Positive control: the tripwire is not disabled for late gates in general, only for
    ones with nothing to compare against."""
    root = _profile(tmp_path, "[yields]\nintegrity = 0.9000\n")
    plan = size(100, profile_root=root, pool_available=1_000_000)
    assert check_stage("integrity", 100, 30, plan)["ok"] is False


# --- the table is ordered by leak size --------------------------------------


def test_the_table_leads_with_the_biggest_loss(tmp_path):
    """The operator's question is "where did my 500 go?" and the answer is the biggest
    number. Pipeline order buries it behind stages that lose nothing."""
    # `integrity` is the ninth stage and the biggest leak; `seat_found` is third and small.
    # In pipeline order the operator reads five lines before reaching the answer.
    body = (
        "[yields]\nscored = 1.0000\nseat_found = 0.9500\ncontact_usable = 0.9500\n"
        "compliant = 0.9500\nunsuppressed = 0.9500\nlist_fit = 0.9500\n"
        "integrity = 0.2000\nlane_enrollable = 0.9500\ncell_assignable = 0.9500\n"
    )
    plan = size(100, profile_root=_profile(tmp_path, body), pool_available=10_000_000)
    lines = render_stage_table(plan)
    assert "integrity" in lines[0], lines
    # Pipeline position is still shown, so the re-sort is legible as a re-sort.
    assert lines[0].strip().startswith("9.")
    assert "seat_found" not in lines[0]


def test_unmeasured_gates_sort_last_and_carry_no_rate(tmp_path):
    lines = render_stage_table(
        size(100, profile_root=_profile(tmp_path, "[yields]\n"), pool_available=1_000_000)
    )
    measured_last = max(i for i, ln in enumerate(lines) if " x" in ln)
    unmeasured_first = min(i for i, ln in enumerate(lines) if "unmeasured" in ln)
    assert measured_last < unmeasured_first
    for line in lines[unmeasured_first:]:
        assert " x" not in line, "an unmeasured gate must carry no rate for a reader to use"


# --- record_actuals accepts the new stages ----------------------------------


def test_a_late_gate_measured_once_is_modelled_from_then_on(tmp_path):
    """The loop P5b closes: measure, write, and the next plan stops calling it unmeasured."""
    root = _profile(tmp_path, "[yields]\n")
    assert "integrity" in size(100, profile_root=root, pool_available=1_000_000).unmeasured
    record_actuals(root, {"integrity": 0.62})
    after = size(100, profile_root=root, pool_available=1_000_000)
    assert "integrity" not in after.unmeasured
    assert load_yields(root)["integrity"] == 0.62


# --- P5b: the measurement direction, and what the seeded profile now models ----
#
# Sequential multiplication is sound ONLY if each yield is survivors-out / survivors-in at
# that position. Measuring it the natural way — drops / total — double-counts every row that
# fails two gates. On the list P5b seeded from, 22 rows fail both `unsuppressed` and
# `integrity`, and the naive reading modelled 590 researched rows down to 78 where the real
# gates produce 143.


def test_a_row_failing_two_gates_is_counted_once(tmp_path):
    """The property the whole model rests on, on a list built to break it.

    100 rows. `unsuppressed` refuses 20 and `integrity` refuses 30, and 10 rows are in BOTH
    sets — so 40 distinct rows are refused and 60 survive. Chained yields reproduce 60.
    Drops-over-total yields (0.80 and 0.70) reproduce 56, because the 10 overlapping rows
    are charged at both gates.
    """
    n = 100
    refused_by_either = 40  # 20 + 30 - 10 overlapping
    survivors = n - refused_by_either

    chained = {"unsuppressed": 80 / 100, "integrity": 60 / 80}
    naive = {"unsuppressed": 1 - 20 / n, "integrity": 1 - 30 / n}

    assert round(n * chained["unsuppressed"] * chained["integrity"]) == survivors
    assert round(n * naive["unsuppressed"] * naive["integrity"]) < survivors, (
        "drops/total must under-forecast survivors — if it did not, this test could not "
        "tell the two measurement directions apart"
    )

    body = (
        "[yields]\n"
        + "".join(f"{s} = 1.0000\n" for s in STAGES if s not in chained)
        + "".join(f"{k} = {v:.4f}\n" for k, v in chained.items())
    )
    plan = size(survivors, profile_root=_profile(tmp_path, body), pool_available=1_000_000)
    assert plan.discovery_needed == n


def test_the_seeded_profile_models_the_late_gates(tmp_path):
    """P5b's deliverable, read off the committed tenant file rather than retyped (§R14).

    The seed is what turns the six gates from `unmeasured` into part of the forecast, and
    the point of the phase is that the number MOVES: a target the discovery stages alone
    size at ~900 accounts needs several times that once the gates that actually refuse rows
    are in the product.
    """
    root = _seeded_profile()
    y = load_yields(root)
    seeded = LATE_GATES & set(y)
    assert len(seeded) >= 5, f"P5b seeded too few late gates: {sorted(seeded)}"
    for gate in seeded:
        assert 0 < y[gate] < 1, f"{gate} seeded at {y[gate]} — a boundary value is not a rate"

    plan = size(500, profile_root=root, pool_available=1_000_000)
    discovery_only = size(500, pool_available=1_000_000).discovery_needed
    assert plan.discovery_needed > discovery_only * 2, (
        "the seeded gates barely moved the forecast — either the seed did not land or the "
        "gates are being multiplied in somewhere they should not be"
    )


def test_compliant_is_deliberately_left_unmeasured(tmp_path):
    """The finding P5b's data produced, pinned so it is not "fixed" by writing 1.0.

    `compliant` reads 1.0 on any researched list because the market gate runs at
    consolidation, BEFORE research completes — every row that reached the researched
    population had already passed it. On the raw pool the same gate refuses 43 of 1265.
    Recording 1.0 would tell every future forecast that compliance refuses nobody, which is
    exactly the lie `DEFAULT_YIELDS` withholds. The PRD placed this gate at position 6; the
    pipeline runs it earlier, and that mismatch is the finding rather than the number.
    """
    root = _seeded_profile()
    assert "compliant" not in load_yields(root)
    text = (root / "knowledge" / "funnel-yields.toml").read_text(encoding="utf-8")
    assert "compliant" in text, "the reason it is unmeasured must be written down, not implied"


def test_the_seed_did_not_destroy_the_earlier_provenance(tmp_path):
    """A2 in production. The 2026-08-12 derivation block is still in the file after a real
    `record_actuals` call wrote five new stages into it."""
    path = _seeded_profile() / "knowledge" / "funnel-yields.toml"
    text = path.read_text(encoding="utf-8")
    assert "Seeded 2026-08-12" in text
    assert text.count("[yields]") == 1


def test_a_rate_is_known_behind_an_unmeasured_gate_even_when_the_count_is_not(tmp_path):
    """Both facts, never one quietly chosen. A measured gate sitting behind an unmeasured
    one still shows its RATE; only its absolute survivor count is withheld, because
    computing that would assume the gate in front of it refuses nobody."""
    body = "[yields]\n" + "".join(f"{s} = 0.9000\n" for s in STAGES if s != "compliant")
    plan = size(100, profile_root=_profile(tmp_path, body), pool_available=10_000_000)
    lines = render_stage_table(plan)
    behind = [ln for ln in lines if "count unknown" in ln]
    # Every gate after `compliant` is behind it, and every one still shows its rate.
    assert len(behind) == len(STAGES) - STAGES.index("compliant") - 1
    for line in behind:
        assert "x0.90" in line, line
    assert any("compliant" in ln and "unmeasured" in ln for ln in lines)
