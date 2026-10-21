"""R2.5: when a source list may stand in for a premise's research terms (Amendment A).

A list attests a premise only if the list is itself about AI agents (`attestation = "agentic"`),
only for the product the run is for, only while its source is live, and only for a premise that
opts in with `attested_by_source`. Every condition is one test that fails when the condition goes.
"""

from __future__ import annotations

import dataclasses
import datetime

import pytest

from gtm_core.hook_coverage.premise import Premise, premise_unsupported
from gtm_core.hook_coverage.source_attest import AttestContext, source_attests
from gtm_core.signal_obs.registry import Registry, Source

TODAY = datetime.date(2026, 10, 1)
SHA = "a" * 64
DOMAIN = "northwind.example.test"


def _source(**kw) -> Source:
    base = {
        "id": "agent-register",
        "title": "Agent pilot register",
        "url": "https://register.example.test/cohort",
        "kind": "regulator_register",
        "premise": "agents-in-operation",
        "claim_gap": "Listing shows a pilot, not production.",
        "precision": "8/10",
        "member_role": "mixed",
        "timing": "listing_date",
        "cadence_days": 90,
        "extractor": "brain_list",
        "extractor_args": {},
        "max_members": 80,
        "expires_on": datetime.date(2027, 3, 20),
        "owner": "ops",
        "notes": "",
        "attestation": "agentic",
        "agentic_basis": "The cohort was selected on agent use cases.",
    }
    return Source(**{**base, **kw})


def _obs(**kw) -> dict:
    base = {
        "schema": 1,
        "kind": "source_member",
        "product": "alpha",
        "source_id": "agent-register",
        "source_url": "https://register.example.test/cohort",
        "capture_sha256": SHA,
        "account_key": DOMAIN,
        "observed": "2026-09-20",
        "observed_basis": "first_seen_in_capture",
        "role": "mixed",
        "premise_at_write": "agents-in-operation",
        "writer": "amy",
        "obs_id": "x",
    }
    return {**base, **kw}


def _ctx(*, source: Source | None = None, obs=None, shas=frozenset({SHA}), product="alpha"):
    s = source or _source()
    reg = Registry(
        profile="p",
        product=product,
        path=None,
        origin="profile",
        sources=(s,),
        active=(s,),
        by_id={s.id: s},
    )
    return AttestContext(
        product=product,
        registry=reg,
        observations=tuple(obs if obs is not None else [_obs()]),
        capture_shas=shas,
        today=TODAY,
    )


def _premise(**kw) -> Premise:
    base = {
        "key": "agents-in-operation",
        "claim": "the recipient has put AI agents into operation or pilot",
        "terms": frozenset(),
        "min_distinct": 1,
        "attests_boundary": False,
        "attested_by_source": True,
    }
    fields = {f.name for f in dataclasses.fields(Premise)}
    return Premise(**{k: v for k, v in {**base, **kw}.items() if k in fields})


ROW = {"email": "a@northwind.example.test", "domain": DOMAIN, "company": "Northwind Traders"}


def test_an_agentic_list_member_attests_the_premise():
    assert source_attests(ROW, _premise(), _ctx()) is True


def test_a_pool_list_never_attests_by_membership():
    pool = _source(attestation="pool", agentic_basis="")
    assert source_attests(ROW, _premise(), _ctx(source=pool)) is False


def test_a_premise_that_does_not_opt_in_is_never_attested_by_a_list():
    assert source_attests(ROW, _premise(attested_by_source=False), _ctx()) is False


def test_another_products_observation_does_not_attest():
    ctx = _ctx(obs=[_obs(product="beta")])
    assert source_attests(ROW, _premise(), ctx) is False


def test_a_member_of_a_different_account_does_not_attest():
    other = {**ROW, "domain": "contoso.example.test"}
    assert source_attests(other, _premise(), _ctx()) is False


def test_a_row_with_no_domain_is_never_attested():
    assert source_attests({**ROW, "domain": ""}, _premise(), _ctx()) is False


def test_the_www_prefix_and_case_do_not_hide_a_member():
    assert source_attests({**ROW, "domain": "WWW." + DOMAIN.upper()}, _premise(), _ctx()) is True


