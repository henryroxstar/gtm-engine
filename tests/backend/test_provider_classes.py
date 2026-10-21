"""MM5: which providers may be pooled on the house account, and which never may.

Two classes. A ``data`` provider (RocketReach, Apollo, the platform's own workers) is a subscription the
operator can share out under a quota, behind its OWN switch. An ``identity`` provider (Saleshandy, Syften,
the Google OAuth) is the tenant's own account or nothing: a shared sending identity shares a reputation, and
metering cannot undo that. An unclassified provider is ``identity`` — absence refuses.

These are pure checks (no database): the registry, the switch parser, ``_workspace_scoped_config`` and
``tenant_run_config`` with the pool stubbed. The wiring into BOTH run starters is pinned by an AST check so a
third caller can not quietly skip the classes.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
from pathlib import Path

import pytest

pytest.importorskip("asyncpg")

import backend.session as backend_session  # noqa: E402
from agent import mcp_config  # noqa: E402
from agent.config import Config  # noqa: E402
from backend.services import provider_pool  # noqa: E402
from gtm_core import provider_registry as reg  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
WS = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
HOUSE = "platform-key-not-a-secret"  # nosec B105 -- throwaway test value

_EVERY_CREDENTIAL = {
    "news_db_dsn": "postgres://ro@db/news",
    "deepseek_api_key": HOUSE,
    "anthropic_api_key": HOUSE,
    "google_oauth": {"client_id": "a", "client_secret": "b", "refresh_token": "c"},
    "gemini_api_key": HOUSE,
    "higgsfield_api_key": HOUSE,
    "higgsfield_api_secret": HOUSE,
    "deck_renderer_url": "http://deck:3000",
    "firecrawl_api_key": HOUSE,
    "rocketreach_api_key": HOUSE,
    "apollo_api_key": HOUSE,
    "saleshandy_api_key": HOUSE,
    "syften_api_key": HOUSE,
    "elevenlabs_api_key": HOUSE,
}

#: The classification PRD §MM5 and plan Task 11 decided. Pinned as data so a change is a visible diff.
EXPECTED = {
    "rocketreach": "data",
    "apollo": "data",
    "firecrawl": "data",
    "worker": "data",
    "vision": "data",
    "judge": "data",
    "gemini_image": "data",
    "higgsfield_video": "data",
    "tts": "data",
    "deck": "data",
    "news": "data",
    "saleshandy": "identity",
    "syften": "identity",
    "google": "identity",
}


@pytest.fixture
def base_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    return dataclasses.replace(Config.from_env(repo_root=tmp_path), **_EVERY_CREDENTIAL)


@pytest.fixture(autouse=True)
def _no_ambient_switches(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith(reg.POOL_SWITCH_PREFIX):
            monkeypatch.delenv(name)


# ── 1. every server has a class ──────────────────────────────────────────────────────────────────


def _assigned_server_names() -> set[str]:
    """Every ``servers["<name>"] = …`` in ``build_mcp_servers`` — read from the source, so a server added
    behind a gate this test forgot to open still shows up."""
    tree = ast.parse((REPO / "agent" / "mcp_config.py").read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "servers"
                and isinstance(target.slice, ast.Constant)
            ):
                names.add(target.slice.value)
    return names


def test_every_server_the_brain_can_be_given_has_a_declared_class(base_cfg):
    emitted = set(mcp_config.build_mcp_servers(base_cfg, "acme"))
    assert emitted == _assigned_server_names(), "a server is gated on a field this test did not set"
    assert {name: reg.server_class(name) for name in emitted} == EXPECTED


def test_the_classification_has_no_stale_entry(base_cfg):
    """A class for a server that no longer exists is a promise nobody keeps."""
    assert set(EXPECTED) == set(mcp_config.build_mcp_servers(base_cfg, "acme"))
    assert set(reg.UNPRICED_SERVER_CLASSES) | set(reg.load_provider_costs()) == set(EXPECTED)


def test_a_name_has_one_home_for_its_class_never_two():
    assert not set(reg.UNPRICED_SERVER_CLASSES) & set(reg.load_provider_costs())


def test_a_provider_nobody_classified_is_identity():
    assert reg.server_class("brand_new_vendor") == "identity"
    assert reg.DEFAULT_CLASS == "identity"


def test_the_closed_table_only_names_known_classes():
    assert set(reg.UNPRICED_SERVER_CLASSES.values()) <= set(reg.CLASSES)


_IDENTITY_WITH_A_SHARE = (
    "[provider.sender]\n"
    'class = "identity"\nunit = "send"\nusd_per_unit = 0.1\nstatus = "estimate"\nsource = "x"\n'
    "[provider.sender.default_share]\npro = 10\n"
)


def test_an_identity_provider_cannot_carry_a_pooled_share(tmp_path):
    path = tmp_path / "provider_costs.toml"
    path.write_text(_IDENTITY_WITH_A_SHARE, encoding="utf-8")
    with pytest.raises(reg.ProviderRegistryError, match="identity"):
        reg.load_provider_costs(path)


def test_an_unclassified_priced_provider_cannot_carry_a_pooled_share_either(tmp_path):
    """No ``class`` key means identity (the loader's default) — and identity may not be pooled."""
    path = tmp_path / "provider_costs.toml"
    path.write_text(_IDENTITY_WITH_A_SHARE.replace('class = "identity"\n', ""), encoding="utf-8")
    with pytest.raises(reg.ProviderRegistryError, match="identity"):
        reg.load_provider_costs(path)


# ── 2. pooling needs its own switch, default off ─────────────────────────────────────────────────


def test_nothing_is_pooled_by_default():
    assert reg.pooled_providers({}) == frozenset()
    assert reg.pooled_providers() == frozenset()  # the process environment, switches cleared


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on"])
def test_a_data_provider_is_pooled_only_when_its_own_switch_is_on(raw):
    assert reg.pooled_providers({"GTM_POOL_ROCKETREACH": raw}) == {"rocketreach"}
    assert reg.pooled_providers({"GTM_POOL_APOLLO": raw}) == {"apollo"}


@pytest.mark.parametrize("raw", ["", "0", "false", "off", "no", "maybe", "2", "enabled"])
def test_anything_but_a_clear_yes_leaves_a_provider_unpooled(raw):
    assert reg.pooled_providers({"GTM_POOL_ROCKETREACH": raw}) == frozenset()


def test_the_switches_are_independent():
    assert reg.pooled_providers({"GTM_POOL_APOLLO": "true", "GTM_POOL_ROCKETREACH": "0"}) == {
        "apollo"
    }


@pytest.mark.parametrize("name", ["SALESHANDY", "SYFTEN", "GOOGLE"])
def test_pooling_an_identity_provider_is_refused_at_load(name):
    with pytest.raises(reg.PoolingRefused, match="identity"):
        reg.pooled_providers({f"GTM_POOL_{name}": "true"})


@pytest.mark.parametrize("name", ["FIRECRAWL", "DEEPSEEK", "NOT_A_PROVIDER"])
def test_a_switch_for_something_with_no_pool_to_share_is_refused_too(name):
    """``data`` permits pooling; it does not create a pool. Firecrawl is already one house key for every
    run (F13) and has no quota to divide, and an unknown name is a typo that must not pass silently."""
    with pytest.raises(reg.PoolingRefused):
        reg.pooled_providers({f"GTM_POOL_{name}": "true"})


def test_a_switched_off_identity_provider_is_harmless():
    assert reg.pooled_providers({"GTM_POOL_SALESHANDY": "false"}) == frozenset()


def test_one_refused_switch_refuses_the_lot():
    with pytest.raises(reg.PoolingRefused):
        reg.pooled_providers({"GTM_POOL_APOLLO": "true", "GTM_POOL_SALESHANDY": "true"})


# ── 3. what a tenant config inherits ─────────────────────────────────────────────────────────────


def _scoped(base_cfg, tmp_path, *, credentials=None, pooled=frozenset()):
    return backend_session._workspace_scoped_config(
        base_cfg, WS, tmp_path, credentials=credentials, pooled=pooled
    )


def test_by_default_a_tenant_inherits_no_provider_key(base_cfg, tmp_path):
    cfg = _scoped(base_cfg, tmp_path, credentials={})
    assert cfg.rocketreach_api_key is None and cfg.rocketreach_key_source is None
    assert cfg.apollo_api_key is None and cfg.apollo_key_source is None
    assert cfg.saleshandy_api_key is None and cfg.syften_api_key is None


def test_a_pooled_data_provider_gives_a_keyless_tenant_the_house_key_marked_house(
    base_cfg, tmp_path
):
    cfg = _scoped(base_cfg, tmp_path, credentials={}, pooled=frozenset({"rocketreach"}))
    assert (cfg.rocketreach_api_key, cfg.rocketreach_key_source) == (HOUSE, "house")
    assert (cfg.apollo_api_key, cfg.apollo_key_source) == (None, None)  # its switch is off


def test_a_tenants_own_key_always_wins_over_the_pool(base_cfg, tmp_path):
    cfg = _scoped(
        base_cfg,
        tmp_path,
        credentials={"rocketreach": "their-own"},
        pooled=frozenset({"rocketreach"}),
    )
    assert (cfg.rocketreach_api_key, cfg.rocketreach_key_source) == ("their-own", "byok")


def test_a_pooled_provider_with_no_house_key_gives_nothing(base_cfg, tmp_path):
    bare = dataclasses.replace(base_cfg, apollo_api_key=None)
    cfg = _scoped(bare, tmp_path, credentials={}, pooled=frozenset({"apollo"}))
    assert (cfg.apollo_api_key, cfg.apollo_key_source) == (None, None)


@pytest.mark.parametrize("provider", ["saleshandy", "syften", "google", "firecrawl", "mystery"])
def test_pooling_anything_but_a_pooled_data_provider_is_refused_here_too(
    base_cfg, tmp_path, provider
):
    """The second lock on the same door: even a caller that skips the switch parser cannot pool an
    identity provider through the config builder."""
    with pytest.raises(ValueError, match="pool"):
        _scoped(base_cfg, tmp_path, credentials={}, pooled=frozenset({provider}))


def test_a_tenants_own_saleshandy_and_syften_keys_still_work(base_cfg, tmp_path):
    cfg = _scoped(base_cfg, tmp_path, credentials={"saleshandy": "mine", "syften": "mine-too"})
    assert (cfg.saleshandy_api_key, cfg.syften_api_key) == ("mine", "mine-too")


def test_the_platforms_google_oauth_never_rides_into_a_tenant_config(base_cfg, tmp_path):
    """``google`` is identity-class: a tenant gets its own or nothing, never the operator's mailbox."""
    assert base_cfg.google_oauth  # the control: the platform config HAS one
    assert _scoped(base_cfg, tmp_path, credentials={}).google_oauth == {}
    servers = mcp_config.build_mcp_servers(_scoped(base_cfg, tmp_path, credentials={}), "acme")
    assert "google" not in servers


def test_the_pooled_table_names_only_data_providers():
    backend_session.assert_pooled_providers_are_data(backend_session._POOLED_PROVIDERS)
    assert set(backend_session._POOLED_PROVIDERS) <= {
        name for name, cls in EXPECTED.items() if cls == "data"
    }


def test_a_pooled_table_naming_an_identity_provider_fails_at_load():
    bad = {**backend_session._POOLED_PROVIDERS, "saleshandy": ("x_key_source", "x_cap")}
    with pytest.raises(ValueError, match="saleshandy"):
        backend_session.assert_pooled_providers_are_data(bad)


# ── 4. tenant_run_config applies the switches ────────────────────────────────────────────────────


def test_tenant_run_config_pools_only_what_the_operator_switched_on(
    base_cfg, tmp_path, monkeypatch
):
    asked: list[tuple] = []

    async def shares(pool, workspace_id, providers):
        asked.append((workspace_id, tuple(providers)))
        return dict.fromkeys(providers, 5)

    monkeypatch.setattr(provider_pool, "resolve_provider_shares", shares)
    monkeypatch.setenv("GTM_POOL_APOLLO", "true")
    cfg = asyncio.run(
        backend_session.tenant_run_config(object(), base_cfg, WS, tmp_path, credentials={})
    )
    assert (cfg.apollo_api_key, cfg.apollo_key_source, cfg.apollo_monthly_cap) == (
        HOUSE,
        "house",
        5,
    )
    assert (cfg.rocketreach_api_key, cfg.rocketreach_key_source) == (None, None)
    assert asked == [(WS, ("apollo",))]


def test_tenant_run_config_with_every_switch_off_never_touches_the_pool(
    base_cfg, tmp_path, monkeypatch
):
    async def never(*_a, **_k):
        raise AssertionError("no switch is on, so there is no pool to ask")

    monkeypatch.setattr(provider_pool, "resolve_provider_shares", never)
    cfg = asyncio.run(
        backend_session.tenant_run_config(object(), base_cfg, WS, tmp_path, credentials={})
    )
    assert cfg.rocketreach_api_key is None and cfg.apollo_api_key is None


def test_tenant_run_config_without_a_database_cannot_pool(base_cfg, tmp_path, monkeypatch):
    """A house key with no pool to meter its share against is exactly the unbounded spend MM4 forbids."""
    monkeypatch.setenv("GTM_POOL_ROCKETREACH", "true")
    cfg = asyncio.run(
        backend_session.tenant_run_config(None, base_cfg, WS, tmp_path, credentials={})
    )
    assert cfg.rocketreach_api_key is None and cfg.rocketreach_key_source is None


def test_tenant_run_config_refuses_a_switch_that_pools_an_identity_provider(
    base_cfg, tmp_path, monkeypatch
):
    monkeypatch.setenv("GTM_POOL_SALESHANDY", "true")
    with pytest.raises(reg.PoolingRefused):
        asyncio.run(
            backend_session.tenant_run_config(object(), base_cfg, WS, tmp_path, credentials={})
        )


def test_a_pooled_worker_is_handed_the_house_marker_and_its_share(base_cfg, tmp_path, monkeypatch):
    async def shares(pool, workspace_id, providers):
        return dict.fromkeys(providers, 40)

    monkeypatch.setattr(provider_pool, "resolve_provider_shares", shares)
    monkeypatch.setenv("GTM_POOL_ROCKETREACH", "true")
    cfg = asyncio.run(
        backend_session.tenant_run_config(object(), base_cfg, WS, tmp_path, credentials={})
    )
    env = mcp_config.build_mcp_servers(cfg, "acme")["rocketreach"]["env"]
    assert env["ROCKETREACH_KEY_SOURCE"] == "house"
    assert env["ROCKETREACH_MONTHLY_LOOKUP_CAP"] == "40"
    assert env["ROCKETREACH_API_KEY"] == HOUSE
    assert WS in env["GTM_CONTENT_ROOT"]


# ── 5. both run starters go through it ───────────────────────────────────────────────────────────


def _calls_in(path: str, function: str | None) -> set[str]:
    tree = ast.parse((REPO / path).read_text())
    scope: ast.AST = tree
    if function:
        scope = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function
        )
    names: set[str] = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return names


