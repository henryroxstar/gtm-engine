"""Tests for Email Portfolio & Distribution Analysis (Emails Tab Meta-Data Analysis).

Verifies the 7 dimensions:
1. Persona type & role (Champion, Economic Buyer, Evaluator, Governance Co-signer, Technical Founder)
2. Public Anchor
3. Core Argument
4. Offer
5. Industry
6. Contacts
7. % of Pool

Invariants:
- INV1: Zero disk I/O in render_email_portfolio_html.
- INV2: Design tokens only. Zero hex/rgba literals outside :root.
- Section ID allowlist compliance: "email-portfolio" in SECTIONS["emails"].
- Fictional data only (§R9).
"""

from __future__ import annotations

import re
from unittest.mock import patch

from gtm_core import email_campaign_dashboard as gd
from gtm_core.email_campaign_dashboard import views_emails as ve
from gtm_core.email_campaign_dashboard.config import SECTIONS
from gtm_core.email_campaign_dashboard.portfolio import (
    derive_committee_role,
    extract_offer,
    normalize_industry,
    render_email_portfolio_html,
)
from tests.contracts.dashboard_page import section, section_ids
from tests.contracts.test_dashboard_ps20_injection import ESCAPED_SCRIPT, RAW_SCRIPT, SCRIPT_PAYLOAD
from tests.test_email_campaign_dashboard import _seed

RAW_COLOUR_RE = re.compile(r"rgba?\(|#[0-9a-fA-F]{3,8}\b")


def test_normalize_industry():
    assert normalize_industry("Computer Software") == "DevTools & AI Platforms"
    assert normalize_industry("AI Platform / DevTools") == "DevTools & AI Platforms"
    assert normalize_industry("Commercial Banking & Finance") == "Financial Services"
    assert normalize_industry("Hospital & Health Care") == "Healthcare & Life Sciences"
    assert normalize_industry("Property & Casualty Insurance") == "Insurance"
    assert normalize_industry("Supply Chain & Logistics") == "Trade & Logistics"
    assert normalize_industry("") == "Cross-Industry"
    assert normalize_industry("(blank)") == "Cross-Industry"


def test_derive_committee_role():
    assert derive_committee_role("ai-platform") == "Champion"
    assert derive_committee_role("cto") == "Champion"
    assert derive_committee_role("security") == "Economic Buyer"
    assert derive_committee_role("ceo") == "Economic Buyer"
    assert derive_committee_role("architect") == "Technical Evaluator"
    assert derive_committee_role("risk-compliance") == "Governance Co-signer"
    assert derive_committee_role("compliance") == "Governance Co-signer"
    assert derive_committee_role("founder-operator") == "Technical Founder"
    # A seat this map does not name — Unclassified, not the "Evaluator" default the map
    # used to guess a role for. Unclassified says the map has no entry; it is not a claim
    # about the buyer.
    assert derive_committee_role("unknown-seat") == "Unclassified"


def test_extract_offer():
    """The label is ONLY the spec's own declared `offer:`, never guessed from the CTA
    wording — a ten-entry keyword table used to match this tenant's own copy idioms
    ("write-up", "one-pager", "guardrail") and invent a label; deleted along with the
    tenant vocabulary it carried. The CTA question itself is always read from the body,
    labelled or not.
    """
    copy_admission = [
        {
            "body": "Hi Riley,\n\nSome text.\n\nWould the write-up on how one team structured that admission review be useful?"
        }
    ]
    label, q = extract_offer(copy_admission)
    assert label == "Offer not labelled"
    assert "admission review" in q

    label, q = extract_offer(copy_admission, declared_offer="Admission Review Teardown")
    assert label == "Admission Review Teardown"
    assert "admission review" in q


def test_portfolio_section_in_allowlist():
    assert "email-portfolio" in SECTIONS["emails"]


