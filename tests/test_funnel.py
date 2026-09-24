"""Tests for gtm_core.funnel — the delivery-target sizing gate.

These lock in the failure that motivated the module: on 2026-08-12 an operator asked for
"500 accounts end to end", the skill read it as 500 *discovered*, and delivered 7. The
sizing call must say so up front, and the per-stage tripwire must stop a cold run rather
than let it narrow silently.
"""

from __future__ import annotations

import pytest

from gtm_core.funnel import (
    LATE_GATES,
    STAGES,
    TIER_YIELDS,
    WHY_NOW_YIELDS,
    FunnelInfeasible,
    Plan,
    check_stage,
    load_yields,
    record_actuals,
    size,
    valid_yield,
)


def test_delivery_target_needs_more_discovery_than_the_target() -> None:
    """The whole point: 500 delivered != 500 discovered."""
    plan = size(500, pool_available=100_000)
    assert plan.discovery_needed > 500
    assert plan.stage_counts["contact_usable"] >= 500 * 0.9


def test_tier_a_plus_b_is_cheaper_than_tier_a_alone() -> None:
    a = size(200, tier="a", pool_available=100_000)
    ab = size(200, tier="a+b", pool_available=100_000)
    assert a.discovery_needed > ab.discovery_needed


def test_news_why_now_costs_far_more_discovery_than_structural() -> None:
    """The dominant lever — demanding dated news roughly triples the top of the funnel."""
    structural = size(200, why_now_mode="structural", pool_available=100_000)
    news = size(200, why_now_mode="news", pool_available=100_000)
    assert news.discovery_needed > structural.discovery_needed * 2.5


def test_infeasible_when_pool_too_small_names_the_shortfall() -> None:
    """Fail closed, and say what would fix it — this is the message that was missing."""
    with pytest.raises(FunnelInfeasible) as exc:
        size(500, tier="a", why_now_mode="news", pool_available=1_303)
    msg = str(exc.value)
    assert "1,303" in msg
    assert "discover" in msg


def test_infeasible_when_lookup_credits_short() -> None:
    with pytest.raises(FunnelInfeasible) as exc:
        size(500, pool_available=100_000, lookup_credits_remaining=5, no_fallback=True)
    assert "credits remain" in str(exc.value)


def test_backlog_reduces_discovery_and_is_reported() -> None:
    """An already-qualified account needing only a lookup is the cheapest source."""
    without = size(300, pool_available=100_000)
    with_backlog = size(300, pool_available=100_000, backlog_ready=400)
    assert with_backlog.discovery_needed < without.discovery_needed
    assert any("backlog" in w for w in with_backlog.warnings)


def test_backlog_alone_can_remove_the_need_to_discover() -> None:
    plan = size(50, pool_available=0, backlog_ready=500)
    assert plan.discovery_needed == 0


def test_stage_tripwire_passes_at_modelled_rate() -> None:
    plan = size(100, pool_available=100_000)
    v = check_stage("contact_usable", 100, 68, plan)
    assert v["ok"] is True


def test_stage_tripwire_stops_a_cold_stage_and_projects_the_shortfall() -> None:
    """A stage at half the modelled rate must stop the run, not shrink the delivery."""
    plan = size(500, pool_available=100_000)
    v = check_stage("contact_usable", 100, 20, plan)  # 20% vs 68% modelled
    assert v["ok"] is False
    assert v["projected_delivery"] < 500
    assert "STOP" in v["message"]


def test_bad_inputs_rejected() -> None:
    with pytest.raises(ValueError):
        size(0)
    with pytest.raises(ValueError):
        size(10, tier="nope", pool_available=100)
    with pytest.raises(ValueError):
        size(10, why_now_mode="vibes", pool_available=100)
    with pytest.raises(ValueError):
        check_stage("not_a_stage", 1, 1, size(10, pool_available=100))


