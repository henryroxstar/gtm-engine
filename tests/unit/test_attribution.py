"""Unit tests for W9 Attribution (R9.1) in gtm_core.cells.

Verifies:
1. cells.toml points each approved sequence at an -enrolled.csv holding approved members.
2. resolve_reply_cell resolves a fictional reply email back to the expected cell_id.
3. sequencer_outcomes dry-run correctly attributes replies to the cell_id.
"""

from __future__ import annotations

from pathlib import Path

from gtm_core import cells
from gtm_core import prospects_consolidate as pc
from gtm_core import sequencer_outcomes as so

CSV_HEADER = "first,last,email,title,company,company_domain,city,country,segment,tier,signal_clause,why_now,case_study,src,suppression,suppression_date\n"


def _row(email: str, title: str, segment: str = "enterprise") -> str:
    domain = email.split("@")[1]
    company = domain.split(".")[0].capitalize()
    return (
        f"Alex,Fictional,{email},{title},{company},{domain},Austin,United States,"
        f"{segment},A,Builds agentic systems,,,,,\n"
    )


def _setup_sequence(
    tmp_path: Path,
    profile: str,
    seq_name: str,
    csv_filename: str,
    spec_filename: str,
    rows: list[str],
    *,
    lane: str = "personalised",
    overlay: str = "base",
) -> None:
    seq_dir = pc._prospects_dir(profile, tmp_path) / "sequences"
    seq_dir.mkdir(parents=True, exist_ok=True)
    (seq_dir / csv_filename).write_text(CSV_HEADER + "".join(rows), encoding="utf-8")

    cells_file = seq_dir / "cells.toml"
    overlay_line = f'overlay = "{overlay}"\n' if overlay != "base" else ""
    block = (
        f"[[sequence]]\n"
        f'id = "{seq_name}"\n'
        f'csv = "{csv_filename}"\n'
        f'spec = "{spec_filename}"\n'
        f'lane = "{lane}"\n'
        f"{overlay_line}\n"
    )
    if cells_file.is_file():
        cells_file.write_text(cells_file.read_text(encoding="utf-8") + block, encoding="utf-8")
    else:
        cells_file.write_text(block, encoding="utf-8")


def test_resolve_reply_cell_resolves_fictional_email_to_expected_cell(tmp_path: Path):
    profile = "acme"
    reply_email = "quinn@summitline.example"
    _setup_sequence(
        tmp_path,
        profile,
        seq_name="S1",
        csv_filename="wave1-security-enrolled.csv",
        spec_filename="spec-wave1-security-2026-09-25.md",
        rows=[
            _row(reply_email, "CISO", "enterprise"),
            _row("jordan@meridian.example", "VP Security", "enterprise"),
        ],
    )

    resolved = cells.resolve_reply_cell(reply_email, profile, content_root=tmp_path)
    assert resolved == "base:enterprise:security:wave1-security"


def test_resolve_reply_cell_handles_mixed_case_and_whitespace(tmp_path: Path):
    profile = "acme"
    enrolled_email = "quinn@summitline.example"
    _setup_sequence(
        tmp_path,
        profile,
        seq_name="S1",
        csv_filename="wave1-security-enrolled.csv",
        spec_filename="spec-wave1-security-2026-09-25.md",
        rows=[_row(enrolled_email, "CISO", "enterprise")],
    )

    resolved = cells.resolve_reply_cell(
        "  QUINN@Summitline.EXAMPLE  ", profile, content_root=tmp_path
    )
    assert resolved == "base:enterprise:security:wave1-security"


def test_resolve_reply_cell_returns_none_for_unknown_email(tmp_path: Path):
    profile = "acme"
    _setup_sequence(
        tmp_path,
        profile,
        seq_name="S1",
        csv_filename="wave1-security-enrolled.csv",
        spec_filename="spec-wave1-security-2026-09-25.md",
        rows=[_row("quinn@summitline.example", "CISO", "enterprise")],
    )

    resolved = cells.resolve_reply_cell("stranger@unknown.example", profile, content_root=tmp_path)
    assert resolved is None


