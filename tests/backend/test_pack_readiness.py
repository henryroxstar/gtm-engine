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

from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.routers import packs as packs_router  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402

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


def _provision(profiles_root: Path, *, knowledge: dict, profile_md: str = "brand_name: X\n"):
    pdir = profiles_root / PROFILE
    (pdir / "knowledge").mkdir(parents=True, exist_ok=True)
    (pdir / "PROFILE.md").write_text(profile_md)
    (pdir / "packs.toml").write_text('active = ["marketing"]\n')
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
        patch.object(runs_router, "workspace_scope", scope_entered),
        patch.object(runs_router, "acheck_budget", budget),
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

    @contextlib.asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    with (
        patch.object(runs_router, "workspace_scope", _scope),
        patch.object(runs_router, "_execute_pack_run", _skip_run),
    ):
        resp = client.post("/v1/runs", json=_run_body())
    assert resp.status_code == 202, "degraded must NOT refuse the run"
    assert any("INSERT INTO runs" in c.args[0] for c in conn.execute.call_args_list)


# ── T4: fully provisioned → ready, empty items in listing ─────────────────────


def test_t4_ready_profile_lists_clean(client, ws_env):
    _provision(ws_env.profiles_root, knowledge=_FULL_KNOWLEDGE)
    d = _linkedin(client.get(f"/v1/packs?profile_name={PROFILE}").json())
    # brand_name (ask) is in PROFILE.md here, all knowledge present and fresh.
    assert d["readiness"] == {"status": "ready", "items": []}


# ── T5: error ordering — request-shaped before profile-shaped ─────────────────


def test_t5_missing_settings_wins_over_pack_not_ready(client, ws_env):
    _provision(ws_env.profiles_root, knowledge={})  # also knowledge-blocked
    resp = client.post("/v1/runs", json=_run_body(inputs={}))  # AND missing brand_name
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

    @contextlib.asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    with (
        patch.object(runs_router, "workspace_scope", _scope),
        patch.object(runs_router, "_execute_pack_run", _skip_run),
    ):
        resp = client.post("/v1/runs", json=_run_body())  # brand_name provided in inputs
    assert resp.status_code == 202
