"""Fleet executor contract, Task 4 — per-agent daily dispatch ceilings (Piece A) and
per-principal rate limiting (Piece B), backend/callers/limits.py.

No live DB and no live Redis: pool/conn are small in-memory fakes (mirrors
tests/backend/test_agents.py's ``AgentsDb`` style and tests/backend/test_callers_ports.py's
``_PoolStub``/``_AcquireCtx``), and the rate-limit pieces run against the real, tiny
``limits`` in-process ``MemoryStorage`` so bucket-sharing/independence is proven against
real strategy behavior rather than a mock of our own code.

Convention: no pytest-asyncio in this repo — async bodies run via asyncio.run(...).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

import pytest  # noqa: E402
from limits import parse  # noqa: E402
from limits.storage import MemoryStorage  # noqa: E402
from limits.strategies import STRATEGIES  # noqa: E402

from backend.agents import (  # noqa: E402
    acheck_agent_budget,
    acheck_agent_daily_cap,
    agent_today_dispatch_count,
)
from backend.callers.limits import enforce_agent_daily_cap, enforce_principal_rate  # noqa: E402
from backend.callers.ports import Refusal  # noqa: E402
from backend.callers.principal import Principal  # noqa: E402
from gtm_core.capabilities import Entitlement  # noqa: E402

WORKSPACE = str(uuid.uuid4())
AGENT_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())


def _service_principal(subject: str = "key-default", agent_id: str | None = AGENT_ID) -> Principal:
    return Principal(
        kind="service",
        subject=subject,
        workspace_id=WORKSPACE,
        entitlement=Entitlement.PRO,
        agent_id=agent_id,
        credential_id=subject,
        verifier="api_key",
    )


# ── fakes: pool/conn (mirrors test_callers_ports.py's _PoolStub/_AcquireCtx) ────────


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _PoolStub:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


class _NullTransaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def _make_conn(fetchval_return=None, fetchval_side_effect=None):
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_NullTransaction())
    conn.execute = AsyncMock(return_value=None)
    if fetchval_side_effect is not None:
        conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        conn.fetchval = AsyncMock(return_value=fetchval_return)
    return conn


def _minimal_cfg(tmp_path):
    """A cheap, real Config instance — enforce_agent_daily_cap's refusal branch calls
    _workspace_scoped_config, which does dataclasses.replace(cfg, ...) and therefore
    needs a real Config dataclass, not object() (mirrors test_callers_ports.py)."""
    from agent.config import Config

    return Config(
        repo_root=tmp_path,
        plugin_path=tmp_path / "plugin",
        profiles_root=tmp_path / "profiles",
        content_root=tmp_path / "content",
        default_profile="acme",
    )


def _agent_row(**over) -> dict:
    row = {"agent_id": AGENT_ID, "daily_dispatch_cap": 5}
    row.update(over)
    return row


# ── agent_today_dispatch_count / acheck_agent_daily_cap ─────────────────────────────


def test_agent_today_dispatch_count_returns_int_from_fetchval():
    conn = _make_conn(fetchval_return=7)
    result = asyncio.run(agent_today_dispatch_count(conn, WORKSPACE, AGENT_ID))
    assert result == 7
    conn.fetchval.assert_called_once()


def test_acheck_agent_daily_cap_no_agent_allows_without_a_db_call():
    conn = _make_conn()
    assert asyncio.run(acheck_agent_daily_cap(conn, WORKSPACE, None, 5)) is True
    conn.fetchval.assert_not_called()


def test_acheck_agent_daily_cap_no_cap_allows_without_a_db_call():
    conn = _make_conn()
    assert asyncio.run(acheck_agent_daily_cap(conn, WORKSPACE, AGENT_ID, None)) is True
    conn.fetchval.assert_not_called()


def test_acheck_agent_daily_cap_count_below_cap_allows():
    conn = _make_conn(fetchval_return=3)
    assert asyncio.run(acheck_agent_daily_cap(conn, WORKSPACE, AGENT_ID, 5)) is True


def test_acheck_agent_daily_cap_count_at_cap_refuses():
    conn = _make_conn(fetchval_return=5)
    assert asyncio.run(acheck_agent_daily_cap(conn, WORKSPACE, AGENT_ID, 5)) is False


def test_acheck_agent_daily_cap_count_above_cap_refuses():
    conn = _make_conn(fetchval_return=6)
    assert asyncio.run(acheck_agent_daily_cap(conn, WORKSPACE, AGENT_ID, 5)) is False


def test_acheck_agent_daily_cap_db_read_error_propagates():
    # "Could not count" must never read as "cap reached" (a 429 that tells a caller to
    # wait until tomorrow). The error propagates; enforce_agent_daily_cap refuses it as
    # a distinct 503.
    conn = _make_conn(fetchval_side_effect=RuntimeError("db exploded"))
    with pytest.raises(RuntimeError):
        asyncio.run(acheck_agent_daily_cap(conn, WORKSPACE, AGENT_ID, 5))


def test_acheck_agent_budget_pinning_unaffected_by_this_task():
    # Regression guard: this task must not have touched acheck_agent_budget's behavior.
    under_cap_conn = _make_conn(fetchval_return=10.0)
    assert asyncio.run(acheck_agent_budget(under_cap_conn, WORKSPACE, AGENT_ID, 20.0)) is True
    over_cap_conn = _make_conn(fetchval_return=25.0)
    assert asyncio.run(acheck_agent_budget(over_cap_conn, WORKSPACE, AGENT_ID, 20.0)) is False


# ── enforce_agent_daily_cap ──────────────────────────────────────────────────────────


def test_enforce_agent_daily_cap_noop_when_agent_row_is_none():
    pool = MagicMock()
    principal = _service_principal()
    asyncio.run(enforce_agent_daily_cap(pool, object(), principal, None, "/v1/runs"))
    pool.acquire.assert_not_called()


def test_enforce_agent_daily_cap_noop_when_agent_has_no_cap_set():
    pool = MagicMock()
    principal = _service_principal()
    row = _agent_row(daily_dispatch_cap=None)
    asyncio.run(enforce_agent_daily_cap(pool, object(), principal, row, "/v1/runs"))
    pool.acquire.assert_not_called()


def test_enforce_agent_daily_cap_under_cap_is_a_silent_noop(tmp_path):
    conn = _make_conn(fetchval_return=1)
    pool = _PoolStub(conn)
    cfg = _minimal_cfg(tmp_path)
    principal = _service_principal()
    row = _agent_row(daily_dispatch_cap=5)
    with patch("backend.callers.limits.audit.record_refusal") as mock_record:
        asyncio.run(enforce_agent_daily_cap(pool, cfg, principal, row, "/v1/runs"))
    mock_record.assert_not_called()


def test_enforce_agent_daily_cap_exceeded_raises_and_audits_workspace_scoped(tmp_path):
    conn = _make_conn(fetchval_return=5)  # count == cap -> refuse
    pool = _PoolStub(conn)
    cfg = _minimal_cfg(tmp_path)
    principal = _service_principal()
    row = _agent_row(daily_dispatch_cap=5)
    with patch("backend.callers.limits.audit.record_refusal") as mock_record:
        with pytest.raises(Refusal) as exc_info:
            asyncio.run(enforce_agent_daily_cap(pool, cfg, principal, row, "/v1/runs"))
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "agent_daily_cap_reached"
    assert exc_info.value.principal is principal
    mock_record.assert_called_once()
    # The config passed to record_refusal must be workspace-scoped, never the raw
    # global cfg — mirrors test_require_principal_paused_agent_refuses_and_records_denial
    # in tests/backend/test_callers_ports.py.
    scoped_cfg = mock_record.call_args.args[0]
    assert scoped_cfg is not cfg
    assert scoped_cfg.content_root != cfg.content_root
    assert WORKSPACE in str(scoped_cfg.content_root)
    assert mock_record.call_args.args[1] == "_admission"
    assert mock_record.call_args.args[2] is principal
    assert mock_record.call_args.args[3] == "/v1/runs"
    assert mock_record.call_args.args[4] == "agent_daily_cap_reached"


def test_enforce_agent_daily_cap_db_error_is_a_distinct_503_and_audited(tmp_path):
    conn = _make_conn(fetchval_side_effect=RuntimeError("db exploded"))
    pool = _PoolStub(conn)
    principal = _service_principal()
    with patch("backend.callers.limits.audit.record_refusal") as mock_record:
        with pytest.raises(Refusal) as exc_info:
            asyncio.run(
                enforce_agent_daily_cap(
                    pool,
                    _minimal_cfg(tmp_path),
                    principal,
                    _agent_row(daily_dispatch_cap=5),
                    "/v1/runs",
                )
            )
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "daily_cap_unavailable"
    assert mock_record.call_args.args[4] == "daily_cap_unavailable"


# ── enforce_principal_rate ───────────────────────────────────────────────────────────


def _fresh_memory_limiter(enabled: bool = True):
    real = STRATEGIES["fixed-window"](MemoryStorage())
    return SimpleNamespace(enabled=enabled, limiter=real)


def test_enforce_principal_rate_two_service_principals_get_independent_buckets():
    fake_limiter = _fresh_memory_limiter()
    p1 = _service_principal(subject="key-aaa")
    p2 = _service_principal(subject="key-bbb")
    with (
        patch("backend.callers.limits.limiter", fake_limiter),
        patch("backend.callers.limits._principal_rate_item", parse("1/minute")),
    ):
        enforce_principal_rate(p1)  # consumes p1's one slot
        with pytest.raises(Refusal) as exc_info:
            enforce_principal_rate(p1)  # p1 exhausted
        assert exc_info.value.status_code == 429
        assert exc_info.value.code == "principal_rate_limited"
        # p2 is a DIFFERENT credential -> its own, still-fresh bucket
        enforce_principal_rate(p2)


def test_enforce_principal_rate_same_principal_shares_one_bucket_across_different_ips():
    # The limit is keyed on the credential alone: the dependency receives the principal
    # and never the request, so a key calling from a second IP lands in the same bucket.
    import inspect

    assert list(inspect.signature(enforce_principal_rate).parameters) == ["principal"]
    fake_limiter = _fresh_memory_limiter()
    p = _service_principal(subject="key-shared")
    with (
        patch("backend.callers.limits.limiter", fake_limiter),
        patch("backend.callers.limits._principal_rate_item", parse("1/minute")),
    ):
        enforce_principal_rate(p)
        with pytest.raises(Refusal):
            enforce_principal_rate(p)


def test_enforce_principal_rate_user_principal_is_never_limited():
    fake_limiter = _fresh_memory_limiter()
    hit_spy = MagicMock(wraps=fake_limiter.limiter.hit)
    fake_limiter.limiter.hit = hit_spy
    user = Principal(
        kind="user", subject=USER_ID, workspace_id=WORKSPACE, entitlement=Entitlement.PRO
    )
    with (
        patch("backend.callers.limits.limiter", fake_limiter),
        patch("backend.callers.limits._principal_rate_item", parse("1/minute")),
    ):
        for _ in range(5):
            enforce_principal_rate(user)  # never raises
    hit_spy.assert_not_called()  # a user never even consumes a slot


def test_enforce_principal_rate_respects_limiter_disabled():
    fake_limiter = SimpleNamespace(enabled=False, limiter=MagicMock())
    p = _service_principal(subject="key-disabled")
    with patch("backend.callers.limits.limiter", fake_limiter):
        for _ in range(10):
            enforce_principal_rate(p)  # never raises
    fake_limiter.limiter.hit.assert_not_called()
