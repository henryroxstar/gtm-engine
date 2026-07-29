"""P4 tests — pipeline stage→role routing + publish-gate integrity (PRD §6/§10).

The hybrid-brain split is enforced at one point: the stage→role map in
``agent.pipeline_executor``. These tests pin that map against the *registry* (radar/
research → the cheap brain_radar role = Haiku; plan/studio/publish → Claude Sonnet) and
assert the publish stage can never smuggle an external action onto a worker model —
it short-circuits to SKIPPED before any model is selected.

NOTE: brain_radar resolves to claude-haiku-4-5, not DeepSeek — the Claude Code CLI
rejects non-Claude model ids before the API call fires (2026-06-22 live-smoke fix).
DeepSeek cost savings flow through the httpx-based worker_draft MCP role instead.

SDK-INDEPENDENT: pipeline_executor imports the SDK lazily inside execute_stage; the
publish short-circuit returns before that import, so this runs without the SDK.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from gtm_core.models import resolve_model

pytest.importorskip("agent.pipeline_executor", reason="agent.pipeline_executor not available")

from agent import pipeline_executor as pe  # noqa: E402
from agent.pipeline import SKIPPED  # noqa: E402

_REGISTRY = Path(__file__).resolve().parents[2] / "gtm_core" / "models.toml"


# --- the stage→role split (the single enforcement point) ----------------------


def test_mechanical_stages_route_to_brain_radar():
    """radar/research run on the cheap brain_radar role (claude-haiku-4-5, Anthropic path)."""
    for stage in ("radar", "research"):
        role = pe._STAGE_ROLES[stage]
        assert role == "brain_radar"
        spec = resolve_model(role, registry_path=_REGISTRY)
        assert spec.provider == "anthropic"
        assert spec.model == "claude-haiku-4-5"


def test_gate_and_pii_stages_stay_on_claude():
    """plan/studio/publish — judgment + the human gates + PII — stay on a Claude role."""
    for stage in ("plan", "studio", "publish"):
        role = pe._STAGE_ROLES[stage]
        assert role == "brain_plan"
        assert resolve_model(role, registry_path=_REGISTRY).provider == "anthropic"


def test_every_canonical_stage_is_mapped():
    from agent.pipeline import STAGES

    assert set(STAGES) <= set(pe._STAGE_ROLES)


def test_unknown_stage_defaults_to_claude():
    assert pe._STAGE_ROLES.get("mystery", "brain_plan") == "brain_plan"
    assert resolve_model("brain_plan", registry_path=_REGISTRY).provider == "anthropic"


# --- publish gate intact: a worker stage cannot smuggle an external action -----


def test_publish_short_circuits_to_skipped_without_a_model(monkeypatch):
    """execute_stage('publish') returns SKIPPED before any model/SDK selection.

    Two independent regression guards, both armed to fire if the publish short-circuit
    ever moves below the work it is meant to skip:

    * ``build_agent_options`` is replaced with a tripwire — proving the publish path
      never routes to (and cannot be smuggled onto) a worker brain.
    * ``claude_agent_sdk`` is forced to fail on import — proving the publish path is
      genuinely SDK-independent (the import must sit *below* the publish guard). This
      also keeps the test order-independent: it never imports the real SDK, so it can't
      be tripped by an ``import mcp`` shadow leaking in from another test module.
    """

    def _boom(*a, **k):  # pragma: no cover - must never be called for publish
        raise AssertionError("publish must not build agent options / select a model")

    # None in sys.modules makes any `import claude_agent_sdk` raise; monkeypatch
    # restores the prior entry on teardown.
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    monkeypatch.setattr(pe, "build_agent_options", _boom)

    class _Cfg:
        repo_root = Path(".")

    outcome = asyncio.run(pe.execute_stage(_Cfg(), "example", "publish", {}))
    assert outcome.status == SKIPPED
    assert outcome.outputs == ("publish-manual",)
