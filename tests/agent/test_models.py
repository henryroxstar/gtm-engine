"""Tests for the in-process model registry resolver (gtm_core.models).

Covers the contract the brain/worker/vision wiring relies on:
  - each logical role resolves to the right provider / model / capability flags
  - the shipped gtm_core/models.toml is internally valid (integrity)
  - HERMES_MODEL is a break-glass override for brain_plan ONLY
  - unknown role / unsafe segment raise ValueError
  - fallback chains resolve in order and are cycle-guarded (never infinite-loop)
  - the secret guard refuses an inline api_key / secret-shaped value / bad env name
  - api_key() reads the named env var at call time (key never stored on the spec)
  - the CLI prints the resolved provider/model and uses the documented exit codes

SDK-INDEPENDENT: gtm_core.models is pure stdlib (tomllib), so this runs in CI even
when the editable install (claude-agent-sdk) is unavailable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import models
from gtm_core.models import ModelSpec, resolve_model

# The shipped registry — referenced explicitly so these assertions are immune to a
# stray GTM_MODELS_REGISTRY in the environment.
REAL_REGISTRY = Path(models.__file__).resolve().parent / "models.toml"


def _write_registry(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "models.toml"
    p.write_text(body)
    return p


# --- shipped-registry integrity ----------------------------------------------


@pytest.mark.parametrize(
    ("role", "provider", "model"),
    [
        ("brain_plan", "anthropic", "claude-sonnet-4-6"),
        ("brain_radar", "anthropic", "claude-haiku-4-5"),
        ("brain_cheap", "anthropic", "claude-haiku-4-5"),
        ("worker_draft", "deepseek", "deepseek-v4-flash"),
        ("vision", "anthropic", "claude-haiku-4-5"),
    ],
)
def test_each_role_resolves(role, provider, model):
    spec = resolve_model(role, registry_path=REAL_REGISTRY)
    assert spec.role == role
    assert spec.provider == provider
    assert spec.model == model
    assert spec.api_key_env in {"ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"}
    assert spec.base_url.startswith("https://")


def test_default_path_resolves_without_explicit_registry(monkeypatch):
    """The co-located models.toml is found when no arg/env override is set."""
    monkeypatch.delenv("GTM_MODELS_REGISTRY", raising=False)
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    spec = resolve_model("brain_plan")
    assert spec.model == "claude-sonnet-4-6"


def test_capability_flags_match_the_400_fix():
    """brain_plan supports effort+thinking; the cheap/mechanical roles do NOT."""
    plan = resolve_model("brain_plan", registry_path=REAL_REGISTRY)
    assert plan.supports_effort is True
    assert plan.supports_adaptive_thinking is True
    for role in ("brain_radar", "brain_cheap", "vision"):
        spec = resolve_model(role, registry_path=REAL_REGISTRY)
        assert spec.supports_effort is False, role
        assert spec.supports_adaptive_thinking is False, role


def test_worker_draft_carries_v4_flash_rates():
    spec = resolve_model("worker_draft", registry_path=REAL_REGISTRY)
    assert spec.input_usd_per_1k == 0.00014
    assert spec.output_usd_per_1k == 0.00028


def test_brain_roles_fall_back_to_brain_cheap():
    for role in ("brain_plan", "brain_radar"):
        spec = resolve_model(role, registry_path=REAL_REGISTRY)
        assert [fb.role for fb in spec.fallbacks] == ["brain_cheap"]
        assert spec.fallbacks[0].model == "claude-haiku-4-5"
        assert spec.fallbacks[0].fallbacks == ()  # leaf — no further chain


# --- HERMES_MODEL break-glass ------------------------------------------------


def test_hermes_model_overrides_brain_plan_only(monkeypatch):
    monkeypatch.setenv("HERMES_MODEL", "claude-opus-4-8")
    assert resolve_model("brain_plan", registry_path=REAL_REGISTRY).model == "claude-opus-4-8"
    # Other roles are untouched by the brain_plan break-glass.
    assert resolve_model("brain_radar", registry_path=REAL_REGISTRY).model == "claude-haiku-4-5"
    assert resolve_model("vision", registry_path=REAL_REGISTRY).model == "claude-haiku-4-5"


def test_empty_hermes_model_is_ignored(monkeypatch):
    monkeypatch.setenv("HERMES_MODEL", "")
    assert resolve_model("brain_plan", registry_path=REAL_REGISTRY).model == "claude-sonnet-4-6"


# --- errors ------------------------------------------------------------------


def test_unknown_role_raises():
    with pytest.raises(ValueError, match="unknown model role"):
        resolve_model("does_not_exist", registry_path=REAL_REGISTRY)


@pytest.mark.parametrize("role", ["../evil", "a/b", "..", "", "a\\b"])
def test_unsafe_role_segment_rejected(role):
    with pytest.raises(ValueError):
        resolve_model(role, registry_path=REAL_REGISTRY)


@pytest.mark.parametrize("profile", ["../evil", "a/b", ".."])
def test_unsafe_profile_segment_rejected(profile):
    with pytest.raises(ValueError):
        resolve_model("brain_plan", profile=profile, registry_path=REAL_REGISTRY)


def test_missing_registry_raises(tmp_path):
    with pytest.raises(ValueError, match="model registry not found"):
        resolve_model("brain_plan", registry_path=tmp_path / "nope.toml")


def test_unknown_provider_raises(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.anthropic]
base_url = "https://api.anthropic.com"
api_key_env = "ANTHROPIC_API_KEY"

[roles.orphan]
provider = "ghost"
model = "m"
""",
    )
    with pytest.raises(ValueError, match="unknown provider"):
        resolve_model("orphan", registry_path=reg)


