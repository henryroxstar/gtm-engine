"""`gtm_core.design_render` — the `.md` is the single source for the `.html` companion.

Fixtures are built in ``tmp_path`` and every name in them is fictional (§R9). The HTML shape
mirrors the solution-design template: a head, the one markdown script block filled as
newline + markdown + newline, then the renderer script.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from gtm_core import design_render
from gtm_core.design_render import MAX_IMAGE_BYTES, OPEN_TAG, main

HEAD = (
    '<!DOCTYPE html>\r\n<html lang="en"><head><meta charset="utf-8">\n'
    "<title>Northwind Analytics × Brightpath Labs — Solution Overview</title>\n"
    '<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>\n'
    '</head><body><main id="content"></main>\n'
)
TAIL = (
    '</script>\n<script type="module">\nconst c=document.getElementById("src");\n'
    "</script></body></html>\n"
)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>'
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

MD = """# Northwind Analytics × Brightpath Labs — Solution Overview

<!-- appendix: solution-design-northwind-2026-01-01-appendix.md -->

## Executive summary

![Context](diagrams/context.svg)

![Flow](diagrams/flow.png "the flow")

![Remote](https://example.com/remote.svg)

```markdown
![A sample, not an image](diagrams/context.svg)
```
"""


def _html(stale: str = "\nplaceholder\n") -> str:
    return HEAD + OPEN_TAG + stale + TAIL


@pytest.fixture
def design(tmp_path: Path) -> Path:
    (tmp_path / "diagrams").mkdir()
    (tmp_path / "diagrams" / "context.svg").write_bytes(SVG)
    (tmp_path / "diagrams" / "flow.png").write_bytes(PNG)
    md = tmp_path / "solution-design-northwind-2026-01-01.md"
    md.write_text(MD, encoding="utf-8")
    md.with_suffix(".html").write_bytes(_html().encode("utf-8"))
    return md


def _block(html: str) -> str:
    start = html.index(OPEN_TAG) + len(OPEN_TAG)
    return html[start : html.index("</script>", start)]


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


# ── round trip ────────────────────────────────────────────────────────────────────────


def test_render_then_check_is_current_and_an_md_edit_makes_it_stale(design: Path, capsys) -> None:
    assert main([str(design)]) == 0
    assert main([str(design), "--check"]) == 0
    design.write_text(MD.replace("Executive summary", "Summary for the board"), encoding="utf-8")
    capsys.readouterr()
    assert main([str(design), "--check"]) == 1
    out = capsys.readouterr().out
    assert "block line 6" in out and "Summary for the board" in out
    assert len(out.splitlines()) == 1  # a hint, never a dump


def test_check_writes_nothing(design: Path) -> None:
    html = design.with_suffix(".html")
    before = html.read_bytes()
    assert main([str(design), "--check"]) == 1
    assert html.read_bytes() == before


def test_render_is_idempotent(design: Path) -> None:
    html = design.with_suffix(".html")
    assert main([str(design)]) == 0
    first = html.read_bytes()
    assert main([str(design)]) == 0
    assert html.read_bytes() == first


def test_everything_outside_the_block_is_byte_identical(design: Path) -> None:
    html = design.with_suffix(".html")
    before = _read(html)
    assert main([str(design)]) == 0
    after = _read(html)
    head = before[: before.index(OPEN_TAG) + len(OPEN_TAG)]
    tail = before[before.index("</script>", len(head)) :]
    assert after.startswith(head)  # including the CRLF in the head
    assert after.endswith(tail)
    # the template's fill shape: a newline, the markdown (ending in its own newline), a newline
    assert _block(after).startswith("\n# Northwind") and _block(after).endswith("```\n\n")


def test_html_flag_targets_another_path(design: Path, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere.html"
    other.write_bytes(_html().encode("utf-8"))
    assert main([str(design), "--html", str(other)]) == 0
    assert "Executive summary" in _block(_read(other))
    assert "placeholder" in _block(_read(design.with_suffix(".html")))


# ── images ────────────────────────────────────────────────────────────────────────────


def test_relative_svg_and_png_are_inlined_and_remote_is_untouched(design: Path) -> None:
    assert main([str(design)]) == 0
    block = _block(_read(design.with_suffix(".html")))
    svg = "data:image/svg+xml;base64," + base64.b64encode(SVG).decode()
    png = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert f"![Context]({svg})" in block
    assert f'![Flow]({png} "the flow")' in block
    assert "![Remote](https://example.com/remote.svg)" in block
    # an image inside a fenced code block is a sample, not a picture
    assert "![A sample, not an image](diagrams/context.svg)" in block


def test_an_existing_data_uri_is_left_as_written(design: Path) -> None:
    uri = "data:image/png;base64,AAAA"
    design.write_text(f"# T\n\n![x]({uri})\n", encoding="utf-8")
    assert main([str(design)]) == 0
    assert f"![x]({uri})" in _block(_read(design.with_suffix(".html")))


@pytest.mark.parametrize(
    "target",
    ["../outside.svg", "diagrams/../../outside.svg", "%2e%2e/outside.svg", "/etc/outside.svg"],
)
def test_an_image_outside_the_folder_is_refused(design: Path, target: str, capsys) -> None:
    (design.parent.parent / "outside.svg").write_bytes(SVG)
    html = design.with_suffix(".html")
    before = html.read_bytes()
    design.write_text(f"# T\n\n![x]({target})\n", encoding="utf-8")
    assert main([str(design)]) == 2
    assert html.read_bytes() == before
    assert "inside the design's folder" in capsys.readouterr().err


def test_a_symlink_that_escapes_the_folder_is_refused(design: Path) -> None:
    outside = design.parent.parent / "secret.svg"
    outside.write_bytes(SVG)
    (design.parent / "diagrams" / "link.svg").symlink_to(outside)
    design.write_text("# T\n\n![x](diagrams/link.svg)\n", encoding="utf-8")
    assert main([str(design)]) == 2


def test_an_oversize_image_is_refused(design: Path, capsys) -> None:
    (design.parent / "diagrams" / "huge.png").write_bytes(b"\x00" * (MAX_IMAGE_BYTES + 1))
    design.write_text("# T\n\n![x](diagrams/huge.png)\n", encoding="utf-8")
    assert main([str(design)]) == 2
    assert "cap" in capsys.readouterr().err


def test_a_missing_image_is_refused(design: Path) -> None:
    design.write_text("# T\n\n![x](diagrams/absent.svg)\n", encoding="utf-8")
    assert main([str(design)]) == 2


# ── refusals ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("close", ["</script>", "</SCRIPT >", "</Script"])
def test_a_script_close_in_the_markdown_is_refused(design: Path, close: str) -> None:
    html = design.with_suffix(".html")
    before = html.read_bytes()
    design.write_text(f"# T\n\nsee {close} here\n", encoding="utf-8")
    assert main([str(design)]) == 2
    assert main([str(design), "--check"]) == 2
    assert html.read_bytes() == before


def test_missing_script_block_is_refused(design: Path, capsys) -> None:
    design.with_suffix(".html").write_text("<html><body></body></html>", encoding="utf-8")
    assert main([str(design)]) == 2
    assert "found 0" in capsys.readouterr().err


def test_duplicate_script_block_is_refused(design: Path, capsys) -> None:
    html = design.with_suffix(".html")
    html.write_text(_html() + OPEN_TAG + "\nagain\n</script>", encoding="utf-8")
    assert main([str(design)]) == 2
    assert "found 2" in capsys.readouterr().err


def test_missing_html_is_refused(design: Path) -> None:
    design.with_suffix(".html").unlink()
    assert main([str(design)]) == 2


def test_the_module_opens_no_network_or_subprocess() -> None:
    source = Path(design_render.__file__).read_text(encoding="utf-8")
    for banned in ("import subprocess", "urllib.request", "import socket", "import http"):
        assert banned not in source
