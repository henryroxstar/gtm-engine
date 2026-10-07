"""`gtm_core.design_render` — the `.md` is the single source for the `.html` companion.

Fixtures are built in ``tmp_path`` and every name in them is fictional (§R9). The HTML shape
mirrors the solution-design template: a head, the content div holding the one marker pair the
render writes between, then the page script. The shipped template itself is read from the skill's
reference doc, so the tests that render it cover the page a customer opens.
"""

from __future__ import annotations

import base64
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from gtm_core import design_render
from gtm_core.design_render import END, MAX_IMAGE_BYTES, START, main, render_html

COMPANION_DOC = (
    Path(__file__).resolve().parents[1]
    / "plugin/skills/solution-design/references/html-companion.md"
)
HEAD = (
    '<!DOCTYPE html>\r\n<html lang="en"><head><meta charset="utf-8">\n'
    "<title>Northwind Analytics × Brightpath Labs — Solution Overview</title>\n"
    '</head><body><main class="doc"><div id="content">'
)
TAIL = (
    '</div></main>\n<script type="module">\nconst c=document.getElementById("content");\n'
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


def _html(stale: str = "\n<p>placeholder</p>\n") -> str:
    return HEAD + START + stale + END + TAIL


@pytest.fixture
def design(tmp_path: Path) -> Path:
    (tmp_path / "diagrams").mkdir()
    (tmp_path / "diagrams" / "context.svg").write_bytes(SVG)
    (tmp_path / "diagrams" / "flow.png").write_bytes(PNG)
    md = tmp_path / "solution-design-northwind-2026-01-01.md"
    md.write_text(MD, encoding="utf-8")
    md.with_suffix(".html").write_bytes(_html().encode("utf-8"))
    return md


def _region(page: str) -> str:
    return page[page.index(START) + len(START) : page.index(END)]


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


class _Elements(HTMLParser):
    """Every start tag in a fragment with its attributes, plus whether the fragment balances."""

    def __init__(self, fragment: str) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, dict[str, str | None]]] = []
        # every attribute as written: a browser honours the FIRST of a duplicate, a dict the last
        self.pairs: list[tuple[str, list[tuple[str, str | None]]]] = []
        self.unbalanced: list[str] = []
        self._open: list[str] = []
        self.feed(fragment)
        self.close()
        self.unbalanced += self._open

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.found.append((tag, dict(attrs)))
        self.pairs.append((tag, attrs))
        if tag not in {"br", "col", "hr", "img", "wbr"}:
            self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._open and self._open[-1] == tag:
            self._open.pop()
        else:
            self.unbalanced.append(f"</{tag}>")


# ── round trip ────────────────────────────────────────────────────────────────────────


def test_render_then_check_is_current_and_an_md_edit_makes_it_stale(design: Path, capsys) -> None:
    assert main([str(design)]) == 0
    assert main([str(design), "--check"]) == 0
    design.write_text(MD.replace("Executive summary", "Summary for the board"), encoding="utf-8")
    capsys.readouterr()
    assert main([str(design), "--check"]) == 1
    out = capsys.readouterr().out
    assert "rendered line" in out and "Summary for the board" in out
    assert len(out.splitlines()) == 1  # a hint, never a dump


def test_check_covers_the_rendered_html_not_only_the_source(design: Path) -> None:
    html = design.with_suffix(".html")
    assert main([str(design)]) == 0
    page = _read(html)
    html.write_text(page.replace("<h2>Executive summary</h2>", "<h2>Hand-edited</h2>"), "utf-8")
    assert main([str(design), "--check"]) == 1


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


def test_everything_outside_the_region_is_byte_identical(design: Path) -> None:
    html = design.with_suffix(".html")
    before = _read(html)
    assert main([str(design)]) == 0
    after = _read(html)
    assert after.startswith(before[: before.index(START) + len(START)])  # including the CRLF
    assert after.endswith(before[before.index(END) :])
    # the fill shape: a newline, then the rendered HTML (which ends in its own newline)
    assert _region(after).startswith("\n<h1>Northwind") and _region(after).endswith("</pre>\n")


