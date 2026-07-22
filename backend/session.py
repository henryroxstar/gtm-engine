"""Multi-tenant agent session manager for the backend API.

Wraps agent.session.build_agent_options — per fix #4, we import it, not move it.
Each (workspace_id, profile_name) pair gets its own AgentSession, re-used across
requests for that workspace (warm session = no re-connect overhead).

Session lifecycle:
  - Created on first run request for a (workspace_id, profile_name) pair.
  - Kept warm in the BackendSessionStore while the server is running.
  - Closed and evicted after IDLE_SECONDS of inactivity.

Publish gate invariants are unchanged: the backend emits ⟦GATE:publish⟧ blocks
the same way the personal VPS does; the mobile app's gate endpoint plays the role
of the Telegram approve button (operator approves exact bytes → agent/publish.py
makes the call server-side).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

IDLE_SECONDS = 300  # evict sessions idle for 5 minutes


def _workspace_scoped_config(base_cfg: Any, workspace_id: str, repo_root: Path) -> Any:
    """Return a copy of ``base_cfg`` with content + profile roots pinned to this
    workspace's isolated tree (``data/workspaces/<ws>/``), creating the dirs.

    This is the P3 filesystem tenant boundary: a run can only touch its own
    workspace's files. ``build_agent_options`` carries these roots into the SDK
    subprocess env, so the run's skills are scoped too — with no shared-os.environ
    race across concurrent runs. Kept as a module function so it is unit-testable
    without an SDK session.
    """
    import dataclasses

    from gtm_core.paths import workspace_content_root, workspace_profiles_root

    profiles_dir = workspace_profiles_root(workspace_id, repo_root)
    content_dir = workspace_content_root(workspace_id, repo_root)
    profiles_dir.mkdir(parents=True, exist_ok=True)
    content_dir.mkdir(parents=True, exist_ok=True)
    return dataclasses.replace(base_cfg, content_root=content_dir, profiles_root=profiles_dir)


class BackendSessionStore:
    """Session cache keyed by (workspace_id, profile_name).

    Concurrency model — a cached session is a **warm serial** session that serves
    ONE run at a time. ``run()`` pops the warm session out of the cache for the
    whole run and returns it when finished, so a second concurrent run for the same
    key cannot share its ``ClaudeSDKClient`` and interleave streams, cost metering,
    or publish-gate bytes; a concurrent same-key run that finds no idle session
    builds its own isolated one. The ``asyncio.Lock`` guards only the cache dict
    (brief holds) — never a run — so a gate-paused run (up to 24h) never blocks
    another. This supersedes the earlier shared-unlocked-client design, under which
    two runs for one workspace could cross-contaminate output and gate approvals.
    """

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self._sessions: dict[tuple[str, str], _BackendSession] = {}
        self._lock = asyncio.Lock()  # guards _sessions only; held briefly, never over a run

    async def run(
        self,
        pool: Any,
        workspace_id: str,
        profile_name: str,
        prompt: str,
        run_id: str,
    ) -> AsyncGenerator[str, None]:
        """Run a prompt in an isolated session for (workspace_id, profile_name).

        ``pool`` + ``run_id`` are threaded so the brain's cost is metered to the
        workspace's ``cost_records`` (RLS-scoped) tagged with this run. Claims the
        warm session if one is idle, else builds a fresh one; the session serves
        this run EXCLUSIVELY, then returns to the cache if still healthy.
        """
        key = (workspace_id, profile_name)
        async with self._lock:
            session = self._sessions.pop(key, None)  # exclusive ownership for this run
        if session is not None and not session.connected:
            await session.close()
            session = None
        if session is None:
            session = _BackendSession(
                self._repo_root, profile_name, pool=pool, workspace_id=workspace_id
            )
            await session.connect()

        healthy = False
        try:
            async for chunk in session.run(prompt, run_id):
                yield chunk
            healthy = session.connected
        finally:
            returned = False
            if healthy:
                async with self._lock:
                    if key not in self._sessions:  # first finisher warms the cache
                        session.touch()
                        self._sessions[key] = session
                        returned = True
            if not returned:  # unhealthy, cancelled, or a warm slot already taken
                await session.close()

    async def close_idle(self) -> None:
        """Close and evict sessions idle for more than IDLE_SECONDS.

        Only idle sessions live in the cache — an in-flight run has popped its
        session out — so this can never evict a session mid-stream.
        """
        cutoff = time.monotonic() - IDLE_SECONDS
        async with self._lock:
            to_close = [k for k, s in self._sessions.items() if s.last_used < cutoff]
            evicted = [self._sessions.pop(k) for k in to_close]
        for session in evicted:  # close outside the lock (close() awaits)
            await session.close()

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.close()


class _BackendSession:
    """One agent session for one (workspace_id, profile_name).

    A thin wrapper around the production :class:`agent.session.AgentSession` so
    the backend and the VPS/Telegram cockpit share ONE implementation of the SDK
    streaming contract (``query()`` + ``receive_response()`` + AssistantMessage/
    TextBlock filtering). Delegating instead of re-implementing is deliberate: an
    earlier copy here drifted to a non-existent ``client.run()`` API and would
    have crashed the first time the mobile backend streamed a run. We import
    ``AgentSession``/``build_agent_options`` from ``agent.session`` (fix #4 — we
    import, not move). The repo root is passed in so the backend running in Docker
    can point to the mounted source tree.
    """

    def __init__(
        self,
        repo_root: Path,
        profile_name: str,
        *,
        pool: Any = None,
        workspace_id: str | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._profile = profile_name
        self._pool = pool
        self._workspace_id = workspace_id
        self._current_run_id: str | None = None
        self._agent = None  # agent.session.AgentSession, created in connect()
        self.connected = False
        self.last_used = time.monotonic()
        # Strong refs to in-flight metering writes so the event loop can't GC them
        # mid-flight (fire-and-forget create_task otherwise holds no reference).
        self._meter_tasks: set[asyncio.Task] = set()

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def _meter_brain(self, rec: Any) -> None:
        """Brain-cost sink (sync, called from AgentSession._log_usage).

        Enriches the record with this run's workspace/run scope and schedules a
        fire-and-forget, RLS-scoped INSERT into ``cost_records``. Best-effort: a
        metering failure must never break the stream. The write runs inside a
        ``workspace_scope`` so the INSERT satisfies the table's RLS WITH CHECK.
        """
        if self._pool is None or self._workspace_id is None:
            return
        from dataclasses import replace

        full = replace(
            rec,
            runtime="backend",
            workspace_id=self._workspace_id,
            run_id=self._current_run_id,
            stage="brain",
        )

        async def _write() -> None:
            from gtm_core.metering import PgSink, ameter

            from .database import workspace_scope

            try:
                async with workspace_scope(self._pool, self._workspace_id) as conn:
                    await ameter(full, sink=PgSink(self._pool, "cost_records"), conn=conn)
            except Exception:  # noqa: BLE001 — metering is best-effort
                pass

        try:
            task = asyncio.create_task(_write())
            self._meter_tasks.add(task)  # keep a ref until it completes
            task.add_done_callback(self._meter_tasks.discard)
        except RuntimeError:
            pass  # no running loop (shouldn't happen on the backend stream path)

    async def connect(self) -> None:
        from agent.config import Config
        from agent.permissions import make_headless_can_use_tool
        from agent.session import AgentSession

        cfg = Config.from_env(repo_root=self._repo_root)
        if self._workspace_id:
            cfg = _workspace_scoped_config(cfg, self._workspace_id, self._repo_root)
        self._agent = AgentSession(
            cfg,
            self._profile,
            can_use_tool=make_headless_can_use_tool(),
            usage_sink=self._meter_brain,
        )
        await self._agent.connect()
        self.connected = True

    async def run(self, prompt: str, run_id: str | None = None) -> AsyncGenerator[str, None]:
        if not self.connected or self._agent is None:
            raise RuntimeError("session not connected")
        self._current_run_id = run_id
        async for text in self._agent.run(prompt):
            yield text

    async def close(self) -> None:
        # Let in-flight cost writes finish before tearing down the pool/agent, so
        # the final paid call of a session is not lost against the monthly cap.
        # Best-effort + bounded: metering must never hang shutdown.
        if self._meter_tasks:
            try:
                await asyncio.wait(set(self._meter_tasks), timeout=5)
            except Exception:  # noqa: BLE001 — draining is best-effort
                pass
        if self._agent is not None:
            try:
                await self._agent.close()
            except Exception:
                pass
        self.connected = False
        self._agent = None
