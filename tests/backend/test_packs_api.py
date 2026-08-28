"""A1 — pack surface API: listing, run-creation envelopes, golden trajectory.

Convention notes (see tests/backend/test_phase_f4_sse.py): no pytest-asyncio —
async bodies run under asyncio.run(); the fail-closed probe rule applies — every
denial case has a positive-control twin.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.routers import packs as packs_router  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from tests.contracts.minijsonschema import validate as schema_validate  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DESCRIPTOR_SCHEMA = json.loads((REPO / "schemas" / "pack-descriptor.schema.json").read_text())


# ── workspace profile fixtures ────────────────────────────────────────────────


PROFILE = "example-ws-profile"

_PROFILE_MD = """# PROFILE
brand_name: ExampleCo
language: en
"""

_KNOWLEDGE = {
    "voice": "# Voice\n\nPlain, concrete.\n",
    "icp-personas": "# ICP\n\nFictional personas.\n",
}


def _provision(
    profiles_root: Path,
    *,
    packs_toml: str | None,
    knowledge: dict | None = None,
    profile_md: str = _PROFILE_MD,
) -> None:
    pdir = profiles_root / PROFILE
    (pdir / "knowledge").mkdir(parents=True, exist_ok=True)
    (pdir / "PROFILE.md").write_text(profile_md)
    for topic, body in (knowledge if knowledge is not None else _KNOWLEDGE).items():
        (pdir / "knowledge" / f"{topic}.md").write_text(body)
    if packs_toml is not None:
        (pdir / "packs.toml").write_text(packs_toml)


# ws_env fixture: tests/backend/conftest.py (shared with the A2/A11 suites).


@pytest.fixture()
def client(ws_env):
    app = FastAPI()
    app.include_router(packs_router.router, prefix="/v1")
    app.include_router(runs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    app.state.sessions = MagicMock()
    app.state.cfg = MagicMock(repo_root=REPO)
    # pro_plus: this fixture backs the run-CREATION mechanics tests below (traversal
    # guards, missing-settings ordering, ...), none of which is about entitlement — the
    # one test that is (test_descriptor_entitlement_lock) builds its own scenario
    # rather than using this fixture. pro_plus keeps every activated pack (including
    # marketing, now gated at pro_plus — gtm_core/gating.toml) reachable so those tests
    # keep exercising the code path they were written for.
    ctx = WorkspaceCtx(user_id=str(uuid.uuid4()), workspace_id=ws_env.ws_id, entitlement="pro_plus")
    app.dependency_overrides[require_auth] = lambda: ctx
    return TestClient(app, raise_server_exceptions=True)


# ── listing ───────────────────────────────────────────────────────────────────


def test_listing_shows_only_activated_packs(client, ws_env):
    """Tenant filtering is ABSENCE from the listing, not 403-on-run (A1 acceptance)."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    resp = client.get(f"/v1/packs?profile_name={PROFILE}")
    assert resp.status_code == 200
    got = resp.json()
    assert got, "activated pack must be listed"
    assert {d["pack"] for d in got} == {"marketing"}
    # prospecting exists in the repo but is not activated — absent, not locked.
    assert not any(d["pack"] == "prospecting" for d in got)


def test_listing_empty_without_packs_toml(client, ws_env):
    """No packs.toml ⇒ nothing active (fail-closed, matches reachability)."""
    _provision(ws_env.profiles_root, packs_toml=None)
    assert client.get(f"/v1/packs?profile_name={PROFILE}").json() == []


def test_listing_descriptors_validate_against_contract(client, ws_env):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing", "prospecting"]\n')
    got = client.get(f"/v1/packs?profile_name={PROFILE}").json()
    assert len(got) >= 3  # linkedin-post + long-form-blog + case-study + prospect-outreach
    for d in got:
        errors = schema_validate(d, DESCRIPTOR_SCHEMA)
        assert not errors, (d["pack"], d["variant"], errors)