def test_profile_yields_override_defaults_and_roundtrip(tmp_path) -> None:
    root = tmp_path / "profiles" / "acme"
    (root / "knowledge").mkdir(parents=True)
    record_actuals(root, {"seat_found": 0.5, "contact_usable": 0.5})
    y = load_yields(root)
    assert y["seat_found"] == 0.5
    assert y["contact_usable"] == 0.5
    # a worse-yielding profile must need more discovery for the same delivery
    worse = size(100, profile_root=root, pool_available=100_000)
    default = size(100, pool_available=100_000)
    assert worse.discovery_needed > default.discovery_needed


# --- record_actuals merges, it does not rewrite (2026-09-23) -------------------
#
# The writer rebuilt the file from a 5-line header and only the stages present in its
# `measured` dict. Two consequences, both live and both invisible: the tenant's provenance
# block - the derivation of the very numbers being written - was destroyed on the first
# call, and a partial dict dropped every omitted stage so the next `load_yields` fell back
# to DEFAULT_YIELDS for them. A reverted stage is indistinguishable from a measured one.

_PROVENANCE = """\
# Measured funnel yields for the acme profile.
#
# Seeded 2026-08-12 from the 2026-08-11 bulk run:
#   seat_found      65/84 Tier-A (77%), 71/82 Tier-B (86%)   -> 0.82 blended
#   contact_usable  44/65 Tier-A (67%), 50/71 Tier-B (70%)   -> 0.68 blended

[yields]
seat_found = 0.8200
contact_usable = 0.6800
"""


def _seeded(tmp_path, text: str = _PROVENANCE):
    root = tmp_path / "profiles" / "acme"
    (root / "knowledge").mkdir(parents=True)
    (root / "knowledge" / "funnel-yields.toml").write_text(text, encoding="utf-8")
    return root


def test_the_provenance_block_survives_a_write(tmp_path) -> None:
    """The headline A2 case. The derivation of a number outlives the number."""
    root = _seeded(tmp_path)
    record_actuals(root, {"scored": 0.9})
    after = (root / "knowledge" / "funnel-yields.toml").read_text(encoding="utf-8")
    assert "Seeded 2026-08-12 from the 2026-08-11 bulk run" in after
    assert "65/84 Tier-A (77%), 71/82 Tier-B (86%)" in after
    assert "44/65 Tier-A (67%), 50/71 Tier-B (70%)" in after
    assert load_yields(root)["scored"] == 0.9


def test_a_partial_measured_dict_leaves_the_other_stages_alone(tmp_path) -> None:
    """A stage omitted from this run keeps the value the last run measured.

    Under the old full rewrite it vanished from the file entirely, so `load_yields` served
    DEFAULT_YIELDS for it - which for `seat_found` is the same 0.82 the file happened to
    hold, and for a stage measured away from its default is a silent reversion.
    """
    root = _seeded(tmp_path)
    record_actuals(root, {"contact_usable": 0.41})
    record_actuals(root, {"scored": 0.77})
    y = load_yields(root)
    assert y["contact_usable"] == 0.41, "the earlier measurement reverted"
    assert y["seat_found"] == 0.82, "a stage never in any `measured` dict was dropped"
    assert y["scored"] == 0.77


def test_a_second_write_does_not_duplicate_the_table(tmp_path) -> None:
    """Merging must edit the table in place, not append another one beside it."""
    root = _seeded(tmp_path)
    record_actuals(root, {"scored": 0.9})
    record_actuals(root, {"scored": 0.8})
    after = (root / "knowledge" / "funnel-yields.toml").read_text(encoding="utf-8")
    assert after.count("[yields]") == 1
    assert after.count("scored =") == 1
    assert load_yields(root)["scored"] == 0.8


def test_a_trailing_section_is_preserved(tmp_path) -> None:
    """Everything from the next table header onward is not this function's to rewrite."""
    root = _seeded(tmp_path, _PROVENANCE + '\n[notes]\nowner = "revops"\n')
    record_actuals(root, {"scored": 0.9})
    after = (root / "knowledge" / "funnel-yields.toml").read_text(encoding="utf-8")
    assert '[notes]\nowner = "revops"' in after


