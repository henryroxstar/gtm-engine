"""Contract test for G1 (external_ref boundary).

Asserts that no module in gtm_core/** or agent/** reads or references the
external_ref field. external_ref is an API join key only for correlation
by outside callers (such as a control plane), and must never be read by
internal pipeline skills, prompts, or execution nodes.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_no_gtm_core_or_agent_reads_external_ref():
    violations = []
    scanned_count = 0

    for root_dir in ("gtm_core", "agent"):
        path = REPO / root_dir
        if not path.is_dir():
            continue
        for py_file in path.rglob("*.py"):
            scanned_count += 1
            source = py_file.read_text(encoding="utf-8")
            if "external_ref" not in source:
                continue

            # Parse AST to check if it's referenced as an identifier, attribute, or string literal
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "external_ref":
                    violations.append(f"{py_file.relative_to(REPO)}:{node.lineno} (Attribute)")
                elif isinstance(node, ast.Name) and node.id == "external_ref":
                    violations.append(f"{py_file.relative_to(REPO)}:{node.lineno} (Name)")
                elif isinstance(node, ast.Constant) and node.value == "external_ref":
                    violations.append(
                        f"{py_file.relative_to(REPO)}:{node.lineno} (String constant)"
                    )

    assert scanned_count > 50, f"Expected to scan at least 50 files, scanned {scanned_count}"
    assert not violations, (
        "external_ref boundary violated! Found references in internal modules:\n"
        + "\n".join(violations)
    )
