"""Test that external mail and chat send leaves are denied across all connectors.

R-16: six leaves join _EXTERNAL_EFFECT_LEAVES. Leaf denial is connector-agnostic
on purpose: reply and forward are denied on any server that exposes them.
Draft creation and searching remain allowed.
"""

import pytest

from agent.permissions import classify_tool

DENIED_SEND_TOOLS = [
    "mcp__gmail__send_message",
    "mcp__gmail__forward",
    "mcp__gmail__reply",
    "mcp__slack__slack_send_message",
    "mcp__slack__slack_schedule_message",
    "mcp__saleshandy__reply_to_email",
    "mcp__buffer__create_post",
    "mcp__opaque_uuid__send_message",
    "mcp__any_server__reply",
]

ALLOWED_READ_OR_DRAFT_TOOLS = [
    "mcp__gmail__create_draft",
    "mcp__gmail__search_threads",
    "mcp__slack__conversations_history",
]


@pytest.mark.parametrize("tool", DENIED_SEND_TOOLS)
def test_send_leaves_are_denied(tool: str) -> None:
    decision = classify_tool(tool, {})
    assert decision == "deny", f"Expected {tool} to be denied, got {decision}"


@pytest.mark.parametrize("tool", ALLOWED_READ_OR_DRAFT_TOOLS)
def test_read_and_draft_leaves_remain_allowed(tool: str) -> None:
    decision = classify_tool(tool, {})
    assert decision == "allow", f"Expected {tool} to be allowed, got {decision}"
