"""Renderer contract: self-contained, theme-aware, and stored-XSS safe.

The security-critical assertion is that any untrusted match-derived string is HTML-escaped
before it reaches the page (§R5 / stored-XSS), and that no external resource is fetched.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gtm_core.community_signal.model import ModelError, load_model
from gtm_core.community_signal.render import render_html

FIXTURE = Path(__file__).parent / "fixtures" / "sample-signal-model.json"


@pytest.fixture
def sample_html() -> str:
    return render_html(load_model(FIXTURE))


def test_renders_full_document(sample_html: str) -> None:
    assert sample_html.startswith("<!DOCTYPE html>")
    assert "<title>Community Signal" in sample_html
    assert sample_html.rstrip().endswith("</html>")


def test_is_self_contained_no_external_assets(sample_html: str) -> None:
    # No network fetch of any kind: no external links, scripts, images, or url()s.
    for needle in ("http://", "https://example.com", "src=", "<link", "@import", "url("):
        if needle == "https://example.com":
            # a source_url in the fixture is allowed, but only inside an href we control
            continue
        assert needle not in sample_html.replace('href="https://example.com/thread/1"', ""), needle
    # The only <script> is the inline theme toggle (no src attribute on it).
    scripts = re.findall(r"<script[^>]*>", sample_html)
    assert scripts == ["<script>"]


def test_all_three_theme_blocks_present(sample_html: str) -> None:
    assert "@media (prefers-color-scheme: light)" in sample_html
    assert ':root[data-theme="light"]' in sample_html
    assert ':root[data-theme="dark"]' in sample_html
    assert 'id="themeBtn"' in sample_html


def test_n_category_legend_renders(sample_html: str) -> None:
    # All three generic categories appear in the stacked-bar legend.
    for label in ("Runtime / Platform", "Identity / NHI", "AI Gateway"):
        assert label in sample_html


def test_untrusted_strings_are_escaped() -> None:
    payload = "<script>alert(1)</script>"
    model = {
        "meta": {"title": "T"},
        "signals": [
            {
                "tag": "demand",
                "title": payload,
                "body": "x" + payload,
                "source_url": "javascript:alert(2)",
                "source_label": payload,
            }
        ],
        "share_of_voice": [{"name": payload, "category": "x", "value": 5}],
    }
    html = render_html(model)
    # The raw payload must never appear un-escaped.
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    # A javascript: URL must be dropped, never emitted as an href.
    assert "javascript:alert(2)" not in html
    # The only <script> tag is still just the theme toggle.
    assert re.findall(r"<script[^>]*>", html) == ["<script>"]


def test_safe_source_url_survives_but_bad_scheme_dropped() -> None:
    model = {
        "meta": {"title": "T"},
        "signals": [
            {
                "tag": "move",
                "title": "ok",
                "body": "b",
                "source_url": "https://good.example/x",
                "source_label": "src",
            },
            {
                "tag": "move",
                "title": "ok2",
                "body": "b2",
                "source_url": "data:text/html,evil",
                "source_label": "src2",
            },
        ],
    }
    html = render_html(model)
    assert 'href="https://good.example/x"' in html
    assert "data:text/html,evil" not in html


def test_missing_title_raises() -> None:
    with pytest.raises(ModelError):
        load_model({"meta": {}})


def test_sparse_model_still_renders() -> None:
    html = render_html({"meta": {"title": "Only a title"}})
    assert "<title>Only a title</title>" in html
    assert html.rstrip().endswith("</html>")
