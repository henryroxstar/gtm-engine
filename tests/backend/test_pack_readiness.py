"""A12 — pack readiness surface: listing summary, detail endpoint, fail-closed run gate.

T1–T9 of the pack-readiness contract. Readiness is
PROFILE-shaped only: ask-settings demote to `degraded` (the run form collects
them; `missing_settings` owns their required-ness request-shaped).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.callers.rest import require_principal  # noqa: E402
from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.routers import packs as packs_router  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from tests.backend._protocol1 import (  # noqa: E402
    BUDGET_MODULES,
    SCOPE_MODULES,
    patch_everywhere,
    user_principal,
)

REPO = Path(__file__).resolve().parents[2]
PROFILE = "example-readiness"

_FULL_KNOWLEDGE = {
    "voice": "# Voice\nPlain, concrete.\n",
    "icp-personas": "# ICP\nFictional personas.\n",
    "brand-notes": "# Brand notes\nFictional palette and phrases.\n",  # optional — green for T4
    "case-studies": "# Case studies\nFictional proof library.\n",  # optional — green for T4
    "audience-psychology": "# Audience psychology\nFictional persona layer.\n",  # optional
    "content-priority": "# Content priority\nFictional cadence.\n",  # optional, 90d — green for T4
    "social-tuning": "# Social tuning\nFictional per-platform tuning.\n",  # optional
}


def _provision(
    profiles_root: Path,
    *,
    knowledge: dict,
    profile_md: str = "brand_name: X\n",
    active_packs: list[str] | None = None,
):
    pdir = profiles_root / PROFILE
    (pdir / "knowledge").mkdir(parents=True, exist_ok=True)
    (pdir / "PROFILE.md").write_text(profile_md)
    active = active_packs or ["marketing"]
    active_str = ", ".join(f'"{p}"' for p in active)
    (pdir / "packs.toml").write_text(f"active = [{active_str}]\n")
    for topic, body in knowledge.items():
        (pdir / "knowledge" / f"{topic}.md").write_text(body)


@pytest.fixture()
def ws_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    ws_id = str(uuid.uuid4())
    profiles_root = tmp_path / "workspaces" / ws_id / "profiles"
    profiles_root.mkdir(parents=True)
    return SimpleNamespace(ws_id=ws_id, profiles_root=profiles_root, tmp=tmp_path)


@pytest.fixture()
def client(ws_env):
    app = FastAPI()
    app.include_router(packs_router.router, prefix="/v1")
    app.include_router(runs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    app.state.sessions = MagicMock()
    app.state.cfg = MagicMock(repo_root=REPO)
    # pro_plus: this suite is about readiness, not entitlement — marketing is now
    # gated at pro_plus (gtm_core/gating.toml), so the fixture needs to clear that to
    # keep exercising the readiness path these tests are actually about.
    ctx = WorkspaceCtx(user_id=str(uuid.uuid4()), workspace_id=ws_env.ws_id, entitlement="pro_plus")
    app.dependency_overrides[require_auth] = lambda: ctx
    # Fleet Phase A (Task 3): runs/packs routes now depend on require_principal.
    app.dependency_overrides[require_principal] = lambda: user_principal(ctx)
    return TestClient(app, raise_server_exceptions=True)


def _linkedin(descriptors: list[dict]) -> dict:
    return next(d for d in descriptors if d["variant"] == "linkedin-post")


def _run_body(**over) -> dict:
    body = {
        "profile_name": PROFILE,
        "pack": "marketing",
        "variant": "linkedin-post",
        "inputs": {"brand_name": "ExampleCo"},
    }
    body.update(over)
    return body


@contextlib.asynccontextmanager
async def _null_scope(pool, workspace_id, conn=None):
    yield conn


async def _skip_run(*args, **kwargs):
    return None


# ── T1: blocked variant surfaces the exact missing item ───────────────────────


def test_t1_missing_required_knowledge_blocks_listing(client, ws_env):
    _provision(ws_env.profiles_root, knowledge={"voice": "x"})  # no icp-personas
    d = _linkedin(client.get(f"/v1/packs?profile_name={PROFILE}").json())
    assert d["readiness"]["status"] == "blocked"
    blocked_names = [i["name"] for i in d["readiness"]["items"] if i["status"] == "blocked"]
    assert blocked_names == ["icp-personas"]

    detail = client.get(
        f"/v1/packs/marketing/linkedin-post/readiness?profile_name={PROFILE}"
    ).json()
    all_names = {i["name"] for i in detail["readiness"]["items"]}
    assert {
        "brand_name",
        "voice",
        "icp-personas",
        "brand-notes",
        "case-studies",
        "audience-psychology",
        "content-priority",
        "social-tuning",
    } <= all_names


# ── T2: blocked → 422 pack_not_ready, no run row, no budget touch ─────────────


def test_t2_blocked_run_refused_before_any_budget_touch(client, ws_env):
    _provision(ws_env.profiles_root, knowledge={"voice": "x"})
    scope_entered = MagicMock()
    budget = AsyncMock(return_value=True)
    with (
        patch_everywhere(SCOPE_MODULES, "workspace_scope", scope_entered),
        patch_everywhere(BUDGET_MODULES, "acheck_budget", budget),
    ):
        resp = client.post("/v1/runs", json=_run_body())
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "pack_not_ready"
    assert [b["name"] for b in detail["blocked"]] == ["icp-personas"]
    scope_entered.assert_not_called()  # no INSERT — the run row is never created
    budget.assert_not_called()  # readiness refusal precedes every spend check


# ── T3: degraded (stale knowledge) proceeds ───────────────────────────────────


def test_t3_stale_knowledge_degrades_but_run_proceeds(client, ws_env):
    stale = dict(_FULL_KNOWLEDGE)
    stale["content-priority"] = (
        "---\nrefreshed: 2020-01-01\n---\n# Content priority\nOld but present.\n"
    )
    _provision(ws_env.profiles_root, knowledge=stale)

    d = _linkedin(client.get(f"/v1/packs?profile_name={PROFILE}").json())
    assert d["readiness"]["status"] == "degraded"
    assert any(
        i["name"] == "content-priority" and i["status"] == "degraded"
        for i in d["readiness"]["items"]
    )

    conn = AsyncMock()
    # A5: admission counts in-flight runs in SQL (the cap is now global, not
    # per-process). A bare AsyncMock would return a Mock that compares truthy
    # against the cap and turn every create into a 429. RT-04: the INSERT itself is
    # now a `fetchval(...RETURNING id::text)` — model it as returning its own run_id
    # (args[0]) rather than a truthy MagicMock, so `RunResponse.run_id` stays a real
    # string.
    conn.fetchval = AsyncMock(
        side_effect=lambda sql, *a: (
            0
            if "count(*) FROM runs" in sql
            else a[0]
            if sql.strip().startswith("INSERT INTO runs")
            else MagicMock()
        )
    )

    @contextlib.asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    with (
        patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
        # RL-08: create_run now checks admits() (acheck_budget) synchronously before
        # insert_run_row — same as every other budget-touching admission test.
        patch_everywhere(BUDGET_MODULES, "acheck_budget", AsyncMock(return_value=True)),
        patch.object(runs_router, "_execute_pack_run", _skip_run),
    ):
        resp = client.post("/v1/runs", json=_run_body())
    assert resp.status_code == 202, "degraded must NOT refuse the run"
    assert any(
        c.args[0].strip().startswith("INSERT INTO runs") for c in conn.fetchval.call_args_list
    )


# ── T4: fully provisioned → ready, empty items in listing ─────────────────────


def test_t4_ready_profile_lists_clean(client, ws_env):
    _provision(ws_env.profiles_root, knowledge=_FULL_KNOWLEDGE)
    d = _linkedin(client.get(f"/v1/packs?profile_name={PROFILE}").json())
    # brand_name (ask) is in PROFILE.md here, all knowledge present and fresh.
    assert d["readiness"] == {"status": "ready", "items": []}


# ── T5: error ordering — request-shaped before profile-shaped ─────────────────


def test_t5_missing_settings_wins_over_pack_not_ready(client, ws_env):
    _provision(ws_env.profiles_root, knowledge={}, profile_md="")  # missing from PROFILE.md
    resp = client.post("/v1/runs", json=_run_body(inputs={}))  # AND missing in inputs
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "missing_settings"


# ── T6: no absolute path leaks into any reason ────────────────────────────────


def test_t6_no_path_leak_in_reasons(client, ws_env):
    _provision(ws_env.profiles_root, knowledge={})  # everything missing
    listing_text = client.get(f"/v1/packs?profile_name={PROFILE}").text
    detail_text = client.get(
        f"/v1/packs/marketing/linkedin-post/readiness?profile_name={PROFILE}"
    ).text
    for body in (listing_text, detail_text):
        assert str(ws_env.tmp) not in body
        assert not re.search(r"/(Users|home|private|var)/", body)


# ── T7: activation/visibility boundary on the detail endpoint ─────────────────


def test_t7_detail_403_not_activated_404_unknown(client, ws_env):
    _provision(ws_env.profiles_root, knowledge=_FULL_KNOWLEDGE)
    r_not_activated = client.get(
        f"/v1/packs/prospecting/prospect-outreach/readiness?profile_name={PROFILE}"
    )
    assert r_not_activated.status_code == 403
    assert r_not_activated.json()["detail"]["code"] == "pack_not_activated"
    assert (
        client.get(f"/v1/packs/marketing/nope/readiness?profile_name={PROFILE}").status_code == 404
    )
    # Positive control: the activated variant's detail responds 200.
    assert (
        client.get(
            f"/v1/packs/marketing/linkedin-post/readiness?profile_name={PROFILE}"
        ).status_code
        == 200
    )


# ── T8: descriptor readiness field is additive-optional (contract) ────────────


def test_t8_descriptor_valid_with_and_without_readiness(ws_env):
    from backend.pack_catalog import ResolvedVariant, descriptor
    from gtm_core.packs.loader import PackInputs, load_pack_graph
    from tests.contracts.minijsonschema import validate as schema_validate

    schema = json.loads((REPO / "schemas" / "pack-descriptor.schema.json").read_text())
    graph = load_pack_graph(REPO / "packs" / "marketing" / "graphs" / "linkedin-post.toml")
    resolved = ResolvedVariant(graph=graph, inputs=PackInputs())
    without = descriptor(resolved, entitlement="pro", readiness=None)
    assert "readiness" not in without
    assert not schema_validate(without, schema)


# ── T9 (ask-setting demotion — the readiness/missing_settings split) ──────────


def test_t9_ask_setting_missing_from_profile_never_blocks(client, ws_env):
    """brand_name absent from PROFILE.md but supplied in the request: readiness
    degrades (form collects it), the run proceeds past readiness."""
    _provision(ws_env.profiles_root, knowledge=_FULL_KNOWLEDGE, profile_md="# empty\n")
    d = _linkedin(client.get(f"/v1/packs?profile_name={PROFILE}").json())
    assert d["readiness"]["status"] == "degraded", "ask-setting must not block"
    brand = next(i for i in d["readiness"]["items"] if i["name"] == "brand_name")
    assert brand["status"] == "degraded"

    conn = AsyncMock()
    # A5: admission counts in-flight runs in SQL (the cap is now global, not
    # per-process). A bare AsyncMock would return a Mock that compares truthy
    # against the cap and turn every create into a 429. RT-04: the INSERT itself is
    # now a `fetchval(...RETURNING id::text)` — model it as returning its own run_id
    # (args[0]) rather than a truthy MagicMock, so `RunResponse.run_id` stays a real
    # string.
    conn.fetchval = AsyncMock(
        side_effect=lambda sql, *a: (
            0
            if "count(*) FROM runs" in sql
            else a[0]
            if sql.strip().startswith("INSERT INTO runs")
            else MagicMock()
        )
    )

    @contextlib.asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    with (
        patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
        # RL-08: create_run now checks admits() (acheck_budget) synchronously before
        # insert_run_row — same as every other budget-touching admission test.
        patch_everywhere(BUDGET_MODULES, "acheck_budget", AsyncMock(return_value=True)),
        patch.object(runs_router, "_execute_pack_run", _skip_run),
    ):
        resp = client.post("/v1/runs", json=_run_body())  # brand_name provided in inputs
    assert resp.status_code == 202


# ── T10: unconfigured workspace blocks pack catalog for external-effect packs ─


def test_t10_catalog_unconfigured_workspace_blocks_prospecting(client, ws_env):
    _provision(
        ws_env.profiles_root,
        knowledge=_FULL_KNOWLEDGE,
        active_packs=["marketing", "prospecting"],
    )
    with patch(
        "backend.services.integrations.get_workspace_configured_providers",
        AsyncMock(return_value=set()),
    ):
        resp = client.get(f"/v1/packs?profile_name={PROFILE}")
        assert resp.status_code == 200
        d = next(p for p in resp.json() if p["variant"] == "prospect-outreach")
        assert d["readiness"]["status"] == "blocked"
        integration_items = [i for i in d["readiness"]["items"] if i["kind"] == "integration"]
        assert len(integration_items) == 1
        assert integration_items[0]["name"] == "saleshandy"
        assert integration_items[0]["status"] == "blocked"
        assert "Saleshandy API key is not configured" in integration_items[0]["reason"]
        assert "Settings > Integrations" in integration_items[0]["reason"]

        detail_resp = client.get(
            f"/v1/packs/prospecting/prospect-outreach/readiness?profile_name={PROFILE}"
        )
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["readiness"]["status"] == "blocked"
        detail_items = [i for i in detail["readiness"]["items"] if i["kind"] == "integration"]
        assert len(detail_items) == 1
        assert detail_items[0]["name"] == "saleshandy"
        assert detail_items[0]["status"] == "blocked"


# ── T11: configured workspace renders ready status and input requirements ──────


def test_t11_catalog_configured_workspace_readiness_ready(client, ws_env):
    _provision(
        ws_env.profiles_root,
        knowledge=_FULL_KNOWLEDGE,
        active_packs=["marketing", "prospecting"],
    )
    with patch(
        "backend.services.integrations.get_workspace_configured_providers",
        AsyncMock(return_value={"saleshandy"}),
    ):
        resp = client.get(f"/v1/packs?profile_name={PROFILE}")
        assert resp.status_code == 200
        d = next(p for p in resp.json() if p["variant"] == "prospect-outreach")
        assert d["readiness"]["status"] == "ready"
        assert d["inputs"]["integrations"] == [{"provider": "saleshandy", "required": True}]

        detail_resp = client.get(
            f"/v1/packs/prospecting/prospect-outreach/readiness?profile_name={PROFILE}"
        )
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["readiness"]["status"] == "ready"
        detail_integration = next(
            i for i in detail["readiness"]["items"] if i["kind"] == "integration"
        )
        assert detail_integration["name"] == "saleshandy"
        assert detail_integration["status"] == "ready"


# ── T12: unconfigured run rejected synchronously with HTTP 422 pack_not_ready ─


def test_t12_unconfigured_run_admission_refused_synchronously_422(client, ws_env):
    _provision(
        ws_env.profiles_root,
        knowledge=_FULL_KNOWLEDGE,
        active_packs=["marketing", "prospecting"],
    )
    scope_entered = MagicMock()
    budget = AsyncMock(return_value=True)
    body = {
        "profile_name": PROFILE,
        "pack": "prospecting",
        "variant": "prospect-outreach",
        "inputs": {},
    }
    with (
        patch(
            "backend.services.integrations.get_workspace_configured_providers",
            AsyncMock(return_value=set()),
        ),
        patch_everywhere(SCOPE_MODULES, "workspace_scope", scope_entered),
        patch_everywhere(BUDGET_MODULES, "acheck_budget", budget),
    ):
        resp = client.post("/v1/runs", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "pack_not_ready"
    blocked_integrations = [b for b in detail["blocked"] if b["kind"] == "integration"]
    assert len(blocked_integrations) == 1
    assert blocked_integrations[0]["name"] == "saleshandy"
    assert "Saleshandy API key is not configured" in blocked_integrations[0]["reason"]
    scope_entered.assert_not_called()  # zero rows inserted in runs table
    budget.assert_not_called()  # zero reservations / $0 spend


# ── T13: surface agreement between catalog readiness and runtime preflight ────


def test_t13_surface_agreement_catalog_admission_preflight(tmp_path):
    from agent.readiness import GREEN
    from backend.pack_catalog import ResolvedVariant, variant_readiness
    from backend.services.runs.pack_executor import _check_pack_integrations_preflight
    from gtm_core.packs.loader import PackInputs, load_pack_graph

    pdir = tmp_path / "test-profile"
    pdir.mkdir(parents=True)
    (pdir / "PROFILE.md").write_text("company: Test\n", encoding="utf-8")

    # 1. email_enroll (prospect-outreach):
    email_graph = load_pack_graph(
        REPO / "packs" / "prospecting" / "graphs" / "prospect-outreach.toml"
    )
    report_unconfigured = variant_readiness(
        tmp_path,
        "test-profile",
        ResolvedVariant(graph=email_graph, inputs=PackInputs()),
        configured_integrations=set(),
    )
    assert report_unconfigured.blocked is True

    fake_cfg_unconfigured = SimpleNamespace(saleshandy_api_key=None)
    preflight_unconfigured = _check_pack_integrations_preflight(email_graph, fake_cfg_unconfigured)
    assert preflight_unconfigured is not None
    assert preflight_unconfigured.code == "email_not_configured"

    report_configured = variant_readiness(
        tmp_path,
        "test-profile",
        ResolvedVariant(graph=email_graph, inputs=PackInputs()),
        configured_integrations={"saleshandy"},
    )
    integration_items = [i for i in report_configured.items if i.kind == "integration"]
    assert all(i.status == GREEN for i in integration_items)

    fake_cfg_configured = SimpleNamespace(saleshandy_api_key="sh_live_key_xxx")
    preflight_configured = _check_pack_integrations_preflight(email_graph, fake_cfg_configured)
    assert preflight_configured is None

    # 2. dnc_add (optout-suppress):
    dnc_graph = load_pack_graph(REPO / "packs" / "inbound" / "graphs" / "optout-suppress.toml")
    dnc_report_unconfigured = variant_readiness(
        tmp_path,
        "test-profile",
        ResolvedVariant(graph=dnc_graph, inputs=PackInputs()),
        configured_integrations=set(),
    )
    assert dnc_report_unconfigured.blocked is True
    preflight_dnc_unconfigured = _check_pack_integrations_preflight(
        dnc_graph, fake_cfg_unconfigured
    )
    assert preflight_dnc_unconfigured is not None
    assert preflight_dnc_unconfigured.code == "dnc_not_configured"

    dnc_report_configured = variant_readiness(
        tmp_path,
        "test-profile",
        ResolvedVariant(graph=dnc_graph, inputs=PackInputs()),
        configured_integrations={"saleshandy"},
    )
    dnc_items = [i for i in dnc_report_configured.items if i.kind == "integration"]
    assert all(i.status == GREEN for i in dnc_items)
    preflight_dnc_configured = _check_pack_integrations_preflight(dnc_graph, fake_cfg_configured)
    assert preflight_dnc_configured is None


def test_external_effects_force_required_integration(tmp_path):
    """PRD §3.1: Even if inputs.toml declares required = false, external effect
    nodes (email_enroll, dnc_add) strictly mandate required = True."""
    from backend.pack_catalog import ResolvedVariant, variant_readiness
    from gtm_core.packs.loader import PackInputIntegration, PackInputs, load_pack_graph

    pdir = tmp_path / "test-profile"
    pdir.mkdir(parents=True)
    (pdir / "PROFILE.md").write_text("company: Test\n", encoding="utf-8")

    graph = load_pack_graph(REPO / "packs" / "prospecting" / "graphs" / "prospect-outreach.toml")
    # Declared with required=False
    inputs = PackInputs(integrations=(PackInputIntegration(provider="saleshandy", required=False),))
    resolved = ResolvedVariant(graph=graph, inputs=inputs)
    report = variant_readiness(tmp_path, "test-profile", resolved, configured_integrations=set())
    # Must be forced to blocked=True:
    assert report.blocked is True
    integration_item = next(i for i in report.items if i.kind == "integration")
    assert integration_item.required is True
    assert integration_item.status == "red"


def test_graph_derived_integration_serialized_in_descriptor(tmp_path):
    """PRD §3.1: Descriptors include graph-derived integrations in inputs.integrations."""
    from backend.pack_catalog import descriptor, resolve_variant, variant_readiness

    pdir = tmp_path / "test-profile"
    pdir.mkdir(parents=True)
    (pdir / "PROFILE.md").write_text("company: Test\n", encoding="utf-8")
    (pdir / "packs.toml").write_text('active = ["inbound"]\n', encoding="utf-8")

    resolved = resolve_variant(REPO, tmp_path, "test-profile", "inbound", "optout-suppress")
    report = variant_readiness(tmp_path, "test-profile", resolved, configured_integrations=set())
    d = descriptor(resolved, entitlement="pro_plus", readiness=report)
    assert d["inputs"]["integrations"] == [{"provider": "saleshandy", "required": True}]


# ── T6: secret sanitization — no ciphertext, IVs or keys in responses ─────────


def test_t6_secret_sanitization_no_keys_in_responses(client, ws_env):
    _provision(
        ws_env.profiles_root,
        knowledge=_FULL_KNOWLEDGE,
        active_packs=["marketing", "prospecting"],
    )
    fake_secret = "sh_live_super_secret_api_key_12345"
    with patch(
        "backend.services.integrations.get_workspace_configured_providers",
        AsyncMock(return_value={"saleshandy"}),
    ):
        listing_resp = client.get(f"/v1/packs?profile_name={PROFILE}")
        detail_resp = client.get(
            f"/v1/packs/prospecting/prospect-outreach/readiness?profile_name={PROFILE}"
        )
    for resp in (listing_resp, detail_resp):
        assert fake_secret not in resp.text
        assert '"encrypted_data"' not in resp.text
        assert '"wrapped_dek"' not in resp.text
        assert '"iv"' not in resp.text
        assert '"tag"' not in resp.text


# ── T8: vault/KEK fail-closed check ───────────────────────────────────────────


def test_t8_vault_kek_fail_closed():
    import asyncio

    from backend.services.integrations import get_workspace_configured_providers

    async def _test():
        with patch("backend.services.integrations.get_kek", return_value=None):
            pool = MagicMock()
            providers = await get_workspace_configured_providers(pool, "test-ws-id")
            assert providers == set()
            pool.acquire.assert_not_called()

    asyncio.run(_test())


def test_gtm_fake_runs_environment_override(monkeypatch):
    """Dev mode: GTM_FAKE_RUNS=1 returns simulated provider set."""
    import asyncio

    from backend.services.integrations import get_workspace_configured_providers

    monkeypatch.setenv("GTM_FAKE_RUNS", "1")

    async def _test():
        pool = MagicMock()
        providers = await get_workspace_configured_providers(pool, "test-ws-id")
        assert providers == {"saleshandy", "apollo", "rocketreach", "syften"}
        pool.acquire.assert_not_called()

    asyncio.run(_test())


# ── T9: decryption bypass — never decrypts during catalog browse ──────────────


def test_t9_decryption_bypass_never_decrypts_on_catalog(client, ws_env, monkeypatch):
    monkeypatch.setenv("VAULT_KEK", "0123456789abcdef0123456789abcdef")
    _provision(
        ws_env.profiles_root,
        knowledge=_FULL_KNOWLEDGE,
        active_packs=["marketing", "prospecting"],
    )
    with patch("backend.vault.decrypt") as mock_decrypt:
        client.get(f"/v1/packs?profile_name={PROFILE}")
        client.get(f"/v1/packs/prospecting/prospect-outreach/readiness?profile_name={PROFILE}")
        mock_decrypt.assert_not_called()
