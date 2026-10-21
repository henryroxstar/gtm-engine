"""MM9 wiring: every tenant run path hands its permission callback the run's OWN workspace confinement.

``test_permissions_confine.py`` proves the policy. This proves it is actually ASKED on the two run
paths that carry a tenant's brain: a pack run (``make_executor_from_pack`` → ``execute_stage``) and a
warm prompt session (``BackendSession.connect``). A policy that is correct and never consulted is the
shape of bug a unit test of the policy cannot see — so each test drives the real path to the point
where the callback exists, then asks the callback itself.

The single-tenant VPS paths are asserted to pass NOTHING (``confine`` stays ``None``): their behaviour
is the golden's, and a confinement there would be a regression.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")

from agent import pipeline_executor as pe  # noqa: E402
from agent.path_confinement import Confinement  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
WS_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WS_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture
def two_workspaces(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "data" / "workspaces"))
    for wid in (WS_A, WS_B):
        (tmp_path / "data" / "workspaces" / wid / "content" / "acme").mkdir(parents=True)
        (tmp_path / "data" / "workspaces" / wid / "profiles").mkdir(parents=True)
    return tmp_path


def _decision(callback, tool, inp) -> str:
    return type(asyncio.run(callback(tool, inp, None))).__name__


# ── pack runs: execute_stage builds the callback from the confinement it is given ──


def _capture_callback(monkeypatch):
    """Stop ``execute_stage`` at ``build_agent_options`` and keep the callback it was handed."""
    from claude_agent_sdk import ResultMessage

    got: dict = {}

    def fake_options(*_a, **kw):
        got.update(kw)
        return object()

    async def fake_stream(*_a, **_k):
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
        )

    monkeypatch.setattr(pe, "build_agent_options", fake_options)
    monkeypatch.setattr(pe, "stream_brain_messages", fake_stream)
    return got


class _Cfg:
    repo_root = REPO
    content_root = None


def _run_stage(tmp_path, **kw):
    cfg = _Cfg()
    cfg.content_root = tmp_path / "content"
    return asyncio.run(
        pe.execute_stage(cfg, "acme", "dossier", {}, prompts={"dossier": "go"}, **kw)
    )


def test_a_pack_stage_with_a_confinement_refuses_another_workspace_and_serves_its_own(
    monkeypatch, two_workspaces
):
    got = _capture_callback(monkeypatch)
    confine = Confinement.for_workspace(WS_A, REPO)
    _run_stage(two_workspaces, confine=confine)
    cb = got["can_use_tool"]
    own = str(two_workspaces / "data/workspaces" / WS_A / "content/acme/x.md")
    other = str(two_workspaces / "data/workspaces" / WS_B / "content/acme/x.md")
    assert _decision(cb, "Read", {"file_path": own}) == "PermissionResultAllow"
    assert _decision(cb, "Read", {"file_path": other}) == "PermissionResultDeny"


def test_a_pack_stage_with_no_confinement_is_the_unconfined_policy(monkeypatch, two_workspaces):
    got = _capture_callback(monkeypatch)
    _run_stage(two_workspaces)
    other = str(two_workspaces / "data/workspaces" / WS_B / "content/acme/x.md")
    assert _decision(got["can_use_tool"], "Read", {"file_path": other}) == "PermissionResultAllow"


def test_make_executor_from_pack_hands_the_confinement_to_every_stage(monkeypatch, two_workspaces):
    from agent.packs import make_executor_from_pack
    from gtm_core.packs.loader import load_pack_graph

    got = _capture_callback(monkeypatch)
    confine = Confinement.for_workspace(WS_A, REPO)
    graph = load_pack_graph(REPO / "packs" / "marketing" / "graphs" / "linkedin-post.toml")
    cfg = _Cfg()
    cfg.content_root = two_workspaces / "content"
    executor = make_executor_from_pack(cfg, "acme", graph, confine=confine)
    asyncio.run(executor(graph.nodes[0].id, {}))
    other = str(two_workspaces / "data/workspaces" / WS_B / "content/acme/x.md")
    assert _decision(got["can_use_tool"], "Read", {"file_path": other}) == "PermissionResultDeny"


# ── the backend's own call sites ────────────────────────────────────────────────


def test_the_backend_pack_run_asks_for_its_workspaces_confinement():
    """``_execute_pack_run`` builds the executor for ONE workspace; the call must carry that workspace's
    confinement. Structural, because driving a whole pack run needs the full harness — the behaviour
    behind the keyword is covered above."""
    import ast

    src = (REPO / "backend/services/runs/pack_executor.py").read_text(encoding="utf-8")
    calls = [
        n
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", getattr(n.func, "attr", "")) == "make_executor_from_pack"
    ]
    assert calls, "pack_executor no longer builds its executor with make_executor_from_pack"
    for call in calls:
        kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
        assert "confine" in kw, "a tenant pack run is built with no path confinement"
        assert "workspace_id" in kw["confine"]


def test_a_warm_prompt_session_is_built_with_its_workspaces_confinement(
    monkeypatch, two_workspaces
):
    from backend import session as backend_session

    captured: dict = {}

    class FakeAgentSession:
        def __init__(self, cfg, profile, **kw):
            captured.update(kw)

        async def connect(self):
            return None

    monkeypatch.setattr("agent.session.AgentSession", FakeAgentSession)
    store = backend_session._BackendSession(REPO, "acme", pool=None, workspace_id=WS_A)
    asyncio.run(store.connect())

    cb = captured["can_use_tool"]
    own = str(two_workspaces / "data/workspaces" / WS_A / "content/acme/x.md")
    other = str(two_workspaces / "data/workspaces" / WS_B / "content/acme/x.md")
    assert _decision(cb, "Read", {"file_path": own}) == "PermissionResultAllow"
    assert _decision(cb, "Read", {"file_path": other}) == "PermissionResultDeny"
    assert _decision(cb, "Bash", {"command": f"cat {other}"}) == "PermissionResultDeny"


def test_a_session_with_no_workspace_is_the_unconfined_single_tenant_one(
    monkeypatch, two_workspaces
):
    from backend import session as backend_session

    captured: dict = {}

    class FakeAgentSession:
        def __init__(self, cfg, profile, **kw):
            captured.update(kw)

        async def connect(self):
            return None

    monkeypatch.setattr("agent.session.AgentSession", FakeAgentSession)
    store = backend_session._BackendSession(REPO, "acme", pool=None, workspace_id=None)
    asyncio.run(store.connect())
    other = str(two_workspaces / "data/workspaces" / WS_B / "content/acme/x.md")
    assert (
        _decision(captured["can_use_tool"], "Read", {"file_path": other}) == "PermissionResultAllow"
    )
