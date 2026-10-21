"""Tests for the DeepSeek worker MCP server's registry wiring (P3).

The worker no longer hardcodes its model id or cost rates: it resolves the
``worker_draft`` role from ``gtm_core/models.toml``. These tests pin that contract:
  - the resolved model is the V4 Flash pin (off the deprecating ``deepseek-chat`` alias)
  - the cost-ledger rates are the V4 Flash rates from the registry
  - a missing key degrades to a ``[worker-error]`` string (never raises), naming the
    registry-declared env var

SDK-independent except for mcp/httpx (the worker's own deps), skipped if absent.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")
pytest.importorskip("httpx", reason="httpx not installed")

from agent.mcp.worker import server  # noqa: E402


def test_worker_model_resolves_to_v4_flash_pin():
    """No hardcoded id: the worker's model comes from the registry, pinned off the alias."""
    assert server.DEEPSEEK_MODEL == "deepseek-v4-flash"
    assert server._SPEC.role == "worker_draft"
    assert server._SPEC.provider == "deepseek"


def test_worker_rates_are_v4_flash():
    """Cost-ledger rates track V4 Flash from the registry (not the stale 0.00027/0.00110)."""
    assert server._INPUT_USD_PER_1K == 0.00014
    assert server._OUTPUT_USD_PER_1K == 0.00028


def test_chat_no_key_returns_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    out = asyncio.run(server._chat([{"role": "user", "content": "hi"}], op="draft"))
    assert out.startswith("[worker-error]")
    assert "DEEPSEEK_API_KEY" in out


def test_meter_uses_resolved_model(monkeypatch, tmp_path):
    """The cost record names the registry-resolved model, not a hardcoded literal."""
    monkeypatch.setenv("GTM_PROFILE", "example")
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    server._meter("draft", {"prompt_tokens": 1000, "completion_tokens": 1000})
    ledger = tmp_path / "example" / "costs.jsonl"
    assert ledger.is_file()
    import json

    rec = json.loads(ledger.read_text().splitlines()[-1])
    assert rec["model"] == "deepseek-v4-flash"
    # 1000/1000 tokens at V4 Flash rates → input 0.00014 + output 0.00028.
    assert rec["cost_usd"] == pytest.approx(0.00042, abs=1e-9)