# --- fallback cycle guard ----------------------------------------------------


def test_mutual_fallback_cycle_does_not_loop(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.anthropic]
base_url = "https://api.anthropic.com"
api_key_env = "ANTHROPIC_API_KEY"

[roles.a]
provider = "anthropic"
model = "m-a"
fallbacks = ["b"]

[roles.b]
provider = "anthropic"
model = "m-b"
fallbacks = ["a"]
""",
    )
    spec = resolve_model("a", registry_path=reg)
    assert [fb.role for fb in spec.fallbacks] == ["b"]
    assert spec.fallbacks[0].fallbacks == ()  # b->a skipped (a already on this branch)


def test_self_fallback_does_not_loop(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.anthropic]
base_url = "https://api.anthropic.com"
api_key_env = "ANTHROPIC_API_KEY"

[roles.c]
provider = "anthropic"
model = "m-c"
fallbacks = ["c"]
""",
    )
    spec = resolve_model("c", registry_path=reg)
    assert spec.fallbacks == ()


# --- secret guard (PRD §5.4) -------------------------------------------------


def test_inline_api_key_is_rejected(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.bad]
base_url = "https://api.example.com"
api_key = "sk-ant-pasted-secret-1234"

[roles.x]
provider = "bad"
model = "m"
""",
    )
    with pytest.raises(ValueError, match="api_key_env"):
        resolve_model("x", registry_path=reg)


def test_secret_shaped_value_is_rejected(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.bad]
base_url = "https://api.example.com"
api_key_env = "sk-ant-leaked-into-the-name-9999"

[roles.x]
provider = "bad"
model = "m"
""",
    )
    with pytest.raises(ValueError, match="secret"):
        resolve_model("x", registry_path=reg)


def test_non_env_name_api_key_env_is_rejected(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.bad]
base_url = "https://api.example.com"
api_key_env = "lower-case-not-a-name"

[roles.x]
provider = "bad"
model = "m"
""",
    )
    with pytest.raises(ValueError, match="plausible env var name"):
        resolve_model("x", registry_path=reg)


def test_missing_api_key_env_is_rejected(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.bad]
base_url = "https://api.example.com"

[roles.x]
provider = "bad"
model = "m"
""",
    )
    with pytest.raises(ValueError, match="api_key_env"):
        resolve_model("x", registry_path=reg)


# --- api_key() resolves from env, never stored -------------------------------


def test_api_key_reads_named_env(monkeypatch):
    spec = resolve_model("brain_plan", registry_path=REAL_REGISTRY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-value")
    assert spec.api_key() == "sk-test-value"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert spec.api_key() is None


def test_spec_is_frozen():
    spec = resolve_model("brain_plan", registry_path=REAL_REGISTRY)
    with pytest.raises(Exception):  # FrozenInstanceError (a dataclasses subclass)
        spec.model = "mutated"  # type: ignore[misc]
    assert isinstance(spec, ModelSpec)


# --- CLI ---------------------------------------------------------------------


def test_cli_prints_provider_model(capsys, monkeypatch):
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    rc = models.main(["brain_plan", "--registry", str(REAL_REGISTRY)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "anthropic/claude-sonnet-4-6"


def test_cli_json_round_trips(capsys):
    rc = models.main(["brain_plan", "--json", "--registry", str(REAL_REGISTRY)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["role"] == "brain_plan"
    assert payload["fallbacks"][0]["role"] == "brain_cheap"


def test_cli_unknown_role_exits_2(capsys):
    rc = models.main(["nope", "--registry", str(REAL_REGISTRY)])
    assert rc == 2
    assert "unknown model role" in capsys.readouterr().err


def test_cli_unsafe_role_exits_2(capsys):
    rc = models.main(["../evil", "--registry", str(REAL_REGISTRY)])
    assert rc == 2
