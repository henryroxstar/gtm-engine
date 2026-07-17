"""Unit tests for `agent.ledgers.Ledgers` (spec §10, §13) — SDK-INDEPENDENT.

The ledger is pure stdlib (json / pathlib / datetime). These tests point
`Config.content_root` at pytest's `tmp_path` (via the `cfg` fixture in conftest.py) so they
never touch the real, gitignored `content/` volume.

Locked interface under test (class Ledgers(cfg, profile)):
  append_history(record: dict)
  append_cost(record: dict)
  write_run_manifest(manifest: dict) -> Path
  month_cost_total(year_month: str | None = None) -> float
  over_monthly_cap(cap_usd: float) -> bool

State layout (under content/<profile>/): history.jsonl, costs.jsonl, runs/<run_id>.json.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

# Defer imports so a missing sibling module skips this file rather than collection-erroring.
pytest.importorskip("agent.ledgers", reason="agent.ledgers not built yet (component 0.12)")
pytest.importorskip("agent.config", reason="agent.config not built yet (component 0.12)")

from agent.ledgers import Ledgers  # noqa: E402

PROFILE = "example"


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── append_history ────────────────────────────────────────────────────────────


def test_append_history_writes_jsonl_and_creates_dirs(cfg):
    led = Ledgers(cfg, PROFILE)
    led.append_history({"event": "run_started", "skill": "content-radar"})
    led.append_history({"event": "run_finished", "skill": "content-radar"})

    history = cfg.content_root / PROFILE / "history.jsonl"
    assert history.is_file(), "append_history must create content/<profile>/history.jsonl"

    rows = _read_jsonl(history)
    assert len(rows) == 2
    assert rows[0]["event"] == "run_started"
    assert rows[1]["event"] == "run_finished"


# ── append_cost + month_cost_total ────────────────────────────────────────────


def test_append_cost_and_month_total_for_explicit_month(cfg):
    led = Ledgers(cfg, PROFILE)
    ym = "2026-06"
    # `ts` is the ledger's timestamp field — month_cost_total filters on its YYYY-MM prefix.
    # Supplying `ts` explicitly makes the rollup deterministic (no dependence on write time;
    # an omitted `ts` is auto-stamped with the current UTC time, which would defeat the test).
    led.append_cost({"tool": "firecrawl", "cost_usd": 1.50, "ts": f"{ym}-01T10:00:00Z"})
    led.append_cost({"tool": "vibe", "cost_usd": 2.25, "ts": f"{ym}-14T08:30:00Z"})
    # A different month must NOT count toward June's total.
    led.append_cost({"tool": "vibe", "cost_usd": 99.0, "ts": "2026-05-31T23:59:00Z"})

    costs = cfg.content_root / PROFILE / "costs.jsonl"
    assert costs.is_file()
    assert len(_read_jsonl(costs)) == 3

    total = led.month_cost_total(ym)
    assert isinstance(total, float)
    assert total == pytest.approx(3.75)


def test_month_cost_total_defaults_to_current_month(cfg):
    led = Ledgers(cfg, PROFILE)
    this_month = dt.datetime.now(dt.UTC).strftime("%Y-%m")
    # No `ts` → the ledger auto-stamps the current UTC time, which lands in this_month.
    led.append_cost({"tool": "deepseek", "cost_usd": 0.40})

    # Called with no arg → uses the current YYYY-MM.
    assert led.month_cost_total() == pytest.approx(0.40)
    assert led.month_cost_total(this_month) == pytest.approx(0.40)


def test_month_cost_total_zero_when_no_costs(cfg):
    led = Ledgers(cfg, PROFILE)
    # No costs written yet — must be 0.0, not an error / missing file.
    assert led.month_cost_total("2026-06") == 0.0


# ── over_monthly_cap ──────────────────────────────────────────────────────────


def test_over_monthly_cap_threshold(cfg):
    led = Ledgers(cfg, PROFILE)
    # Two charges this month totalling 60.00 (no `ts` → auto-stamped to the current month,
    # which is what over_monthly_cap() always evaluates).
    led.append_cost({"tool": "higgsfield", "cost_usd": 40.0})
    led.append_cost({"tool": "elevenlabs", "cost_usd": 20.0})

    # over_monthly_cap uses >= (at-or-over the cap is "over" — the hard stop before a metered call).
    assert led.over_monthly_cap(100.0) is False  # 60 < 100
    assert led.over_monthly_cap(60.0) is True  # 60 >= 60 (boundary)
    assert led.over_monthly_cap(50.0) is True  # 60 >= 50


# ── write_run_manifest ────────────────────────────────────────────────────────


def test_write_run_manifest_returns_path_and_roundtrips(cfg):
    led = Ledgers(cfg, PROFILE)
    manifest = {
        "run_id": "r-20260614-0001",
        "trigger": "cron",
        "profile": PROFILE,
        "stages": [{"name": "radar", "status": "ok", "outputs": ["digest.md"]}],
    }
    path = led.write_run_manifest(manifest)
    path = Path(path)

    assert path.is_file(), "write_run_manifest must return a real written path"
    # Locked layout: runs/<run_id>.json under the per-profile content tree.
    assert path.parent == cfg.content_root / PROFILE / "runs"
    assert path.name == "r-20260614-0001.json"

    loaded = json.loads(path.read_text())
    assert loaded["run_id"] == "r-20260614-0001"
    assert loaded["stages"][0]["status"] == "ok"


# ── published_content_hashes (durable publish idempotency source) ─────────────


def test_published_content_hashes_collects_only_published_events(cfg):
    led = Ledgers(cfg, PROFILE)
    # A successful publish writes a "published" event carrying the content hash.
    led.append_history({"event": "published", "platform": "linkedin", "content_sha256": "aaa"})
    led.append_history({"event": "published", "platform": "linkedin", "content_sha256": "bbb"})
    # Non-publish events and failed attempts must NOT count toward the dedup set.
    led.append_history({"event": "radar_complete", "clusters": 5})
    led.append_history({"event": "publish_failed", "content_sha256": "ccc"})

    hashes = led.published_content_hashes()
    assert hashes == {"aaa", "bbb"}


def test_published_content_hashes_empty_when_no_history(cfg):
    led = Ledgers(cfg, PROFILE)
    assert led.published_content_hashes() == set()


# ── per-profile isolation ─────────────────────────────────────────────────────


def test_ledgers_are_isolated_per_profile(cfg):
    a = Ledgers(cfg, "example")
    b = Ledgers(cfg, "example2")
    a.append_history({"event": "only_example"})

    assert (cfg.content_root / "example" / "history.jsonl").is_file()
    # example2's tree must be untouched — state is namespaced per profile.
    assert not (cfg.content_root / "example2" / "history.jsonl").exists()
    # And a's cost total must not leak into b.
    assert b.month_cost_total("2026-06") == 0.0