def test_html_flag_targets_another_path(design: Path, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere.html"
    other.write_bytes(_html().encode("utf-8"))
    assert main([str(design), "--html", str(other)]) == 0
    assert "<h2>Executive summary</h2>" in _region(_read(other))
    assert "placeholder" in _region(_read(design.with_suffix(".html")))


# ── images ────────────────────────────────────────────────────────────────────────────


def test_relative_svg_and_png_are_inlined_and_remote_is_untouched(design: Path) -> None:
    assert main([str(design)]) == 0
    region = _region(_read(design.with_suffix(".html")))
    svg = "data:image/svg+xml;base64," + base64.b64encode(SVG).decode()
    png = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert f'<img src="{svg}" alt="Context">' in region
    assert f'<img src="{png}" alt="Flow" title="the flow">' in region
    assert '<img src="https://example.com/remote.svg" alt="Remote">' in region
    # an image inside a fenced code block is a sample, not a picture
    assert "![A sample, not an image](diagrams/context.svg)" in region


def test_an_existing_data_uri_is_left_as_written(design: Path) -> None:
    uri = "data:image/png;base64,AAAA"
    design.write_text(f"# T\n\n![x]({uri})\n", encoding="utf-8")
    assert main([str(design)]) == 0
    assert f'<img src="{uri}" alt="x">' in _region(_read(design.with_suffix(".html")))


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


# a raw <img> — how the skill writes a plate or a product screenshot — travels like a markdown one


def test_a_relative_html_img_is_inlined_like_a_markdown_one(design: Path, capsys) -> None:
    svg = "data:image/svg+xml;base64," + base64.b64encode(SVG).decode()
    png = "data:image/png;base64," + base64.b64encode(PNG).decode()
    design.write_text(
        "# T\n\n"
        '<figure class="plate"><img src="diagrams/context.svg" alt="Decorative plate."></figure>\n\n'
        # a browser trims the ends of a URL, so the path judged is the one it would fetch
        '<figure class="shot"><img src=" diagrams/flow.png " alt="Flow" width="40">'
        "<figcaption>Cap</figcaption></figure>\n\n"
        "![Also flow](diagrams/flow.png)\n\n"
        '<img src="https://example.com/remote.png" alt="Remote">\n\n'
        '```html\n<img src="diagrams/context.svg" alt="a sample">\n```\n',
        encoding="utf-8",
    )
    assert main([str(design)]) == 0
    region = _region(_read(design.with_suffix(".html")))
    assert f'<figure class="plate"><img src="{svg}" alt="Decorative plate."></figure>' in region
    assert (
        f'<figure class="shot"><img src="{png}" alt="Flow" width="40">'
        "<figcaption>Cap</figcaption></figure>"
    ) in region
    assert f'<img src="{png}" alt="Also flow">' in region
    assert '<img src="https://example.com/remote.png" alt="Remote">' in region
    # an <img> inside a code block is a sample, not a picture
    assert '&lt;img src="diagrams/context.svg" alt="a sample"&gt;' in region
    # two raw and one markdown image — the markdown one is not counted twice
    assert "(3 image(s) inlined)" in capsys.readouterr().out
    assert main([str(design), "--check"]) == 0


@pytest.mark.parametrize(
    "src",
    [
        "../outside.svg",
        "diagrams/../../outside.svg",
        "%2e%2e/outside.svg",
        "&#46;&#46;/outside.svg",  # the parser decodes it to `..`, as a browser would
        "/etc/outside.svg",
    ],
)
def test_an_html_img_outside_the_folder_is_refused(design: Path, src: str, capsys) -> None:
    (design.parent.parent / "outside.svg").write_bytes(SVG)
    html = design.with_suffix(".html")
    before = html.read_bytes()
    design.write_text(f'# T\n\n<figure class="shot"><img src="{src}" alt="x"></figure>\n', "utf-8")
    assert main([str(design)]) == 2
    assert html.read_bytes() == before
    assert "inside the design's folder" in capsys.readouterr().err


def test_an_oversize_html_img_is_refused(design: Path, capsys) -> None:
    (design.parent / "diagrams" / "huge.png").write_bytes(b"\x00" * (MAX_IMAGE_BYTES + 1))
    html = design.with_suffix(".html")
    before = html.read_bytes()
    design.write_text(
        '# T\n\n<figure class="shot"><img src="diagrams/huge.png"></figure>\n', "utf-8"
    )
    assert main([str(design)]) == 2
    assert html.read_bytes() == before
    assert "cap" in capsys.readouterr().err


def test_a_missing_html_img_is_refused_not_left_to_break(design: Path) -> None:
    html = design.with_suffix(".html")
    before = html.read_bytes()
    design.write_text(
        '# T\n\n<figure class="plate"><img src="plate-cover.png"></figure>\n', "utf-8"
    )
    assert main([str(design)]) == 2
    assert html.read_bytes() == before


def test_render_html_given_no_base_reads_nothing_and_leaves_an_img_as_written() -> None:
    out, dropped = render_html('<img src="diagrams/context.svg" alt="x">\n')
    assert '<img src="diagrams/context.svg" alt="x">' in out and not dropped


# ── what the page keeps ───────────────────────────────────────────────────────────────


def test_components_keep_their_classes_styles_roles_and_data() -> None:
    md = (
        '<div class="trust-strip" data-anim role="group" aria-label="Every request passes 2 checks">\n'
        '<div class="ts-check" style="--d:0"><em>01</em><b>Check</b><i class="ok" aria-hidden="true"></i></div>\n'
        "</div>\n\n"
        '<table class="cov"><thead><tr class="grp"><th rowspan="2">Requirement</th>'
        '<th colspan="2" class="split">Parties</th></tr></thead><tbody><tr><td><b>R1</b>Need</td>'
        '<td><span class="role-none" aria-label="Not involved">—</span></td></tr></tbody></table>\n\n'
        '<details class="gloss" open><summary>Key terms</summary><ul class="defs"><li><b>Term</b>'
        "<span>One sentence.</span></li></ul></details>\n\n"
        '<ul class="ladder"><li><div class="meter"><i style="width:25%"></i></div></li></ul>\n\n'
        '<ol class="ln-steps"><li><i class="ln-dots" aria-hidden="true"><b data-own="4"><s>1</s></b></i></li></ol>\n'
    )
    out, dropped = render_html(md)
    assert not dropped
    for kept in (
        '<div class="trust-strip" data-anim role="group" aria-label="Every request passes 2 checks">',
        '<div class="ts-check" style="--d:0">',
        '<i class="ok" aria-hidden="true"></i>',
        '<th rowspan="2">Requirement</th><th colspan="2" class="split">',
        '<span class="role-none" aria-label="Not involved">—</span>',
        '<details class="gloss" open><summary>Key terms</summary>',
        '<i style="width:25%"></i>',
        '<b data-own="4"><s>1</s></b>',
    ):
        assert kept in out


def test_gfm_tables_strikethrough_and_safe_links_render() -> None:
    out, dropped = render_html(
        "| a | b |\n|:-:|---|\n| ~~old~~ | [docs](https://example.com/docs) |\n\n"
        "[section](#key-terms) · <mailto:team@example.com> · [file](appendix.md)\n"
    )
    assert not dropped
    assert '<th style="text-align:center">a</th>' in out
    assert "<s>old</s>" in out and '<a href="https://example.com/docs">docs</a>' in out
    assert '<a href="#key-terms">' in out and '<a href="mailto:team@example.com">' in out
    assert '<a href="appendix.md">' in out


def test_a_mermaid_block_stays_a_readable_code_block() -> None:
    out, _ = render_html("```mermaid\nflowchart LR\n  A-->B\n```\n")
    assert out == '<pre><code class="language-mermaid">flowchart LR\n  A--&gt;B\n</code></pre>\n'


# ── what the page refuses ─────────────────────────────────────────────────────────────

_NEVER_RENDERED = {
    "base", "button", "embed", "form", "iframe", "input", "link", "math", "meta", "noscript",
    "object", "script", "style", "svg", "textarea", "title",
}  # fmt: skip


@pytest.mark.parametrize(
    "markdown",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        '<a href="javascript:alert(1)">x</a>',
        '<a href="JaVaScRiPt:alert(1)">x</a>',
        '<a href="&#106;avascript:alert(1)">x</a>',
        '<a href="java&#x09;script:alert(1)">x</a>',
        '<a href=" &#x01;javascript:alert(1)">x</a>',
        '<a href=" javascript:alert(1)">x</a>',
        "[x](javascript:alert(1))",
        '<a href="vbscript:msgbox(1)">x</a>',
        '<a href="data:text/html,x">x</a>',
        '<a href="data:image/svg+xml;base64,PHN2Zz4=">x</a>',
        "[x](data:image/png;base64,AAAA)",
        '<img src="data:text/html;base64,PHNjcmlwdD4=">',
        '<a href="//attacker.example/share">x</a>',
        '<img src="\\\\attacker.example\\share\\x.png">',
        '<iframe src="https://attacker.example">fallback</iframe>',
        "<svg onload=alert(1)><text>hi</text></svg>",
        '<math><mi xlink:href="javascript:alert(1)">x</mi></math>',
        "<style>body{display:none}</style>",
        '<object data="x.swf">fallback</object><embed src="x.swf">',
        '<form action="https://attacker.example"><input name=q><button>go</button></form>',
        '<div onclick="alert(1)">c</div>',
        '<details open ontoggle="alert(1)"><summary>s</summary>b</details>',
        '<base href="https://attacker.example/">',
        '<meta http-equiv="refresh" content="0;url=https://attacker.example">',
        '<link rel="stylesheet" href="https://attacker.example/x.css">',
        '<noscript><p title="</noscript><img src=x onerror=alert(1)>"></noscript>',
        "<textarea></textarea><img src=x onerror=alert(1)>",
        '<div data-x"onclick=alert(1)//>q</div>',
        '<img src="x" name="getElementById">',
        '<a href="https://ok.example" href="javascript:alert(1)">x</a>',
        '<a href="javascript:alert(1)" href="https://ok.example">x</a>',
    ],
)
def test_nothing_that_can_run_or_redirect_reaches_the_page(markdown: str) -> None:
    out, _ = render_html(markdown)
    elements = _Elements(out)
    for tag, attrs in elements.pairs:
        assert tag not in _NEVER_RENDERED, out
        for name, value in attrs:
            assert not name.startswith("on") and name != "name", out
            # judged as a browser reads it: tabs and newlines deleted, the ends trimmed. A link
            # may never carry a data: URI; an image only a data:image/… one
            seen = re.sub(r"[\t\n\r]", "", value or "").strip("".join(map(chr, range(0x21))))
            data = r"data:" if name == "href" else r"data:(?!image/)"
            if name in ("href", "src"):
                assert not re.match(rf"(javascript:|vbscript:|{data})|[/\\]{{2}}", seen, re.I), out
    assert "alert(1)</" not in out and "<!--" not in out