def test_portfolio_metadata_enriched_in_model(tmp_path):
    profile = _seed(tmp_path)
    m = gd.build_model(profile, tmp_path)
    assert m.get("messages")
    for msg in m["messages"]:
        p = msg.get("portfolio")
        assert p is not None
        assert "persona_type" in p
        assert "seat" in p
        assert "seat_label" in p
        assert "public_anchor" in p
        assert "core_argument" in p
        assert "offer" in p
        assert "industry" in p
        assert "contacts" in p


def test_portfolio_html_renders_all_seven_dimensions(tmp_path):
    profile = _seed(tmp_path)
    m = gd.build_model(profile, tmp_path)
    html = ve._emails_view(m)

    # Section ID check
    assert "email-portfolio" in section_ids(html)
    assert set(section_ids(html)) <= SECTIONS["emails"]

    portfolio_sec = section(html, "email-portfolio")
    assert portfolio_sec != ""

    # Check 7 table headers
    headers = re.findall(r"<th[^>]*>(.*?)</th>", portfolio_sec)
    expected_headers = [
        "Persona &amp; Role",
        "Public Anchor",
        "Core Argument",
        "Offer",
        "Industry",
        "Contacts",
        # Renamed from "% of Pool": the denominator is people in this profile's CURRENT
        # (non-archived) sequences, never the whole historical pool.
        "% Current",
    ]
    for eh in expected_headers:
        assert eh in headers or any(eh in h for h in headers), (
            f"Header '{eh}' not found in {headers}"
        )

    # Check summary card metrics
    assert "Committee Role Balance" in portfolio_sec
    assert "Public Anchors &amp; Triggers" in portfolio_sec
    assert "Give-First Offers" in portfolio_sec
    assert "Target Industry Coverage" in portfolio_sec

    # Check table rows exist
    assert '<table id="portfolio-table"' in portfolio_sec
    assert 'data-portfolio-row="' in portfolio_sec


def test_portfolio_render_zero_file_io(tmp_path):
    """INV1: render_email_portfolio_html must not read files from disk."""
    profile = _seed(tmp_path)
    m = gd.build_model(profile, tmp_path)

    def guarded_open(*args, **kwargs):
        raise AssertionError("Disk file I/O detected during render: open() called!")

    with patch("builtins.open", side_effect=guarded_open):
        out = render_email_portfolio_html(m)
        assert "Email Portfolio &amp; Distribution Analysis" in out


def test_portfolio_no_raw_colours_in_styles(tmp_path):
    """INV2: No hex or rgba literals in portfolio card styles or inline styles."""
    profile = _seed(tmp_path)
    m = gd.build_model(profile, tmp_path)
    out = render_email_portfolio_html(m)

    violations = []
    for m_style in re.finditer(r'style="([^"]*)"', out):
        style_val = m_style.group(1)
        if RAW_COLOUR_RE.search(style_val):
            violations.append(style_val)

    assert not violations, f"Raw colour violations in inline styles: {violations}"


def test_portfolio_escapes_untrusted_input(tmp_path):
    profile = _seed(tmp_path)
    m = gd.build_model(profile, tmp_path)

    # Inject script payload into portfolio metadata
    m["messages"][0]["portfolio"] = {
        "persona_type": SCRIPT_PAYLOAD,
        "seat": SCRIPT_PAYLOAD,
        "seat_label": SCRIPT_PAYLOAD,
        "public_anchor": SCRIPT_PAYLOAD,
        "core_argument": SCRIPT_PAYLOAD,
        "offer": SCRIPT_PAYLOAD,
        "offer_quote": SCRIPT_PAYLOAD,
        "industry": SCRIPT_PAYLOAD,
        "contacts": 10,
        "spec": SCRIPT_PAYLOAD,
        "sequence_id": "test-seq",
    }

    out = render_email_portfolio_html(m)
    assert RAW_SCRIPT not in out
    assert ESCAPED_SCRIPT in out
