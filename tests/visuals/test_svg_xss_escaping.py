from __future__ import annotations

import pytest

from gtm_core.visuals.brand_embed import BrandEmbedder
from gtm_core.visuals.render_html import HTMLArtboardBuilder, sanitize_text
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


@pytest.mark.parametrize(
    ("raw_input", "expected"),
    [
        ("hello <world>", "hello &lt;world&gt;"),
        ("quotes \"and\" 'apostrophes'", "quotes &quot;and&quot; &#x27;apostrophes&#x27;"),
        ("control \x00\x08 chars \x1b\x1f\x0c", "control  chars "),
        ("", ""),
    ],
)
def test_sanitize_text_direct(raw_input: str, expected: str) -> None:
    assert sanitize_text(raw_input) == expected


def test_xml_control_chars_and_xss_stripped(fictional_brand_profile):
    spec = CVESpec(
        title="Title with \x00\x08 Control Chars & <script>alert(1)</script>",
        subtitle='Subtitle with \x1f char & "quotes"',
        profile=fictional_brand_profile["profile"],
        viewport="social-4x5",
        blocks=[
            StatCalloutBlock(
                id="stat-1", value="99%", label="Safe <tag> & \x1b ESC", footnote_ref="fn-1"
            )
        ],
        footnotes=[Footnote(id="fn-1", text="Footnote with <b>bold</b> and \x0c form feed")],
    )
    embedder = BrandEmbedder(
        profile=fictional_brand_profile["profile"],
        profiles_root=fictional_brand_profile["profiles_root"],
    )
    builder = HTMLArtboardBuilder(spec=spec, embedder=embedder)
    html = builder.build()

    # Control chars removed
    assert "\x00" not in html
    assert "\x08" not in html
    assert "\x1b" not in html
    assert "\x1f" not in html
    assert "\x0c" not in html

    # HTML tags escaped
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html

    # Structure assertions
    assert 'class="cve-artboard"' in html
    assert "fn-1" in html
    assert "cve-footnotes" in html


def test_render_all_blocks(fictional_brand_profile):
    spec = CVESpec(
        title="Comparison Matrix",
        profile=fictional_brand_profile["profile"],
        viewport="onepager-pdf",
        blocks=[
            ScorecardGridBlock(
                id="sec-1",
                columns=["Category", "Ours", "Competitor"],
                rows=[
                    ScorecardRow(
                        category="Security",
                        cells=[
                            ScorecardCell(text="Zero Trust", status=StatusPill.YOURS),
                            ScorecardCell(text="Legacy SSO", status=StatusPill.PARTIAL),
                        ],
                    )
                ],
            ),
            StatCalloutBlock(id="sec-2", value="10x", label="Efficiency"),
            ContrastPairBlock(
                id="sec-3",
                left_title="Before",
                left_points=["Manual configuration"],
                right_title="After",
                right_points=["Fully automated policy enforcement"],
            ),
        ],
    )
    embedder = BrandEmbedder(
        profile=fictional_brand_profile["profile"],
        profiles_root=fictional_brand_profile["profiles_root"],
    )
    builder = HTMLArtboardBuilder(spec=spec, embedder=embedder)
    html = builder.build()

    assert "cve-scorecard-table" in html
    assert "Zero Trust" in html
    assert "cve-pill-yours" in html
    assert "cve-pill-partial" in html
    assert "cve-stat-card" in html
    assert "10x" in html
    assert "Manual configuration" in html
    assert "Fully automated policy enforcement" in html