@pytest.mark.parametrize("precision", ["", "6/10", "9/9"])
def test_a_source_that_is_not_yet_trusted_does_not_attest(precision):
    ctx = _ctx(source=_source(precision=precision))
    assert source_attests(ROW, _premise(), ctx) is False


def test_an_expired_source_does_not_attest():
    ctx = _ctx(source=_source(expires_on=datetime.date(2026, 9, 30)))
    assert source_attests(ROW, _premise(), ctx) is False


def test_a_vendor_member_does_not_attest():
    assert source_attests(ROW, _premise(), _ctx(obs=[_obs(role="vendor")])) is False


def test_a_registry_edit_retracts_attestation_without_rewriting_history():
    ctx = _ctx(source=_source(premise="something-else"))
    # The old premise is no longer what the registry points at
    assert source_attests(ROW, _premise(key="agents-in-operation"), ctx) is False
    # The new premise is not what was written into the observation historical record
    assert source_attests(ROW, _premise(key="something-else"), ctx) is False


def test_a_future_dated_observation_does_not_attest():
    ctx = _ctx(obs=[_obs(observed="2026-10-05")])
    assert source_attests(ROW, _premise(), ctx) is False
    assert premise_unsupported([ROW], _premise(), source_ctx=ctx) != []


def test_an_observation_whose_capture_is_gone_does_not_attest():
    assert source_attests(ROW, _premise(), _ctx(shas=frozenset())) is False


def test_an_observation_whose_source_left_the_registry_does_not_attest():
    assert source_attests(ROW, _premise(), _ctx(obs=[_obs(source_id="gone")])) is False


def test_a_membership_older_than_a_year_does_not_attest():
    assert source_attests(ROW, _premise(), _ctx(obs=[_obs(observed="2025-09-30")])) is False


def test_a_leave_note_is_not_membership():
    assert source_attests(ROW, _premise(), _ctx(obs=[_obs(kind="source_leave_noted")])) is False


def test_a_join_attests_like_a_member():
    assert source_attests(ROW, _premise(), _ctx(obs=[_obs(kind="source_join")])) is True


# premise_unsupported: the one place the gate asks.


def test_the_gate_passes_a_row_a_list_attests_and_refuses_it_without_the_list():
    p = _premise()
    assert premise_unsupported([ROW], p) != []  # no terms, no source: fails closed
    assert premise_unsupported([ROW], p, source_ctx=_ctx()) == []
    assert premise_unsupported([ROW], p, source_ctx=_ctx(shas=frozenset())) != []


def test_a_term_premise_ignores_the_list_route_entirely():
    p = _premise(
        key="multi-cloud", terms=frozenset({"aws", "gcp"}), min_distinct=2, attested_by_source=False
    )
    row = {**ROW, "why_now": "runs on AWS only"}
    assert premise_unsupported([row], p, source_ctx=_ctx()) == premise_unsupported([row], p)


def test_the_list_route_and_the_terms_route_are_independent():
    p = _premise(terms=frozenset({"agent"}), min_distinct=1)
    by_terms = {**ROW, "why_now": "launched an agent pilot"}
    assert premise_unsupported([by_terms], p) == []  # terms alone
    assert premise_unsupported([ROW], p, source_ctx=_ctx()) == []  # list alone
    assert premise_unsupported([ROW], p) != []  # neither


def test_w3b_premise_evidence_independence():
    """W3b independence: removing/adding evidence does not affect the source route,
    and presence/absence of source_ctx does not affect the terms/evidence route."""
    p = _premise(terms=frozenset({"agent"}), min_distinct=1)
    # Row with terms/evidence only
    terms_row = {**ROW, "signal_evidence": "running autonomous agent in production"}
    assert premise_unsupported([terms_row], p, source_ctx=None) == []
    assert premise_unsupported([terms_row], p, source_ctx=_ctx()) == []

    # Row with source route: absence or presence of evidence fields does not change source attestation
    no_ev_row = {
        k: v
        for k, v in ROW.items()
        if k not in ("premise_evidence", "why_now", "signal_evidence", "signal_clause")
    }
    assert source_attests(no_ev_row, p, _ctx()) is True
    assert premise_unsupported([no_ev_row], p, source_ctx=_ctx()) == []
    with_unrelated_ev = {**no_ev_row, "signal_evidence": "unrelated medical device manufacturing"}
    assert source_attests(with_unrelated_ev, p, _ctx()) is True
    assert premise_unsupported([with_unrelated_ev], p, source_ctx=_ctx()) == []


