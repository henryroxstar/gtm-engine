"""The email text must come from the shared renderer, not a second implementation.

Two renderers of one template is how a page starts showing copy nobody sends. The outreach log
fills a spec with ``outreach.render`` — the same function the status page and the merge-render
gate use — and adds only a detector around it. This pins that by reading the source.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "gtm_core"
NEW_MODULES = (
    "outreach_touches.py",
    "outreach_people.py",
    "outreach_model.py",
    "outreach_findings.py",
    "outreach_strict.py",
)


def _tree(name: str) -> ast.AST:
    return ast.parse((CORE / name).read_text(encoding="utf-8"))


def test_touches_imports_the_shared_render_and_defines_no_renderer():
    tree = _tree("outreach_touches.py")
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "outreach"
        for alias in node.names
    }
    assert {"render", "parse_spec", "parse_prospect_pack"} <= imported


def test_no_new_module_defines_its_own_merge_substitution():
    for name in NEW_MODULES:
        for node in ast.walk(_tree(name)):
            if isinstance(node, ast.FunctionDef):
                assert node.name not in {"render", "render_template", "fill", "substitute"}, name
        text = (CORE / name).read_text(encoding="utf-8")
        # the merge-tag pattern itself belongs to the shared package; only the detector may
        # ask the renderer for it, never write its own
        assert 're.compile(r"\\{\\{' not in text, name
