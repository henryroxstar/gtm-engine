"""Contract test (§3.3): gtm_core.prospect_status must NEVER be imported by a gate or router.

`gtm_core.prospect_status` is an output translation layer only: it derives plain words
for people from `(lane, reason)`. An enrollment gate or router must never import or consume
`gtm_core.prospect_status`. Today's allowable importers under `gtm_core/` are strictly:
- `gtm_core/prospect_status_cli.py`
- `gtm_core/email_campaign_dashboard/format.py`
- `gtm_core/email_campaign_dashboard/lane_state.py`
- `gtm_core/email_campaign_dashboard/model.py`
- `gtm_core/email_campaign_dashboard/views_overview.py`
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GTM_CORE = REPO_ROOT / "gtm_core"

ALLOWED_IMPORTERS: frozenset[str] = frozenset(
    {
        "gtm_core/prospect_status_cli.py",
        "gtm_core/email_campaign_dashboard/format.py",
        "gtm_core/email_campaign_dashboard/lane_state.py",
        "gtm_core/email_campaign_dashboard/model.py",
        "gtm_core/email_campaign_dashboard/views_overview.py",
    }
)


def imports_prospect_status(tree: ast.AST) -> bool:
    """Return True if the AST imports gtm_core.prospect_status."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (
                    alias.name == "gtm_core.prospect_status"
                    or alias.name.endswith(".prospect_status")
                    or alias.name == "prospect_status"
                ):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if (
                mod == "prospect_status"
                or mod.endswith(".prospect_status")
                or "prospect_status." in mod
            ):
                return True
            if mod == "gtm_core":
                for alias in node.names:
                    if alias.name == "prospect_status":
                        return True
    return False


def find_prospect_status_importers(root: Path) -> dict[str, list[int]]:
    """Scan all .py files under root and return {rel_path: [linenos]} for prospect_status imports."""
    hits: dict[str, list[int]] = {}
    for py_file in sorted(root.rglob("*.py")):
        # Skip prospect_status.py itself
        if py_file.name == "prospect_status.py":
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        if imports_prospect_status(tree):
            try:
                rel = str(py_file.relative_to(REPO_ROOT))
            except ValueError:
                rel = py_file.name
            hits[rel] = [
                n.lineno
                for n in ast.walk(tree)
                if isinstance(n, (ast.Import, ast.ImportFrom))
                and (
                    any(
                        a.name in ("gtm_core.prospect_status", "prospect_status")
                        or a.name.endswith(".prospect_status")
                        for a in getattr(n, "names", [])
                    )
                    or (getattr(n, "module", None) or "") == "prospect_status"
                    or (getattr(n, "module", None) or "").endswith(".prospect_status")
                )
            ]
    return hits


def test_prospect_status_is_imported_only_by_allowlist():
    """No gate, router, or unexpected module in gtm_core may import prospect_status."""
    actual_importers = find_prospect_status_importers(GTM_CORE)
    disallowed = {k: v for k, v in actual_importers.items() if k not in ALLOWED_IMPORTERS}
    assert not disallowed, (
        "PRD §3.3 violation: gtm_core.prospect_status is an output translation layer only.\n"
        "The following disallowed module(s) import it:\n"
        + "\n".join(f"  {k} (lines {v})" for k, v in disallowed.items())
    )
    # Also assert that expected importers are indeed found so the test cannot rot
    for expected in ALLOWED_IMPORTERS:
        assert expected in actual_importers, (
            f"Expected importer {expected} no longer imports prospect_status; update allowlist."
        )


def test_negative_control_fails_on_synthetic_gate_import(tmp_path):
    """Negative control (§R18): prove the checker fails if a gate imports prospect_status."""
    gate_py = tmp_path / "fake_gate.py"
    gate_py.write_text(
        "from gtm_core.prospect_status import status_of\ndef gate(): pass\n",
        encoding="utf-8",
    )
    hits = find_prospect_status_importers(tmp_path)
    assert any(k.endswith("fake_gate.py") for k in hits)