def test_script_content_is_dropped_with_the_tag() -> None:
    out, dropped = render_html("before\n\n<script>alert('x')</script>\n\nafter\n")
    assert "alert" not in out and "before" in out and "after" in out
    assert dropped["<script>"] == 1


def test_an_attribute_value_cannot_break_out_of_its_quotes() -> None:
    out, _ = render_html("""<span title='a" onmouseover="alert(1)'>t</span>""")
    assert _Elements(out).found == [("p", {}), ("span", {"title": 'a" onmouseover="alert(1)'})]


def test_stray_end_tags_and_unclosed_elements_cannot_reshape_the_page() -> None:
    out, _ = render_html('</div></main></body>\n\n<div class="who"><section>unclosed\n')
    elements = _Elements(out)
    assert elements.unbalanced == [], out
    assert "</main>" not in out and "</body>" not in out


def test_a_new_item_closes_the_open_one_through_a_div_as_a_browser_does() -> None:
    # `<li>` ends an open `<li>` through a `<div>`; if the output left the div open, its later
    # `</div>` would close an element OUTSIDE the content
    out, _ = render_html("<ul><li><div><li>x</li></div></li></ul>")
    assert out == "<ul><li><div></div></li><li>x</li></ul>"
    out, _ = render_html("<dl><dd><div><dd>x</dd></div></dd></dl>")
    assert out == "<dl><dd><div></div></dd><dd>x</dd></dl>"
    # a nested list is a real child, not a sibling
    out, _ = render_html("<ul><li>a<ul><li>b</li></ul></li></ul>")
    assert out == "<ul><li>a<ul><li>b</li></ul></li></ul>"


