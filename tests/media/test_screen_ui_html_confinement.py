"""Tests for path confinement and security boundaries in HTML motion scenes."""

from __future__ import annotations

import pytest

from gtm_core.screen_ui.html_lint import HtmlLintError, lint_composition
from gtm_core.shots_lint.screen_ui import _lint_screen_ui


def test_shots_lint_rejects_directory_traversal_composition():
    spec = {"backend": "html", "composition": "../evil.html"}
    findings: list[str] = []
    _lint_screen_ui({"production": {"screen_ui": spec}}, prefix="shots[0]", errors=findings)
    assert any("path traversal ('..') is refused" in f for f in findings)


def test_shots_lint_rejects_absolute_path_composition():
    spec = {"backend": "html", "composition": "/etc/passwd"}
    findings: list[str] = []
    _lint_screen_ui({"production": {"screen_ui": spec}}, prefix="shots[0]", errors=findings)
    assert any("must be a relative path" in f for f in findings)


def test_shots_lint_rejects_non_html_composition():
    spec = {"backend": "html", "composition": "video/anim.js"}
    findings: list[str] = []
    _lint_screen_ui({"production": {"screen_ui": spec}}, prefix="shots[0]", errors=findings)
    assert any("must be an .html file" in f for f in findings)


def test_shots_lint_rejects_both_scene_and_composition():
    spec = {"backend": "html", "scene": "kinetic-type", "composition": "my_anim.html"}
    findings: list[str] = []
    _lint_screen_ui({"production": {"screen_ui": spec}}, prefix="shots[0]", errors=findings)
    assert any("both 'scene' and 'composition'" in f for f in findings)


def test_shots_lint_rejects_invalid_backend():
    spec = {"backend": "remotion", "scene": "kinetic-type"}
    findings: list[str] = []
    _lint_screen_ui({"production": {"screen_ui": spec}}, prefix="shots[0]", errors=findings)
    assert any("must be 'pillow' or 'html'" in f for f in findings)


def test_html_lint_rejects_external_network_urls():
    bad_html = """<!doctype html>
<html>
  <head>
    <script src="https://cdn.example.com/gsap.min.js"></script>
  </head>
  <body>
    <div id="root" data-no-timeline="true"></div>
  </body>
</html>"""
    with pytest.raises(HtmlLintError, match="H-SEC-01"):
        lint_composition(bad_html)


def test_html_lint_rejects_external_stylesheets():
    bad_html = """<!doctype html>
<html>
  <head>
    <link rel="stylesheet" href="http://fonts.example.com/font.css" />
  </head>
  <body>
    <div id="root" data-no-timeline="true"></div>
  </body>
</html>"""
    with pytest.raises(HtmlLintError, match="H-SEC-01"):
        lint_composition(bad_html)
