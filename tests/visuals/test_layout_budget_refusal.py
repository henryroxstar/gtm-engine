import pytest

from gtm_core.visuals.compiler import DoesNotFitError, LayoutCompiler, compute_cache_key
from gtm_core.visuals.schema import (
    ContrastPairBlock,
    CVESpec,
    ScorecardCell,
    ScorecardGridBlock,
    ScorecardRow,
    StatCalloutBlock,
    StatusPill,
)


def test_compiler_auto_downscale_across_all_blocks():
    # Long label in StatCalloutBlock triggers downscale tier
    spec = CVESpec(
        title="Valid Title",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[
            StatCalloutBlock(
                id="stat-1",
                value="100%",
                label="A long label that fits via modest auto-downscaling within 15 percent margin",
            )
        ],
    )
    compiler = LayoutCompiler(spec)
    result = compiler.compile()
    assert result["scale_factor"] < 1.0
    assert result["scale_factor"] >= 0.85


def test_compiler_hard_refusal_emits_structured_json():
    # Egregiously long text that exceeds budget
    spec = CVESpec(
        title="Over budget",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[
            ScorecardGridBlock(
                id="grid-overflow",
                columns=["Col1", "Col2"],
                rows=[
                    ScorecardRow(
                        category="Row 1",
                        cells=[
                            ScorecardCell(
                                text="This text is deliberately way too long to fit into any reasonable social card cell without destroying layout typography hierarchy and breaking visual readability completely.",
                                status=StatusPill.YOURS,
                            )
                        ],
                    )
                ],
            )
        ],
    )
    compiler = LayoutCompiler(spec)
    with pytest.raises(DoesNotFitError) as exc_info:
        compiler.compile()
    diag = exc_info.value.diagnostics
    assert diag["block_id"] == "grid-overflow"
    assert diag["field"] == "text"
    assert diag["current_count"] == len(spec.blocks[0].rows[0].cells[0].text)
    assert diag["max_allowed"] == 60
    assert diag["target_viewport"] == "social-4x5"
    assert diag["suggested_reduction_pct"] > 0


def test_scorecard_row_count_exceeded_refusal():
    # social-4x5 max_rows is 6; 7 rows should raise DoesNotFitError
    rows = [
        ScorecardRow(
            category=f"Category {i}",
            cells=[ScorecardCell(text=f"Cell {i}", status=StatusPill.SHIPPED)],
        )
        for i in range(7)
    ]
    spec = CVESpec(
        title="Too Many Rows",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[
            ScorecardGridBlock(
                id="grid-row-overflow",
                columns=["Category", "Status"],
                rows=rows,
            )
        ],
    )
    compiler = LayoutCompiler(spec)
    with pytest.raises(DoesNotFitError) as exc_info:
        compiler.compile()
    diag = exc_info.value.diagnostics
    assert diag["block_id"] == "grid-row-overflow"
    assert diag["field"] == "rows"
    assert diag["current_count"] == 7
    assert diag["max_allowed"] == 6
    assert diag["target_viewport"] == "social-4x5"
    assert diag["suggested_reduction_pct"] == int((7 - 6) / 7 * 100)


def test_stat_callout_overflow_refusal():
    # social-4x5 max_chars_stat is 70; 70 * 1.30 = 91 chars max
    # 110 chars should exceed 1.30 ratio and trigger DoesNotFitError
    spec = CVESpec(
        title="Stat Overflow",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[
            StatCalloutBlock(
                id="stat-overflow",
                value="99.9%",
                label="Excessively wordy stat label that clearly exceeds the one point three overflow threshold for social cards",
            )
        ],
    )
    compiler = LayoutCompiler(spec)
    with pytest.raises(DoesNotFitError) as exc_info:
        compiler.compile()
    diag = exc_info.value.diagnostics
    assert diag["block_id"] == "stat-overflow"
    assert diag["field"] == "label"
    assert diag["current_count"] == len(spec.blocks[0].label)
    assert diag["max_allowed"] == 70
    assert diag["target_viewport"] == "social-4x5"
    assert diag["suggested_reduction_pct"] > 0


def test_contrast_pair_downscale_and_overflow_refusal():
    # Downscale test: point between 80 and 80 * 1.30 = 104 chars
    downscale_spec = CVESpec(
        title="Contrast Downscale",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[
            ContrastPairBlock(
                id="contrast-scale",
                left_title="Legacy Way",
                left_points=[
                    "A moderately long statement describing the old approach that requires a modest auto-scale down"
                ],
                right_title="Modern Way",
                right_points=["Fast and automated modern workflow"],
            )
        ],
    )
    compiler = LayoutCompiler(downscale_spec)
    res = compiler.compile()
    assert 0.85 <= res["scale_factor"] < 1.0

    # Overflow test: point exceeding 80 * 1.30 = 104 chars
    overflow_point = "This point is written with unnecessary verbosity describing legacy infrastructure architectures in extreme detail exceeding the maximum allowable layout budget for contrast pairs completely"
    overflow_spec = CVESpec(
        title="Contrast Overflow",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[
            ContrastPairBlock(
                id="contrast-overflow",
                left_title="Legacy Architecture",
                left_points=[overflow_point],
                right_title="Modern Architecture",
                right_points=["Streamlined microservices"],
            )
        ],
    )
    compiler_overflow = LayoutCompiler(overflow_spec)
    with pytest.raises(DoesNotFitError) as exc_info:
        compiler_overflow.compile()
    diag = exc_info.value.diagnostics
    assert diag["block_id"] == "contrast-overflow"
    assert diag["field"] == "point"
    assert diag["current_count"] == len(overflow_point)
    assert diag["max_allowed"] == 80
    assert diag["target_viewport"] == "social-4x5"
    assert diag["suggested_reduction_pct"] > 0