@pytest.mark.parametrize("tag", ["iframe", "svg", "video", "object", "math", "script", "style"])
def test_an_unclosed_drop_whole_tag_refuses_rather_than_truncating(
    design: Path, tag: str, capsys
) -> None:
    html = design.with_suffix(".html")
    before = html.read_bytes()
    design.write_text(f"# T\n\nembedded via <{tag}> on the portal\n\n## Risks\n\nkept?\n", "utf-8")
    assert main([str(design)]) == 2
    assert main([str(design), "--check"]) == 2
    assert f"<{tag}> is opened and never closed" in capsys.readouterr().err
    assert html.read_bytes() == before


def test_the_end_marker_in_the_markdown_cannot_end_the_region(design: Path) -> None:
    design.write_text(f"# T\n\n{END}\n\nafter `{END}`\n\n```\n{END}\n```\n", encoding="utf-8")
    assert main([str(design)]) == 0
    page = _read(design.with_suffix(".html"))
    assert page.count(END) == 1 and page.count(START) == 1
    assert "after <code>&lt;!-- design_render:end --&gt;</code>" in _region(page)
    assert main([str(design), "--check"]) == 0


@pytest.mark.parametrize("close", ["</script>", "</SCRIPT >", "</Script"])
def test_a_script_close_in_the_markdown_is_harmless(design: Path, close: str) -> None:
    design.write_text(f"# T\n\nsee {close} here, and `{close}` in code\n", encoding="utf-8")
    assert main([str(design)]) == 0
    region = _region(_read(design.with_suffix(".html")))
    assert not re.search(r"</script", region, re.IGNORECASE)
    assert main([str(design), "--check"]) == 0


