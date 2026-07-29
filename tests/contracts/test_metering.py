"""Contract + regression tests for the unified metering layer (gtm_core.metering).

Covers: the canonical CostRecord↔JSONL shape (back-compat with the existing readers),
the brain-cost footgun (rates must follow the resolved model), the budget-guard fail
policy (deny at cap / hard ceiling, fail-open on read error), and the PgSink projection
+ async guard over a fake pool (no live Postgres).
"""

from __future__ import annotations

import asyncio
import functools
import types

import pytest

from gtm_core.metering import (
    CostRecord,
    JsonlSink,
    PgSink,
    acheck_budget,
    ameter,
    brain_cost_usd,
    check_budget,
    meter,
    resolve_rates,
)


def _run(coro_fn):
    """Drive an async test with asyncio.run (repo convention — no pytest-asyncio plugin)."""

    @functools.wraps(coro_fn)
    def wrapper(*a, **k):
        return asyncio.run(coro_fn(*a, **k))

    return wrapper


# ── fakes ─────────────────────────────────────────────────────────────────────


class _StubSink:
    """Sync Sink stub for check_budget policy tests."""

    def __init__(self, total: float | None = None, raise_: bool = False) -> None:
        self._total = total
        self._raise = raise_
        self.writes: list[CostRecord] = []

    def write(self, rec: CostRecord) -> None:
        self.writes.append(rec)

    def month_total(self, scope: str | None = None) -> float:
        if self._raise:
            raise RuntimeError("ledger unreadable")
        return self._total or 0.0


class _FakeConn:
    def __init__(self, fetchrow_result=None, raise_=False) -> None:
        self._fetchrow_result = fetchrow_result
        self._raise = raise_
        self.executed: list[tuple] = []

    async def fetchrow(self, sql, *args):
        if self._raise:
            raise RuntimeError("db down")
        return self._fetchrow_result

    async def execute(self, sql, *args):
        if self._raise:
            raise RuntimeError("db down")
        self.executed.append((sql, args))


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _Ctx()


# ── CostRecord ↔ JSONL back-compat ────────────────────────────────────────────


def test_to_jsonl_dict_emits_reader_keys():
    """source→'tool', cache fields preserved, Nones dropped — the shape readers consume."""
    rec = CostRecord(
        runtime="vps",
        source="brain",
        cost_usd=0.05,
        model_or_sku="claude-sonnet-4-6",
        profile="example2",
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=200,
        cache_read_input_tokens=80,
    )
    d = rec.to_jsonl_dict()
    assert d["tool"] == "brain"  # /cost-breakdown groups on 'tool'
    assert d["cost_usd"] == 0.05  # month_cost_total sums 'cost_usd'
    assert d["model"] == "claude-sonnet-4-6"
    assert d["cache_creation_input_tokens"] == 200
    # Unset optional fields are dropped (no run_id/stage/units noise).
    assert "run_id" not in d and "stage" not in d and "units" not in d


def test_jsonlsink_roundtrip_summed_by_month_total(tmp_path):
    """A record written via JsonlSink is summed by the same reader the dashboard uses."""
    cfg = types.SimpleNamespace(content_root=tmp_path)
    sink = JsonlSink(cfg, "example2")
    meter(CostRecord(runtime="vps", source="brain", cost_usd=0.10), sink=sink)
    meter(CostRecord(runtime="vps", source="vision-worker", cost_usd=0.02), sink=sink)
    # Ledgers.month_cost_total reads the same file.
    from gtm_core.ledgers import Ledgers

    assert round(Ledgers(cfg, "example2").month_cost_total(), 4) == 0.12