def test_resolve_reply_cell_returns_none_for_empty_or_none_email(tmp_path: Path):
    profile = "acme"
    _setup_sequence(
        tmp_path,
        profile,
        seq_name="S1",
        csv_filename="wave1-security-enrolled.csv",
        spec_filename="spec-wave1-security-2026-09-25.md",
        rows=[_row("quinn@summitline.example", "CISO", "enterprise")],
    )

    assert cells.resolve_reply_cell("", profile, content_root=tmp_path) is None
    assert cells.resolve_reply_cell("   ", profile, content_root=tmp_path) is None
    assert cells.resolve_reply_cell(None, profile, content_root=tmp_path) is None


def test_resolve_reply_cell_returns_none_when_cells_toml_absent(tmp_path: Path):
    profile = "acme"
    assert (
        cells.resolve_reply_cell("quinn@summitline.example", profile, content_root=tmp_path) is None
    )


def test_resolve_reply_cell_with_overlay_dimension(tmp_path: Path):
    profile = "acme"
    reply_email = "morgan@fictional-domain.example"
    _setup_sequence(
        tmp_path,
        profile,
        seq_name="S2",
        csv_filename="exp-wedge-enrolled.csv",
        spec_filename="spec-wedge-2026-09-25.md",
        rows=[_row(reply_email, "CISO", "enterprise")],
        overlay="wedge-experiment",
    )

    resolved = cells.resolve_reply_cell(reply_email, profile, content_root=tmp_path)
    assert resolved == "wedge-experiment:enterprise:security:wedge"


def test_register_sequence_helper_creates_and_updates_enrolled_entry(tmp_path: Path):
    profile = "acme"
    seq_dir = pc._prospects_dir(profile, tmp_path) / "sequences"
    seq_dir.mkdir(parents=True, exist_ok=True)

    enrolled_csv = "wave2-cto-enrolled.csv"
    (seq_dir / enrolled_csv).write_text(
        CSV_HEADER + _row("taylor@copperline.example", "CTO", "startup"),
        encoding="utf-8",
    )

    # Register sequence
    cells.register_sequence(
        profile=profile,
        sequence_id="S3",
        csv="wave2-cto-enrolled.csv",
        spec="spec-wave2-cto-2026-09-25.md",
        lane="personalised",
        content_root=tmp_path,
    )

    # Verify cells.toml entry
    cells_file = seq_dir / "cells.toml"
    assert cells_file.is_file()
    entries = cells.load_cell_map(profile, content_root=tmp_path)
    assert len(entries) == 1
    assert entries[0]["sequence_id"] == "S3"
    assert entries[0]["csv"] == "wave2-cto-enrolled.csv"
    assert entries[0]["spec"] == "spec-wave2-cto-2026-09-25.md"
    assert entries[0]["lane"] == "personalised"

    # Verify reply resolution
    resolved = cells.resolve_reply_cell("taylor@copperline.example", profile, content_root=tmp_path)
    assert resolved == "base:startup:cto:wave2-cto"

    # Verify idempotency / update
    cells.register_sequence(
        profile=profile,
        sequence_id="S3",
        csv="wave2-cto-enrolled.csv",
        spec="spec-wave2-cto-v2-2026-09-25.md",
        lane="personalised",
        content_root=tmp_path,
    )
    entries_updated = cells.load_cell_map(profile, content_root=tmp_path)
    assert len(entries_updated) == 1
    assert entries_updated[0]["spec"] == "spec-wave2-cto-v2-2026-09-25.md"