def test_a_retired_stage_in_the_file_is_kept_not_tidied_away(tmp_path) -> None:
    """A key STAGES no longer names is tenant data, not garbage. Deleting it to tidy the
    table is the same class of loss as deleting the provenance block."""
    root = _seeded(tmp_path, _PROVENANCE + "retired_stage = 0.5000\n")
    record_actuals(root, {"scored": 0.9})
    after = (root / "knowledge" / "funnel-yields.toml").read_text(encoding="utf-8")
    assert "retired_stage = 0.5000" in after


def test_an_unknown_stage_name_raises_rather_than_writing_nothing(tmp_path) -> None:
    """Closed set, same as `check_stage`. A typo used to write nothing and report success,
    so a run could claim a yield it never recorded."""
    root = _seeded(tmp_path)
    with pytest.raises(ValueError, match="seat_fnd"):
        record_actuals(root, {"seat_fnd": 0.9})
    assert "scored" not in (root / "knowledge" / "funnel-yields.toml").read_text(encoding="utf-8")


def test_a_corrupt_yields_file_is_refused_not_overwritten(tmp_path) -> None:
    """Refuse vs skip. The old writer never parsed the file, so a corrupt one was destroyed
    by the next run instead of reported."""
    root = _seeded(tmp_path, "[yields\nseat_found = ")
    with pytest.raises(ValueError, match="not readable TOML"):
        record_actuals(root, {"scored": 0.9})
    assert (
        (root / "knowledge" / "funnel-yields.toml")
        .read_text(encoding="utf-8")
        .startswith("[yields\n")
    )


def test_missing_yields_file_falls_back_to_defaults(tmp_path) -> None:
    assert load_yields(tmp_path)["seat_found"] > 0
    assert load_yields(None)["contact_usable"] > 0


def test_plan_is_serialisable_and_covers_every_stage() -> None:
    """`stage_counts` still names every stage; `yields` no longer can.

    Since 2026-09-23 the six late gates ship with no `DEFAULT_YIELDS` entry on purpose, so a
    profile that has never measured them has no yield to serialise for them. Asserting
    `yields >= STAGES` would therefore be asserting the defaults this phase deliberately
    withholds — it is the same check, inverted into a demand for the lie.
    """
    plan = size(100, pool_available=100_000)
    assert isinstance(plan, Plan)
    d = plan.to_dict()
    assert set(d["stage_counts"]) == set(STAGES)
    assert set(d["yields"]) == set(STAGES) - set(d["unmeasured"])
    assert set(d["unmeasured"]) == set(LATE_GATES)


def test_yield_tables_are_sane() -> None:
    for table in (TIER_YIELDS, WHY_NOW_YIELDS):
        for k, v in table.items():
            assert 0 < v <= 1, f"{k}={v} outside (0,1]"
    # Tier A must be strictly harder to clear than A+B, else the model is inverted.
    assert TIER_YIELDS["a"] < TIER_YIELDS["a+b"]
    assert WHY_NOW_YIELDS["news"] < WHY_NOW_YIELDS["structural"]


# --- one rule for what a yield is, across all three readers (PH8, 2026-09-24) ---
#
# The defect was a DIVERGENCE, not a missing guard, and it ran in both directions.
# `load_yields` range-checked but took a TOML `true` as 1.0 — the most dangerous value it
# could have become, since it reads as "this gate refuses nobody". `_read_yields_file`
# excluded bools but took any magnitude, so an out-of-range value survived a merge, was
# written back, and was then silently dropped on the next read: the file said one thing
# and the model used another, with nothing reporting the gap. `record_actuals` validated
# stage NAMES against a closed set and validated no value at all.
#
# These tests pin the three to ONE predicate. A test per reader would have let them drift
# again; what is asserted here is their AGREEMENT.