def test_dropped_markup_is_named_when_writing_and_quiet_on_check(design: Path, capsys) -> None:
    design.write_text(
        '# T\n\n<div onclick="x()">a</div>\n\n<uuid> and <script>x()</script>\n', "utf-8"
    )
    assert main([str(design)]) == 0
    err = capsys.readouterr().err
    assert "<script> ×1" in err and "<uuid> ×1" in err and "onclick= ×1" in err
    assert main([str(design), "--check"]) == 0
    assert capsys.readouterr().err == ""


# ── refusals ──────────────────────────────────────────────────────────────────────────


def test_missing_markers_are_refused(design: Path, capsys) -> None:
    design.with_suffix(".html").write_text("<html><body></body></html>", encoding="utf-8")
    assert main([str(design)]) == 2
    assert "found 0 and 0" in capsys.readouterr().err


def test_duplicate_markers_are_refused(design: Path, capsys) -> None:
    design.with_suffix(".html").write_text(_html() + START + "\nagain\n" + END, encoding="utf-8")
    assert main([str(design)]) == 2
    assert "found 2 and 2" in capsys.readouterr().err


def test_an_end_marker_before_the_start_is_refused(design: Path, capsys) -> None:
    design.with_suffix(".html").write_text(HEAD + END + START + TAIL, encoding="utf-8")
    assert main([str(design)]) == 2
    assert "comes before" in capsys.readouterr().err


def test_a_page_from_the_earlier_template_is_refused_with_the_way_out(design: Path, capsys) -> None:
    html = design.with_suffix(".html")
    html.write_text(
        HEAD
        + '</div><script type="text/markdown" id="src">\n# T\n</script>'
        + '<script type="module">\nimport m from "https://cdn.example/m.mjs";\n</script>',
        encoding="utf-8",
    )
    before = html.read_bytes()
    assert main([str(design)]) == 2
    assert "re-create it once from the current template" in capsys.readouterr().err
    assert main([str(design), "--check"]) == 2
    assert html.read_bytes() == before


def test_missing_html_is_refused(design: Path) -> None:
    design.with_suffix(".html").unlink()
    assert main([str(design)]) == 2


def test_the_module_opens_no_network_or_subprocess() -> None:
    source = Path(design_render.__file__).read_text(encoding="utf-8")
    for banned in ("import subprocess", "urllib.request", "import socket", "import http"):
        assert banned not in source


# ── the shipped template ──────────────────────────────────────────────────────────────


def _template() -> str:
    doc = COMPANION_DOC.read_text(encoding="utf-8")
    (template,) = re.findall(r"^````html\n(.*?)\n````$", doc, re.MULTILINE | re.DOTALL)
    return template


