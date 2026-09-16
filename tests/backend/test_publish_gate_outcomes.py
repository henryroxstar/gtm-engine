"""Gate-2 publish-dispatch outcome handling inside ``_execute_pack_run``.

Closes a silent-failure gap: previously only ``hash_mismatch`` and
``disclosure_missing`` failed the run when a Gate-2 dispatch didn't actually
publish — every other non-success ``DispatchOutcome`` (most importantly
``publish_failed``, and ``dispatch_backend_publish`` returning ``None`` for an
unconfigured workspace) fell through to "mark the node complete, run keeps
going" as if the post had gone out. These tests drive the real
``marketing/linkedin-post`` pack graph (its ``publish`` node already declares
``gate = true`` + ``external_effect = "publish"`` — see
``packs/marketing/graphs/linkedin-post.toml``) through the actual Gate-2 branch
in ``backend/routers/runs.py``, with ``dispatch_backend_publish`` mocked to
return each interesting outcome.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from agent.publish import PublishResult  # noqa: E402
from agent.publish_dispatch import DispatchOutcome  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from backend.services.runs import pack_executor as runs_pack_executor  # noqa: E402
from tests.backend._protocol1 import REPO, drive_gate, fake_executor, pack_run_harness  # noqa: E402
from tests.backend.test_packs_api import PROFILE, _provision  # noqa: E402


def _run_gate2(ws_env, dispatch_return):
    """Provision the real linkedin-post pack, pause at the `publish` gate,
    approve it with edited_content (the approved post bytes), and drive the
    run to completion with `dispatch_backend_publish` mocked to return
    `dispatch_return`. Returns (conn, fail_run_mock)."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id = str(uuid.uuid4())
    recorded: list[str] = []
    conn = AsyncMock()
    executor = fake_executor(recorded, awaiting_on="publish")
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
                "marketing",
                "linkedin-post",
                {},
                entitlement="pro_plus",
            )
        )
        await drive_gate(run_id, "approve", edited_content="Approved post text.")
        await asyncio.wait_for(task, timeout=10)

    with (
        pack_run_harness(conn, executor),
        patch.object(runs_pack_executor, "dispatch_backend_publish", dispatch_mock),
        patch.object(runs_pack_executor, "_fail_run", fail_run_mock),
    ):
        asyncio.run(_go())
    return conn, fail_run_mock


def _reached_ok(conn) -> bool:
    # RL-03: complete_run now reads its guarded write's match back via `fetchrow(...
    # RETURNING id)` instead of a bare `execute` — check both call lists.
    calls = list(conn.execute.call_args_list) + list(conn.fetchrow.call_args_list)
    return any("status = 'ok'" in c.args[0] for c in calls)


def test_hash_mismatch_fails_the_run(ws_env):
    conn, fail_run_mock = _run_gate2(ws_env, DispatchOutcome(ok=False, status="hash_mismatch"))
    fail_run_mock.assert_awaited_once()
    assert "integrity" in fail_run_mock.await_args.args[3].lower()
    assert fail_run_mock.await_args.kwargs == {"error_code": "draft_integrity_failed"}
    assert not _reached_ok(conn)


def test_disclosure_missing_fails_the_run(ws_env):
    conn, fail_run_mock = _run_gate2(ws_env, DispatchOutcome(ok=False, status="disclosure_missing"))
    fail_run_mock.assert_awaited_once()
    assert "disclos" in fail_run_mock.await_args.args[3].lower()
    assert fail_run_mock.await_args.kwargs == {"error_code": "disclosure_required"}
    assert not _reached_ok(conn)


def test_publish_failed_fails_the_run(ws_env):
    """The gap this closes: a real send failure (rate limit, non-2xx, network
    error, disabled kill switch, ...) previously fell through as if the post
    had published."""
    outcome = DispatchOutcome(
        ok=False, status="publish_failed", result=PublishResult(ok=False, status="rate_limited")
    )
    conn, fail_run_mock = _run_gate2(ws_env, outcome)
    fail_run_mock.assert_awaited_once()
    assert "rate limit" in fail_run_mock.await_args.args[3].lower()
    assert fail_run_mock.await_args.kwargs == {"error_code": "dispatch_failed"}
    assert not _reached_ok(conn)


def test_no_destination_configured_fails_the_run(ws_env):
    """`dispatch_backend_publish` returns None when the workspace has no enabled
    publish destination — previously this also fell through silently."""
    conn, fail_run_mock = _run_gate2(ws_env, None)
    fail_run_mock.assert_awaited_once()
    assert "destination" in fail_run_mock.await_args.args[3].lower()
    assert fail_run_mock.await_args.kwargs == {"error_code": "publish_not_configured"}
    assert not _reached_ok(conn)


def test_dry_run_does_not_fail_the_run(ws_env):
    """dry_run is a structurally intentional no-op, not a failure."""
    conn, fail_run_mock = _run_gate2(ws_env, DispatchOutcome(ok=False, status="dry_run"))
    fail_run_mock.assert_not_awaited()
    assert _reached_ok(conn)


def test_duplicate_publish_does_not_fail_the_run(ws_env):
    """A `duplicate` PublishResult means the exact approved bytes were already
    published on a prior attempt at this same approval (the A5 gate-decision-
    replay path re-entering after a restart) — idempotency, not failure."""
    outcome = DispatchOutcome(
        ok=False, status="publish_failed", result=PublishResult(ok=False, status="duplicate")
    )
    conn, fail_run_mock = _run_gate2(ws_env, outcome)
    fail_run_mock.assert_not_awaited()
    assert _reached_ok(conn)


def test_published_success_does_not_fail_the_run(ws_env):
    """Regression pin: the generalized check must not start failing the happy path."""
    outcome = DispatchOutcome(
        ok=True,
        status="published",
        result=PublishResult(ok=True, status="published", post_id="urn:li:share:123"),
    )
    conn, fail_run_mock = _run_gate2(ws_env, outcome)
    fail_run_mock.assert_not_awaited()
    assert _reached_ok(conn)
