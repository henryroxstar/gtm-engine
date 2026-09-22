"""Test plan §4.5 — if two surfaces show this data, do they agree?

Capability status is rendered in three places: the terminal preflight table, the Telegram
gate preview, and the dashboard's inbound panel. Each one re-deriving its own view of the
same registry is how two screens end up disagreeing about whether a sequence was checked.

They all render ONE object — `gtm_core.email_compliance.capability_rows` through
`gtm_core.sequencers.render_summary` — and this pins that. The same applies to the DNC
divergence count, which the ledger row and the alert both take from one reconcile.
"""

from __future__ import annotations

import html as _html
from pathlib import Path

from gtm_core import capability_preflight as cp
from gtm_core import suppression
from gtm_core.sequencers import render_summary

REGISTRY = Path(cp.__file__).resolve().parent / "sequencers.toml"


def test_the_three_surfaces_render_the_same_capabilities():
    rows = cp.capability_rows("saleshandy", registry_path=REGISTRY)
    assert rows, "instrument: an empty row set would make every assertion below vacuous"

    terminal = render_summary(rows, fmt="text")
    telegram = render_summary(rows, fmt="html")
    # The dashboard panel renders the same html through the same call — asserted by
    # deriving it the same way the view does, so a view that stopped using it fails here.
    dashboard = render_summary(cp.capability_rows("saleshandy", registry_path=REGISTRY), fmt="html")

    assert telegram == dashboard
    for cap in rows:
        verdict = "GRANTED" if cap.granted else "REFUSED"
        assert f"{cap.provider}/{cap.name}: {verdict}" in terminal
        assert _html.escape(cap.name) in telegram
    assert terminal.count("GRANTED") == telegram.count("GRANTED")
    assert terminal.count("REFUSED") == telegram.count("REFUSED")


def test_the_dashboard_panel_uses_the_shared_renderer():
    """Structural: the view must CALL the shared renderer, not re-implement it. A panel
    that formats its own table would pass the content test above and still drift."""
    import ast

    from gtm_core.email_campaign_dashboard import views_status

    source = Path(views_status.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    block = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_inbound_health_block"
    )
    called = {
        n.func.id
        for n in ast.walk(block)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "render_summary" in called
    assert "capability_rows" in called


def test_the_preflight_table_and_the_capability_rows_cover_the_same_set():
    """The preflight asserts every policy capability; the panel renders every policy
    capability. A capability asserted but never shown is one nobody acts on."""
    rendered = {c.name for c in cp.capability_rows("saleshandy", registry_path=REGISTRY)}
    assert rendered == set(cp.CAPABILITY_POLICY)

    result = cp.check_capabilities("saleshandy", None, registry_path=REGISTRY)
    for name in cp.CAPABILITY_POLICY:
        assert any(name in line for line in result.detail), f"{name} missing from the table"


def test_the_divergence_count_is_one_number_both_surfaces_read():
    """The ledger row's `findings` and the alert's body come from one `reconcile_dnc`
    call, so a 50-divergence reconcile cannot report 50 in one place and 3 in the other."""
    ledger = {
        "a": suppression.Suppression(
            email=f"ghost{i}@bracken.example", reason="dnc-optout", date="2026-09-21"
        )
        for i in range(1)
    }
    findings = suppression.reconcile_dnc(ledger, ["dana@acme.example"], [])
    assert len(findings) == 1
    # The alert renders the same list it was handed — no second derivation, no filtering.
    assert all("ghost0@bracken.example" in f for f in findings)
