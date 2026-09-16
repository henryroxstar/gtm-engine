"""Regression + invariant tests for the Saleshandy staging wrapper.

Locks two things a docs-only build missed and a live API test caught:

1. The step ``type`` is an INTEGER channel code, not the channel name — the raw REST
   API rejects the string "Email" with "type must be a valid enum value". The wrapper
   maps friendly names → codes (Email=1, verified live).
2. The security invariant: NO activate/resume/send/delete tool is exposed, so the brain
   cannot make Saleshandy send email — build is a capability, send is not (the email
   analogue of the publish gate).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

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
        # Inbox is read-only: a reply is a ⟦GATE:reply⟧ artifact, never a tool call.
        "reply_to_thread",
        "reply_to_email",
        "send_reply",
        "send_email",
        "send_message",
    ]
    for name in forbidden:
        assert not hasattr(server, name), f"forbidden send/activate tool exposed: {name}"

    # DNC is a suppression control the brain READS and obeys — never one it edits.
    # An add/remove tool would let the brain un-suppress someone who opted out, which
    # is a compliance failure, not merely a permissions one (docs/email-compliance.md).
    for name in (
        "add_dnc_items",
        "remove_dnc_items",
        "delete_dnc_item",
        "create_dnc_list",
        "delete_dnc_list",
        "update_dnc_list",
        "clear_dnc_list",
    ):
        assert not hasattr(server, name), f"forbidden DNC write tool exposed: {name}"


def test_pre_enrollment_reads_are_python_only_not_mcp_tools():
    """The read-back agent.email_dispatch runs before enrolling (client issue #244) is not
    a tool surface: the brain already has list_sequences, and needs nothing new."""
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    for name in (
        "_list_sequences_page_request",
        "_get_step_variants_request",
        "_list_fields_request",
    ):
        assert callable(getattr(server, name))
        assert name not in tools and name.lstrip("_") not in tools


def test_step_variants_path_cannot_be_steered_by_an_id():
    """Ids come from a model-written draft; one containing a separator must stay a single
    path segment instead of reaching another endpoint."""
    seen = []

    async def _fake_call(method, path, **kwargs):
        seen.append(path)
        return "[]"

    with patch.object(server, "_call", _fake_call):
        asyncio.run(server._get_step_variants_request("k", "seq/../dnc", "st?x=1"))
    assert seen == ["/sequences/seq%2F..%2Fdnc/steps/st%3Fx%3D1"]


def test_dnc_read_tools_are_exposed():
    """The read side must exist — without it the scheduled path has no live suppression
    source and silently consolidates against a stale cache."""
    assert hasattr(server, "list_dnc_lists")
    assert hasattr(server, "get_dnc_items")


def test_get_dnc_items_requires_a_list_id():
    """Guard the caller-error path: an empty id must not become GET /dnc/ (which would
    return the *list of lists* and read as an empty suppression set)."""
    out = asyncio.run(server.get_dnc_items(dnc_list_id="   "))
    assert out.startswith("[saleshandy-error]")
    assert "list_dnc_lists" in out
    # ...and no step-type code maps to a status change either.
    assert "resume" not in server._STEP_TYPE_CODES
    assert "activate" not in server._STEP_TYPE_CODES


def test_expected_staging_and_read_tools_present():
    for name in [
        "list_email_accounts",
        "list_sequences",
        "get_sequence_stats",
        "get_inbox_threads",
        "get_thread",
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
