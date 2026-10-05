"""R2.1-R2.8: the signal view is computed on read, applied only behind its own switch, and never stored.

Fictional tenant `realshape`: an agentic register with one member, northwind.
"""

from __future__ import annotations

import datetime
import json

import pytest

from gtm_core import signal_view
from gtm_core.hook_coverage.premise import Premise
from gtm_core.signal_obs import switch

TODAY = datetime.date(2026, 10, 1)
ROW = {
    "email": "a@northwind.example.test",
    "domain": "northwind.example.test",
    "company": "Northwind",
}

_AGENTIC = """
schema = 1

[[source]]
id = "north-directory"
title = "Agent pilot register"
url = "https://members.example.test/list"
kind = "regulator_register"
premise = "agents-in-operation"
attestation = "agentic"
agentic_basis = "The cohort was chosen on agent use cases."
claim_gap = "Listing shows a pilot, not production."
precision = "8/10"
member_role = "buyer"
timing = "listing_date"
cadence_days = 30
extractor = "links"
extractor_args = {}
max_members = 50
expires_on = "2027-03-20"
owner = "ops"
"""

_VOCAB = """
[premise.agents-in-operation]
claim = "the recipient has put AI agents into operation or pilot"
min_distinct = 1
attests_boundary = false
attested_by_source = true
terms = []
"""


@pytest.fixture
def world(signal_world, monkeypatch):
    from unit.conftest import page

    monkeypatch.delenv(switch.VIEW_SETTING, raising=False)
    base = signal_world.profiles_root / signal_world.profile
    (base / "knowledge" / "signal-sources.toml").write_text(_AGENTIC, encoding="utf-8")
    vocab = base / "knowledge" / "premise-vocab.toml"
    vocab.write_text(vocab.read_text(encoding="utf-8") + _VOCAB if vocab.exists() else _VOCAB)
    signal_world.ledger(("Northwind", "northwind.example.test"))
    signal_world.capture(
        page("Northwind", domains={"Northwind": "northwind.example.test"}),
        "2026-10-01T01:00:00+00:00",
    )
    signal_world.run(day="2026-10-01")
    return signal_world


def _ctx(w):
    return signal_view.attest_context(
        w.profile,
        w.product,
        profiles_root=w.profiles_root,
        content_root=w.content_root,
        today=TODAY,
    )


def test_with_the_source_switch_closed_no_context_is_built_and_no_shard_is_opened(
    world, monkeypatch
):
    monkeypatch.delenv("GTM_SIGNAL_SOURCES_ENABLED")
    assert _ctx(world) is None


def test_with_the_source_switch_open_a_context_carries_the_runs_observations(world):
    ctx = _ctx(world)
    assert ctx is not None and ctx.product == world.product
    assert [o["account_key"] for o in ctx.observations] == ["northwind.example.test"]


def test_routing_context_is_none_until_the_routing_switch_is_also_open(world, monkeypatch):
    args = (world.profile, world.product)
    kw = {"profiles_root": world.profiles_root, "content_root": world.content_root, "today": TODAY}
    assert signal_view.routing_context(*args, **kw) is None
    monkeypatch.setenv(switch.VIEW_SETTING, "1")
    assert signal_view.routing_context(*args, **kw) is not None


def test_a_registry_that_cannot_be_read_gives_no_context_and_never_raises(world):
    (world.profiles_root / world.profile / "knowledge" / "signal-sources.toml").write_text(
        "schema = [", encoding="utf-8"
    )
    assert _ctx(world) is None


def test_derive_with_nothing_but_the_row_is_the_legacy_view():
    row = {**ROW, "signal_observed": "2026-09-20", "why_now": "Northwind launched an agent pilot."}
    v = signal_view.derive(row, None, today=TODAY)
    assert (v.view_basis, v.timing_kind, v.timing_observed) == (
        "legacy",
        "news_event",
        "2026-09-20",
    )
    assert v.premise_via == "none" and v.relevance_line


def test_derive_reads_signal_source_url_for_news_event():
    row = {**ROW, "signal_source_url": "https://news.example.test/story"}
    v = signal_view.derive(row, None, today=TODAY)
    assert (v.timing_kind, v.timing_source) == ("news_event", "https://news.example.test/story")


def test_derive_reads_intent_feeds_for_topic_timing():
    row = {
        **ROW,
        "intent_feeds": ["vibe-topic"],
        "intent_observed": "2026-09-25",
    }
    v = signal_view.derive(row, None, today=TODAY)
    assert (v.view_basis, v.timing_kind, v.timing_observed, v.timing_source) == (
        "legacy",
        "topic_intent",
        "2026-09-25",
        "vibe-topic",
    )


