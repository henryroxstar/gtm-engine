"""MM1 — a tenant's worker is told whose vendor account its key is on.

``agent/mcp/{rocketreach,apollo}`` waive the unit price when ``<PROVIDER>_KEY_SOURCE == "byok"``
(the tenant's own vendor account: billing the wallet too would charge them twice) and price every
other call from ``gtm_core/provider_costs.toml``. The waiver is only safe if the BACKEND, not the
worker, decides it: a tenant worker launched with the variable UNSET would be priced like the
house key, and one launched with a stale ``house`` would bill a tenant for lookups on their own key.

Nothing here is a DB test: ``_workspace_scoped_config`` and ``build_mcp_servers`` are pure.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
from pathlib import Path

import pytest

from agent.config import Config
from agent.mcp_config import build_mcp_servers
from backend.session import _workspace_scoped_config

REPO_ROOT = Path(__file__).resolve().parents[2]

#: provider -> the key-source env var its worker reads (the worker server shares the provider name)
PROVIDERS = {
    "rocketreach": "ROCKETREACH_KEY_SOURCE",
    "apollo": "APOLLO_KEY_SOURCE",
}
HOUSE_KEYS = {"rocketreach": "house-rr-key", "apollo": "house-apollo-key"}
TENANT_KEYS = {"rocketreach": "tenant-rr-key", "apollo": "tenant-apollo-key"}


@pytest.fixture
def base(tmp_path, monkeypatch) -> Config:
    """A platform (single-tenant VPS) config that DOES carry house keys and an explicit
    ``house`` source — the worst thing a tenant config could inherit."""
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path))
    return dataclasses.replace(
        Config.from_env(repo_root=tmp_path),
        rocketreach_api_key=HOUSE_KEYS["rocketreach"],
        apollo_api_key=HOUSE_KEYS["apollo"],
        rocketreach_key_source="house",
        apollo_key_source="house",
    )


def _servers(cfg: Config) -> dict:
    return build_mcp_servers(cfg, "example")


def test_a_tenant_key_launches_a_byok_worker(base, tmp_path):
    cfg = _workspace_scoped_config(base, "ws-aaaa", tmp_path, credentials=dict(TENANT_KEYS))
    servers = _servers(cfg)
    for provider, source_env in PROVIDERS.items():
        assert provider in servers, "positive control: a tenant key must still launch its worker"
        env = servers[provider]["env"]
        assert env[source_env] == "byok"
        assert env[f"{provider.upper()}_API_KEY"] == TENANT_KEYS[provider]


def test_no_tenant_key_means_no_worker_and_no_inherited_source(base, tmp_path):
    """The BYOK fail-closed rule is unchanged: a keyless tenant loses the tool, and the platform's
    ``house`` marker must not survive the copy (a key arriving later must not inherit it)."""
    cfg = _workspace_scoped_config(base, "ws-aaaa", tmp_path)
    servers = _servers(cfg)
    for provider in PROVIDERS:
        assert provider not in servers
        assert getattr(cfg, f"{provider}_api_key") is None
        assert getattr(cfg, f"{provider}_key_source") is None


def _cred_sets() -> list[dict[str, str]]:
    """Every combination of absent / empty / present tenant credential per provider."""
    sets = []
    for rr, ap in itertools.product((None, "", "tenant-any-key"), repeat=2):
        pair = (("rocketreach", rr), ("apollo", ap))
        sets.append({k: v for k, v in pair if v is not None})
    return sets


@pytest.mark.parametrize("creds", _cred_sets(), ids=lambda c: repr(sorted(c.items())))
def test_a_tenant_config_never_launches_a_key_bearing_worker_without_a_key_source(
    base, tmp_path, creds
):
    """(g) The rule: whatever a workspace holds, every rocketreach/apollo worker the backend can
    launch carries ``<PROVIDER>_KEY_SOURCE=byok`` — never unset, never the platform's ``house``."""
    cfg = _workspace_scoped_config(base, "ws-aaaa", tmp_path, credentials=creds)
    servers = _servers(cfg)
    launched = 0
    for provider, source_env in PROVIDERS.items():
        if provider not in servers:
            continue
        launched += 1
        env = servers[provider]["env"]
        assert env.get(source_env) == "byok", f"{provider} launched without a byok key source"
        assert env[f"{provider.upper()}_API_KEY"] != HOUSE_KEYS[provider]
    assert launched == sum(1 for v in creds.values() if v), "a real key must launch its worker"


def test_the_providers_with_a_key_source_are_exactly_the_ones_the_registry_prices():
    """Adding a key-bearing provider to the registry (or a ``*_key_source`` field to Config)
    without the other, or without a decision here, must not pass silently."""
    from gtm_core import provider_registry

    fields = {
        f.name.removesuffix("_key_source")
        for f in dataclasses.fields(Config)
        if f.name.endswith("_key_source")
    }
    assert fields == set(PROVIDERS) == set(provider_registry.load_provider_costs())


def test_only_the_scoped_config_assigns_a_provider_key_in_backend():
    """A second path that put a key on a tenant Config would bypass the byok rule above. The only
    backend code that may NAME these fields is ``_workspace_scoped_config`` (and it names the
    source with them); anything else reaching them is a new tenant-key path to review."""
    watched = {f"{p}_api_key" for p in PROVIDERS} | {f"{p}_key_source" for p in PROVIDERS}
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "backend").rglob("*.py")):
        if path.name == "session.py" and path.parent.name == "backend":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = (
                node.attr
                if isinstance(node, ast.Attribute)
                else node.arg
                if isinstance(node, ast.keyword)
                else None
            )
            if name in watched:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} {name}")
    assert offenders == []