def test_legacy_and_new_lines_both_sum(tmp_path):
    """A legacy worker-shaped append_cost line and a new meter() line both sum.

    Guards the decision to leave the 5 existing writers on append_cost untouched.
    """
    from gtm_core.ledgers import Ledgers

    cfg = types.SimpleNamespace(content_root=tmp_path)
    led = Ledgers(cfg, "example2")
    # Legacy shape (what the deepseek/vision workers emit today):
    led.append_cost({"tool": "deepseek-worker", "op": "draft_post", "cost_usd": 0.008})
    # New shape via the contract:
    meter(
        CostRecord(runtime="vps", source="brain", cost_usd=0.05),
        sink=JsonlSink(cfg, "example2"),
    )
    assert round(led.month_cost_total(), 4) == 0.058


# ── brain-cost footgun: rates follow the resolved model ───────────────────────


def test_brain_cost_cache_aware_formula():
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 200,
        "cache_read_input_tokens": 80,
    }
    # (100 + 200*1.25 + 80*0.10)/1000*0.003 + 50/1000*0.015 = 0.001824
    assert brain_cost_usd(usage, input_usd_per_1k=0.003, output_usd_per_1k=0.015) == 0.001824


def test_haiku_rates_bill_cheaper_than_sonnet():
    """A brain_plan→brain_cheap (Sonnet→Haiku) fallback must bill Haiku rates, not Sonnet's."""
    usage = {"input_tokens": 1000, "output_tokens": 1000}
    sonnet = brain_cost_usd(usage, input_usd_per_1k=0.003, output_usd_per_1k=0.015)
    haiku = brain_cost_usd(usage, input_usd_per_1k=0.001, output_usd_per_1k=0.005)
    assert haiku < sonnet


def test_resolve_rates_registry_then_env_then_default(monkeypatch):
    spec = types.SimpleNamespace(input_usd_per_1k=0.001, output_usd_per_1k=0.005)
    monkeypatch.delenv("X_IN", raising=False)
    monkeypatch.delenv("X_OUT", raising=False)
    # registry spec wins when no env override
    assert resolve_rates(spec, env_input="X_IN", env_output="X_OUT") == (0.001, 0.005)
    # env override wins over the spec
    monkeypatch.setenv("X_IN", "0.009")
    assert resolve_rates(spec, env_input="X_IN", env_output="X_OUT")[0] == 0.009
    # no spec rate + no env → list-pricing default
    bare = types.SimpleNamespace(input_usd_per_1k=None, output_usd_per_1k=None)
    monkeypatch.delenv("X_IN", raising=False)
    assert resolve_rates(bare, env_input="X_IN", env_output="X_OUT") == (0.003, 0.015)


# ── check_budget policy (sync / VPS) ──────────────────────────────────────────


def test_check_budget_allows_under_cap():
    assert check_budget("example2", 10.0, sink=_StubSink(total=5.0)) is True


def test_check_budget_denies_at_or_over_cap():
    assert check_budget("example2", 10.0, sink=_StubSink(total=10.0)) is False


def test_check_budget_hard_ceiling_denies_even_under_cap():
    # total 9 < cap 10 but >= hard ceiling 8 → deny (defense-in-depth on a misconfigured cap)
    assert check_budget("example2", 10.0, sink=_StubSink(total=9.0), hard_ceiling_usd=8.0) is False


def test_check_budget_fails_open_on_read_error():
    assert check_budget("example2", 10.0, sink=_StubSink(raise_=True)) is True


def test_meter_swallows_sink_errors():
    class _Boom:
        def write(self, rec):
            raise RuntimeError("nope")

        def month_total(self, scope=None):
            return 0.0

    # Must not raise — metering is best-effort.
    meter(CostRecord(runtime="vps", source="brain", cost_usd=0.1), sink=_Boom())


# ── PgSink projection + async guard (fake pool) ───────────────────────────────


def test_pgsink_rejects_unknown_table():
    with pytest.raises(ValueError, match="unknown cost table"):
        PgSink(object(), "evil_table")