def test_boundary_conditions_and_edge_cases():
    # 1. Exact boundary ratio == 1.0 -> scale_factor == 1.0
    exact_label = "x" * 70  # max_chars_stat for social-4x5 is 70
    spec_exact = CVESpec(
        title="Exact",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[StatCalloutBlock(id="s-exact", value="1", label=exact_label)],
    )
    assert LayoutCompiler(spec_exact).compile()["scale_factor"] == 1.0

    # 2. Ratio exactly at 1.30 -> fits with scale_factor == 0.85 (clamped)
    # 70 * 1.30 = 91 chars
    spec_91 = CVESpec(
        title="At Boundary",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[StatCalloutBlock(id="s-91", value="1", label="x" * 91)],
    )
    res_91 = LayoutCompiler(spec_91).compile()
    assert res_91["scale_factor"] == 0.85

    # 3. Ratio strictly > 1.30 -> raises DoesNotFitError (92 chars > 91)
    spec_92 = CVESpec(
        title="Exceeds Boundary",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[StatCalloutBlock(id="s-92", value="1", label="x" * 92)],
    )
    with pytest.raises(DoesNotFitError):
        LayoutCompiler(spec_92).compile()

    # 4. Empty/zero length string does not divide by zero or crash
    spec_empty = CVESpec(
        title="Empty",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[StatCalloutBlock(id="s-empty", value="0", label="")],
    )
    res_empty = LayoutCompiler(spec_empty).compile()
    assert res_empty["scale_factor"] == 1.0


def test_density_budget_scaling():
    # Base max_chars_stat on social-4x5 is 70
    # For micro (0.85): max is int(70 * 0.85) = 59. 59 * 1.30 = 76 chars max.
    # A label of 70 chars fits standard with scale_factor 1.0, but for micro:
    # 70 / 59 = 1.186 -> requires downscaling
    spec_micro = CVESpec(
        title="Micro Test",
        profile="mockcorp",
        viewport="social-4x5",
        density="micro",
        blocks=[StatCalloutBlock(id="s-micro", value="1", label="x" * 70)],
    )
    res_micro = LayoutCompiler(spec_micro).compile()
    assert res_micro["density"] == "micro"
    assert res_micro["scale_factor"] < 1.0

    # For deep (1.15): max is int(70 * 1.15) = 80 chars.
    # 70 chars fits with scale_factor 1.0
    spec_deep = CVESpec(
        title="Deep Test",
        profile="mockcorp",
        viewport="social-4x5",
        density="deep",
        blocks=[StatCalloutBlock(id="s-deep", value="1", label="x" * 70)],
    )
    res_deep = LayoutCompiler(spec_deep).compile()
    assert res_deep["density"] == "deep"
    assert res_deep["scale_factor"] == 1.0


def test_multi_block_interaction():
    # Spec with two blocks: one normal (scale 1.0) and one slightly long (scale 0.90)
    # The overall layout scale factor must reflect the worst (lowest) scale factor
    spec_multi = CVESpec(
        title="Multi Block",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[
            StatCalloutBlock(id="stat-1", value="1", label="Short label"),
            ContrastPairBlock(
                id="contrast-1",
                left_title="Title A",
                left_points=[
                    "A point that is somewhat long and triggers downscaling because it exceeds eighty characters."
                ],
                right_title="Title B",
                right_points=["Another normal point"],
            ),
        ],
    )
    res = LayoutCompiler(spec_multi).compile()
    assert res["scale_factor"] < 1.0


def test_compute_cache_key_determinism():
    spec1 = CVESpec(
        title="Cache Test",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[StatCalloutBlock(id="s1", value="10", label="Test")],
    )
    spec2 = CVESpec(
        title="Cache Test",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[StatCalloutBlock(id="s1", value="10", label="Test")],
    )
    compiler1 = LayoutCompiler(spec1)
    compiler2 = LayoutCompiler(spec2)

    key1 = compiler1.compute_cache_key("token_hash_abc")
    key2 = compiler2.compute_cache_key("token_hash_abc")
    assert key1 == key2
    assert len(key1) == 64

    # Standalone function matches method
    assert compute_cache_key(spec1, "token_hash_abc") == key1

    # Different token digest yields different key
    assert compiler1.compute_cache_key("token_hash_def") != key1

    # Modified spec yields different key
    spec3 = CVESpec(
        title="Cache Test Modified",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[StatCalloutBlock(id="s1", value="10", label="Test")],
    )
    assert LayoutCompiler(spec3).compute_cache_key("token_hash_abc") != key1