def test_listing_never_leaks_prompt_or_model_role(client, ws_env):
    """The schema's normative exclusion: no orchestration detail on the wire."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    body = client.get(f"/v1/packs?profile_name={PROFILE}").text
    assert "model_role" not in body
    assert "brain_plan" not in body
    assert '"prompt"' not in body


def test_prospecting_missing_inputs_toml_tolerated(client, ws_env):
    """Two shipped packs declare no inputs.toml — the catalog must not 500."""
    _provision(ws_env.profiles_root, packs_toml='active = ["prospecting"]\n')
    got = client.get(f"/v1/packs?profile_name={PROFILE}").json()
    assert [d["pack"] for d in got] == ["prospecting"]
    assert got[0]["inputs"] == {"settings": [], "knowledge": []}
    # No declared inputs ⇒ nothing can be missing ⇒ ready.
    assert got[0]["readiness"]["status"] == "ready"


def test_descriptor_entitlement_lock():
    """Server-computed available/locked_reason — the client never orders tiers."""
    import dataclasses

    from backend.pack_catalog import ResolvedVariant, descriptor
    from gtm_core.packs.loader import PackInputs, load_pack_graph

    # Built from the PLANNING graph, whose nodes all derive a "free" floor, so this
    # test's own explicit "pro" header sits ABOVE the node floor and is a legal raise.
    # (It used to borrow marketing/linkedin-post and rename the pack off "marketing/*"
    # to dodge that pack's graph-level override; the 2026-08-25 repricing moved the
    # pro_plus floor onto the marketing SKILLS themselves, so renaming the graph no
    # longer escapes it and the explicit "pro" became a below_floor error.) This test is
    # about descriptor()'s available/locked_reason arithmetic in isolation, not about
    # any pack's actual price.
    graph = load_pack_graph(REPO / "packs" / "planning" / "graphs" / "planning.toml")
    gated = dataclasses.replace(graph, pack="example", variant="demo", min_entitlement="pro")
    resolved = ResolvedVariant(graph=gated, inputs=PackInputs())

    free_view = descriptor(resolved, entitlement="free", readiness=None)
    assert free_view["available"] is False
    assert free_view["locked_reason"] == "entitlement_required"
    pro_view = descriptor(resolved, entitlement="pro", readiness=None)  # positive control
    assert pro_view["available"] is True
    assert "locked_reason" not in pro_view


# ── run-creation envelopes (fail-closed, in normative order) ─────────────────


def _run_body(**over) -> dict:
    body = {
        "profile_name": PROFILE,
        "pack": "marketing",
        "variant": "linkedin-post",
        "inputs": {"brand_name": "ExampleCo"},
    }
    body.update(over)
    return body


def test_create_unknown_variant_404(client, ws_env):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    resp = client.post("/v1/runs", json=_run_body(variant="no-such-variant"))
    assert resp.status_code == 404


def test_create_traversal_shaped_names_404(client, ws_env):
    """Pack/variant are client-supplied path segments — traversal shapes are inert."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    for pack, variant in [("../marketing", "linkedin-post"), ("marketing", "../../etc/passwd")]:
        resp = client.post("/v1/runs", json=_run_body(pack=pack, variant=variant))
        assert resp.status_code == 404, (pack, variant)


def test_create_traversal_shaped_profile_name_422(client, ws_env):
    """B1: profile_name is a path segment too — a traversal shape is rejected at the
    boundary (before any join can escape the workspace root), and a bare name passes."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    for bad in ["../../../../profiles/acme", "/etc/passwd", "..", ".", "a/b", ""]:
        resp = client.post("/v1/runs", json=_run_body(profile_name=bad))
        # "" trips the pydantic "profile_name required" gate (also 422); every
        # other shape trips the invalid_profile_name guard. Both are the fail-closed
        # outcome we want — assert the guard code for the non-empty shapes.
        assert resp.status_code == 422, bad
        if bad:
            assert resp.json()["detail"]["code"] == "invalid_profile_name", bad
    # positive control: a valid bare name passes the shape guard and reaches pack
    # logic (403 not_activated here), proving the guard doesn't reject legit names.
    ok = client.post(
        "/v1/runs",
        json=_run_body(
            profile_name=PROFILE, pack="prospecting", variant="prospect-outreach", inputs={}
        ),
    )
    assert ok.status_code == 403
    assert ok.json()["detail"]["code"] == "pack_not_activated"


def test_list_packs_traversal_shaped_profile_name_422(client, ws_env):
    """B1: the listing route's explicit profile_name is a path segment as well."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    resp = client.get("/v1/packs?profile_name=../../../../profiles/acme")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_profile_name"
    # positive control: a valid bare name lists normally.
    assert client.get(f"/v1/packs?profile_name={PROFILE}").status_code == 200


