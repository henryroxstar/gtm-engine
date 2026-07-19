"""Regression + invariant tests for the Saleshandy staging wrapper.

Locks two things a docs-only build missed and a live API test caught:

1. The step ``type`` is an INTEGER channel code, not the channel name — the raw REST
   API rejects the string "Email" with "type must be a valid enum value". The wrapper
   maps friendly names → codes (Email=1, verified live).
2. The security invariant: NO activate/resume/send/delete tool is exposed, so the brain
   cannot make Saleshandy send email — build is a capability, send is not (the email
   analogue of the publish gate). Design: docs/prds/2026-07-13-email-sequence.md.
"""

from __future__ import annotations

from agent.mcp.saleshandy import server


def test_step_type_names_map_to_integer_codes():
    assert server._step_type_code("Email") == 1
    assert server._step_type_code("LinkedInMessage") == 3
    assert server._step_type_code("LinkedInInMail") == 4
    assert server._step_type_code("CallFollowUp") == 13
    assert server._step_type_code("WhatsappMessage") == 16
    assert server._step_type_code("1") == 1  # a raw integer string is accepted too
    assert server._step_type_code("Bogus") is None
    assert server._step_type_code("resume") is None  # not a step channel


def test_no_send_activate_or_delete_tool_is_exposed():
    """Sending is triggered by resuming a sequence; that must be unrepresentable here."""
    forbidden = [
        "resume_sequence",
        "activate_sequence",
        "update_sequence_status",
        "start_sequence",
        "launch_sequence",
        "pause_sequence",
        "delete_sequence",
        "revoke_sequence",
        "send_sequence",
    ]
    for name in forbidden:
        assert not hasattr(server, name), f"forbidden send/activate tool exposed: {name}"
    # ...and no step-type code maps to a status change either.
    assert "resume" not in server._STEP_TYPE_CODES
    assert "activate" not in server._STEP_TYPE_CODES


def test_expected_staging_and_read_tools_present():
    for name in [
        "list_email_accounts",
        "list_sequences",
        "get_sequence_stats",
        "get_sequence_settings",
        "update_sequence_settings",
        "create_sequence",
        "add_sequence_step",
        "add_step_variant",
        "create_schedule",
        "add_email_accounts_to_sequence",
        "add_leads_to_sequence",
        "import_prospects_to_sequence",
    ]:
        assert hasattr(server, name), f"missing staging/read tool: {name}"