@pytest.mark.parametrize(
    ("path", "function"),
    [
        ("backend/services/runs/pack_executor.py", "_execute_pack_run"),
        ("backend/session.py", "connect"),
    ],
    ids=["pack_run", "warm_session"],
)
def test_a_run_that_spawns_workers_builds_its_config_through_tenant_run_config(path, function):
    """The two places a tenant's workers are launched. Calling ``_workspace_scoped_config`` directly there
    would skip the classes, the switches and the share — the pooled key would be handed over unmetered."""
    calls = _calls_in(path, function)
    assert "tenant_run_config" in calls, f"{path}:{function} does not use tenant_run_config"
    assert "_workspace_scoped_config" not in calls, f"{path}:{function} bypasses the classes"


# ── 6. the switches are settable without a code change, and default to nothing ───────────────────


def _compose_env(service: str) -> dict:
    import yaml

    compose = yaml.safe_load((REPO / "deploy" / "docker-compose.yml").read_text())
    env = compose["services"][service]["environment"]
    return env if isinstance(env, dict) else dict(item.split("=", 1) for item in env)


@pytest.mark.private_tree  # reads deploy/, which the public carve does not ship
@pytest.mark.parametrize("service", ["api", "worker"])
def test_every_pooling_switch_is_passed_through_with_an_empty_default(service):
    """The run is started by either service, so both read the switches. Empty default = off: an
    operator who sets nothing pools nothing."""
    env = _compose_env(service)
    for provider in backend_session._POOLED_PROVIDERS:
        name = f"{reg.POOL_SWITCH_PREFIX}{provider.upper()}"
        assert env.get(name) == f"${{{name}:-}}", f"{service}: {name}"
        assert env.get(f"{provider.upper()}_API_KEY") == f"${{{provider.upper()}_API_KEY:-}}", (
            f"{service}: the house key a pooled tenant would inherit"
        )
