"""Multi-tenant agent session manager for the backend API.

Wraps agent.session.build_agent_options. Each (workspace_id, profile_name) pair
gets its own AgentSession, re-used across requests for that workspace.
Closed and evicted after IDLE_SECONDS of inactivity or when workspace is evicted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from gtm_core.context_metrics import run_context_record

_METRICS_LOG = logging.getLogger("gtm.context_metrics")

IDLE_SECONDS = 300  # evict sessions idle for 5 minutes


def _skills_key(allowed_skills: frozenset[str] | None) -> tuple[str, ...] | None:
    """Hashable, order-stable fingerprint of a run's skill scope for the session cache.

    ``None`` (unscoped) stays ``None`` so it can never collide with an empty scope —
    "no scope at all" and "entitled to nothing" are opposite verdicts.
    """
    return None if allowed_skills is None else tuple(sorted(allowed_skills))


#: The data providers a tenant may run on the pooled house key, and the Config field pairs that carry
#: each one's key source and allocated share (MM4/MM5). Everything else is BYOK-only. Every name here must
#: be ``data``-class (:func:`assert_pooled_providers_are_data`, run at import): an identity provider added
#: to this table would hand the operator's sending account to every tenant.
_POOLED_PROVIDERS = {
    "rocketreach": ("rocketreach_key_source", "rocketreach_monthly_cap"),
    "apollo": ("apollo_key_source", "apollo_monthly_cap"),
}


def assert_pooled_providers_are_data(table: dict) -> None:
    """Refuse, at load, a pooled-provider table that names anything but a ``data``-class provider."""
    from gtm_core.provider_registry import server_class

    for provider in table:
        if server_class(provider) != "data":
            raise ValueError(
                f"{provider}: a {server_class(provider)} provider cannot be pooled on the house account"
            )


assert_pooled_providers_are_data(_POOLED_PROVIDERS)


def _workspace_scoped_config(
    base_cfg: Any,
    workspace_id: str,
    repo_root: Path,
    credentials: dict[str, str] | None = None,
    pooled: frozenset[str] = frozenset(),
) -> Any:
    """Return a copy of ``base_cfg`` with content + profile roots pinned to this
    workspace's isolated tree (``data/workspaces/<ws>/``), creating the dirs.

    This is the P3 filesystem tenant boundary: a run can only touch its own
    workspace's files. ``build_agent_options`` carries these roots into the SDK
    subprocess env, so the run's skills are scoped too — with no shared-os.environ
    race across concurrent runs. Kept as a module function so it is unit-testable
    without an SDK session.

    BYOK fail-closed (2026-09-14): the four provider fields below are ALWAYS
    overwritten from ``credentials`` — never left at ``base_cfg``'s value — even
    when the workspace has no key for a provider (``None`` in that case). Leaving
    them inherited would let a workspace with no BYOK key silently fall back to
    ``base_cfg``'s platform-level key (``SALESHANDY_API_KEY`` etc. from Doppler,
    meant for the single-tenant personal VPS): one tenant's outreach running
    through another party's real Saleshandy/Apollo/RocketReach/Syften account —
    cost, quota, and sending-reputation leakage, not just a scoping gap. With the
    field set to ``None``, ``agent/mcp_config.py``'s ``if cfg.<provider>_api_key:``
    simply omits that MCP tool from the run instead.

    Every key a tenant DOES supply for rocketreach/apollo is stamped ``byok`` in the same
    replacement (a key and its source travel together, never one without the other): their
    lookups run on their own vendor account, so the worker records the units at $0 instead of
    billing the wallet. The platform's key reaches a tenant only for a provider named in ``pooled``
    (MM5: the operator switched it on, and it is ``data``-class), only when the tenant brought no key
    of their own, and always stamped ``house``. The source field is overwritten with the key either
    way, so a platform ``house`` marker can never ride a tenant config unasked. ``saleshandy``,
    ``syften`` and the Google OAuth are ``identity``-class: the tenant's own or nothing.
    """
    import dataclasses

    from gtm_core.paths import workspace_content_root, workspace_profiles_root

    if refused := sorted(set(pooled) - set(_POOLED_PROVIDERS)):
        raise ValueError(f"cannot pool {refused}: only {sorted(_POOLED_PROVIDERS)} may be pooled")

    profiles_dir = workspace_profiles_root(workspace_id, repo_root)
    content_dir = workspace_content_root(workspace_id, repo_root)
    profiles_dir.mkdir(parents=True, exist_ok=True)
    content_dir.mkdir(parents=True, exist_ok=True)

    creds = credentials or {}
    replacements = {
        "content_root": content_dir,
        "profiles_root": profiles_dir,
        "saleshandy_api_key": creds.get("saleshandy"),
        "syften_api_key": creds.get("syften"),
        # The operator's own Google Workspace login: identity-class, never a tenant's (MM5).
        "google_oauth": {},
        # A tenant run leaves no transcript outside its own workspace tree (MM11).
        "persist_sessions": False,
    }
    for provider, (source_field, cap_field) in _POOLED_PROVIDERS.items():
        own = creds.get(provider)
        house = getattr(base_cfg, f"{provider}_api_key", None) if provider in pooled else None
        replacements[f"{provider}_api_key"] = own or house
        replacements[source_field] = "byok" if own else ("house" if house else None)
        # A cap is the allocated share of the POOLED key (tenant_run_config sets it, for a house
        # provider only): it never rides in from the platform config.
        replacements[cap_field] = None

    return dataclasses.replace(base_cfg, **replacements)


async def tenant_run_config(
    pool: Any,
    base_cfg: Any,
    workspace_id: str,
    repo_root: Path,
    *,
    credentials: dict[str, str] | None = None,
) -> Any:
    """The worker config for one tenant run: its own tree, its own keys, and its own shares.

    The ONE place a workspace's content tree and its provider share are paired. A worker carries no
    workspace id — its identity is the ledger path it counts against — so the share it enforces and
    the tree it counts must be derived from the same ``workspace_id`` here, in one call, not handed
    over as two arguments a caller could pair wrongly. A provider whose key source is ``house`` gets
    the share allocated to THIS workspace; a provider with no share (or no pool) carries no cap, which
    a house worker reads as a share of zero. A tenant on its own key (``byok``) touches no pool.
    """
    import dataclasses

    from gtm_core.provider_registry import pooled_providers

    from .services import provider_pool

    # Read (and so validated) even with no pool: a switch that pools an identity provider is refused
    # at load, whether or not this particular run could have used it.
    switched_on = pooled_providers()
    # Without a database there is no share to enforce, and a house key with no quota is unbounded spend.
    pooled = switched_on if pool is not None else frozenset()
    cfg = _workspace_scoped_config(
        base_cfg, workspace_id, repo_root, credentials=credentials, pooled=pooled
    )
    house = [
        provider
        for provider, (source_field, _cap) in _POOLED_PROVIDERS.items()
        if getattr(cfg, source_field, None) == "house"
    ]
    if not house:
        return cfg
    shares = await provider_pool.resolve_provider_shares(pool, workspace_id, house)
    # A zero share is carried as 0 and agent/mcp_config.py emits no cap env for it: absent, to a house
    # worker, IS a share of zero. (One place decides that — the worker env builder.)
    caps = {_POOLED_PROVIDERS[p][1]: shares.get(p, 0) for p in house}
    return dataclasses.replace(cfg, **caps)


# Strong refs to in-flight pack-run metering writes (fire-and-forget create_task
# otherwise holds no reference) — module-level because pack runs build sinks per
# run, not per warm session.
_PACK_METER_TASKS: set[asyncio.Task] = set()


def make_pg_usage_sink(pool: Any, workspace_id: str, run_id: str, agent_id: str | None = None):
    """A CostRecord sink for pack-mode runs: RLS-scoped INSERT into cost_records.

    Peer of :meth:`_BackendSession._meter_brain`, but PRESERVES the record's
    ``stage`` (the pack node id) so per-node cost rollups work — the prompt-mode
    sink stamps stage="brain" because a prompt run has no node structure.
    ``agent_id`` (A4) attributes every row to the acting agent.
    Best-effort: a metering failure must never fail a stage.
    """

    def _sink(rec: Any) -> None:
        from dataclasses import replace

        full = replace(
            rec, runtime="backend", workspace_id=workspace_id, run_id=run_id, agent_id=agent_id
        )

        async def _write() -> None:
            from gtm_core.metering import PgSink, ameter

            from .database import workspace_scope

            try:
                async with workspace_scope(pool, workspace_id) as conn:
                    await ameter(full, sink=PgSink(pool, "cost_records"), conn=conn)
            except Exception:  # noqa: BLE001 — metering is best-effort
                pass  # nosec B110 — intentional best-effort swallow

        try:
            task = asyncio.create_task(_write())
            _PACK_METER_TASKS.add(task)
            task.add_done_callback(_PACK_METER_TASKS.discard)
        except RuntimeError:
            pass  # no running loop

    return _sink


def _default_max_warm_sessions(override: int | None) -> int:
    if override is not None:
        return max(0, int(override))
    if (
        os.environ.get("GTM_RUN_ISOLATION") == "container"
        and "GTM_MAX_WARM_SESSIONS" not in os.environ
    ):
        return 0
    raw = os.environ.get("GTM_MAX_WARM_SESSIONS", "8")
    return max(0, int(raw)) if raw.lstrip("-").isdigit() else 8


class BackendSessionStore:
    """Session cache keyed by (workspace_id, profile_name, skill-scope)."""

    def __init__(
        self, repo_root: Path | None = None, *, max_warm_sessions: int | None = None
    ) -> None:
        self._repo_root = repo_root if repo_root is not None else Path.cwd()
        self._max_warm_sessions = _default_max_warm_sessions(max_warm_sessions)
        self._sessions: dict[Any, _BackendSession] = {}
        self._in_use: dict[Any, set[_BackendSession]] = {}
        self._evict_epoch: dict[str, int] = {}
        self._lock = asyncio.Lock()
        # W4 measurement (evidence for or against warm-session retirement): runs that reused a kept-warm
        # session vs runs that had to start one. Per-process; the per-run record carries the same flag.
        self._warm_hits = 0
        self._cold_starts = 0

    @property
    def warm_stats(self) -> dict[str, int]:
        return {"warm_hits": self._warm_hits, "cold_starts": self._cold_starts}

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def _workspace_epoch(self, workspace_id: str) -> int:
        return self._evict_epoch.get(workspace_id, 0)

    def _checkout_in_use(self, key: Any, session: _BackendSession) -> int:
        self._in_use.setdefault(key, set()).add(session)
        return self._evict_epoch.get(key[0], 0)

    def _discard_in_use(self, key: Any, session: _BackendSession) -> None:
        if (bucket := self._in_use.get(key)) is not None:
            bucket.discard(session)
            if not bucket:
                self._in_use.pop(key, None)

    async def _return_session(self, key: Any, session: _BackendSession, *, epoch: int) -> bool:
        to_close: list[_BackendSession] = []
        returned = False
        async with self._lock:
            self._discard_in_use(key, session)
            if (
                self._evict_epoch.get(key[0], 0) != epoch
                or self._max_warm_sessions <= 0
                or key in self._sessions
            ):
                to_close.append(session)
            else:
                session.touch()
                self._sessions[key] = session
                returned = True
                while len(self._sessions) > self._max_warm_sessions:
                    lru_k, lru_s = min(self._sessions.items(), key=lambda kv: kv[1].last_used)
                    self._sessions.pop(lru_k, None)
                    to_close.append(lru_s)
        for s in to_close:
            await s.close()
        return returned

    async def run(
        self,
        pool: Any,
        workspace_id: str,
        profile_name: str,
        prompt: str,
        run_id: str,
        agent_id: str | None = None,
        *,
        allowed_skills: frozenset[str] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Run a prompt in an isolated session for (workspace_id, profile_name)."""
        key = (workspace_id, profile_name, _skills_key(allowed_skills))
        async with self._lock:
            session = self._sessions.pop(key, None)
            epoch = self._evict_epoch.get(workspace_id, 0)
            if session is not None:
                self._checkout_in_use(key, session)
        if session is not None and not session.connected:
            async with self._lock:
                self._discard_in_use(key, session)
            await session.close()
            session = None
        warm = session is not None
        if warm:
            self._warm_hits += 1
        else:
            self._cold_starts += 1
        if session is None:
            session = _BackendSession(
                self._repo_root,
                profile_name,
                pool=pool,
                workspace_id=workspace_id,
                allowed_skills=allowed_skills,
            )
            async with self._lock:
                epoch = self._checkout_in_use(key, session)
            try:
                await session.connect()
            except Exception:
                async with self._lock:
                    self._discard_in_use(key, session)
                await session.close()
                raise

        session.warm_hit = warm
        healthy = False
        try:
            kwargs = {"agent_id": agent_id} if agent_id is not None else {}
            async for chunk in session.run(prompt, run_id, **kwargs):
                yield chunk
            # An interrupted/cancelled session is closed, never returned to the warm pool (F8): the next run
            # on this workspace must not inherit a half-finished turn.
            healthy = session.connected and not getattr(session, "poisoned", False)
        finally:
            if healthy:
                await self._return_session(key, session, epoch=epoch)
            else:
                async with self._lock:
                    self._discard_in_use(key, session)
                await session.close()

    async def interrupt_run(self, run_id: str) -> bool:
        """Interrupt the in-flight turn of the session executing ``run_id`` (F8). False if none is.

        Matches on the run a checked-out session is currently working on, so cancelling one run can never
        interrupt another run's session. The session is marked poisoned: it is closed, not pooled, afterwards.
        """
        async with self._lock:
            target = next(
                (
                    s
                    for bucket in self._in_use.values()
                    for s in bucket
                    if getattr(s, "_current_run_id", None) == run_id
                ),
                None,
            )
        if target is None:
            return False
        return bool(await target.interrupt())

    async def close_idle(self) -> None:
        """Close and evict sessions idle for more than IDLE_SECONDS."""
        cutoff = time.monotonic() - IDLE_SECONDS
        async with self._lock:
            evicted = [
                self._sessions.pop(k)
                for k, s in list(self._sessions.items())
                if s.last_used < cutoff
            ]
        for session in evicted:
            await session.close()

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values()) + [s for b in self._in_use.values() for s in b]
            self._sessions.clear()
            self._in_use.clear()
        for session in sessions:
            await session.close()

    async def evict_workspace(self, workspace_id: str, *, close_in_use: bool = True) -> int:
        """Evict sessions for a workspace and bump its epoch (R10)."""
        evicted: list[_BackendSession] = []
        async with self._lock:
            self._evict_epoch[workspace_id] = self._evict_epoch.get(workspace_id, 0) + 1
            for k in [k for k in self._sessions if k[0] == workspace_id]:
                evicted.append(self._sessions.pop(k))
            if close_in_use:
                for k in [k for k in self._in_use if k[0] == workspace_id]:
                    evicted.extend(self._in_use.pop(k))
        for session in evicted:
            await session.close()
        return len(evicted)


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
        allowed_skills: frozenset[str] | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._profile = profile_name
        # This session's skill scope, fixed for its lifetime (connect() bakes it into
        # the SDK options AND the permission callback). None ⇒ unscoped.
        self._allowed_skills = allowed_skills
        self._pool = pool
        self._workspace_id = workspace_id
        self._current_run_id: str | None = None
        self._current_agent_id: str | None = None  # A4 attribution, per-run like run_id
        self._agent = None  # agent.session.AgentSession, created in connect()
        self.connected = False
        self.poisoned = False  # set when a turn was interrupted: close it, never pool it
        self.last_used = time.monotonic()
        # Strong refs to in-flight metering writes so the event loop can't GC them
        # mid-flight (fire-and-forget create_task otherwise holds no reference).
        self._meter_tasks: set[asyncio.Task] = set()
        # W4 measurement: was this run served by a session the store had kept warm, and how many runs
        # has this session served (a warm session's context grows with each).
        self.warm_hit = False
        self._runs_served = 0

    def _emit_context_metrics(self, run_id: str | None, agent_id: str | None) -> None:
        """One structured log line per run: what the context is made of and how far this run grew it.

        Ids only (no prompt, no content). Logged, not stored: a Postgres column or a ledger would be a
        schema decision this measurement phase does not need to make.
        """
        rec = run_context_record(
            run_id=run_id,
            workspace_id=self._workspace_id,
            agent_id=agent_id,
            profile=self._profile,
            warm_hit=self.warm_hit,
            session_runs=self._runs_served,
            tally=self._agent.run_tally.as_dict(),
            context=self._agent.context_summary,
        )
        try:
            _METRICS_LOG.info("run_context_metrics %s", json.dumps(rec, sort_keys=True))
        except Exception:  # noqa: BLE001 — a measurement must never break a run
            pass  # nosec B110 — intentional best-effort swallow

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
            agent_id=self._current_agent_id,
        )

        async def _write() -> None:
            from gtm_core.metering import PgSink, ameter

            from .database import workspace_scope

            try:
                async with workspace_scope(self._pool, self._workspace_id) as conn:
                    await ameter(full, sink=PgSink(self._pool, "cost_records"), conn=conn)
            except Exception:  # noqa: BLE001 — metering is best-effort
                pass  # nosec B110 — intentional best-effort swallow

        try:
            task = asyncio.create_task(_write())
            self._meter_tasks.add(task)  # keep a ref until it completes
            task.add_done_callback(self._meter_tasks.discard)
        except RuntimeError:
            pass  # no running loop (shouldn't happen on the backend stream path)

    async def connect(self) -> None:
        from agent.config import Config
        from agent.denial_log import make_denial_sink
        from agent.path_confinement import Confinement
        from agent.permissions import make_headless_can_use_tool
        from agent.session import AgentSession

        cfg = Config.from_env(repo_root=self._repo_root)
        if self._workspace_id:
            credentials = None
            if self._pool:
                from .services.integrations import get_workspace_credentials

                credentials = await get_workspace_credentials(self._pool, self._workspace_id)
            cfg = await tenant_run_config(
                self._pool, cfg, self._workspace_id, self._repo_root, credentials=credentials
            )
        self._agent = AgentSession(
            cfg,
            self._profile,
            # Denials land in the workspace-scoped denials.jsonl (P0-1) — same
            # fail-closed policy as before, now with a queryable audit record.
            # The scope is passed here as WELL as to AgentSession: this callback is
            # explicit, so build_agent_options' default-callback branch (which would
            # otherwise carry the scope) never runs. Without both, prompt mode would
            # get only the SDK `skills=` context filter and lose the defence-in-depth
            # deny — the pair is the point (SECURITY-SELF-ASSESSMENT §7 item 12).
            can_use_tool=make_headless_can_use_tool(
                on_deny=make_denial_sink(cfg, self._profile, "backend"),
                allowed_skills=self._allowed_skills,
                # MM9: a tenant's warm session may touch only its own workspace's tree. A session
                # with no workspace (the single-tenant VPS) is unconfined, as before.
                confine=(
                    Confinement.for_workspace(self._workspace_id, self._repo_root)
                    if self._workspace_id
                    else None
                ),
            ),
            usage_sink=self._meter_brain,
            allowed_skills=self._allowed_skills,
            measure_context=True,
        )
        await self._agent.connect()
        self.connected = True

    async def run(
        self, prompt: str, run_id: str | None = None, agent_id: str | None = None
    ) -> AsyncGenerator[str, None]:
        if not self.connected or self._agent is None:
            raise RuntimeError("session not connected")
        self._current_run_id = run_id
        self._current_agent_id = agent_id
        self._runs_served += 1
        async for text in self._agent.run(prompt):
            yield text
        self._emit_context_metrics(run_id, agent_id)

    async def interrupt(self) -> bool:
        """Interrupt this session's in-flight turn and poison it. False when it has nothing running."""
        if self._agent is None:
            return False
        stopped = await self._agent.interrupt()
        if stopped:
            self.poisoned = True
        return stopped

    async def close(self) -> None:
        # Let in-flight cost writes finish before tearing down the pool/agent, so
        # the final paid call of a session is not lost against the monthly cap.
        # Best-effort + bounded: metering must never hang shutdown.
        if self._meter_tasks:
            try:
                await asyncio.wait(set(self._meter_tasks), timeout=5)
            except Exception:  # noqa: BLE001 — draining is best-effort
                pass  # nosec B110 — intentional best-effort swallow
        if self._agent is not None:
            try:
                await self._agent.close()
            except Exception:
                pass  # nosec B110 — intentional best-effort swallow
        self.connected = False
        self._agent = None