def _branded(template: str) -> str:
    page = template.replace("__TITLE__", "Northwind Analytics × Brightpath Labs").replace(
        "__BRAND_BODY_FACE__", '"Brightpath Sans"'
    )
    return re.sub(r"__BRAND_ACCENT_[A-Z]+__", "#1f5fbf", page)


def _shipped_page(tmp_path: Path) -> Path:
    page = _branded(_template())
    md = tmp_path / "solution-design-northwind-2026-01-01.md"
    md.write_text(MD + "\n## Flow\n\n```mermaid\nflowchart LR\n  A-->B\n```\n", encoding="utf-8")
    (tmp_path / "diagrams").mkdir()
    (tmp_path / "diagrams" / "context.svg").write_bytes(SVG)
    (tmp_path / "diagrams" / "flow.png").write_bytes(PNG)
    md.with_suffix(".html").write_text(page, encoding="utf-8")
    assert main([str(md)]) == 0
    return md


def test_a_rendered_companion_loads_nothing_from_a_cdn_to_open(tmp_path: Path) -> None:
    md = _shipped_page(tmp_path)
    page = _read(md.with_suffix(".html"))
    assert 'src="https://cdn' not in page and 'from "https://cdn' not in page
    assert not re.search(r"<script[^>]*\ssrc=", page)  # no external script of any kind
    assert "<h2>Executive summary</h2>" in page
    assert '<pre><code class="language-mermaid">' in page  # readable until Mermaid loads
    assert main([str(md), "--check"]) == 0


def test_the_template_fetches_nothing_but_a_lazy_mermaid_import() -> None:
    template = _template()
    (script,) = re.findall(r'<script type="module">(.*?)</script>', template, re.DOTALL)
    # a static import of an unreachable host stops the whole module, and the page with it
    assert not re.search(r"^\s*import[\s{*]", script, re.MULTILINE)
    # nor may the page name any other remote resource: a font stylesheet on a blackholed host
    # holds back first paint until it times out
    svg_namespace = "http://www.w3.org/2000/svg"  # a name for createElementNS, never fetched
    remote = [u for u in re.findall(r"https?://[^\"')\s]+", template) if u != svg_namespace]
    assert remote == ["https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"]
    assert f'await import("{remote[0]}")' in script and "catch" in script
    assert "language-mermaid" in script.split("import(")[0]  # gated on a Mermaid block existing


# ── the case-study companion ──────────────────────────────────────────────────────────
# The case-study skill's page is the solution-design shell without the contents rail and without
# Mermaid, rendered by this same module — so it opens offline for the same reason, and the one
# remote host the shell names (Mermaid's) is the thing the case-study recipe cuts.

CASE_STUDY_DOC = (
    Path(__file__).resolve().parents[1] / "plugin/skills/case-study/references/html-companion.md"
)
SVG_NAMESPACE = "http://www.w3.org/2000/svg"  # a name for createElementNS, never fetched
# The cuts that doc states, as (start, end) anchors in the shell: everything from `start` up to
# `end` goes. The rail, the grid that makes room for it, the script that fills it, and Mermaid.
_CASE_STUDY_CUTS = (
    (' <aside class="side">', ' <main class="doc">'),
    (" @media (min-width:1040px){", " .skip{"),
    ("// numbered table of contents", "// coverage table"),
    ("// mermaid", "// motion-gated reveal"),
)
CASE_STUDY_MD = """# Northwind Analytics: Solution Story

*Prepared 2026-01-01* · <span class="tier-badge">Solution story — modeled outcomes</span>

<div class="results" role="group" aria-label="Modeled outcomes">
<div><b>720</b><span>hours a month back</span><small class="basis modeled">modeled</small></div>
<div><b>3</b><span>weeks to first result</span><small class="basis modeled">modeled</small></div>
<div><b>1</b><span>shared record</span><small class="basis">(~unverified~)</small></div>
</div>

<blockquote class="pq"><p>[QUOTE PENDING — customer approval]</p><p class="by">Head of Operations</p></blockquote>

<div class="applies"><h3>This applies to you if…</h3><ul><li>One.</li><li>Two.</li><li>Three.</li></ul></div>

## Evidence log

| Claim | Basis | Source & date |
|---|---|---|
| 720 hours a month | modeled | [Discovery notes](https://example.com/notes), 2026-01-01 |
| One shared record | (~unverified~) | <https://example.com/record> |
"""


