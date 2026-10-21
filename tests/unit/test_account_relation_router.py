"""The router's regulator and competitor triggers delegate to the classifier.

Trigger ids and the hold sheet's copy do not change — a recorded hold decision is honoured only
when its trigger AND its detail string match — so the byte-level details of the cases the old rules
already covered are pinned here beside the new ones. Everything runs through the real
``load_context`` on files under ``tmp_path``; nothing in the classifier is mocked.

Fictional data only (§R9); see ``relation_fixtures``.
"""

from __future__ import annotations

import pytest

from gtm_core import lanes
from gtm_core.account_relation import RelationConfigError
from gtm_core.lanes.context import DEFAULT_REGULATED_SUFFIXES, RouterContext
from tests.unit.relation_fixtures import PROFILE, TODAY, row, write_profile


def _ctx(tmp_path, *, regulators="default", competitors="default", policy: str | None = None):
    kw = {}
    if regulators != "default":
        kw["regulators"] = regulators
    if competitors != "default":
        kw["competitors"] = competitors
    root = write_profile(tmp_path, **kw)
    if policy is not None:
        (root / PROFILE / "knowledge" / "lane-policy.toml").write_text(policy, encoding="utf-8")
    content = tmp_path / "content"
    (content / PROFILE / "prospects" / "sequences").mkdir(parents=True, exist_ok=True)
    return lanes.load_context(PROFILE, content_root=content, profiles_root=root, as_of=TODAY)


def _route(ctx, **kw):
    return lanes.route_row(row(**kw), ctx, None)


# --- a regulator holds -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "kw",
    [
        {"company": "Examplia Central Bank", "company_domain": "atlas.wideloop.example"},
        {"company": "Anyco", "company_domain": "mail.atlas.wideloop.example"},
        {"company": "Anyco", "company_domain": "", "email": "a.person@atlas.wideloop.example"},
        {"company": "Bank of Examplia", "company_domain": ""},
        {"company": "Anyco", "company_domain": "treasury.gov.example"},
    ],
    ids=["by-domain", "by-subdomain", "by-email-only", "by-name-only", "by-ending"],
)
def test_a_regulator_is_held_under_the_regulator_trigger(tmp_path, kw):
    r = _route(_ctx(tmp_path), **kw)
    assert (r.lane, r.trigger) == ("hold", "regulator"), kw


def test_a_body_hit_carries_the_plain_words_reason_as_its_detail(tmp_path):
    r = _route(_ctx(tmp_path), company_domain="atlas.wideloop.example")
    assert r.detail == (
        "Examplia Central Bank (central bank) — matched its domain atlas.wideloop.example"
    )


def test_an_ending_hit_keeps_the_detail_string_the_old_rule_wrote(tmp_path):
    """A hold decision recorded before this change is keyed on this exact string."""
    ctx = _ctx(tmp_path)
    r = _route(ctx, company="Anyco", company_domain="treasury.gov.example")
    assert r.detail == "domain treasury.gov.example matches .gov.example"
    decided = {
        (
            "regulator",
            lanes.account_key(row(company="Anyco", company_domain="treasury.gov.example")),
        ): {
            "decision": "generic",
            "detail": "domain treasury.gov.example matches .gov.example",
        }
    }
    released = lanes.route_row(
        row(company="Anyco", company_domain="treasury.gov.example"), ctx, None, decisions=decided
    )
    assert released.lane == "generic", "the recorded decision still releases the row"


def test_the_model_written_label_keeps_its_detail(tmp_path):
    r = _route(_ctx(tmp_path), category_relation="regulator")
    assert (r.lane, r.trigger, r.detail) == ("hold", "regulator", "category_relation=regulator")


@pytest.mark.parametrize(
    "company_domain",
    ["examplia-exchange.example", "examplia-standards.example", "examplia-brokers.example"],
)
def test_an_exchange_a_standards_body_and_a_trade_body_hold_under_the_same_trigger(
    tmp_path, company_domain
):
    r = _route(_ctx(tmp_path), company_domain=company_domain)
    assert (r.lane, r.trigger) == ("hold", "regulator")


def test_an_nhs_style_ending_holds_as_public_health(tmp_path):
    r = _route(_ctx(tmp_path), company="Anyco", company_domain="trust.nhs.example")
    assert (r.lane, r.trigger) == ("hold", "regulator")
    assert r.detail == "domain trust.nhs.example matches .nhs.example"


def test_an_unrelated_row_is_not_held(tmp_path):
    r = _route(_ctx(tmp_path))
    assert r.lane != "hold"


def test_an_allow_does_not_release_the_router_hold(tmp_path):
    """The hold sheet is the human decision at the router; the allow exists for the gate."""
    reg = (
        'schema = 1\n[[body]]\nname = "Examplia Central Bank"\nkind = "central-bank"\n'
        'domains = ["atlas.wideloop.example"]\n[[allow]]\ndomain = "atlas.wideloop.example"\n'
        'reason = "agreed in writing with its innovation unit"\ndecided = "2026-10-02"\n'
        'expires = "2026-12-31"\n'
    )
    r = _route(_ctx(tmp_path, regulators=reg), company_domain="atlas.wideloop.example")
    assert (r.lane, r.trigger) == ("hold", "regulator")


