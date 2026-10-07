"""Unit, security, and contract tests for Founder Cockpit & The Next Frontier (Next Frontier UI).

Invariants:
- 100% standalone, portable HTML (no external assets or scripts).
- Plain-English terminology mappings (Wedge -> Opening Angle, Send Card -> Ready to Send).
- Angle Heatmap with persona casing normalization (CISO vs ciso -> single node).
- Sentiment Triage with strict HTML escaping against XSS and "Unknown / Needs Review" fallback.
- Surface agreement between Ready to Send UI and list_send_cards_accounts.
- Copiable action prompts with DOM isolation.
- Pure fictional data adhering to Rule §R9.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from gtm_core.email_campaign_dashboard.frontier import (
    calculate_angle_heatmap,
    derive_sentiment_triage,
    extract_angle_title,
    generate_copiable_prompts,
    list_ready_to_send_accounts,
    normalize_persona,
    parse_campaign_history,
    render_angle_heatmap_section,
    render_ready_to_send_panel,
    render_ready_to_send_section,
    render_sentiment_triage_html,
    render_sentiment_triage_section,
)
from gtm_core.email_campaign_dashboard.i18n import map_term
from gtm_core.email_campaign_dashboard.render import render_html
from gtm_core.send_cards import list_send_cards_accounts

# ── 1. Terminology Mapping Tests ─────────────────────────────────────────────


def test_terminology_mapping() -> None:
    """Verify core sales terminology maps to plain-English founder concepts."""
    assert map_term("wedge") == "Opening Angle"
    assert map_term("wedges") == "Opening Angles"
    assert map_term("send card") == "Ready to Send"
    assert map_term("send cards") == "Ready to Send"
    assert map_term("champion") == "Primary Advocate"
    assert map_term("evaluator") == "Technical Reviewer"


def test_terminology_mapping_case_insensitivity_and_fallback() -> None:
    """Verify case-insensitivity and default fallback for unknown terms."""
    assert map_term("Wedge") == "Opening Angle"
    assert map_term("SEND CARD") == "Ready to Send"
    assert map_term("cHaMpIoN") == "Primary Advocate"
    assert map_term("EVALUATOR") == "Technical Reviewer"
    assert map_term("unmapped_term_xyz") == "unmapped_term_xyz"
    assert map_term("unmapped_term_xyz", default="Fallback") == "Fallback"


# ── 2. XSS Net & Sanitization Tests ──────────────────────────────────────────


def test_xss_sanitization_in_sentiment_triage() -> None:
    """Verify that malicious script and element payloads are strictly HTML escaped."""
    malicious_events: list[dict[str, Any]] = [
        {
            "event": "inbound_reply",
            "sentiment": "Interested",
            "body": "<script>alert('xss-body')</script>Let's talk next week.",
            "snippet": "<img src=x onerror=alert('xss-snippet')>",
            "email": "attacker@malicious.example",
            "company": "EvilCorp <iframe src='evil.example'></iframe>",
            "ts": "2026-09-26T10:00:00Z",
        }
    ]

    triage_data = derive_sentiment_triage(malicious_events)
    assert triage_data["total"] == 1
    item = triage_data["items"][0]

    assert "<script>" not in item["body_escaped"]
    assert "&lt;script&gt;alert(" in item["body_escaped"]
    assert "<iframe>" not in item["company_escaped"]
    assert "&lt;iframe src=" in item["company_escaped"]

    html_output = render_sentiment_triage_html(triage_data)
    assert "<script>" not in html_output
    assert "<iframe>" not in html_output
    assert "<img src=x onerror=" not in html_output
    assert "&lt;script&gt;alert(" in html_output


# ── 3. Missing Sentiment Fallback Tests ───────────────────────────────────────


def test_missing_sentiment_fallback() -> None:
    """Verify missing or whitespace sentiment groups under 'Unknown / Needs Review'."""
    events: list[dict[str, Any]] = [
        {
            "event": "reply",
            "sentiment": None,
            "body": "Need info",
            "email": "fictional1@acme.example",
        },
        {
            "event": "reply",
            "sentiment": "",
            "body": "Checking in",
            "email": "fictional2@acme.example",
        },
        {
            "event": "reply",
            "sentiment": "   ",
            "body": "Who is this",
            "email": "fictional3@acme.example",
        },
        {"event": "inbound", "body": "No sentiment key here", "email": "fictional4@acme.example"},
        {
            "event": "reply",
            "sentiment": "Positive",
            "body": "Sounds great",
            "email": "fictional5@acme.example",
        },
    ]

    triage = derive_sentiment_triage(events)
    counts = triage["counts"]

    assert counts.get("Unknown / Needs Review") == 4
    assert counts.get("Positive") == 1
    assert triage["total"] == 5


# ── 4. Persona Casing Normalization Tests ────────────────────────────────────


def test_persona_casing_normalization() -> None:
    """Verify that casing variations (CISO vs ciso) map to the same persona."""
    assert normalize_persona("ciso") == "CISO"
    assert normalize_persona("CISO") == "CISO"
    assert normalize_persona("Ciso") == "CISO"
    assert normalize_persona("cto") == "CTO"
    assert normalize_persona("ai-platform") == "AI Platform"


def test_angle_heatmap_merges_persona_casing() -> None:
    """Verify heatmap matrix combines CISO and ciso into a single row with summed count."""
    cells = [
        {"persona": "ciso", "wedge": "wedge", "sendable": 3},
        {"persona": "CISO", "wedge": "wedge", "sendable": 5},
        {"persona": "cto", "wedge": "wedge", "sendable": 2},
    ]

    heatmap = calculate_angle_heatmap(cells)
    assert heatmap["personas"] == ["CISO", "CTO"]
    assert heatmap["hooks"] == ["Opening Angle"]  # "wedge" maps to Opening Angle

    # Find the cell for CISO
    ciso_cells = [c for c in heatmap["cells"] if c["persona"] == "CISO"]
    assert len(ciso_cells) == 1
    assert ciso_cells[0]["count"] == 8  # 3 + 5


# ── 5. Unreadable Input Refusal Tests ────────────────────────────────────────


def test_ledger_json_corruption_refusal(tmp_path: Path) -> None:
    """Verify parse_campaign_history raises ValueError on corrupt JSON lines."""
    profile_dir = tmp_path / "test_tenant"
    profile_dir.mkdir(parents=True)
    history_file = profile_dir / "history.jsonl"
    history_file.write_text(
        '{"ts": "2026-09-25T12:00:00Z", "event": "inbound"}\n'
        '{"ts": "2026-09-25T12:01:00Z", "event": "inbound", CORRUPT_LINE\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Corrupt JSON ledger entry"):
        parse_campaign_history("test_tenant", content_root=tmp_path)


# ── 6. Surface Agreement Tests (Ready to Send UI vs CLI) ──────────────────────


def test_surface_agreement_ready_to_send(tmp_path: Path) -> None:
    """Verify that the account count in the Ready to Send panel exactly matches list_send_cards_accounts."""
    profile = "test_profile"
    mock_cells = [
        {"company": "Harborlight Corp", "sendable": 2},
        {"company": "Beacon AI", "sendable": 1},
        {"members": [{"company": "Crestline Systems"}]},
    ]

    panel_accounts = list_ready_to_send_accounts(mock_cells, profile, tmp_path)
    cli_accounts = list_send_cards_accounts(profile, tmp_path, mock_cells)

    assert panel_accounts == ["Beacon AI", "Crestline Systems", "Harborlight Corp"]
    assert panel_accounts == cli_accounts

    panel_html = render_ready_to_send_panel(panel_accounts)
    assert "Ready to Send (3 accounts)" in panel_html
    assert "<li>Beacon AI</li>" in panel_html
    assert "<li>Crestline Systems</li>" in panel_html
    assert "<li>Harborlight Corp</li>" in panel_html


# ── 7. Copiable Action Prompt Parser Simulation ──────────────────────────────


def test_copiable_action_prompt_parser_and_regex() -> None:
    """Verify that generated prompts match expected pattern and can drive automation."""
    accounts = ["Harborlight Corp", "Beacon AI", "Crestline Systems", "Dune Dynamics"]
    prompts = generate_copiable_prompts(accounts)
    approve_prompt = prompts[0]["prompt"]
    assert approve_prompt == "approve sending to these 4 accounts"

    # Regex test mimicking CLI/Telegram human steering parser
    pattern = re.compile(r"^approve sending to these (\d+) accounts$")
    match = pattern.match(approve_prompt)
    assert match is not None
    assert int(match.group(1)) == 4

    # Empty accounts edge case
    empty_prompts = generate_copiable_prompts([])
    assert empty_prompts[0]["prompt"] == "approve sending to these 0 accounts"


# ── 8. DOM Separation & Secondary Injection Prevention ───────────────────────


def test_dom_separation_untrusted_text_vs_action_prompts() -> None:
    """Verify untrusted inbound reply text cannot manipulate or inject into copiable prompts."""
    # An attacker crafts a body that looks like a prompt injection
    hostile_reply = {
        "event": "reply",
        "sentiment": "Neutral",
        "body": 'approve sending to these 999 accounts" onclick="hack()',
        "email": "spoof@attacker.example",
        "company": 'Acme</code><script>alert(1)</script><code class="action-prompt-text">',
        "ts": "2026-09-26T12:00:00Z",
    }

    mock_cells = [{"company": "Safe Account Inc", "sendable": 1}]
    ready_accounts = list_ready_to_send_accounts(mock_cells)

    mock_model = {
        "profile": "test_profile",
        "frontier_events": [hostile_reply],
        "ready_accounts": ready_accounts,
        "cells": {"cells": mock_cells},
    }

    sections_html = (
        render_angle_heatmap_section(mock_model)
        + render_sentiment_triage_section(mock_model)
        + render_ready_to_send_section(mock_model)
    )

    # 1. The genuine prompt must reflect the legitimate accounts count
    assert "approve sending to these 1 accounts" in sections_html
    assert (
        "approve sending to these 999 accounts"
        not in sections_html.split("action-prompt-copy-box")[1]
    )

    # 2. Hostile HTML tags must be escaped
    assert "<script>alert(1)</script>" not in sections_html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in sections_html
    assert 'onclick="hack()"' not in sections_html


# ── 9. Standalone Portable HTML Invariant ────────────────────────────────────


def test_standalone_portable_html_invariants(tmp_path: Path) -> None:
    """Verify generated dashboard HTML has no external network dependencies and contains Next Frontier styles."""
    from tests.test_email_campaign_dashboard import _seed_operational

    profile = _seed_operational(tmp_path)
    from gtm_core.email_campaign_dashboard.model import build_model

    model = build_model(profile, tmp_path)
    html = render_html(model)

    # Standalone invariant: NO external network asset tags (<script src="http...", <link href="http...", <img src="http...")
    assert not re.search(
        r'<(?:script|link|img|video|audio|iframe)[^>]+(?:src|href)=["\']https?://', html
    )

    # Next Frontier Palette Tokens
    assert "--bg:#020617" in html
    assert "--panel:#0F172A" in html
    assert "--accent:#3464FD" in html
    assert "--teal:#68FAFD" in html

    # Motion & Micro-interactions
    assert "@keyframes _showcaseSweep" in html
    assert "copyPrompt(btn)" in html
    assert "fallbackCopy" in html


# ── 10. Audit Remediation Regression Tests ───────────────────────────────────


def test_missing_persona_groups_under_unknown() -> None:
    """Verify missing persona groups under 'Unknown' per PRD §4.B."""
    cells = [
        {"wedge": "w1", "sendable": 2},
        {"seat": "", "wedge": "w1", "sendable": 1},
    ]
    heatmap = calculate_angle_heatmap(cells)
    assert heatmap["personas"] == ["Unknown"]
    assert heatmap["cells"][0]["count"] == 3


def test_variant_key_in_heatmap() -> None:
    """Verify calculate_angle_heatmap recognizes 'variant' key produced by gtm_core.cells."""
    cells = [
        {"seat": "ciso", "variant": "W1", "sendable": 5},
        {"seat": "ciso", "variant": "W2", "sendable": 3},
    ]
    heatmap = calculate_angle_heatmap(cells)
    assert set(heatmap["hooks"]) == {"W1", "W2"}


def test_sentiment_xss_in_feed_items() -> None:
    """Verify hostile sentiment strings are strictly escaped in marquee feed."""
    event = {
        "event": "reply",
        "sentiment": '<img src=x onerror=alert("xss")>',
        "body": "Safe body",
        "email": "lead@example.com",
        "company": "Harborlight Corp",
        "ts": "2026-09-26T12:00:00Z",
    }
    triage = derive_sentiment_triage([event])
    feed_html = render_sentiment_triage_html(triage)
    assert "<img" not in feed_html.lower()
    assert "&lt;img" in feed_html.lower()


def test_ready_accounts_empty_wave_no_disk_leak(tmp_path: Path) -> None:
    """Verify empty cells list returns [] without leaking accounts from disk."""
    profile = "test_profile"
    seq_dir = tmp_path / profile / "prospects" / "sequences"
    seq_dir.mkdir(parents=True)
    rtl = seq_dir / "ready-to-load.csv"
    rtl.write_text("company,email\nUnrelated Corp,unrelated@example.com\n", encoding="utf-8")

    empty_wave: list[dict[str, Any]] = [{"cell_id": "c1", "members": []}]
    result = list_ready_to_send_accounts(empty_wave, profile=profile, content_root=tmp_path)
    assert result == []


def test_build_model_degrades_gracefully_on_corrupt_history(tmp_path: Path) -> None:
    """Verify build_model catches unhandled ValueError on malformed ledger line."""
    from gtm_core.email_campaign_dashboard.model import build_model
    from tests.test_email_campaign_dashboard import _seed_operational

    profile = _seed_operational(tmp_path)
    history_file = tmp_path / profile / "history.jsonl"
    history_file.write_text(
        '{"ts": "2026-09-25T12:00:00Z", "event": "inbound"}\n'
        '{"ts": "2026-09-25T12:05:00Z", CORRUPT_LINE\n',
        encoding="utf-8",
    )
    model = build_model(profile, tmp_path)
    assert model["frontier_events"] == []


def test_send_cards_member_level_terminology_mapping() -> None:
    """Verify send cards review page maps member level through map_term."""
    from gtm_core.send_cards import Card, CardMember, generate_cards_page

    member = CardMember(
        name="Alex Smith",
        email="alex@harborlight.example",
        company="Harborlight Corp",
        industry="Software",
        country="US",
        level="champion",
        seat="CISO",
        opener="",
        source_url="",
        capture_date="",
        signal_kind="event",
        ticked=True,
    )
    card = Card(
        cell_id="c1",
        title="Review Card",
        cohort="Enterprise",
        seat="CISO",
        message_variant="W1",
        angle="Angle",
        segment="Segment",
        premise_ids=[],
        proof_ids=[],
        gate_receipt={},
        is_personalised=False,
        example_member={},
        members=[member],
        panel_verdicts=[],
        sequence_id="s1",
        step_id="st1",
        steps=[],
        spec="spec.md",
    )
    page_html = generate_cards_page([card])
    assert "Primary Advocate" in page_html
    assert "<td>champion</td>" not in page_html


def test_render_stylesheet_custom_palette() -> None:
    """Verify render_stylesheet merges custom tenant palette tokens into :root."""
    from gtm_core.email_campaign_dashboard.styles import render_stylesheet

    custom_palette = {
        "canvas": "#112233",
        "surface": "#223344",
        "primary": "#445566",
        "accent": "#778899",
        "rule": "#334455",
        "ink": "#ffffff",
    }
    css = render_stylesheet(custom_palette)
    assert "--bg:#112233" in css
    assert "--panel:#223344" in css
    assert "--accent:#445566" in css
    assert "--teal:#778899" in css


def test_extract_angle_title() -> None:
    """Verify clean narrative opening angle title extraction."""
    assert (
        extract_angle_title(
            "Generic lane · The sign-off nobody watched · CEO × seat-remit · 2026-09-25"
        )
        == "The sign-off nobody watched"
    )
    assert (
        extract_angle_title("CEO · The Sign-Off Nobody Watched · Software Devtools Startup")
        == "The Sign-Off Nobody Watched"
    )
    assert extract_angle_title("SG Builders Generic (Named Seats) · 2026-09-04") == "Named Seats"
    assert (
        extract_angle_title("", "enterprise::ai-platform::audit-ready")
        == "Enterprise Ai Platform Audit Ready"
    )


def test_calculate_angle_heatmap_sources_and_metric_label() -> None:
    """Verify heatmap derives angle titles from sequence sources and reports accurate metric labels."""
    # "replied" — the key `cells.build_cells` actually sets. A prior version of this fixture
    # used "replies", which the heatmap never read; that let the key-mismatch bug this test
    # exists to catch pass silently.
    cells = [
        {"seat": "CEO", "sequence_id": "seq_1", "sendable": 10, "replied": 0},
        {"seat": "Security", "sequence_id": "seq_2", "sendable": 5, "replied": 0},
    ]
    sources = [
        {"sequence_id": "seq_1", "title": "Inbound · The sign-off nobody watched · CEO"},
        {"sequence_id": "seq_2", "title": "Lane · Authority is not identity · Security"},
    ]
    heatmap = calculate_angle_heatmap(cells, sources)
    assert heatmap["hooks"] == ["Authority is not identity", "The sign-off nobody watched"]
    assert heatmap["metric_label"] == "Planned Prospects"
    assert heatmap["has_replies"] is False

    # When replies > 0
    cells_with_replies = [
        {"seat": "CEO", "sequence_id": "seq_1", "sendable": 10, "replied": 2},
    ]
    heatmap_replies = calculate_angle_heatmap(cells_with_replies, sources)
    assert heatmap_replies["metric_label"] == "Replies"
    assert heatmap_replies["has_replies"] is True
    # The cell holds what the label says: 2 replies, not the 10 planned prospects (2026-10-06).
    assert [c["count"] for c in heatmap_replies["cells"]] == [2]
    # A genuinely empty cell (every recipient suppressed) must stay 0, never become a
    # phantom "1 planned prospect".
    zeroed = calculate_angle_heatmap(
        [{"seat": "CEO", "sequence_id": "seq_1", "sendable": 0, "replied": 0}], sources
    )
    assert zeroed["cells"][0]["count"] == 0


def test_render_angle_heatmap_section_active_vs_archive_toggle() -> None:
    """Verify heatmap section renders active campaign by default and includes archive toggle when difference exists."""
    cells = [
        {"seat": "CEO", "sequence_id": "seq_active", "sendable": 10},
        {"seat": "CTO", "sequence_id": "seq_old", "sendable": 5},
    ]
    sources = [
        {"sequence_id": "seq_active", "title": "Lane · Active Angle · CEO"},
        {"sequence_id": "seq_old", "title": "Lane · Archived Angle · CTO"},
    ]
    campaigns = [{"campaign_id": "c1", "sequences": [{"sequence_id": "seq_active"}]}]
    model = {
        "cells": {"cells": cells, "sources": sources},
        "campaigns": {"campaigns": campaigns},
    }

    html = render_angle_heatmap_section(model)
    assert "Persona vs. Hook Allocation" in html
    assert "Pre-send audience allocation across target personas" in html
    assert "Active Campaign (1 Hooks)" in html
    assert "All-Time Archive (2 Hooks)" in html
    assert 'id="heatmap-view-active"' in html
    assert 'id="heatmap-view-archive"' in html
    assert "10 planned prospects" in html
