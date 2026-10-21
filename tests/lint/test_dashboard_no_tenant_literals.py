"""Guard against tenant facts re-entering the de-branded dashboard engine (PS20 follow-up).

A 2026-09-27 review found tenant-specific nouns hardcoded under ``gtm_core/email_campaign_dashboard/``:
OpenText/Okta/LangChain in typed "hypothesis" prose, a campaign-slug branch (``if slug == "..."``)
in the Overview snapshot, and a hardcoded sign-off name ("henry") in the Emails portfolio. All were
removed. This lint holds the regression closed: any of these literals reappearing under
``gtm_core/`` is a tenant-boundary violation (CLAUDE.md "Tenant boundary"), not a style nit — the
engine must stay company-agnostic no matter which tenant's data it renders.

The sequencer login-URL map in ``views_lede.py`` is a deliberate exception: it lists every
sequencer this codebase integrates with (Saleshandy, Apollo, GMass), keyed off the profile's own
declared ``email_tool`` — the same URL for every tenant on that sequencer, not a per-tenant fact.
So this lint does not ban "saleshandy.com" outright; it instead asserts that literal never again
stands alone as the codebase's only sequencer, by requiring it to co-occur with the other two
vendors in the same file.
"""

from __future__ import annotations

from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "gtm_core" / "email_campaign_dashboard"

#: Small, explicit literals that were once hardcoded into the engine and must never return.
BANNED_LITERALS = [
    "OpenText",
    "Okta",
    "LangChain",
    '"henry"',
    'slug == "',
]


def _dashboard_files() -> list[Path]:
    files = sorted(DASHBOARD_DIR.glob("*.py"))
    assert files, f"no dashboard source files found under {DASHBOARD_DIR}"
    return files


def test_no_banned_tenant_literals_in_dashboard_source():
    for path in _dashboard_files():
        src = path.read_text(encoding="utf-8")
        for literal in BANNED_LITERALS:
            assert literal not in src, (
                f"{literal!r} reappeared in {path.name} — tenant fact leaked into gtm_core"
            )


def test_saleshandy_url_never_stands_alone():
    """A sequencer URL map must name more than one vendor, or it is a single-tenant assumption."""
    for path in _dashboard_files():
        src = path.read_text(encoding="utf-8")
        if "saleshandy.com" not in src:
            continue
        assert "apollo.io" in src and "gmass.co" in src, (
            f"{path.name} hardcodes saleshandy.com without the other declared sequencers — "
            "this looks like the single-vendor assumption the fix removed"
        )