def test_sequencer_outcomes_dry_run_attribution_r9_1(tmp_path: Path):
    """R9.1: a fictional reply is attributed to its cell_id by sequencer_outcomes (dry run)."""
    profile = "acme"
    prospect_email = "quinn@summitline.example"
    rep_email = "rep@ourco.example"

    _setup_sequence(
        tmp_path,
        profile,
        seq_name="S1",
        csv_filename="wave1-security-enrolled.csv",
        spec_filename="spec-wave1-security-2026-09-25.md",
        rows=[_row(prospect_email, "CISO", "enterprise")],
    )

    # Construct incoming reply thread payload
    thread_payload = {
        "emailThreadId": "thread-101",
        "payload": [
            {
                "emailId": "msg-1",
                "fromEmail": rep_email,
                "to": [prospect_email],
                "fromProspectId": None,
                "toProspectId": 999111,
            },
            {
                "emailId": "msg-2",
                "fromEmail": prospect_email,
                "to": [rep_email],
                "fromProspectId": 999111,
                "toProspectId": None,
            },
        ],
    }

    email_list_payload = {
        "payload": {
            "items": [
                {
                    "emailThreadId": "thread-101",
                    "sequenceId": "S1",
                    "categoryId": 1,
                    "sentiment": "Positive",
                    "isRepliedByProspect": 1,
                    "subject": "Re: agent evaluation",
                    "sentAt": "2026-09-25T12:00:00.000Z",
                    "prospectName": "Quinn Summit",
                    "isUnsubscribed": 0,
                }
            ]
        }
    }

    taxonomy_payload = {
        "payload": {
            "items": [
                {"id": 1, "name": "Interested", "sentiment": "Positive"},
            ]
        }
    }

    # Verify sequencer_outcomes resolves via email_index (backed by cells.toml)
    email_to_cell = cells.email_index(profile, tmp_path)
    assert email_to_cell.get(prospect_email) == "base:enterprise:security:wave1-security"

    planned, unresolved = so.plan_rows(
        email_list_payload,
        [thread_payload],
        taxonomy_payload,
        email_to_cell,
    )

    assert unresolved == []
    assert len(planned) >= 1
    reply_row = planned[0]
    assert reply_row["outcome"] == "reply"
    assert "cell:base:enterprise:security:wave1-security" in reply_row["tags"]
    assert "seq:S1" in reply_row["tags"]


def test_send_cards_apply_registers_sequence_and_resolves_attribution(tmp_path: Path):
    import json

    from gtm_core.send_cards import create_card_export, send_cards_apply

    profile = "acme"
    prospect_email = "lead1@corp.example"
    cells_data = [
        {
            "cell_id": "cell-sec-01",
            "cohort": "enterprise",
            "seat": "security",
            "angle": "threat-intel",
            "segment": "enterprise",
            "title": "Security Leaders Threat Intel",
            "sequence_id": "seq-sec-101",
            "spec": "spec-threat-intel.md",
            "is_personalised": False,
            "members": [
                {
                    "email": prospect_email,
                    "first": "Sam",
                    "last": "Security",
                    "company": "Corp",
                }
            ],
        }
    ]
    export_payload = create_card_export(
        cells_data,
        decisions={"cell-sec-01": "send this cell"},
        run_id="run-wave-99",
    )
    export_file = tmp_path / "export.json"
    export_file.write_text(json.dumps(export_payload), encoding="utf-8")

    res = send_cards_apply(
        export_file, profile=profile, content_root=tmp_path, run_id="run-wave-99"
    )
    assert len(res.draft_paths) == 1

    cells_file = tmp_path / profile / "prospects" / "sequences" / "cells.toml"
    assert cells_file.is_file(), "cells.toml was not created by send_cards_apply"
    content = cells_file.read_text(encoding="utf-8")
    assert 'id = "seq-sec-101"' in content
    assert 'csv = "seq-sec-101-enrolled.csv"' in content

    enrolled_csv = tmp_path / profile / "prospects" / "sequences" / "seq-sec-101-enrolled.csv"
    assert enrolled_csv.is_file(), "enrolled CSV was not created by send_cards_apply"

    # Verify attribution resolver resolves back to cell_id
    resolved = cells.resolve_reply_cell(prospect_email, profile=profile, content_root=tmp_path)
    assert resolved == "base:enterprise:security:threat-intel"