def _cut(text: str, start: str, end: str) -> str:
    assert text.count(start) == 1 and text.count(end) == 1, f"anchor moved: {start!r} / {end!r}"
    assert text.index(start) < text.index(end), f"{end!r} comes before {start!r}"
    return text[: text.index(start)] + text[text.index(end) :]


def _case_study_shell() -> str:
    """The solution-design shell with the case-study doc's cuts applied.

    Every cut names an anchor the doc names too, so a template edit that moves one fails here
    rather than leaving the recipe describing a page that no longer exists.
    """
    shell = _template()
    for start, end in _CASE_STUDY_CUTS:
        shell = _cut(shell, start, end)
    return shell


def _rendered_case_study(tmp_path: Path) -> Path:
    md = tmp_path / "case-study-northwind-2026-01-01.md"
    md.write_text(CASE_STUDY_MD, encoding="utf-8")
    md.with_suffix(".html").write_text(_branded(_case_study_shell()), encoding="utf-8")
    assert main([str(md)]) == 0
    return md


def test_a_rendered_case_study_companion_loads_nothing_to_open(tmp_path: Path) -> None:
    md = _rendered_case_study(tmp_path)
    page = _read(md.with_suffix(".html"))
    assert not re.search(r"<script[^>]*\ssrc=", page) and 'src="https://' not in page
    # Mermaid was the shell's only remote host: with it cut, the page chrome names none at all
    chrome = page[: page.index(START)] + page[page.index(END) :]
    assert [u for u in re.findall(r"https?://[^\"')\s]+", chrome) if u != SVG_NAMESPACE] == []
    assert "import(" not in chrome and "<aside" not in chrome
    assert "__BRAND_" not in page and "__TITLE__" not in page
    assert "<h2>Evidence log</h2>" in page
    assert main([str(md), "--check"]) == 0


def test_the_case_study_components_survive_the_allowlist(tmp_path: Path) -> None:
    md = _rendered_case_study(tmp_path)
    region = _region(_read(md.with_suffix(".html")))
    for kept in (
        '<span class="tier-badge">Solution story — modeled outcomes</span>',
        '<div class="results" role="group" aria-label="Modeled outcomes">',
        '<small class="basis modeled">modeled</small>',
        '<blockquote class="pq"><p>[QUOTE PENDING — customer approval]</p><p class="by">',
        '<div class="applies"><h3>This applies to you if…</h3>',
        '<a href="https://example.com/notes">Discovery notes</a>',
        '<a href="https://example.com/record">https://example.com/record</a>',
    ):
        assert kept in region
    # `(~unverified~)` is the basis marker; one tilde on each side must not read as strikethrough
    assert "(~unverified~)" in region and "<s>" not in region


def test_the_case_study_doc_names_every_cut_and_sends_the_page_through_the_render() -> None:
    doc = CASE_STUDY_DOC.read_text(encoding="utf-8")
    assert "uv run python -m gtm_core.design_render" in doc  # the reader reached the right doc
    for start, _ in _CASE_STUDY_CUTS:
        assert start.strip().rstrip("{") in doc, f"the case-study doc does not name {start!r}"
    # what the page did before: render in the reader's browser, and a recipe the brain cannot run
    for retired in ("DOMPurify.sanitize(", "marked.parse(", "insertAdjacentHTML(", "python3 -"):
        assert retired not in doc


def test_a_case_study_page_from_the_browser_rendered_pattern_is_refused(tmp_path: Path) -> None:
    md = tmp_path / "case-study-northwind-2026-01-01.md"
    md.write_text(CASE_STUDY_MD, encoding="utf-8")
    html = md.with_suffix(".html")
    html.write_text(
        '<!DOCTYPE html><html><head><script src="https://cdn.example/marked.js"></script></head>'
        '<body><div id="content"></div><script>const MD = `# T`;'
        "document.getElementById('content').insertAdjacentHTML("
        "'afterbegin', DOMPurify.sanitize(marked.parse(MD)));</script></body></html>",
        encoding="utf-8",
    )
    before = html.read_bytes()
    assert main([str(md)]) == 2
    assert html.read_bytes() == before
