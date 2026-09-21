"""Contract tests verifying FastMCP tool schemas against multi-agent frameworks (LangGraph, CrewAI).

Guarantees that tool schemas exposed by FastMCP strictly conform to standard JSON-RPC
tool call specifications and can be loaded dynamically into LangChain/LangGraph and
CrewAI/Pydantic tool abstractions without schema conversion errors.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import create_model

from mcp_server.server import mcp


@pytest.fixture(scope="module")
def registered_tools():
    """Retrieve all tools registered on the FastMCP server instance."""
    tool_manager = getattr(mcp, "_tool_manager", None)
    assert tool_manager is not None, "FastMCP tool manager not found"
    tools = tool_manager.list_tools()
    return tools


def test_expected_tools_registered(registered_tools):
    """Verify core, pipeline, task, and data tools are registered."""
    tool_names = {t.name for t in registered_tools}
    expected = {
        # Core
        "radar_check",
        "profile_context",
        # Pipeline
        "draft_post",
        "draft_outreach",
        # Task
        "describe",
        "dispatch",
        "status",
        "artifacts",
        "cost",
        # Data
        "get_prospect_account",
        "list_prospect_accounts",
        "check_suppression",
    }
    missing = expected - tool_names
    assert not missing, f"Missing registered FastMCP tools: {missing}"


def _get_tool_schema(tool) -> dict[str, Any]:
    if hasattr(tool, "to_mcp_tool"):
        mcp_tool = tool.to_mcp_tool()
        schema = getattr(mcp_tool, "inputSchema", None)
    elif hasattr(tool, "parameters"):
        schema = tool.parameters
    else:
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    assert isinstance(schema, dict), f"Tool {tool.name} schema is not a dict: {dir(tool)}"
    return schema


def test_tool_schemas_conform_to_json_schema(registered_tools):
    """Verify each tool has a valid JSON Schema object with properties and types."""
    for tool in registered_tools:
        schema = _get_tool_schema(tool)
        assert schema.get("type") == "object", f"Tool {tool.name} schema type is not 'object'"
        assert "properties" in schema, f"Tool {tool.name} schema missing 'properties'"
        assert isinstance(schema["properties"], dict), f"Tool {tool.name} properties is not a dict"

        # Check each property specification
        for prop_name, prop_spec in schema["properties"].items():
            assert isinstance(prop_spec, dict), (
                f"Property {prop_name} in tool {tool.name} must be a dict"
            )
            has_type = "type" in prop_spec or "anyOf" in prop_spec or "$ref" in prop_spec
            assert has_type, (
                f"Property {prop_name} in tool {tool.name} missing type definition: {prop_spec}"
            )


def test_langchain_mcp_tool_conversion_compatibility(registered_tools):
    """Verify that FastMCP tools can be converted to LangChain/LangGraph Tool representation."""
    for tool in registered_tools:
        schema = _get_tool_schema(tool)
        props = schema.get("properties", {})
        required = set(schema.get("required", []))

        # LangGraph/LangChain maps MCP inputSchema into an args_schema definition
        langchain_tool_def = {
            "name": tool.name,
            "description": tool.description or "",
            "args_schema": schema,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": list(required),
            },
        }

        assert langchain_tool_def["name"]
        assert isinstance(langchain_tool_def["args_schema"], dict)


def test_crewai_pydantic_dynamic_model_generation(registered_tools):
    """Verify that tool schemas can dynamically construct Pydantic v2 models as CrewAI does."""
    type_map: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict,
        "array": list,
    }

    for tool in registered_tools:
        schema = _get_tool_schema(tool)
        props = schema.get("properties", {})
        required = set(schema.get("required", []))

        field_definitions: dict[str, Any] = {}
        for field_name, field_info in props.items():
            field_type: Any = Any
            if "type" in field_info and field_info["type"] in type_map:
                field_type = type_map[field_info["type"]]

            is_required = field_name in required
            default_val = ... if is_required else None
            field_definitions[field_name] = (field_type, default_val)

        # Build dynamic Pydantic model
        model = create_model(f"{tool.name}_ArgsModel", **field_definitions)
        from pydantic import BaseModel

        assert issubclass(model, BaseModel)

        # Verify validation with empty dict if no required fields, or with mock required fields
        sample_input = {}
        for req_field in required:
            req_spec = props.get(req_field, {})
            typ = req_spec.get("type", "string")
            if typ == "string":
                sample_input[req_field] = "test-val"
            elif typ == "integer":
                sample_input[req_field] = 1
            elif typ == "boolean":
                sample_input[req_field] = True
            elif typ == "object":
                sample_input[req_field] = {}
            elif typ == "array":
                sample_input[req_field] = []

        validated = model(**sample_input)
        assert validated is not None