def test_premise_attestation_reports_via_source():
    from gtm_core.hook_coverage.premise import premise_attestation

    p = _premise()
    counts = premise_attestation([ROW], p, source_ctx=_ctx())
    assert counts["via=source"] == 1


def test_a_run_for_another_product_is_not_attested_by_this_products_list():
    ctx = _ctx(product="beta", obs=[_obs(product="alpha")])
    assert source_attests(ROW, _premise(), ctx) is False
    assert source_attests(ROW, _premise(), _ctx(product="beta", obs=[_obs(product="beta")])) is True


def test_the_render_gate_takes_the_list_route():
    from linter.outreach.rules_render import lint_premise

    vocab = {"agents-in-operation": _premise()}
    spec = "premise: agents-in-operation\n"
    rules = lambda ctx: {v.rule for v in lint_premise(spec, [ROW], vocab, ctx)}  # noqa: E731
    assert "premise-unsupported" in rules(None)
    assert "premise-unsupported" not in rules(_ctx())


def test_the_judge_cascade_settles_a_listed_account_without_a_judge():
    from gtm_core.groundedness import premise_cascade

    settled, escalate = premise_cascade([ROW], _premise(), source_ctx=_ctx())
    assert [c.entailed for c in settled] == [True] and escalate == []
    settled, _ = premise_cascade([ROW], _premise())
    assert [c.entailed for c in settled] == [False]


def test_a_lane_row_keyed_by_company_domain_is_found_like_a_ledger_row():
    lane_row = {"email": ROW["email"], "company_domain": DOMAIN}
    assert source_attests(lane_row, _premise(), _ctx()) is True


# A second route (cohort rule D12): an account chosen on its OWN dated, quoted announcement. The
# route judges the record's shape AND checks the quote against the stored capture of the cited
# page itself; a quote nothing stored can show is never evidence (verification audit 2026-10-02,
# Critical 2 and 3).

from gtm_core import signal_sources  # noqa: E402
from gtm_core.hook_coverage.source_attest import record_attests  # noqa: E402

OWN = {
    **ROW,
    "signal_agent_kind": "ai",
    "signal_observed": "2026-08-18",
    "signal_evidence": "Northwind today announced the rollout of an agentic AI solution",
    "signal_source_url": "https://news.example.test/rollout",
    "category_relation": "prospect",
}


def _stored(tmp_path, quote=None, url=OWN["signal_source_url"]):
    """A sources folder whose capture of the cited page carries ``quote``."""
    said = OWN["signal_evidence"] if quote is None else quote
    signal_sources.store_capture(
        url, f"# Newsroom\n\n{said}. More follows.\n", sources_dir=tmp_path
    )
    return tmp_path


def test_an_own_dated_announcement_about_agents_attests_an_opted_in_premise(tmp_path):
    assert record_attests(OWN, _premise(), TODAY, sources_dir=_stored(tmp_path)) is True


def test_the_record_route_is_closed_to_a_premise_that_does_not_opt_in(tmp_path):
    p = _premise(attested_by_source=False)
    assert record_attests(OWN, p, TODAY, sources_dir=_stored(tmp_path)) is False


def test_a_premise_object_with_no_opt_in_field_is_refused_not_a_crash(tmp_path):
    """Verification audit Critical 2: a duck-typed premise (older fixtures) must refuse."""

    class Bare:
        key = "x"
        min_distinct = 1
        claim = ""

        def hits_for(self, row, fields):
            return set()

    assert record_attests(OWN, Bare(), TODAY, sources_dir=_stored(tmp_path)) is False
    assert premise_unsupported([OWN], Bare(), today=TODAY) != []


@pytest.mark.parametrize(
    "change",
    [
        {"signal_agent_kind": "human"},
        {"signal_agent_kind": ""},
        {"signal_evidence": ""},
        {"signal_source_url": ""},
        {"signal_observed": ""},
        {"signal_observed": "2025-09-30"},
        {"signal_observed": "2026-10-09"},
        {"category_relation": "competitor"},
        {"category_relation": "unclear"},
    ],
)
def test_an_incomplete_stale_or_off_topic_record_does_not_attest(change, tmp_path):
    assert (
        record_attests({**OWN, **change}, _premise(), TODAY, sources_dir=_stored(tmp_path)) is False
    )