# --- competitors -----------------------------------------------------------------------------


def test_a_direct_competitor_is_excluded_not_held(tmp_path):
    r = _route(_ctx(tmp_path), company="Contoso Agent Broker", company_domain="contoso.example")
    assert (r.lane, r.trigger) == ("excluded", "competitor-direct")
    assert r.detail == "Contoso Agent Broker (direct) — Overlaps the core wedge."


def test_an_adjacent_competitor_is_held(tmp_path):
    r = _route(_ctx(tmp_path), company="Northwind Robotics", company_domain="northwind.example")
    assert (r.lane, r.trigger) == ("hold", "competitor-adjacent")
    assert r.detail == "Northwind Robotics (adjacent) — Neighbouring product."


def test_every_non_direct_tier_holds_as_adjacent(tmp_path):
    r = _route(_ctx(tmp_path), company="Fabrikam Identity", company_domain="fabrikam.example")
    assert (r.lane, r.trigger) == ("hold", "competitor-adjacent")


def test_the_competitor_hold_still_comes_before_the_regulator_hold(tmp_path):
    reg = (
        'schema = 1\n[[body]]\nname = "Examplia Test Body"\nkind = "regulator"\n'
        'domains = ["northwind.example"]\n'
    )
    r = _route(
        _ctx(tmp_path, regulators=reg),
        company="Northwind Robotics",
        company_domain="northwind.example",
    )
    assert r.trigger == "competitor-adjacent"


def test_a_sibling_of_a_listed_competitor_is_no_longer_excluded(tmp_path):
    """The measured false positive, through the router: same first label, different company."""
    ctx = _ctx(tmp_path)
    assert (
        _route(ctx, company="A Medical Center", company_domain="contoso.org.example").lane
        != "excluded"
    )


# --- context ---------------------------------------------------------------------------------


def test_load_context_reads_the_file_and_adds_no_note_when_there_is_none(tmp_path):
    ctx = _ctx(tmp_path, regulators=None)
    assert ctx.regulators is not None and ctx.regulators.bodies == ()
    assert not any("regulators" in n for n in ctx.notes), (
        "a tenant with no file behaves as before and prints nothing new"
    )


def test_the_built_in_endings_still_hold_with_no_file(tmp_path):
    ctx = _ctx(tmp_path, regulators=None)
    for host in ("treasury.gov", "x.gov.uk", "agency.gc.ca"):
        assert _route(ctx, company_domain=host).trigger == "regulator", host


def test_a_hand_built_context_still_holds_a_government_address():
    ctx = RouterContext(profile=PROFILE, as_of=TODAY)
    assert ctx.regulators is None
    assert lanes.route_row(row(company_domain="agency.gov"), ctx, None).trigger == "regulator"
    assert lanes.route_row(row(), ctx, None).lane != "hold"


def test_a_hand_built_competitor_index_keeps_working_with_a_dotted_key():
    from gtm_core.account_integrity import CompetitorHit

    ctx = RouterContext(profile=PROFILE, as_of=TODAY)
    ctx.competitors = {"vertex.example": CompetitorHit("direct", "Vertex (direct) — x")}
    r = lanes.route_row(row(), ctx, None)
    assert (r.lane, r.trigger) == ("excluded", "competitor-direct")


def test_a_corrupt_regulators_file_stops_the_route_and_names_the_file(tmp_path):
    with pytest.raises(RelationConfigError, match="regulators.toml"):
        _ctx(tmp_path, regulators="schema = 1\n[[body]\n")


# --- the deprecated [hold] regulated_domains ---------------------------------------------------


def test_regulated_domains_in_lane_policy_gain_a_dot_boundary_and_a_note(tmp_path):
    ctx = _ctx(
        tmp_path,
        regulators=None,
        policy='[hold]\nregulated_domains = ["agency.example"]\n',
    )
    assert any("regulated_domains" in n and "regulators.toml" in n for n in ctx.notes)
    assert _route(ctx, company_domain="agency.example").trigger == "regulator"
    assert _route(ctx, company_domain="mail.agency.example").trigger == "regulator"
    assert _route(ctx, company_domain="notagency.example").lane != "hold", (
        "without a leading dot the old `endswith` also matched this look-alike"
    )


def test_a_dotted_regulated_domain_still_works(tmp_path):
    ctx = _ctx(
        tmp_path, regulators=None, policy='[hold]\nregulated_domains = [".agency.example"]\n'
    )
    assert _route(ctx, company_domain="mail.agency.example").trigger == "regulator"


def test_the_default_suffixes_are_the_floor():
    from gtm_core.account_relation import FLOOR_ENDINGS

    assert tuple(DEFAULT_REGULATED_SUFFIXES) == FLOOR_ENDINGS