def test_create_not_activated_403(client, ws_env):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    resp = client.post(
        "/v1/runs",
        json=_run_body(pack="prospecting", variant="prospect-outreach", inputs={}),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "pack_not_activated"


def test_create_missing_settings_422_lists_keys(client, ws_env):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    resp = client.post("/v1/runs", json=_run_body(inputs={}))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "missing_settings"
    assert detail["missing"] == ["brand_name"]


def test_create_mode_validation_422(client, ws_env):
    """Request-shaped pydantic gates: pack without variant; neither prompt nor pack."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    assert (
        client.post("/v1/runs", json={"profile_name": PROFILE, "pack": "marketing"}).status_code
        == 422
    )
    assert client.post("/v1/runs", json={"profile_name": PROFILE}).status_code == 422


# ── golden trajectory: pack mode executes the SAME node sequence as the runner twin ──


def _fake_stage_recorder(recorded: list[str], awaiting_on: str | None = None):
    from agent.pipeline import AWAITING_APPROVAL, StageOutcome

    async def _fake(cfg, profile, stage_name, manifest, prompts=None, stage_roles=None, **_kw):
        recorded.append(stage_name)
        if awaiting_on is not None and stage_name == awaiting_on:
            return StageOutcome(status=AWAITING_APPROVAL, outputs=(stage_name,))
        return StageOutcome(status="ok", outputs=(stage_name,))

    return _fake


@contextlib.contextmanager
def _pack_run_harness(ws_env, recorded, awaiting_on=None, budget_side_effect=None):
    """Patches for a DB-less _execute_pack_run: fake executor, mocked scope + budget."""
    conn = AsyncMock()

    @contextlib.asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    budget = (
        AsyncMock(side_effect=budget_side_effect)
        if budget_side_effect
        else AsyncMock(return_value=True)
    )
    with (
        patch("agent.packs.execute_stage", _fake_stage_recorder(recorded, awaiting_on)),
        patch.object(runs_router, "workspace_scope", _scope),
        patch.object(runs_router, "acheck_budget", budget),
        patch.object(runs_router, "send_gate_push", AsyncMock(return_value=0)),
    ):
        yield conn


def _provision_and_run(ws_env, recorded, awaiting_on=None, budget_side_effect=None):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id = str(uuid.uuid4())

    async def _go(conn):
        await runs_router._execute_pack_run(
            MagicMock(),
            REPO,
            ws_env.ws_id,
            run_id,
            PROFILE,
            "marketing",
            "linkedin-post",
            {},
            entitlement="pro_plus",
        )
        return conn

    with _pack_run_harness(ws_env, recorded, awaiting_on, budget_side_effect) as conn:
        asyncio.run(_go(conn))
    return run_id, conn


def test_pack_mode_runs_the_default_graph_sequence(ws_env):
    """The A1 golden: linkedin-post through the backend == the Telegram/cron order.

    Twin of tests/contracts/test_pack_loader.py's trajectory test — same stage
    sequence as DEFAULT_GRAPH/STAGES, byte-identical prompts proven there.
    """
    from agent.pipeline import STAGES

    recorded: list[str] = []
    run_id, conn = _provision_and_run(ws_env, recorded)
    # publish is short-circuited to SKIPPED inside execute_stage on the REAL path;
    # the fake executor records every dispatched node, which is the graph order.
    assert recorded == list(STAGES)
    final_updates = [c.args[0] for c in conn.execute.call_args_list if "status = 'ok'" in c.args[0]]
    assert final_updates, "run must terminate ok"


def test_pack_mode_gate1_pause_approve_resume(ws_env):
    """Return-and-resume Gate 1 (D1): pause at plan, approve promotes the draft,
    downstream nodes run, run completes ok."""
    from agent.pipeline import STAGES

    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    # A pending draft the plan stage "wrote" (the promotion input).
    pending = ws_env.content_root / PROFILE / "plans" / ".pending"
    pending.mkdir(parents=True)
    draft_items = [{"id": "item-1", "platform": "linkedin", "slot": "mon", "status": "draft"}]
    (pending / "2026-32.draft.json").write_text(json.dumps(draft_items))

    recorded: list[str] = []
    run_id = str(uuid.uuid4())

    async def _go(conn):
        task = asyncio.create_task(
            runs_router._execute_pack_run(
                MagicMock(),
                REPO,
                ws_env.ws_id,
                run_id,
                PROFILE,
                "marketing",
                "linkedin-post",
                {},
                entitlement="pro_plus",
            )
        )
        for _ in range(400):  # wait for the gate waiter to register
            if run_id in runs_router._gate_events:
                break
            await asyncio.sleep(0.005)
        assert run_id in runs_router._gate_events, "gate never registered"
        async with runs_router._state_lock:
            runs_router._gate_decisions[run_id] = {"decision": "approve", "edited_content": None}
            runs_router._gate_events[run_id].set()
        await asyncio.wait_for(task, timeout=10)
        return conn

    with _pack_run_harness(ws_env, recorded, awaiting_on="plan") as conn:
        asyncio.run(_go(conn))

    # plan ran once (paused), then approve flipped it and downstream continued.
    assert recorded == ["radar", "plan", "research", "studio", "publish"] == list(STAGES)
    promoted = ws_env.content_root / PROFILE / "plans" / "2026-32-plan.json"
    assert promoted.is_file(), "approve must promote the draft to the final plan"
    assert all(i["status"] == "planned" for i in json.loads(promoted.read_text()))
    assert not (pending / "2026-32.draft.json").exists(), "draft removed on promotion"
    assert any("status = 'ok'" in c.args[0] for c in conn.execute.call_args_list)


def test_pack_mode_gate1_reject_stops_run(ws_env):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    pending = ws_env.content_root / PROFILE / "plans" / ".pending"
    pending.mkdir(parents=True)
    (pending / "2026-32.draft.json").write_text("[]")

    recorded: list[str] = []
    run_id = str(uuid.uuid4())

    async def _go(conn):
        task = asyncio.create_task(
            runs_router._execute_pack_run(
                MagicMock(),
                REPO,
                ws_env.ws_id,
                run_id,
                PROFILE,
                "marketing",
                "linkedin-post",
                {},
                entitlement="pro_plus",
            )
        )
        for _ in range(400):
            if run_id in runs_router._gate_events:
                break
            await asyncio.sleep(0.005)
        async with runs_router._state_lock:
            runs_router._gate_decisions[run_id] = {"decision": "reject", "edited_content": None}
            runs_router._gate_events[run_id].set()
        await asyncio.wait_for(task, timeout=10)
        return conn

    with _pack_run_harness(ws_env, recorded, awaiting_on="plan") as conn:
        asyncio.run(_go(conn))

    assert recorded == ["radar", "plan"], "nothing downstream of a rejected gate runs"
    assert any("status = 'rejected'" in c.args[0] for c in conn.execute.call_args_list)
    assert not (pending / "2026-32.draft.json").exists(), "reject discards the draft"


def test_pack_mode_budget_guard_blocks_batch(ws_env):
    """§R2 per-batch: admission passes, the first dispatch batch is then denied —
    no stage executes and the run fails with the cap error."""
    recorded: list[str] = []
    run_id, conn = _provision_and_run(
        ws_env, recorded, budget_side_effect=[True, False, False, False, False, False]
    )
    assert recorded == [], "no stage may run once the cap check denies the batch"
    fails = [c.args for c in conn.execute.call_args_list if "status = 'failed'" in c.args[0]]
    assert fails and "monthly cost cap" in fails[-1][2]