def test_pgsink_row_projection_cost_records():
    rec = CostRecord(
        runtime="backend",
        source="brain",
        cost_usd=0.05,
        model_or_sku="claude-sonnet-4-6",
        profile="example2",
        workspace_id="ws-1",
        run_id="run-9",
        stage="brain",
        input_tokens=100,
        output_tokens=50,
    )
    row = PgSink(object(), "cost_records")._row_for_table(rec)
    # cols: workspace_id, profile_name, run_id, stage, model, input_tokens, output_tokens, cost_usd
    assert row == ("ws-1", "example2", "run-9", "brain", "claude-sonnet-4-6", 100, 50, 0.05)


def test_pgsink_row_projection_mcp_calls():
    rec = CostRecord(
        runtime="mcp",
        source="draft_post",
        cost_usd=0.008,
        op="draft_post",
        model_or_sku="deepseek-v4-flash",
        profile="example2",
        workspace_id="ws-1",
        api_key_id="key-1",
        input_tokens=200,
        output_tokens=80,
    )
    row = PgSink(object(), "mcp_calls")._row_for_table(rec)
    # cols: workspace_id, api_key_id, tool_name, profile_name, model, prompt_tokens, completion_tokens, cost_usd
    assert row == ("ws-1", "key-1", "draft_post", "example2", "deepseek-v4-flash", 200, 80, 0.008)


@_run
async def test_ameter_inserts_via_conn():
    conn = _FakeConn()
    rec = CostRecord(
        runtime="backend", source="brain", cost_usd=0.05, workspace_id="ws-1", run_id="r1"
    )
    await ameter(rec, sink=PgSink(object(), "cost_records"), conn=conn)
    assert len(conn.executed) == 1
    sql, args = conn.executed[0]
    assert "INSERT INTO cost_records" in sql
    assert "ws-1" in args


@_run
async def test_ameter_swallows_db_error():
    conn = _FakeConn(raise_=True)
    # best-effort: must not raise
    await ameter(
        CostRecord(runtime="backend", source="brain", cost_usd=0.05, workspace_id="ws-1"),
        sink=PgSink(object(), "cost_records"),
        conn=conn,
    )


@_run
async def test_acheck_budget_denies_over_cap():
    pool = _FakePool(_FakeConn(fetchrow_result={"cap": 10.0, "spent": 12.0}))
    assert await acheck_budget(pool, "ws-1", table="cost_records") is False


@_run
async def test_acheck_budget_allows_under_cap():
    pool = _FakePool(_FakeConn(fetchrow_result={"cap": 10.0, "spent": 3.0}))
    assert await acheck_budget(pool, "ws-1", table="cost_records") is True


@_run
async def test_acheck_budget_hard_ceiling():
    # spent 9 < cap 10 but >= 2x*? no: multiplier on cost_records default 2.0 → ceiling 20; 9<20 allow
    pool = _FakePool(_FakeConn(fetchrow_result={"cap": 4.0, "spent": 9.0}))
    # spent 9 >= cap*2 (8) → deny via hard ceiling
    assert await acheck_budget(pool, "ws-1", table="cost_records") is False


@_run
async def test_acheck_budget_no_subscription_allows():
    pool = _FakePool(_FakeConn(fetchrow_result=None))
    assert await acheck_budget(pool, "ws-1", table="cost_records") is True


@_run
async def test_acheck_budget_fails_open_on_error():
    pool = _FakePool(_FakeConn(raise_=True))
    assert await acheck_budget(pool, "ws-1", table="cost_records") is True


@_run
async def test_mcp_guard_no_hard_ceiling():
    # MCP is fail-open per-call: even far over cap*2, with multiplier=None only the soft cap applies.
    pool = _FakePool(_FakeConn(fetchrow_result={"cap": 1.0, "spent": 100.0}))
    # soft cap still denies (spent >= cap)
    assert (
        await acheck_budget(pool, "ws-1", table="mcp_calls", hard_ceiling_multiplier=None) is False
    )
    # under cap allows
    pool2 = _FakePool(_FakeConn(fetchrow_result={"cap": 100.0, "spent": 1.0}))
    assert (
        await acheck_budget(pool2, "ws-1", table="mcp_calls", hard_ceiling_multiplier=None) is True
    )
