"""P4 contract tests — cross-provider brain routing (model routing PRD §5.3/§10).

The contract: when ``build_agent_options`` resolves a NON-Anthropic role (e.g.
``brain_radar`` → DeepSeek), it points THAT subprocess's SDK env at the provider's
base_url + key via ``options.env``; an Anthropic role sets no env override (inherits
the real ``ANTHROPIC_*``). Routing must NOT relax the permission posture or the
guardrail-injection mechanism (setting_sources=["project"] still loads CLAUDE.md §R5).

These are the structural halves of the §10 blocking gate that are checkable without a
live call; the behavioral R5-injection-on-DeepSeek check is a live-smoke item (a fake
gate-marker news row must not be obeyed / must not emit a gate).

SDK-INDEPENDENT: ``ClaudeAgentOptions`` is patched so claude-agent-sdk need not be
installed (mirrors test_session_p2.py).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from gtm_core.models import ModelSpec, resolve_model

pytest.importorskip("agent.session", reason="agent.session not available")

from agent.session import build_agent_options  # noqa: E402

_REGISTRY = Path(__file__).resolve().parents[2] / "gtm_core" / "models.toml"


def _deepseek_spec() -> ModelSpec:
    return ModelSpec(
        role="brain_radar",
        provider="deepseek",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        supports_effort=False,
        supports_adaptive_thinking=False,
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
    monkeypatch.setattr("agent.permissions.make_headless_can_use_tool", lambda: "POLICY")
    monkeypatch.setattr("agent.permissions.DANGEROUS_TOOL_DENY_RULES", ["Bash"])

    with patch("claude_agent_sdk.ClaudeAgentOptions", _CapturingOptions):
        from agent.config import Config

        cfg = Config.from_env()
        build_agent_options(cfg, "example", role=spec.role)

    return captured


# --- cross-provider routing --------------------------------------------------


def test_deepseek_role_routes_subprocess_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")
    kw = _build_kwargs(_deepseek_spec(), monkeypatch)
    assert kw["model"] == "deepseek-v4-flash"
    env = kw["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com"
    # Both the key and the bearer token are overridden so the spawned CLI talks to DeepSeek.
    assert env["ANTHROPIC_API_KEY"] == "sk-deepseek-test"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-deepseek-test"


def test_anthropic_role_sets_no_provider_env_override(monkeypatch):
    """brain_plan (Claude) must NOT override ANTHROPIC_* — it inherits the real creds.

    env is now always present: GTM_CONTENT_ROOT/GTM_PROFILES_ROOT scope the run's
    subprocess to its workspace tree (P3). The security invariant is that a Claude
    role adds NO provider (ANTHROPIC_*) override, so it talks to real Anthropic.
    """
    plan = resolve_model("brain_plan", registry_path=_REGISTRY)
    kw = _build_kwargs(plan, monkeypatch)
    assert "ANTHROPIC_BASE_URL" not in kw["env"]
    assert "GTM_CONTENT_ROOT" in kw["env"] and "GTM_PROFILES_ROOT" in kw["env"]
    assert kw["model"] == "claude-sonnet-4-6"


def test_missing_deepseek_key_routes_with_empty_token(monkeypatch):
    """No key → still routes (empty token) rather than leaking the real Anthropic key to DeepSeek."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    kw = _build_kwargs(_deepseek_spec(), monkeypatch)
    assert kw["env"]["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com"
    assert kw["env"]["ANTHROPIC_API_KEY"] == ""
    assert kw["env"]["ANTHROPIC_AUTH_TOKEN"] == ""


# --- routing must not relax the security posture (tenant/permission boundary) --


def test_deepseek_routing_keeps_permission_posture(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")
    kw = _build_kwargs(_deepseek_spec(), monkeypatch)
    # Same least-privilege posture as the Claude brain — routing changes the model, not the gate.
    assert kw["permission_mode"] == "default"
    assert kw["can_use_tool"] == "POLICY"
    assert kw["disallowed_tools"] == ["Bash"]
    # §R5 guardrail injection mechanism unchanged: CLAUDE.md still loads on the DeepSeek stage.
    assert kw["setting_sources"] == ["project"]
    # Capability guard still holds (no 400 params on the DeepSeek model).
    assert "effort" not in kw
    assert "thinking" not in kw


def test_real_brain_radar_resolves_to_haiku_anthropic_path(monkeypatch):
    """End-to-end through the real resolver: brain_radar is Haiku on the Anthropic path.

    The Claude Code CLI rejects non-Claude model ids before the API call fires, so
    brain_radar uses claude-haiku-4-5 (3-5x cheaper than Sonnet, same provider path)
    rather than DeepSeek — corrected in the 2026-06-22 live-smoke gate. DeepSeek cost
    savings flow through the httpx-based worker_draft MCP role, not this SDK subprocess.
    So brain_radar must set NO provider (ANTHROPIC_*) override — it inherits the real
    ANTHROPIC_* (env is still present for GTM_CONTENT_ROOT/GTM_PROFILES_ROOT scoping).
    """
    radar = resolve_model("brain_radar", registry_path=_REGISTRY)
    kw = _build_kwargs(radar, monkeypatch)
    assert kw["model"] == "claude-haiku-4-5"
    assert "ANTHROPIC_BASE_URL" not in kw["env"]  # Anthropic path — no provider override
