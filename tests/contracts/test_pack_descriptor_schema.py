"""Contract tests for pack descriptor schema conformance (schemas/pack-descriptor.schema.json).

Validates that:
1. Real descriptors generated for all packs conform to the JSON schema.
2. Descriptors with kind="integration" in readiness.items conform to the schema.
3. Descriptors with inputs.integrations conform to the schema.
4. Unknown readiness item kinds or malformed structures fail validation (discriminating test).
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.readiness import RED, ReadinessItem, ReadinessReport
from backend.pack_catalog import ResolvedVariant, descriptor
from gtm_core.packs.loader import (
    PackInputIntegration,
    PackInputs,
    load_pack_graph,
    load_pack_inputs,
)
from tests.contracts.minijsonschema import validate as schema_validate

REPO = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((REPO / "schemas" / "pack-descriptor.schema.json").read_text(encoding="utf-8"))


def test_schema_itself_is_valid_json():
    assert SCHEMA["title"] == "PackDescriptor"
    assert (
        "integration"
        in SCHEMA["properties"]["readiness"]["properties"]["items"]["items"]["properties"]["kind"][
            "enum"
        ]
    )
    assert "integrations" in SCHEMA["properties"]["inputs"]["properties"]


def test_descriptor_with_integration_readiness_validates():
    graph = load_pack_graph(REPO / "packs" / "marketing" / "graphs" / "linkedin-post.toml")
    resolved = ResolvedVariant(
        graph=graph,
        inputs=PackInputs(
            integrations=(PackInputIntegration(provider="saleshandy", required=True),),
        ),
    )
    report = ReadinessReport(
        items=(
            ReadinessItem(
                kind="integration",
                name="saleshandy",
                status=RED,
                required=True,
                detail="Saleshandy API key is not configured — add it in Settings > Integrations",
            ),
        )
    )
    d = descriptor(resolved, entitlement="pro", readiness=report)
    errors = schema_validate(d, SCHEMA)
    assert not errors, f"Schema validation failed: {errors}"
    assert d["inputs"]["integrations"] == [{"provider": "saleshandy", "required": True}]
    assert d["readiness"]["status"] == "blocked"
    assert d["readiness"]["items"] == [
        {
            "kind": "integration",
            "name": "saleshandy",
            "status": "blocked",
            "reason": "Saleshandy API key is not configured — add it in Settings > Integrations",
        }
    ]


def test_descriptor_with_optional_integration_validates():
    graph = load_pack_graph(REPO / "packs" / "marketing" / "graphs" / "linkedin-post.toml")
    resolved = ResolvedVariant(
        graph=graph,
        inputs=PackInputs(
            integrations=(PackInputIntegration(provider="apollo", required=False),),
        ),
    )
    d = descriptor(resolved, entitlement="pro", readiness=None)
    errors = schema_validate(d, SCHEMA)
    assert not errors, f"Schema validation failed: {errors}"
    assert d["inputs"]["integrations"] == [{"provider": "apollo", "required": False}]


def test_invalid_readiness_kind_fails_validation():
    graph = load_pack_graph(REPO / "packs" / "marketing" / "graphs" / "linkedin-post.toml")
    resolved = ResolvedVariant(graph=graph, inputs=PackInputs())
    d = descriptor(resolved, entitlement="pro", readiness=None)
    d["readiness"] = {
        "status": "blocked",
        "items": [
            {
                "kind": "invalid_telepathic_service",
                "name": "saleshandy",
                "status": "blocked",
            }
        ],
    }
    errors = schema_validate(d, SCHEMA)
    assert errors, "Expected invalid readiness kind to fail schema validation"


def test_all_existing_packs_produce_schema_conforming_descriptors():
    graph_paths = sorted((REPO / "packs").glob("*/graphs/*.toml"))
    assert len(graph_paths) >= 3, "Expected at least 3 pack graphs"
    for gp in graph_paths:
        pack_dir = gp.parent.parent
        inputs_path = pack_dir / "inputs.toml"
        graph = load_pack_graph(gp)
        inputs = load_pack_inputs(inputs_path)
        resolved = ResolvedVariant(graph=graph, inputs=inputs)
        d = descriptor(resolved, entitlement="pro_plus", readiness=None)
        errors = schema_validate(d, SCHEMA)
        assert not errors, f"Pack {graph.pack}/{graph.variant} failed schema validation: {errors}"
