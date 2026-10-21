"""Unit tests for pack inputs loader (gtm_core.packs.loader) integrations declaration.

Validates that [[integrations]] blocks are parsed, validated against the closed set
_VALID_INTEGRATION_PROVIDERS, default to required=True, and fail-closed on unknown or
missing providers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.packs.loader import (
    PackInputIntegration,
    PackValidationError,
    load_pack_inputs,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_inputs_parses_valid_integrations(tmp_path):
    path = _write(
        tmp_path,
        "inputs.toml",
        """
        [[integrations]]
        provider = "saleshandy"
        required = true

        [[integrations]]
        provider = "apollo"
        required = false
        """,
    )
    inputs = load_pack_inputs(path)
    assert len(inputs.integrations) == 2
    assert inputs.integrations[0] == PackInputIntegration(provider="saleshandy", required=True)
    assert inputs.integrations[1] == PackInputIntegration(provider="apollo", required=False)


def test_inputs_integration_defaults_required_to_true(tmp_path):
    path = _write(
        tmp_path,
        "inputs.toml",
        """
        [[integrations]]
        provider = "rocketreach"
        """,
    )
    inputs = load_pack_inputs(path)
    assert len(inputs.integrations) == 1
    assert inputs.integrations[0] == PackInputIntegration(provider="rocketreach", required=True)


def test_inputs_rejects_missing_provider(tmp_path):
    path = _write(
        tmp_path,
        "inputs.toml",
        """
        [[integrations]]
        required = true
        """,
    )
    with pytest.raises(PackValidationError) as exc:
        load_pack_inputs(path)
    assert exc.value.rule == "missing_provider"


def test_inputs_rejects_unknown_provider(tmp_path):
    path = _write(
        tmp_path,
        "inputs.toml",
        """
        [[integrations]]
        provider = "unsupported_mailer_service"
        """,
    )
    with pytest.raises(PackValidationError) as exc:
        load_pack_inputs(path)
    assert exc.value.rule == "unknown_provider"


def test_inputs_defaults_integrations_to_empty_tuple_when_omitted(tmp_path):
    path = _write(
        tmp_path,
        "inputs.toml",
        """
        [[settings]]
        key = "brand_name"
        source = "ask"
        """,
    )
    inputs = load_pack_inputs(path)
    assert inputs.integrations == ()


def test_inputs_missing_file_defaults_integrations_to_empty_tuple(tmp_path):
    inputs = load_pack_inputs(tmp_path / "nonexistent" / "inputs.toml")
    assert inputs.integrations == ()
