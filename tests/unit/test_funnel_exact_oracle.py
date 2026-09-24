"""The funnel's numbers are checked against a HAND-COMPUTED plan, not against each other.

What the 2026-09-23 hunt found surviving in `gtm_core/funnel.py`: every `*` in the sizing
arithmetic flipped to `/` — the backlog rate, the backlog draw, all four factors of the lookup
estimate — and no test noticed, because every existing test asserts a RELATION (worse yields
need more discovery, a backlog needs less) and a plan that is wrong in both arms of the
relation still satisfies it. The stage-table renderer's loss arithmetic and the CLI the
prospect skill cites (`python -m gtm_core.funnel`, body_template.md) were never executed at
all. The oracle here is decimal arithmetic done by hand in the comments, on yields chosen so
every intermediate is an exact binary fraction; the code must land on the same integers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.funnel import (
    STAGES,
    FunnelInfeasible,
    _cli,
    check_stage,
    load_yields,
    record_actuals,
    render_stage_table,
    size,
)

# Every stage measured; every value a dyadic fraction so the products are exact.
_EXACT = """\
[yields]
scored = 0.5
seat_found = 0.5
contact_usable = 0.25
compliant = 0.5
unsuppressed = 0.5
list_fit = 0.75
integrity = 0.5
lane_enrollable = 0.5
cell_assignable = 0.5
"""


def _profile(tmp_path: Path, text: str) -> Path:
    root = tmp_path / "profiles" / "acme"
    (root / "knowledge").mkdir(parents=True)
    (root / "knowledge" / "funnel-yields.toml").write_text(text, encoding="utf-8")
    return root


def _exact_plan(tmp_path: Path):
    # tier a+b -> qualified 1.0; structural -> why_now 1.0.
    # backlog_rate  = seat_found x contact_usable x why_now = 0.5 x 0.25 x 1 = 0.125
    # need          = ceil(2 / 0.125) = 16;  from_backlog = min(8, 16) = 8
    # delivered     = min(2, int(8 x 0.125 + 0.5)) = int(1.5) = 1;  remaining = 1
    # overall       = 0.5 x 1 x 0.5 x 0.25 x 1 x 0.5 x 0.5 x 0.75 x 0.5 x 0.5 x 0.5 = 3/2048
    # discovery     = ceil(1 / (3/2048)) = ceil(682.67) = 683
    # lookups       = int(683 x 0.5 x 1 x 0.5) + int(8 x 0.5) = 170 + 4 = 174
    return size(
        2,
        profile_root=_profile(tmp_path, _EXACT),
        pool_available=10_000,
        backlog_ready=8,
    )


def test_size_lands_on_the_hand_computed_plan(tmp_path):
    plan = _exact_plan(tmp_path)

    assert plan.discovery_needed == 683
    assert plan.lookups_needed == 174
    assert plan.unmeasured == () and plan.is_floor is False
    # Survivor chain, carried as a float and floored at each gate: 683 -> 341.5 -> ...
    assert plan.stage_counts == {
        "scored": 341,
        "qualified": 341,
        "seat_found": 170,
        "contact_usable": 42,
        "why_now": 42,
        "compliant": 21,
        "unsuppressed": 10,
        "list_fit": 8,
        "integrity": 4,
        "lane_enrollable": 2,
        "cell_assignable": 1,
    }
    assert plan.stage_counts["cell_assignable"] == 2 - 1  # remaining after the backlog draw
    assert plan.warnings == [
        "8 backlog accounts cover ~1 of the target at 12% — cheaper than discovery; "
        "work these before discovering"
    ]


def test_the_stage_table_orders_by_loss_and_prints_no_loss_for_a_lossless_gate(tmp_path):
    plan = _exact_plan(tmp_path)
    assert render_stage_table(plan) == [
        "   1. scored           x0.50  -> 341  (-342)",
        "   3. seat_found       x0.50  -> 170  (-171)",
        "   4. contact_usable   x0.25  -> 42  (-128)",
        "   6. compliant        x0.50  -> 21  (-21)",
        "   7. unsuppressed     x0.50  -> 10  (-11)",
        "   9. integrity        x0.50  -> 4  (-4)",
        "   8. list_fit         x0.75  -> 8  (-2)",
        "  10. lane_enrollable  x0.50  -> 2  (-2)",
        "  11. cell_assignable  x0.50  -> 1  (-1)",
        "   2. qualified        x1.00  -> 341",
        "   5. why_now          x1.00  -> 42",
    ]


def test_size_with_a_fractional_tier_and_an_unmeasured_gate_in_the_middle(tmp_path):
    """tier a (0.16) and hybrid (0.6) so a `/` in place of a `*` on the why_now or qualified
    factor cannot hide behind a 1.0; integrity measured BEHIND an unmeasured compliant so the
    count chain stops and the rate still renders."""
    root = _profile(
        tmp_path,
        "[yields]\nscored = 0.5\nseat_found = 0.5\ncontact_usable = 0.5\nintegrity = 0.5\n",
    )
    # backlog_rate = 0.5 x 0.5 x 0.6 = 0.15; need = ceil(20/0.15) = 134; from_backlog = 100
    # delivered    = min(20, int(100 x 0.15 + 0.5)) = 15;  remaining = 5
    # overall      = 0.5 x 0.16 x 0.5 x 0.5 x 0.6 x 0.5 = 0.006; discovery = ceil(5/0.006) = 834
    # lookups      = int(834 x 0.5 x 0.16 x 0.5) + int(100 x 0.5) = 33 + 50 = 83
    plan = size(
        20,
        tier="a",
        why_now_mode="hybrid",
        profile_root=root,
        pool_available=100_000,
        backlog_ready=100,
    )
    assert plan.discovery_needed == 834
    assert plan.lookups_needed == 83
    # 834 -> 417 -> 66.72 -> 33.36 -> 16.68 -> 10.008, then the chain stops at compliant.
    assert plan.stage_counts == {
        "scored": 417,
        "qualified": 66,
        "seat_found": 33,
        "contact_usable": 16,
        "why_now": 10,
    }
    assert plan.unmeasured == (
        "compliant",
        "unsuppressed",
        "list_fit",
        "lane_enrollable",
        "cell_assignable",
    )
    assert plan.is_floor is True
    assert plan.to_dict()["stage_counts"]["integrity"] is None
    assert plan.warnings[0].startswith("100 backlog accounts cover ~15 of the target at 15%")
    assert "5 gate(s) have never been measured" in plan.warnings[1]
    assert "~834 is a floor" in plan.warnings[1]

    table = render_stage_table(plan)
    assert table[5] == (
        "   9. integrity        x0.50  -> count unknown (an unmeasured gate sits in front of it)"
    )
    assert table[6].startswith("   6. compliant        unmeasured  -> not modelled")
    assert len(table) == 5 + 1 + 5


def test_a_backlog_that_covers_the_target_needs_no_discovery_and_still_needs_lookups():
    # defaults: backlog_rate = 0.82 x 0.68 = 0.5576; need = ceil(2/0.5576) = 4
    # delivered = min(2, int(4 x 0.5576 + 0.5)) = 2; remaining 0 -> discovery 0
    # lookups = 0 + int(4 x 0.82) = 3
    plan = size(2, pool_available=0, backlog_ready=100)
    assert plan.discovery_needed == 0
    assert plan.lookups_needed == 3
    assert all(plan.stage_counts[s] == 0 for s in plan.yields)


def test_a_target_of_one_is_a_positive_target_sized_on_the_default_card():
    # defaults: overall = 0.82 x 0.68 = 0.5576 -> discovery = ceil(1/0.5576) = 2
    # lookups = int(2 x 0.82) = 1
    plan = size(1, pool_available=10_000)
    assert plan.target_delivered == 1
    assert plan.discovery_needed == 2 and plan.lookups_needed == 1


def test_a_credit_shortfall_warns_by_default_and_refuses_only_with_no_fallback(tmp_path):
    root = _profile(tmp_path, _EXACT)
    kw = {"profile_root": root, "pool_available": 10_000, "backlog_ready": 8}
    # Exactly enough credits is not a shortfall.
    assert not any("DEGRADATION" in w for w in size(2, lookup_credits_remaining=174, **kw).warnings)
    short = size(2, lookup_credits_remaining=173, **kw)
    assert any(
        w.startswith("DEGRADATION WARNING: need ~174 contact lookups but only 173 credits remain")
        for w in short.warnings
    )
    with pytest.raises(FunnelInfeasible, match="--no-fallback is active"):
        size(2, lookup_credits_remaining=173, no_fallback=True, **kw)


def test_a_yield_above_one_in_the_file_is_ignored_not_believed(tmp_path):
    root = _profile(tmp_path, "[yields]\nscored = 1.5\nseat_found = 0.5\n")
    y = load_yields(root)
    assert y["scored"] == 1.0 and y["seat_found"] == 0.5


def test_check_stage_boundaries():
    plan = size(10, pool_available=10_000)
    # Nothing in is nothing out, and that is cold — not a division-by-zero pass.
    assert check_stage("seat_found", 0, 0, plan)["ok"] is False
    # Exactly 70% of modelled is the floor, inclusive: 0.82 x 0.7 = 0.574 of 1000.
    on_the_line = check_stage("seat_found", 1000, 574, plan)
    assert on_the_line["ok"] is True and on_the_line["ratio"] == 0.7
    # A stage running hot projects the target, never more than was asked for.
    hot = check_stage("seat_found", 100, 100, plan)
    assert hot["projected_delivery"] == 10 and hot["ratio"] > 1


def test_check_stage_rounds_to_the_documented_places(tmp_path):
    root = _profile(tmp_path, "[yields]\nseat_found = 0.123456\ncontact_usable = 0.8\n")
    plan = size(10, profile_root=root, pool_available=100_000)
    v = check_stage("seat_found", 100_000, 12_345, plan)
    assert (v["modelled"], v["actual"], v["ratio"]) == (0.1235, 0.1235, 1.0)
    cold = check_stage("seat_found", 100_000, 10_000, plan)
    assert cold["ratio"] == 0.81 and cold["ok"] is True
    # 0.7007 / 0.8 = 0.875875 -> three places
    assert check_stage("contact_usable", 10_000, 7_007, plan)["ratio"] == 0.876
    fresh = check_stage("compliant", 100_000, 12_345, plan)
    assert fresh["actual"] == 0.1235 and fresh["modelled"] is None and fresh["ok"] is True


# --- record_actuals keeps EVERY section it did not write ---------------------------------


def test_every_trailing_section_survives_a_write_and_the_path_comes_back(tmp_path):
    root = _profile(
        tmp_path,
        '[yields]\nscored = 0.5\n\n[notes]\nowner = "revops"\n\n[provenance]\nrun = "2026-08-11"\n',
    )
    path = root / "knowledge" / "funnel-yields.toml"
    assert record_actuals(root, {"seat_found": 0.5}) == path
    after = path.read_text(encoding="utf-8")
    assert '[notes]\nowner = "revops"' in after
    assert '[provenance]\nrun = "2026-08-11"' in after
    assert after.index("[notes]") < after.index("[provenance]")
    assert after.count("[yields]") == 1


def test_a_section_directly_under_an_empty_table_survives(tmp_path):
    root = _profile(tmp_path, '[yields]\n[notes]\nowner = "revops"\n')
    record_actuals(root, {"scored": 0.5})
    after = (root / "knowledge" / "funnel-yields.toml").read_text(encoding="utf-8")
    assert '[notes]\nowner = "revops"' in after
    assert after.index("scored = 0.5000") < after.index("[notes]")


def test_a_table_line_that_merely_ends_in_a_bracket_does_not_end_the_table(tmp_path):
    """Only a `[header]` line closes the yields table. A value carrying a bracketed comment,
    or a non-numeric key, stays IN the table — it is rewritten, never duplicated below."""
    root = _profile(
        tmp_path,
        '[yields]\nseat_found = 0.5 # [seed]\ncomment = "by hand"\nscored = 0.25\n\n[notes]\nowner = "revops"\n',
    )
    path = root / "knowledge" / "funnel-yields.toml"
    record_actuals(root, {"contact_usable": 0.5})
    after = path.read_text(encoding="utf-8")
    assert after.count("seat_found") == 1 and after.count("scored") == 1
    assert after.index("scored = 0.2500") < after.index("[notes]")
    y = load_yields(root)  # a duplicated key would have made this raise
    assert (y["scored"], y["seat_found"], y["contact_usable"]) == (0.25, 0.5, 0.5)


# --- the CLI the prospect skill cites ------------------------------------------------------


def test_the_cli_prints_the_plan_and_its_json(tmp_path, capsys):
    root = _profile(tmp_path, _EXACT)
    argv = ["--target", "2", "--profile-root", str(root), "--backlog-ready", "8"]
    assert _cli([*argv, "--pool-available", "10000"]) == 0
    out = capsys.readouterr().out
    assert "  discover           683 accounts" in out
    assert "  contact lookups    ~174" in out
    assert "   1. scored           x0.50  -> 341  (-342)" in out
    payload = json.loads(out[out.index("{") :])
    assert payload["discovery_needed"] == 683
    assert set(payload["stage_counts"]) == set(STAGES)

    assert _cli([*argv, "--pool-available", "10"]) == 2
    out = capsys.readouterr().out
    assert out.startswith("FUNNEL INFEASIBLE\n  need to discover ~683 accounts")
