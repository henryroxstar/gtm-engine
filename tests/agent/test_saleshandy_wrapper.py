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


# --- unified-inbox endpoint shape (2026-09-21) ------------------------------------------
#
# Nothing here pinned the actual method/path for get_inbox_threads / get_thread before
# 2026-09-21, and the ORIGINAL guess (GET /unified-inbox/threads) 404'd on its first real
# account — a live failure that a test like this would have caught before it ever reached
# a live account. Corrected against the official reference
# (https://developer.saleshandy.com/api-reference/unified-inbox, fetched 2026-09-21).


def test_get_inbox_threads_calls_the_documented_endpoint():
    """POST /unified-inbox/emails, not GET /unified-inbox/threads — the exact defect."""
    seen = []

    async def _fake_call(method, path, **kwargs):
        seen.append((method, path, kwargs.get("params"), kwargs.get("json_body")))
        return "[]"

    with patch.object(server, "_call", _fake_call):
        asyncio.run(server.get_inbox_threads(search="acme", page=2, page_size=50))

    ((method, path, params, body),) = seen
    assert method == "POST"
    assert path == "/unified-inbox/emails"
    assert params is None, "filters belong in the JSON body for this endpoint, not the query string"
    assert body == {"search": "acme", "page": 2, "limit": 50}


def test_get_thread_calls_the_documented_endpoint():
    """GET /unified-inbox/emails/{id}, not GET /unified-inbox/threads/{id}."""
    seen = []

    async def _fake_call(method, path, **kwargs):
        seen.append((method, path))
        return "[]"

    with patch.object(server, "_call", _fake_call):
        asyncio.run(server.get_thread("t1"))

    assert seen == [("GET", "/unified-inbox/emails/t1")]


# --- SC10: two read-only tools added, and NOTHING else -----------------------------------
#
# The connector is NOT read-only — it stages sequences — so "the connector stays read-only"
# would be a false claim to assert. The honest one is *no new verb*: the tool-name set is
# exactly what it was, plus the two reads SC10 needs, pinned by name.

#: The @mcp.tool() surface before SC10, transcribed from the 2026-09-21 tree. A change to
#: this list is a change to what the brain can do, and must be argued for in a PRD.
_TOOL_SURFACE_BEFORE_SC10 = frozenset(
    {
        "list_email_accounts",
        "list_sequences",
        "list_dnc_lists",
        "get_dnc_items",
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
    }
)

_SC10_ADDITIONS = frozenset({"get_unibox_categories", "get_outcomes"})


def _decorated_tool_names() -> set[str]:
    """Every `async def` carrying an `@mcp.tool()` decorator, read from the AST.

    AST rather than `dir(server)`: the decorator is what publishes a function to the brain,
    and a plain module attribute (the Python-only request builders `agent.email_dispatch`
    calls) is deliberately NOT part of that surface.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            func = dec.func if isinstance(dec, ast.Call) else dec
            attr = getattr(func, "attr", None)
            value = getattr(func, "value", None)
            if attr == "tool" and getattr(value, "id", None) == "mcp":
                names.add(node.name)
    return names


def test_sc10_adds_exactly_two_tools_and_no_verb():
    surface = _decorated_tool_names()
    assert surface == _TOOL_SURFACE_BEFORE_SC10 | _SC10_ADDITIONS, (
        "the connector's tool surface changed beyond SC10's two reads: "
        f"added={sorted(surface - _TOOL_SURFACE_BEFORE_SC10 - _SC10_ADDITIONS)} "
        f"removed={sorted(_TOOL_SURFACE_BEFORE_SC10 - surface)}"
    )


def test_no_tool_name_matches_a_write_verb_sc10_must_not_add():
    """Belt and braces on the set assertion above: even a renamed addition cannot smuggle
    a reply/send/DNC-write/outcome-update verb onto the brain's surface."""
    import re

    # The DNC *noun* is fine — `list_dnc_lists` / `get_dnc_items` are the reads the
    # suppression mirror depends on. It is the write VERBS that must never appear, here or
    # after SC9: `dnc_add`'s request builder lives in agent/dnc_dispatch.py, reachable only
    # inside the approved dispatch window, and never as a tool the brain can call.
    forbidden = re.compile(
        r"reply|send|resume|activate|unsubscribe|pause|update_prospect_outcome"
        r"|(?:add|create|delete|remove|update|clear)_dnc",
        re.IGNORECASE,
    )
    offenders = [n for n in _decorated_tool_names() if forbidden.search(n)]
    assert not offenders, f"tool name matches a forbidden write verb: {offenders}"


def test_the_two_new_tools_are_get_requests():
    """A read is a GET. If either of these ever became a POST it would be doing something."""
    seen = []

    async def fake_call(method, path, **kw):
        seen.append((method, path, kw))
        return "{}"

    import asyncio

    original = server._call
    server._call = fake_call
    try:
        asyncio.run(server.get_unibox_categories())
        asyncio.run(server.get_outcomes())
    finally:
        server._call = original
    assert [m for m, _p, _k in seen] == ["GET", "GET"]
    assert seen[0][1] == "/unibox/categories"
    assert seen[1][1] == "/unified-inbox/outcome"


def test_category_ids_rides_in_the_inbox_filter_body():
    """SC10 reads a thread's category as FILTER MEMBERSHIP, so the filter must reach the
    provider — an ignored `category_ids` would silently give every thread every category."""
    seen = []

    async def fake_call(method, path, *, params=None, json_body=None, **kw):
        seen.append((method, path, json_body))
        return "{}"

    import asyncio

    original = server._call
    server._call = fake_call
    try:
        asyncio.run(server.get_inbox_threads(category_ids=[11, 13]))
        asyncio.run(server.get_inbox_threads())
    finally:
        server._call = original
    assert seen[0][2]["categoryIds"] == [11, 13]
    # ...and omitted entirely when not asked for, so an unfiltered sweep is unchanged.
    assert "categoryIds" not in seen[1][2]
