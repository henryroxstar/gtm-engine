"""The bottom-of-file re-export idiom is only safe if the sibling never imports the parent at top.

`render_engines` re-exports from `render_engines_audit` on its last line; `render_manifest` does
the same from `render_manifest_pool`. Both siblings originally imported the parent at module top —
a cycle that crashes whenever the SIBLING is imported first, proved in review with a cold
`import gtm_core.render_manifest_pool`. It survived every in-tree caller only because each happened
to load the parent first. Each pair is imported cold, in both orders, in a FRESH interpreter,
because an in-process check is poisoned by whatever pytest has already loaded.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

PAIRS = [
    ("gtm_core.render_manifest", "gtm_core.render_manifest_pool", "validate_draft_pool"),
    ("gtm_core.render_engines", "gtm_core.render_engines_audit", "audit_registry"),
]


def _cold(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize(("parent", "sibling", "_n"), PAIRS, ids=[s for _, s, _ in PAIRS])
def test_the_sibling_imports_cold_on_its_own(parent, sibling, _n):
    """The order that used to crash."""
    r = _cold(f"import {sibling}")
    assert r.returncode == 0, f"cold `import {sibling}` failed:\n{r.stderr[-800:]}"


@pytest.mark.parametrize(("parent", "sibling", "_n"), PAIRS, ids=[p for p, _, _ in PAIRS])
def test_the_parent_imports_cold_on_its_own(parent, sibling, _n):
    """The order that always worked — the positive control."""
    r = _cold(f"import {parent}")
    assert r.returncode == 0, f"cold `import {parent}` failed:\n{r.stderr[-800:]}"


@pytest.mark.parametrize(("parent", "sibling", "name"), PAIRS, ids=[s for _, s, _ in PAIRS])
def test_the_parents_re_export_still_resolves_after_a_cold_sibling_import(parent, sibling, name):
    """Making the sibling lazy must not break the parent's public surface."""
    r = _cold(f"import {sibling}; from {parent} import {name}; print('ok')")
    assert r.stdout.strip() == "ok", r.stderr[-800:]
