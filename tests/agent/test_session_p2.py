"""P2 contract tests — build_agent_options capability guard (model routing PRD §5.2/§10).

The contract: ``build_agent_options`` OMITS ``effort``/``thinking`` when the resolved
spec's ``supports_*`` flags are False, and INCLUDES them when True.  This prevents
the BadRequestError a Haiku or DeepSeek spec would otherwise trigger (the 400 guard
described in the test spec's "Contract" section).

SDK-INDEPENDENT: the test patches ``ClaudeAgentOptions`` so claude-agent-sdk need
not be installed (mirrors the agent unit-test pattern in test_permissions.py).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from gtm_core.models import ModelSpec, resolve_model

pytest.importorskip("agent.session", reason="agent.session not available")

from agent.session import build_agent_options  # noqa: E402

# Real registry for capability-flag assertions.
REAL_REGISTRY = (
    Path(resolve_model.__module__.replace(".", "/")).parent.parent / "gtm_core" / "models.toml"
)
_REGISTRY = Path(__file__).resolve().parents[2] / "gtm_core" / "models.toml"


def _stub_spec(*, supports_effort: bool, supports_adaptive_thinking: bool) -> ModelSpec:
    return ModelSpec(
        role="brain_plan",
        provider="anthropic",
        model="claude-sonnet-4-6",
        base_url="https://api.anthropic.com",
        api_key_env="ANTHROPIC_API_KEY",
        supports_effort=supports_effort,
        supports_adaptive_thinking=supports_adaptive_thinking,
    )


def _build_kwargs(spec: ModelSpec, monkeypatch) -> dict:
    """Run build_agent_options with a stubbed spec and return the kwargs captured."""
    captured: dict = {}

    class _CapturingOptions:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("agent.session.resolve_model", lambda *a, **k: spec)
    monkeypatch.setattr("agent.mcp_config.build_mcp_servers", lambda *a, **k: [])
    monkeypatch.setattr("agent.profiles.system_prompt_for", lambda *a, **k: "")
    monkeypatch.setattr("agent.permissions.make_headless_can_use_tool", lambda: None)
    monkeypatch.setattr("agent.permissions.DANGEROUS_TOOL_DENY_RULES", [])

    # ClaudeAgentOptions is imported lazily inside build_agent_options via
    # `from claude_agent_sdk import ClaudeAgentOptions` — patch at the SDK level.
    with patch("claude_agent_sdk.ClaudeAgentOptions", _CapturingOptions):
        from agent.config import Config

        cfg = Config.from_env()
        build_agent_options(cfg, "example")

    return captured


# --- capability guard ---------------------------------------------------------


def test_supports_effort_true_includes_effort(monkeypatch):
    kw = _build_kwargs(
        _stub_spec(supports_effort=True, supports_adaptive_thinking=False), monkeypatch
    )
    assert "effort" in kw


def test_supports_effort_false_omits_effort(monkeypatch):
    kw = _build_kwargs(
        _stub_spec(supports_effort=False, supports_adaptive_thinking=False), monkeypatch
    )
    assert "effort" not in kw


def test_supports_adaptive_thinking_true_includes_thinking(monkeypatch):
    kw = _build_kwargs(
        _stub_spec(supports_effort=False, supports_adaptive_thinking=True), monkeypatch
    )
    assert "thinking" in kw
    assert kw["thinking"] == {"type": "adaptive"}


def test_supports_adaptive_thinking_false_omits_thinking(monkeypatch):
    kw = _build_kwargs(
        _stub_spec(supports_effort=False, supports_adaptive_thinking=False), monkeypatch
    )
    assert "thinking" not in kw


def test_brain_plan_spec_includes_both(monkeypatch):
    """Real brain_plan spec → both params present (no regression to current behaviour)."""
    spec = resolve_model("brain_plan", registry_path=_REGISTRY)
    kw = _build_kwargs(spec, monkeypatch)
    assert "effort" in kw
    assert "thinking" in kw


def test_brain_cheap_spec_omits_both(monkeypatch):
    """brain_cheap (Haiku fallback) → neither param (400-guard in effect)."""
    spec = resolve_model("brain_cheap", registry_path=_REGISTRY)
    kw = _build_kwargs(spec, monkeypatch)
    assert "effort" not in kw
    assert "thinking" not in kw


def test_model_from_registry_not_hardcoded(monkeypatch):
    """The model id on ClaudeAgentOptions comes from the registry, not a literal."""
    spec = _stub_spec(supports_effort=False, supports_adaptive_thinking=False)
    kw = _build_kwargs(spec, monkeypatch)
    assert kw["model"] == "claude-sonnet-4-6"


def test_hermes_model_override_reaches_options(monkeypatch):
    """HERMES_MODEL break-glass propagates through resolve_model into the options."""
    monkeypatch.setenv("HERMES_MODEL", "claude-haiku-4-5")
    # Use the real resolver so the env override is exercised end-to-end.
    real_spec = resolve_model("brain_plan", registry_path=_REGISTRY)
    kw = _build_kwargs(real_spec, monkeypatch)
    assert kw["model"] == "claude-haiku-4-5"