_INADMISSIBLE = [
    pytest.param(True, id="bool-true-is-not-a-measurement"),
    pytest.param(False, id="bool-false"),
    pytest.param(5.0, id="out-of-range-high"),
    pytest.param(0.0, id="zero-is-not-a-yield"),
    pytest.param(-0.5, id="negative"),
    pytest.param("0.5", id="string-that-looks-like-a-number"),
    pytest.param(None, id="null"),
]


@pytest.mark.parametrize("bad", _INADMISSIBLE)
def test_no_reader_accepts_an_inadmissible_yield(tmp_path, bad) -> None:
    """The file reader and the merge reader must refuse the same values.

    Written as one test over both readers on purpose: the bug was that each had its own
    rule, so proving them separately is what let the gap exist.
    """
    root = tmp_path / "profiles" / "acme"
    (root / "knowledge").mkdir(parents=True)
    rendered = {True: "true", False: "false", None: "nan"}.get(
        bad, f'"{bad}"' if isinstance(bad, str) else repr(bad)
    )
    # `seat_found`, NOT `qualified`: `DEFAULT_YIELDS["qualified"]` is 1.0, which is exactly
    # what `float(True)` coerces to — so a bool accepted as 1.0 and a bool correctly
    # refused would both read as 1.0 and the test could not tell them apart (§R18).
    # `seat_found` defaults to 0.82, so the two outcomes are distinguishable.
    (root / "knowledge" / "funnel-yields.toml").write_text(
        f"[yields]\nseat_found = {rendered}\n", encoding="utf-8"
    )

    # Reader A: never serves it. Falls back to the module default instead.
    from gtm_core.funnel import DEFAULT_YIELDS

    assert load_yields(root)["seat_found"] == DEFAULT_YIELDS["seat_found"]

    # Reader B: never carries it through a merge, so it cannot be written back either.
    record_actuals(root, {"contact_usable": 0.5})
    assert "seat_found = " not in (root / "knowledge" / "funnel-yields.toml").read_text(
        encoding="utf-8"
    ), "an inadmissible value survived the merge and was rewritten to disk"


@pytest.mark.parametrize("bad", _INADMISSIBLE)
def test_the_writer_refuses_an_inadmissible_yield(tmp_path, bad) -> None:
    """Reader C. `record_actuals` raises rather than coercing, matching its own posture
    on an unknown stage name — a typo that silently writes something is how a run reports
    a yield it never measured. `float(True)` is 1.0, which is why coercion is the trap."""
    root = tmp_path / "profiles" / "acme"
    (root / "knowledge").mkdir(parents=True)
    with pytest.raises(ValueError, match="inadmissible yield"):
        record_actuals(root, {"seat_found": bad})


def test_a_true_yield_never_becomes_a_hundred_percent_gate(tmp_path) -> None:
    """The specific live failure, end to end: `qualified = true` must not model a gate
    that refuses nobody. Kept beside the parametrized cases because this is the one whose
    consequence is a wrong DISCOVERY TARGET, not merely a rejected value."""
    root = tmp_path / "profiles" / "acme"
    (root / "knowledge").mkdir(parents=True)
    (root / "knowledge" / "funnel-yields.toml").write_text(
        "[yields]\nseat_found = true\n", encoding="utf-8"
    )
    # A bool taken as 1.0 would model a gate that loses nobody, and the sizing call would
    # under-target discovery by the whole of this stage's real 18% loss.
    assert load_yields(root)["seat_found"] == 0.82
    assert (
        size(100, profile_root=root, pool_available=100_000).discovery_needed
        == size(100, pool_available=100_000).discovery_needed
    )


def test_valid_yield_is_the_single_rule() -> None:
    """Guards the predicate itself, so a reader cannot be 'fixed' by loosening it."""
    assert valid_yield(1) and valid_yield(1.0) and valid_yield(0.0001)
    assert not valid_yield(True) and not valid_yield(False)
    assert not valid_yield(0) and not valid_yield(-1) and not valid_yield(1.0001)