def test_a_quote_with_no_stored_capture_does_not_attest(tmp_path):
    """The audit's repro: an uncaptured URL and the word "agents" used to resolve an angle."""
    assert record_attests(OWN, _premise(), TODAY, sources_dir=tmp_path) is False


def test_a_capture_that_does_not_contain_the_quote_does_not_attest(tmp_path):
    other = _stored(tmp_path, quote="Northwind opened a new office in the harbour district")
    assert record_attests(OWN, _premise(), TODAY, sources_dir=other) is False


def test_a_capture_of_a_different_page_does_not_attest(tmp_path):
    other = _stored(tmp_path, url="https://news.example.test/another-page")
    assert record_attests(OWN, _premise(), TODAY, sources_dir=other) is False


def test_with_no_place_to_look_the_route_refuses_rather_than_trusting_the_row():
    assert record_attests(OWN, _premise(), TODAY) is False


def test_a_damaged_capture_index_refuses_rather_than_raising(tmp_path):
    (tmp_path / "index.jsonl").write_text("{not json\n", encoding="utf-8")
    assert record_attests(OWN, _premise(), TODAY, sources_dir=tmp_path) is False


def test_a_quote_split_by_markdown_emphasis_in_the_capture_still_matches(tmp_path):
    marked = "Northwind today announced the **rollout** of an agentic AI solution"
    assert record_attests(OWN, _premise(), TODAY, sources_dir=_stored(tmp_path, marked)) is True


def test_the_gate_passes_a_row_with_its_own_record_and_still_refuses_a_bare_one(tmp_path):
    p = _premise()
    where = _stored(tmp_path)
    assert premise_unsupported([OWN], p, today=TODAY, sources_dir=where) == []
    assert premise_unsupported([ROW], p, today=TODAY, sources_dir=where) != []
    # The same row with nothing on file is refused: the record alone is not evidence.
    assert premise_unsupported([OWN], p, today=TODAY, sources_dir=tmp_path / "empty") != []


def test_an_ai_label_alone_does_not_attest_when_the_quote_never_names_an_agent(tmp_path):
    """The `ai` agent kind is a model-written label; the quote itself must say agent or agentic."""
    said = "Northwind introduced a generative AI companion for members"
    companion = {**OWN, "signal_evidence": said}
    where = _stored(tmp_path, said)
    assert record_attests(companion, _premise(), TODAY, sources_dir=where) is False
    ok = "Northwind put two AI agents into its claims desk"
    where2 = _stored(tmp_path / "b", ok)
    assert record_attests({**OWN, "signal_evidence": ok}, _premise(), TODAY, sources_dir=where2)


def test_the_record_route_reads_neither_switch(monkeypatch, tmp_path):
    """Documented, not hidden: a verified own announcement attests with the switches closed."""
    monkeypatch.delenv("GTM_SIGNAL_SOURCES_ENABLED", raising=False)
    monkeypatch.delenv("GTM_SIGNAL_VIEW_ROUTING", raising=False)
    assert record_attests(OWN, _premise(), TODAY, sources_dir=_stored(tmp_path)) is True


def test_the_date_window_follows_the_run_date_not_the_wall_clock(tmp_path):
    where = _stored(tmp_path)
    assert record_attests(OWN, _premise(), datetime.date(2027, 8, 17), sources_dir=where) is True
    assert record_attests(OWN, _premise(), datetime.date(2027, 8, 19), sources_dir=where) is False


def test_the_record_route_reaches_only_the_premises_that_opt_in_today():
    """A second opted-in premise would silently widen a route no switch controls. Decide, then edit."""
    from gtm_core.hook_coverage.premise import load_premise_vocab
    from gtm_core.paths import resolve_profiles_root

    opted_in = set()
    for knowledge in resolve_profiles_root().glob("*/knowledge/premise-vocab.toml"):
        vocab = load_premise_vocab(knowledge.parent.parent.name, None, None)
        opted_in |= {k for k, p in vocab.items() if p.attested_by_source}
    assert opted_in <= {"agents-in-operation"}
