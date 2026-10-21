"""R11: the Gate 1 HTML brief must not hard-code a craft verdict.

The template used to ship four ``qc-card pass`` cards, so every brief claimed motion restraint,
reading pace, safe-zone fit and continuity had passed whether or not anything had been checked.
The verdict area now points at ``craft.json`` (written by the one ``craft_verdict``), and this
test fails if a static pass card comes back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "plugin/skills/creator-brief/references/creative-brief-template.html"
)

if not TEMPLATE.is_file():
    pytest.skip("creator-brief template not present (public cut)", allow_module_level=True)

_STYLE = re.compile(r"<style\b.*?</style>", re.DOTALL | re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CLASS_ATTR = re.compile(r"""class\s*=\s*(["'])(.*?)\1""", re.DOTALL | re.IGNORECASE)


def static_pass_problems(html: str) -> list[str]:
    """What is wrong with a template body's verdict markup, outside CSS and comments."""
    body = _COMMENT.sub("", _STYLE.sub("", html))
    problems = []
    if "qc-card pass" in body:
        problems.append("a `qc-card pass` card is hard-coded")
    for _quote, value in _CLASS_ATTR.findall(body):
        if "pass" in value.split():
            problems.append(f"an element carries the `pass` class: class={value!r}")
    return problems


def test_the_template_has_no_static_pass_card():
    assert static_pass_problems(TEMPLATE.read_text(encoding="utf-8")) == []


def test_the_verdict_area_points_at_craft_json_and_reads_not_checked():
    html = TEMPLATE.read_text(encoding="utf-8")
    start = html.index('<div class="qc-grid">')
    area = html[start : html.index("Virality & Audience Psychology Stack", start)]
    assert area.count('class="qc-card') == 1
    assert "Not checked" in area
    assert "Craft verdict: see craft.json in this run folder." in area


def test_the_css_no_longer_styles_a_pass_card():
    css = "".join(_STYLE.findall(TEMPLATE.read_text(encoding="utf-8")))
    assert ".qc-card.pass" not in css
    assert css.count("{") == css.count("}")


# --- positive controls: the scan fails on the old markup, in any of its spellings -----------------


def test_the_scan_catches_the_old_literal():
    old = '<div class="qc-grid"><div class="qc-card pass"><div>Motion</div></div></div>'
    assert static_pass_problems(old)


def test_the_scan_catches_a_pass_class_in_another_position_or_quote_style():
    assert static_pass_problems('<div class="pass qc-card"></div>')
    assert static_pass_problems("<div class='qc-card  pass'></div>")


def test_the_scan_ignores_css_comments_and_prose():
    ok = (
        "<style>.qc-card.pass { border-left: 4px solid green; }</style>"
        '<!-- <div class="qc-card pass"> -->'
        '<div class="qc-card not-checked">Hours pass.</div>'
    )
    assert static_pass_problems(ok) == []