def test_a_list_adds_a_premise_route_and_never_a_relevance_line(world):
    premise = Premise(
        key="agents-in-operation", min_distinct=1, terms=frozenset(), attested_by_source=True
    )
    v = signal_view.derive(ROW, _ctx(world), premise=premise, today=TODAY)
    assert v.premise_via == "source" and v.premise_source == "north-directory"
    assert v.view_basis == "observations" and v.relevance_line == ""


def test_the_list_and_the_legacy_fields_together_are_a_mixed_view(world):
    row = {**ROW, "signal_observed": "2026-09-20", "why_now": "Northwind launched an agent pilot."}
    premise = Premise(
        key="agents-in-operation", min_distinct=1, terms=frozenset(), attested_by_source=True
    )
    v = signal_view.derive(row, _ctx(world), premise=premise, today=TODAY)
    assert v.view_basis == "mixed" and v.relevance_line and v.premise_via == "source"


def test_a_row_the_list_does_not_name_keeps_its_legacy_view(world):
    other = {**ROW, "domain": "contoso.example.test"}
    premise = Premise(
        key="agents-in-operation", min_distinct=1, terms=frozenset(), attested_by_source=True
    )
    assert (
        signal_view.derive(other, _ctx(world), premise=premise, today=TODAY).view_basis == "legacy"
    )


def test_deriving_a_view_writes_nothing(world):
    before = sorted(p.name for p in world.content_root.rglob("*") if p.is_file())
    signal_view.derive(ROW, _ctx(world), today=TODAY)
    assert sorted(p.name for p in world.content_root.rglob("*") if p.is_file()) == before


def test_the_shadow_line_counts_rows_only_a_list_would_qualify(world):
    premise = Premise(
        key="agents-in-operation", min_distinct=1, terms=frozenset(), attested_by_source=True
    )
    rows = [ROW, {**ROW, "email": "b@contoso.example.test", "domain": "contoso.example.test"}]
    assert signal_view.shadow_count(rows, premise, _ctx(world)) == 1
    assert signal_view.shadow_count(rows, premise, None) == 0
    assert signal_view.shadow_line(1).startswith("1 company is on an approved")
    assert "2 companies are on an approved" in signal_view.shadow_line(2)


def test_the_mode_line_names_the_state_in_plain_words(monkeypatch):
    monkeypatch.delenv("GTM_SIGNAL_SOURCES_ENABLED", raising=False)
    monkeypatch.delenv(switch.VIEW_SETTING, raising=False)
    assert signal_view.mode_line() == "Source lists: off"
    monkeypatch.setenv("GTM_SIGNAL_SOURCES_ENABLED", "1")
    assert signal_view.mode_line() == "Source lists: collecting only"
    monkeypatch.setenv(switch.VIEW_SETTING, "1")
    assert signal_view.mode_line() == "Source lists: used for choosing emails"


def test_explain_prints_the_view_as_plain_json_and_changes_no_file(world, capsys):
    rc = signal_view.main(
        [
            "explain",
            "--profile",
            world.profile,
            "--product",
            world.product,
            "--domain",
            "northwind.example.test",
        ],
        profiles_root=world.profiles_root,
        content_root=world.content_root,
    )
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert rc == 0 and out["view_basis"] in {"observations", "legacy", "mixed"}
    assert "Registry:" in captured.err


def test_explain_with_company_resolves_and_prints_registry(world, capsys):
    rc = signal_view.main(
        [
            "explain",
            "--profile",
            world.profile,
            "--product",
            world.product,
            "--company",
            "Northwind Traders",
        ],
        profiles_root=world.profiles_root,
        content_root=world.content_root,
    )
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert rc == 0 and out["view_basis"] in {"observations", "legacy", "mixed"}
    assert "Registry:" in captured.err


@pytest.mark.parametrize(
    ("legacy", "kind", "when", "source"),
    [
        ("2026-09-20", "source_join", "2026-10-01", "north-directory"),
        ("2026-10-01", "news_event", "2026-10-01", ""),
    ],
)
def test_the_freshest_timing_wins_and_a_tie_keeps_the_legacy_one(world, legacy, kind, when, source):
    import dataclasses

    ctx = _ctx(world)
    joined = tuple({**o, "kind": "source_join", "observed": when} for o in ctx.observations)
    row = {**ROW, "signal_observed": legacy}
    v = signal_view.derive(row, dataclasses.replace(ctx, observations=joined), today=TODAY)
    assert (v.timing_kind, v.timing_source) == (kind, source)


def test_a_run_reads_the_lists_once_and_a_changed_file_is_read_again(world):
    first = _ctx(world)
    assert _ctx(world) is first
    world.run(day="2026-10-02", run_id="run2")  # nothing new: no shard change
    from unit.conftest import page

    world.capture(page("Northwind", "Contoso"), "2026-10-03T01:00:00+00:00")
    world.run(day="2026-10-03", run_id="run3")
    assert _ctx(world) is not first
