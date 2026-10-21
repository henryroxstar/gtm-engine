"""Fleet Executor PRD boundary contract test (§2.2, §3 G9, PENDING.md Contract suites).

Asserts that no field on RunRequest, RunResponse, PackDescriptor, run-event.schema.json,
or any G9 tool schema accepts a publish destination: URL, channel, account, or platform.

Invariants:
  - The destination is pinned server-side and unrepresentable to callers (publish gate).
  - External callers can never specify an egress destination or platform in any task or run field.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from backend.schemas import RunRequest, RunResponse
from mcp_server.server import mcp

REPO = Path(__file__).resolve().parents[2]

# Forbidden terms that would indicate a publish destination / egress target
FORBIDDEN_DESTINATION_FIELDS = frozenset(
    {
        "channel",
        "platform",
        "publish_url",
        "webhook_url",
        "destination",
        "target_url",
        "target_account",
    }
)


def test_run_request_and_response_fields_do_not_accept_destinations():
    for model_cls in (RunRequest, RunResponse):
        for field_name in model_cls.model_fields:
            assert field_name not in FORBIDDEN_DESTINATION_FIELDS, (
                f"{model_cls.__name__}.{field_name} violates destination boundary"
            )


def test_pack_descriptor_schema_does_not_accept_destinations():
    schema_path = REPO / "schemas" / "pack-descriptor.schema.json"
    assert schema_path.exists(), "pack-descriptor.schema.json missing"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    def check_properties(obj, path=""):
        if isinstance(obj, dict):
            props = obj.get("properties", {})
            for prop in props:
                assert prop not in FORBIDDEN_DESTINATION_FIELDS, (
                    f"pack-descriptor property {path}.{prop} violates destination boundary"
                )
            for k, v in obj.items():
                check_properties(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                check_properties(item, f"{path}[{i}]")

    check_properties(schema)


def test_run_event_schema_does_not_accept_destinations():
    schema_path = REPO / "schemas" / "run-event.schema.json"
    assert schema_path.exists(), "run-event.schema.json missing"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    def check_properties(obj, path=""):
        if isinstance(obj, dict):
            props = obj.get("properties", {})
            for prop in props:
                assert prop not in FORBIDDEN_DESTINATION_FIELDS, (
                    f"run-event property {path}.{prop} violates destination boundary"
                )
            for k, v in obj.items():
                check_properties(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                check_properties(item, f"{path}[{i}]")

    check_properties(schema)


def test_g9_mcp_tool_schemas_do_not_accept_destinations():
    tools = asyncio.run(mcp.list_tools())
    g9_tools = {"describe", "dispatch", "status", "artifacts", "cost"}
    found_g9 = set()

    for tool in tools:
        if tool.name in g9_tools:
            found_g9.add(tool.name)
            properties = tool.inputSchema.get("properties", {})
            for param in properties:
                assert param not in FORBIDDEN_DESTINATION_FIELDS, (
                    f"G9 tool {tool.name} parameter {param} violates destination boundary"
                )

    assert found_g9 == g9_tools, (
        f"Expected all G9 tools {g9_tools} to be registered, found {found_g9}"
    )


def test_no_gate_approval_tool_is_exposed_on_mcp():
    tools = asyncio.run(mcp.list_tools())
    tool_names = {tool.name for tool in tools}
    for forbidden in ("approve_gate", "decide_gate", "gate", "resolve_gate"):
        assert forbidden not in tool_names, (
            f"Gate approval tool {forbidden!r} must never be exposed on MCP (G4/G9)"
        )
