"""A11 — Gate-2 email-enrollment-dispatch outcome handling inside ``_execute_pack_run``.

Mirrors tests/backend/test_publish_gate_outcomes.py's structure for the second Python-only
dispatch kind: drives the real ``prospecting/prospect-outreach`` pack graph through the
actual Gate-2 branch in backend/services/runs/pack_executor.py, with
``dispatch_backend_email_enroll`` mocked to return each interesting outcome.

The run pauses where a REAL run pauses — the ``sequence`` gate, which writes the
enroll-draft. ``sequence-enroll`` declares ``external_effect = "email_enroll"`` and so is
short-circuited to SKIPPED by the real engine; it can never pause itself, which is why the
approval of ``sequence`` is what dispatches it (``_dispatch_target``). Also pins that an
email-enroll dispatch is never mistaken for a publish.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from agent.email_dispatch import EnrollDispatchOutcome
from backend.routers import runs as runs_router
from backend.services.runs import pack_executor as runs_pack_executor
from tests.backend._protocol1 import REPO, drive_gate, fake_executor, pack_run_harness
from tests.backend.test_packs_api import PROFILE, _provision


def _draft(lead_ids: list[int], sequence_id: str = "seq-1") -> str:
    return json.dumps(
        {
            "tool": "add_leads_to_sequence",
            "sequence_id": sequence_id,
            "step_id": "step-1",
            "steps": [
                {"step_id": "step-1", "variants": [{"subject": "Hello", "content": "<p>Hi</p>"}]}
            ],
            "lead_ids": lead_ids,
        }
    )


_ENROLL_DRAFT = _draft([111, 222])
_PENDING = "prospects/sequences/.pending"


def _run_gate2(
    ws_env,
    dispatch_return,
    rival_draft: str | None = None,
    before_run: dict[str, str] | None = None,
):
    """Provision the real prospect-outreach pack, pause at the `sequence` gate after it
    writes an enroll-draft, approve it, and drive the run to completion
    with `dispatch_backend_email_enroll` mocked to return `dispatch_return`. With
    `rival_draft`, another run's newer enroll-draft lands in the same profile while this
    run waits at its gate; `before_run` files (paths under the profile's content root) are
    already on disk when the run starts. Returns (conn, fail_run_mock, dispatch_mock,
    recorded)."""
    _provision(ws_env.profiles_root, packs_toml='active = ["prospecting"]\n')
    run_id = str(uuid.uuid4())
    recorded: list[str] = []
    conn = AsyncMock()
    for rel, text in (before_run or {}).items():
        path = ws_env.content_root / PROFILE / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    draft_rel = f"{_PENDING}/{run_id}.enroll-draft.json"
    executor = fake_executor(
        recorded,
        awaiting_on="sequence",
        files_by_stage={"sequence": [(draft_rel, _ENROLL_DRAFT.encode())]},
    )
    dispatch_mock = AsyncMock(return_value=dispatch_return)
    fail_run_mock = AsyncMock()

    async def _go():
        task = asyncio.create_task(
            runs_router._execute_pack_run(
                MagicMock(),
                REPO,
                ws_env.ws_id,
                run_id,
                PROFILE,
                "prospecting",
                "prospect-outreach",
                {},
                entitlement="pro_plus",
            )
        )
        if rival_draft is not None:
            for _ in range(800):
                if run_id in runs_router._gate_events:
                    break
                await asyncio.sleep(0.005)
            pending = ws_env.content_root / PROFILE / "prospects" / "sequences" / ".pending"
            # Sorts after this run's uuid-named draft, so it is "newest in the profile".
            (pending / "zzzz-other-run.enroll-draft.json").write_text(rival_draft)
        await drive_gate(run_id, "approve")
        await asyncio.wait_for(task, timeout=10)

    with (
        pack_run_harness(conn, executor),
        patch.object(runs_pack_executor, "dispatch_backend_email_enroll", dispatch_mock),
        patch.object(runs_pack_executor, "_fail_run", fail_run_mock),
    ):
        asyncio.run(_go())
    return conn, fail_run_mock, dispatch_mock, recorded


def _reached_ok(conn) -> bool:
    # RL-03: complete_run now reads its guarded write's match back via `fetchrow(...
    # RETURNING id)` instead of a bare `execute` — check both call lists.
    calls = list(conn.execute.call_args_list) + list(conn.fetchrow.call_args_list)
    return any("status = 'ok'" in c.args[0] for c in calls)


def test_enroll_dispatch_receives_the_parsed_approved_draft(ws_env):
    """The exact dict the operator's draft parsed to — not raw bytes — reaches the
    dispatcher (unlike publish, which parses its own ⟦POST⟧ block)."""
    _, _, dispatch_mock, _ = _run_gate2(
        ws_env, dispatch_return=EnrollDispatchOutcome(ok=True, status="enrolled")
    )
    call = dispatch_mock.await_args
    assert call.kwargs["draft"]["tool"] == "add_leads_to_sequence"
    assert call.kwargs["draft"]["lead_ids"] == [111, 222]


def test_approval_enrolls_the_shown_draft_not_a_newer_one_from_another_run(ws_env):
    """The profile lock is released while a run waits at its gate, so another run in the
    same profile can write a newer enroll-draft meanwhile. Approval must enroll the list
    the operator was shown, and clear only that draft (client issue #240)."""
    _, fail_run_mock, dispatch_mock, _ = _run_gate2(
        ws_env,
        dispatch_return=EnrollDispatchOutcome(ok=True, status="enrolled"),
        rival_draft=_draft([999], sequence_id="seq-other"),
    )
    fail_run_mock.assert_not_awaited()
    assert dispatch_mock.await_args.kwargs["draft"]["lead_ids"] == [111, 222]
    pending = ws_env.content_root / PROFILE / "prospects" / "sequences" / ".pending"
    assert [p.name for p in pending.iterdir()] == ["zzzz-other-run.enroll-draft.json"]


def test_a_leftover_draft_already_on_disk_is_never_shown_or_enrolled(ws_env):
    """Client issue #245: a draft left by an earlier run (its gate timed out, was cancelled
    or failed to enroll) that sorts after this run's must not become this run's gate."""
    leftover = f"{_PENDING}/zzzz-earlier-run.enroll-draft.json"
    _, fail_run_mock, dispatch_mock, _ = _run_gate2(
        ws_env,
        dispatch_return=EnrollDispatchOutcome(ok=True, status="enrolled"),
        before_run={leftover: _draft([999], sequence_id="seq-earlier")},
    )
    fail_run_mock.assert_not_awaited()
    assert dispatch_mock.await_args.kwargs["draft"]["lead_ids"] == [111, 222]
    assert (ws_env.content_root / PROFILE / leftover).exists(), "another run's draft is not ours"


def test_a_plan_draft_in_the_same_profile_does_not_hijack_the_sequence_gate(ws_env):
    """The old lookup tried plan drafts first, so a marketing run waiting at its plan gate
    turned this enrollment gate into a plan approval. It must stay this run's enroll draft."""
    plan = "plans/.pending/2026-37.draft.json"
    _, fail_run_mock, dispatch_mock, _ = _run_gate2(
        ws_env,
        dispatch_return=EnrollDispatchOutcome(ok=True, status="enrolled"),
        before_run={plan: json.dumps([{"id": "item-1", "status": "draft"}])},
    )
    fail_run_mock.assert_not_awaited()
    assert dispatch_mock.await_args.kwargs["draft"]["lead_ids"] == [111, 222]
    assert (ws_env.content_root / PROFILE / plan).exists(), "the plan draft is not promoted"


def test_enroll_failed_fails_the_run(ws_env):
    conn, fail_run_mock, _, _ = _run_gate2(
        ws_env,
        dispatch_return=EnrollDispatchOutcome(
            ok=False, status="enroll_failed", detail="[saleshandy-error] HTTP 429"
        ),
    )
    fail_run_mock.assert_awaited_once()
    assert "429" in fail_run_mock.await_args.args[3]
    assert fail_run_mock.await_args.kwargs == {"error_code": "dispatch_failed"}
    assert not _reached_ok(conn)


def test_not_configured_fails_the_run(ws_env):
    """dispatch_backend_email_enroll returns None when the workspace has no Saleshandy
    key — previously (before this outcome was checked) this would have fallen through
    silently, exactly the gap test_publish_gate_outcomes.py closed for publish."""
    conn, fail_run_mock, _, _ = _run_gate2(ws_env, dispatch_return=None)
    fail_run_mock.assert_awaited_once()
    assert "saleshandy" in fail_run_mock.await_args.args[3].lower()
    assert fail_run_mock.await_args.kwargs == {"error_code": "email_not_configured"}
    assert not _reached_ok(conn)


def test_dry_run_does_not_fail_the_run(ws_env):
    conn, fail_run_mock, _, _ = _run_gate2(
        ws_env, dispatch_return=EnrollDispatchOutcome(ok=False, status="dry_run")
    )
    fail_run_mock.assert_not_awaited()
    assert _reached_ok(conn)


def test_successful_enrollment_does_not_fail_the_run(ws_env):
    conn, fail_run_mock, _, _ = _run_gate2(
        ws_env, dispatch_return=EnrollDispatchOutcome(ok=True, status="enrolled")
    )
    fail_run_mock.assert_not_awaited()
    assert _reached_ok(conn)


def test_the_real_engine_skips_sequence_enroll_so_it_cannot_pause_itself(tmp_path):
    """Why dispatch is keyed on the `sequence` gate's SUCCESSOR, not on a paused
    `sequence-enroll`: drives the REAL `execute_stage`/`make_executor_from_pack` (no
    `fake_executor`, which can force ANY node into AWAITING_APPROVAL) through the real graph
    with every upstream node `ok`. `sequence-enroll` declares `external_effect`, so the engine
    short-circuits it to SKIPPED before any model call and never dispatches on its own. If
    this ever changes, `_dispatch_target`'s successor rule needs revisiting.
    """
    import dataclasses

    from agent.config import Config
    from agent.packs import load_engine_graph, make_executor_from_pack
    from agent.pipeline import PipelineRunner, terminal_status

    cfg = dataclasses.replace(Config.from_env(repo_root=REPO), content_root=tmp_path / "content")
    graph_path = REPO / "packs" / "prospecting" / "graphs" / "prospect-outreach.toml"
    pack_graph, engine_graph = load_engine_graph(graph_path)

    manifest = {
        "run_id": "r-engine-skip",
        "trigger": "test",
        "profile": PROFILE,
        "stages": [
            {"name": n, "status": "ok"}
            for n in ("prospect", "dossier", "outreach", "quality", "sequence")
        ],
    }
    runner = PipelineRunner(cfg, PROFILE, graph=engine_graph)
    executor = make_executor_from_pack(cfg, PROFILE, pack_graph)

    with patch("agent.email_dispatch.dispatch_approved_enrollment") as mock_dispatch:
        final = asyncio.run(runner.run("r-engine-skip", "test", executor, manifest=manifest))

    assert terminal_status(final) == "ok"
    by_name = {s["name"]: s["status"] for s in final["stages"]}
    assert by_name["sequence-enroll"] == "skipped"
    mock_dispatch.assert_not_called()


def test_email_enroll_node_is_never_dispatched_as_a_publish(ws_env):
    """The bug a plain truthy external_effect check would reintroduce: an approved
    email-enroll gate must never reach dispatch_backend_publish."""
    publish_mock = AsyncMock()
    with patch.object(runs_pack_executor, "dispatch_backend_publish", publish_mock):
        _run_gate2(ws_env, dispatch_return=EnrollDispatchOutcome(ok=True, status="enrolled"))
    publish_mock.assert_not_awaited()


def test_enrollment_is_dispatched_at_the_sequence_gate_and_marks_the_successor_completed(ws_env):
    """The fix for the unreachable backend Gate 2 (PENDING.md "Backend pack-mode Gate-2
    dispatch may be structurally unreachable"): approving `sequence` enrolls inline, and
    `sequence-enroll` is persisted completed with an outcome block — the resumed runner must
    not hand it to the executor again. Before the fix the fake executor ran
    `sequence-enroll` as an ordinary stage and the dispatcher was never awaited."""
    conn, fail_run_mock, dispatch_mock, recorded = _run_gate2(
        ws_env, dispatch_return=EnrollDispatchOutcome(ok=True, status="enrolled")
    )
    fail_run_mock.assert_not_awaited()
    dispatch_mock.assert_awaited_once()
    assert "sequence-enroll" not in recorded
    sqls = [(c.args[0], c.args[1:]) for c in conn.execute.call_args_list]
    assert any(
        "INSERT INTO run_nodes" in sql and args[2] == "sequence-enroll" and args[3] == "completed"
        for sql, args in sqls
    )
    assert any(
        "INSERT INTO run_blocks" in sql and args[3] == "sequence-enroll" and "enrolled" in args[4]
        for sql, args in sqls
    )
    assert _reached_ok(conn)


def _pause_without_approving(ws_env, sequence_files: list[tuple[str, bytes]], before_run=None):
    """Run to the `sequence` gate with `sequence_files` as that node's writes, never
    approving. Returns (conn, fail_run_mock, dispatch_mock)."""
    _provision(ws_env.profiles_root, packs_toml='active = ["prospecting"]\n')
    run_id = str(uuid.uuid4())
    for rel, text in (before_run or {}).items():
        path = ws_env.content_root / PROFILE / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    conn = AsyncMock()
    executor = fake_executor(
        [],
        awaiting_on="sequence",
        files_by_stage={"sequence": [(rel.format(run_id=run_id), b) for rel, b in sequence_files]},
    )
    dispatch_mock = AsyncMock()
    fail_run_mock = AsyncMock()

    async def _go():
        await asyncio.wait_for(
            runs_router._execute_pack_run(
                MagicMock(),
                REPO,
                ws_env.ws_id,
                run_id,
                PROFILE,
                "prospecting",
                "prospect-outreach",
                {},
                entitlement="pro_plus",
            ),
            timeout=10,
        )

    with (
        pack_run_harness(conn, executor),
        patch.object(runs_pack_executor, "dispatch_backend_email_enroll", dispatch_mock),
        patch.object(runs_pack_executor, "_fail_run", fail_run_mock),
    ):
        asyncio.run(_go())
    assert run_id not in runs_router._gate_events, "no gate may open for this run"
    return conn, fail_run_mock, dispatch_mock


def test_no_enroll_draft_for_this_run_fails_at_the_pause(ws_env):
    """No draft written at `sequence` ⇒ nothing to approve. The run fails at the pause —
    and another run's leftover draft does not stand in for the missing one (#245)."""
    conn, fail_run_mock, dispatch_mock = _pause_without_approving(
        ws_env, [], before_run={f"{_PENDING}/zzzz-earlier-run.enroll-draft.json": _ENROLL_DRAFT}
    )
    dispatch_mock.assert_not_awaited()
    fail_run_mock.assert_awaited_once()
    assert "wrote no enrollment draft" in fail_run_mock.await_args.args[3]
    assert fail_run_mock.await_args.kwargs == {"error_code": "internal_error"}
    assert not _reached_ok(conn)


def test_an_enroll_draft_without_the_copy_fails_at_the_pause(ws_env):
    """Client issue #244: the approval must cover the sequence copy, so a draft that does
    not carry it is never shown for approval."""
    no_copy = {k: v for k, v in json.loads(_ENROLL_DRAFT).items() if k != "steps"}
    conn, fail_run_mock, dispatch_mock = _pause_without_approving(
        ws_env, [(f"{_PENDING}/{{run_id}}.enroll-draft.json", json.dumps(no_copy).encode())]
    )
    dispatch_mock.assert_not_awaited()
    fail_run_mock.assert_awaited_once()
    assert "steps" in fail_run_mock.await_args.args[3]
    assert fail_run_mock.await_args.kwargs == {"error_code": "internal_error"}
    assert not _reached_ok(conn)
