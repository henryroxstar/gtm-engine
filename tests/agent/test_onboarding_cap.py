"""MM11 (SECURITY-SELF-ASSESSMENT §6 item 23): onboarding spend is bounded, metered, and cannot be unbounded.

Three holes shared one cause — nothing was ever recorded. ``GTM_ONBOARDING_CAP_USD`` was compared against
the sum of ``onboard.*`` rows in ``content/_system/costs.jsonl``, and no code wrote one, so the cap read
$0 for ever; an UNSET cap skipped every check; and the extraction call carried no budget ceiling. A tenant
can start onboarding by itself (it is the first thing a new workspace does), so this is a path for
strangers to spend the platform's money.

What is pinned, each with a positive twin (a refusal that is also a broken path would pass otherwise):

* an unset, negative or non-finite cap REFUSES before any paid call (crawl, extraction, re-extraction);
  a cap of zero is a real cap that is already reached;
* a real extraction call records a row, the next call sees it, and the cap eventually trips;
* the call is held to the month's remaining budget (``max_budget_usd``), and a call whose cost the
  provider never reported is charged at that ceiling, not at zero;
* a spend ledger that cannot be totalled raises instead of reading as $0.

Not covered, said where it can be found: the Firecrawl crawl itself is not priced (no per-page price
exists in ``provider_costs.toml``), so only the brain calls add to the total; the cap is checked before a
crawl but a crawl adds nothing to it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("claude_agent_sdk")
import importlib  # noqa: E402

from claude_agent_sdk import ResultMessage  # noqa: E402

extract_mod = importlib.import_module(
    "agent.onboard.extract"
)  # the package re-exports a function of the same name
from gtm_core import ingest as ingest_mod  # noqa: E402
from gtm_core.ingest import (  # noqa: E402
    OnboardingCapReachedError,
    OnboardingCapUnsetError,
    _onboarding_month_spend,
    record_onboarding_spend,
    require_onboarding_headroom,
)

REPO = Path(__file__).resolve().parents[2]


def _cfg(tmp_path, cap, key="fc-key"):
    return SimpleNamespace(
        content_root=tmp_path / "content",
        profiles_root=tmp_path / "profiles",
        plugin_path=REPO / "plugin",
        repo_root=REPO,
        firecrawl_api_key=key,
        onboarding_cap_usd=cap,
        model=None,
    )


# ── the headroom function ─────────────────────────────────────────────────────


@pytest.mark.parametrize("cap", [None, -1.0, float("nan"), float("inf")])
def test_a_cap_that_is_unset_or_unusable_refuses_rather_than_meaning_uncapped(tmp_path, cap):
    with pytest.raises(OnboardingCapUnsetError, match="GTM_ONBOARDING_CAP_USD"):
        require_onboarding_headroom(_cfg(tmp_path, cap), "extract a profile")


def test_the_unset_refusal_is_also_a_cap_reached_refusal_so_every_existing_handler_catches_it(
    tmp_path,
):
    assert issubclass(OnboardingCapUnsetError, OnboardingCapReachedError)


def test_a_cap_of_zero_is_a_real_cap_that_is_already_reached(tmp_path):
    with pytest.raises(OnboardingCapReachedError) as err:
        require_onboarding_headroom(_cfg(tmp_path, 0.0), "extract a profile")
    assert not isinstance(err.value, OnboardingCapUnsetError)


def test_headroom_is_the_cap_less_what_has_been_spent_this_month(tmp_path):
    cfg = _cfg(tmp_path, 5.0)
    assert require_onboarding_headroom(cfg, "x") == 5.0
    record_onboarding_spend(cfg, "onboard.extract", 1.25)
    assert require_onboarding_headroom(cfg, "x") == pytest.approx(3.75)
    record_onboarding_spend(cfg, "onboard.extract", 3.75)
    with pytest.raises(OnboardingCapReachedError):
        require_onboarding_headroom(cfg, "x")


def test_only_onboarding_rows_count_and_only_this_months(tmp_path):
    cfg = _cfg(tmp_path, 5.0)
    ledger = cfg.content_root / "_system" / "costs.jsonl"
    from gtm_core.ledgers import Ledgers

    book = Ledgers(cfg, "_system")
    book.append_cost({"tool": "x", "event_type": "pipeline.run", "cost_usd": 4.0})
    book.append_cost({"tool": "x", "event_type": "onboard.extract", "cost_usd": 1.0})
    book.append_cost(
        {
            "tool": "x",
            "event_type": "onboard.extract",
            "cost_usd": 9.0,
            "ts": "2019-01-01T00:00:00Z",
        }
    )
    assert ledger.exists()
    assert _onboarding_month_spend(cfg) == 1.0


def test_a_spend_ledger_that_cannot_be_totalled_raises_instead_of_reading_as_zero(tmp_path):
    cfg = _cfg(tmp_path, 5.0)
    record_onboarding_spend(cfg, "onboard.extract", 1.0)
    ledger = cfg.content_root / "_system" / "costs.jsonl"
    ledger.write_text("{ not json\n" + ledger.read_text(), encoding="utf-8")
    with pytest.raises(Exception, match="valid JSON|spend"):
        require_onboarding_headroom(cfg, "x")


# ── the three paid entry points refuse when the cap is unset, before any paid call ──


def test_a_url_crawl_refuses_before_any_http_when_the_cap_is_unset(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("an HTTP client was built: the crawl was not refused first")

    monkeypatch.setattr(ingest_mod.httpx, "Client", boom)
    with pytest.raises(OnboardingCapUnsetError):
        ingest_mod._ingest_url("https://example.invalid", _cfg(tmp_path, None))


def test_a_url_crawl_with_a_cap_does_reach_the_provider(tmp_path, monkeypatch):
    """The twin: with a cap the same call is NOT stopped at the gate, so the refusal above is the cap."""

    class _Sentinel(Exception):
        pass

    def reached(*_a, **_k):
        raise _Sentinel

    monkeypatch.setattr(ingest_mod.httpx, "Client", reached)
    with pytest.raises(_Sentinel):
        ingest_mod._ingest_url("https://example.invalid", _cfg(tmp_path, 5.0))


def test_an_extraction_refuses_before_the_model_when_the_cap_is_unset(tmp_path, monkeypatch):
    calls: list[str] = []

    async def spy(prompt, cfg):
        calls.append(prompt)
        return "{}"

    monkeypatch.setattr(extract_mod, "_run_brain_query", spy)
    with pytest.raises(OnboardingCapUnsetError):
        asyncio.run(extract_mod.extract("We build widgets.", _cfg(tmp_path, None)))
    assert calls == []


def test_a_product_re_extraction_refuses_before_the_model_when_the_cap_is_unset(
    tmp_path, monkeypatch
):
    calls: list[str] = []

    async def spy(prompt, cfg):
        calls.append(prompt)
        return "{}"

    monkeypatch.setattr(extract_mod, "_run_brain_query", spy)
    draft = {"products": [{"slug": "widget", "name": "Widget"}]}
    with pytest.raises(OnboardingCapUnsetError):
        asyncio.run(extract_mod.extract_product("widget", "more text", draft, _cfg(tmp_path, None)))
    assert calls == []


def test_an_extraction_with_a_cap_does_reach_the_model(tmp_path, monkeypatch):
    calls: list[str] = []

    async def spy(prompt, cfg):
        calls.append(prompt)
        raise RuntimeError("stop here: the gate was passed")

    monkeypatch.setattr(extract_mod, "_run_brain_query", spy)
    with pytest.raises(RuntimeError, match="gate was passed"):
        asyncio.run(extract_mod.extract("We build widgets.", _cfg(tmp_path, 5.0)))
    assert len(calls) == 1


# ── a real call is metered, held to a ceiling, and eventually refused ──────────


def _result(cost):
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        total_cost_usd=cost,
    )


def _fake_brain(monkeypatch, cost):
    """Stand in for the SDK turn; keep the options the call was made with."""
    seen: dict = {}

    async def stream(options, prompt):
        seen["options"] = options
        yield SimpleNamespace(content=[SimpleNamespace(text="{}")])
        yield _result(cost)

    monkeypatch.setattr("agent.session.stream_brain_messages", stream)
    return seen


def test_a_call_records_its_cost_so_the_cap_eventually_trips(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, 0.5)
    _fake_brain(monkeypatch, 0.3)
    asyncio.run(extract_mod._run_brain_query("p", cfg))  # 0.3 of 0.5
    asyncio.run(extract_mod._run_brain_query("p", cfg))  # still under: 0.3 < 0.5 admits it
    assert _onboarding_month_spend(cfg) == pytest.approx(0.6)
    with pytest.raises(OnboardingCapReachedError):  # the third is refused: the cap is finally real
        asyncio.run(extract_mod._run_brain_query("p", cfg))


def test_the_recorded_row_is_an_onboarding_row_with_the_reported_cost(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, 5.0)
    _fake_brain(monkeypatch, 0.25)
    asyncio.run(extract_mod._run_brain_query("p", cfg))
    rows = [
        json.loads(line)
        for line in (cfg.content_root / "_system" / "costs.jsonl").read_text().splitlines()
    ]
    assert [(r["event_type"], r["cost_usd"]) for r in rows] == [("onboard.extract", 0.25)]
    assert "cost_estimated" not in rows[0]


def test_the_call_is_held_to_the_months_remaining_budget(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, 5.0)
    record_onboarding_spend(cfg, "onboard.extract", 1.5)
    seen = _fake_brain(monkeypatch, 0.1)
    asyncio.run(extract_mod._run_brain_query("p", cfg))
    assert seen["options"].max_budget_usd == pytest.approx(3.5)


def test_a_call_whose_cost_was_never_reported_is_charged_at_its_ceiling_not_at_zero(
    tmp_path, monkeypatch
):
    cfg = _cfg(tmp_path, 5.0)
    _fake_brain(monkeypatch, None)
    asyncio.run(extract_mod._run_brain_query("p", cfg))
    rows = [
        json.loads(line)
        for line in (cfg.content_root / "_system" / "costs.jsonl").read_text().splitlines()
    ]
    assert rows[0]["cost_usd"] == 5.0 and rows[0]["cost_estimated"] is True


def test_a_call_that_fails_after_the_model_began_answering_is_still_charged(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, 5.0)

    async def stream(options, prompt):
        yield SimpleNamespace(content=[SimpleNamespace(text="{")])
        raise RuntimeError("the connection dropped mid-answer")

    monkeypatch.setattr("agent.session.stream_brain_messages", stream)
    with pytest.raises(RuntimeError, match="dropped"):
        asyncio.run(extract_mod._run_brain_query("p", cfg))
    assert (
        _onboarding_month_spend(cfg) == 5.0
    )  # charged at the call's ceiling: it may well have spent


def test_a_call_that_fails_before_any_answer_records_nothing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, 5.0)

    async def stream(options, prompt):
        raise RuntimeError("could not connect")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr("agent.session.stream_brain_messages", stream)
    with pytest.raises(RuntimeError, match="connect"):
        asyncio.run(extract_mod._run_brain_query("p", cfg))
    assert _onboarding_month_spend(cfg) == 0.0
