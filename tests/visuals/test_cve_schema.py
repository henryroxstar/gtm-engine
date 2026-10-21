import pytest
from pydantic import ValidationError

from gtm_core.visuals.schema import (
    ContrastPairBlock,
    CVESpec,
    Footnote,
    ScorecardCell,
    ScorecardGridBlock,
    ScorecardRow,
    StatCalloutBlock,
    StatusPill,
)


def test_scorecard_grid_valid_schema():
    spec = CVESpec(
        title="Identity & Access Governance Comparison",
        profile="mockcorp",
        viewport="onepager-pdf",
        blocks=[
            ScorecardGridBlock(
                id="sec-1",
                columns=["Capability", "Our Platform", "Competitor"],
                rows=[
                    ScorecardRow(
                        category="Authentication",
                        cells=[
                            ScorecardCell(
                                text="Decentralized Auth",
                                status=StatusPill.YOURS,
                                footnote_ref="fn-1",
                            ),
                            ScorecardCell(text="Centralized IdP", status=StatusPill.PARTIAL),
                        ],
                    )
                ],
            )
        ],
        footnotes=[Footnote(id="fn-1", text="Requires OpenID4VP protocol support.")],
    )
    assert len(spec.blocks) == 1
    assert spec.footnotes[0].id == "fn-1"


def test_invalid_status_pill_raises():
    with pytest.raises(ValidationError):
        ScorecardCell(text="Invalid", status="unknown_status")


def test_max_footnotes_enforcement():
    with pytest.raises(ValidationError, match="Maximum 3 footnotes allowed"):
        CVESpec(
            title="Too Many Footnotes",
            profile="mockcorp",
            viewport="social-4x5",
            blocks=[StatCalloutBlock(id="stat-1", value="99.9%", label="Uptime")],
            footnotes=[Footnote(id=f"fn-{i}", text=f"Footnote {i}") for i in range(4)],
        )


def test_contrast_pair_block_valid():
    block = ContrastPairBlock(
        id="cp-1",
        left_title="Legacy IAM",
        left_points=["Manual provisioning", "Siloed directories"],
        right_title="Next-Gen Architecture",
        right_points=["Automated lifecycle", "Unified identity plane"],
    )
    assert block.type == "ContrastPair"
    assert len(block.left_points) == 2
    assert len(block.right_points) == 2


def test_contrast_pair_block_max_points_raises():
    with pytest.raises(ValidationError):
        ContrastPairBlock(
            id="cp-1",
            left_title="Legacy",
            left_points=[f"Point {i}" for i in range(6)],
            right_title="Modern",
            right_points=["Point 1"],
        )


def test_stat_callout_block_attributes():
    block = StatCalloutBlock(
        id="stat-1",
        value="99.99%",
        label="Availability SLA",
        delta="+0.09% YoY",
        footnote_ref="fn-1",
    )
    assert block.value == "99.99%"
    assert block.label == "Availability SLA"
    assert block.delta == "+0.09% YoY"
    assert block.footnote_ref == "fn-1"


def test_stat_callout_block_delta_max_length_raises():
    with pytest.raises(ValidationError):
        StatCalloutBlock(
            id="stat-1",
            value="10x",
            label="Throughput",
            delta="A" * 31,
        )


def test_discriminated_union_deserialization():
    raw_data = {
        "title": "Architecture Overview",
        "profile": "mockcorp",
        "viewport": "interactive-hud",
        "blocks": [
            {
                "type": "ScorecardGrid",
                "id": "b-1",
                "columns": ["Feature", "Support"],
                "rows": [
                    {
                        "category": "Core",
                        "cells": [{"text": "OAuth", "status": "shipped"}],
                    }
                ],
            },
            {
                "type": "StatCallout",
                "id": "b-2",
                "value": "42ms",
                "label": "Latency",
                "delta": "-5ms",
            },
            {
                "type": "ContrastPair",
                "id": "b-3",
                "left_title": "Before",
                "left_points": ["Slow"],
                "right_title": "After",
                "right_points": ["Fast"],
            },
        ],
    }
    spec = CVESpec.model_validate(raw_data)
    assert len(spec.blocks) == 3
    assert isinstance(spec.blocks[0], ScorecardGridBlock)
    assert isinstance(spec.blocks[1], StatCalloutBlock)
    assert isinstance(spec.blocks[2], ContrastPairBlock)


def test_footnote_boundary_conditions():
    # Exactly 3 footnotes on social-4x5 passes
    spec_social_3 = CVESpec(
        title="Social 3 FN",
        profile="mockcorp",
        viewport="social-4x5",
        blocks=[StatCalloutBlock(id="stat-1", value="100%", label="Done")],
        footnotes=[Footnote(id=f"fn-{i}", text=f"Footnote {i}") for i in range(3)],
    )
    assert len(spec_social_3.footnotes) == 3

    # 4 footnotes on onepager-pdf passes
    spec_pdf_4 = CVESpec(
        title="PDF 4 FN",
        profile="mockcorp",
        viewport="onepager-pdf",
        blocks=[StatCalloutBlock(id="stat-1", value="100%", label="Done")],
        footnotes=[Footnote(id=f"fn-{i}", text=f"Footnote {i}") for i in range(4)],
    )
    assert len(spec_pdf_4.footnotes) == 4

    # 4 footnotes on deck-16x9 raises ValidationError
    with pytest.raises(
        ValidationError,
        match="Maximum 3 footnotes allowed for viewport 'deck-16x9'",
    ):
        CVESpec(
            title="Deck 4 FN",
            profile="mockcorp",
            viewport="deck-16x9",
            blocks=[StatCalloutBlock(id="stat-1", value="100%", label="Done")],
            footnotes=[Footnote(id=f"fn-{i}", text=f"Footnote {i}") for i in range(4)],
        )


def test_invalid_footnote_id_pattern_raises():
    with pytest.raises(ValidationError):
        Footnote(id="invalid_id", text="Footnote text without fn- prefix")

    with pytest.raises(ValidationError):
        Footnote(id="fn-with spaces", text="Invalid pattern with spaces")
