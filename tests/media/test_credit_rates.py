"""`credit_rates`: the credits→USD conversion that makes a prepaid-pool spend reach the cap.

Two live incidents on the same lane (~580 credits in August 2026, 405 on a tenant 2026-09-03) both
reduce to one missing fact: nothing in the repo could say what a credit was worth, so no honest
`cost_usd` existed to write and `month-total` reported `$0.00` over real money. These tests hold
the fix's two load-bearing properties: the arithmetic is derived from the published plan card, and
an undeclared plan FAILS rather than defaulting — because a guessed dollar figure looks metered.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import credit_rates as cr


def _profile(tmp_path, **settings):
    d = tmp_path / "content" / "acme"
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(json.dumps({"monthly_budget_usd": 25.0, **settings}))
    return tmp_path


# --- the rate card ------------------------------------------------------------------------------


def test_the_rate_is_the_plan_price_divided_by_its_allotment():
    """Not a magic constant: Ultra's 3,000 cr/mo at $129/mo month-to-month is $0.043/credit."""
    plan = cr.HIGGSFIELD_PLANS["ultra"]
    assert cr.plan_usd_per_credit("ultra", "monthly") == pytest.approx(
        plan.usd_per_month_monthly / plan.credits_per_month
    )
    assert cr.plan_usd_per_credit("ultra", "monthly") == pytest.approx(0.043)


def test_annual_and_monthly_are_not_interchangeable():
    """The 1.84x fork this module exists to make explicit. Collapsing them would silently
    under-report a month-to-month tenant's spend by nearly half."""
    annual = cr.plan_usd_per_credit("ultra", "annual")
    monthly = cr.plan_usd_per_credit("ultra", "monthly")
    assert monthly > annual
    assert monthly / annual == pytest.approx(129 / 70)


def test_an_unknown_plan_is_refused():
    with pytest.raises(cr.CreditRateError, match="unknown higgsfield plan"):
        cr.plan_usd_per_credit("enterprise", "monthly")


def test_an_unknown_billing_cadence_is_refused():
    with pytest.raises(cr.CreditRateError, match="unknown billing cadence"):
        cr.plan_usd_per_credit("ultra", "quarterly")


def test_an_approximate_published_price_yields_no_rate():
    """The source quotes Starter's month-to-month price as "~$24/mo". A tilde may not become a
    dollar figure in a ledger row — the approximation has to be resolved, not laundered."""
    assert cr.HIGGSFIELD_PLANS["starter"].usd_per_month_monthly is None
    with pytest.raises(cr.CreditRateError, match="approximation"):
        cr.plan_usd_per_credit("starter", "monthly")


def test_the_card_carries_the_date_it_was_read():
    """A price is stable but not eternal. Recording WHEN it was read is what separates a
    re-verifiable constant from a snapshot — same convention as render_engines' verified_on."""
    assert cr.HIGGSFIELD_PLANS_VERIFIED_ON


def test_no_credit_balance_is_baked_into_the_module():
    """A BALANCE changes on every job and the live read is free; a rate and an allotment do not.
    The positive control on the same drift class video_preflight already guards.

    Scanned with docstrings stripped, so a *dated historical measurement* in the module's
    narrative ("balance 1306 → 899.46 on 2026-09-03") is allowed while an operative constant is
    not. The distinction is the point: a dated observation cannot be mistaken for current state;
    a module-level number can.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(cr))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = ""
    source = ast.unparse(tree)
    for balance in ("1306", "899.46", "2437"):
        assert balance not in source, (
            f"{balance!r} looks like a baked-in credit balance — rates and plan allotments may be "
            "constants; a balance must be read live."
        )


# --- per-profile resolution, fail-closed --------------------------------------------------------


def test_a_profile_declaring_its_plan_resolves(tmp_path):
    root = _profile(tmp_path, higgsfield_plan="ultra", higgsfield_billing="monthly")
    assert cr.usd_per_credit("acme", repo_root=root) == pytest.approx(0.043)


def test_the_bao_spend_converts_to_the_dollars_the_cap_never_saw(tmp_path):
    """The 2026-09-03 reference case: 405 credits on nine seedance renders. Against that tenant's
    $25 cap that is most of the month, and month-total reported $0.00 for all of it."""
    root = _profile(tmp_path, higgsfield_plan="ultra", higgsfield_billing="monthly")
    assert cr.credits_to_usd(405, "acme", repo_root=root) == pytest.approx(17.415)


def test_a_profile_that_declares_no_plan_raises_rather_than_defaulting(tmp_path):
    """The whole posture. A default rate would put a wrong number against the cap, which is
    strictly worse than the $0.00 it replaced — a wrong number looks metered."""
    root = _profile(tmp_path)
    with pytest.raises(cr.CreditRateError, match="declares no higgsfield rate"):
        cr.usd_per_credit("acme", repo_root=root)


def test_a_half_declared_plan_is_as_unusable_as_none(tmp_path):
    """Plan without cadence cannot pick a column of the card, and picking one anyway is the
    1.84x error above."""
    root = _profile(tmp_path, higgsfield_plan="ultra")
    with pytest.raises(cr.CreditRateError, match="higgsfield_billing"):
        cr.usd_per_credit("acme", repo_root=root)


def test_a_missing_settings_file_raises(tmp_path):
    with pytest.raises(cr.CreditRateError, match="no settings.json"):
        cr.usd_per_credit("ghost", repo_root=tmp_path)


def test_an_unreadable_settings_file_raises_rather_than_defaulting(tmp_path):
    d = tmp_path / "content" / "acme"
    d.mkdir(parents=True)
    (d / "settings.json").write_text("{not json")
    with pytest.raises(cr.CreditRateError, match="unreadable"):
        cr.usd_per_credit("acme", repo_root=tmp_path)


# --- the degrading reporter ---------------------------------------------------------------------


def test_rate_report_never_raises_and_never_reports_zero(tmp_path):
    """Callers that must keep going (reconcile-renders) need "unknown" to stay distinguishable
    from "free". `None` plus an error string; never 0.0."""
    root = _profile(tmp_path)
    report = cr.rate_report("acme", repo_root=root)
    assert report["usd_per_credit"] is None
    assert report["error"]


def test_rate_report_shows_its_working_when_it_succeeds(tmp_path):
    root = _profile(tmp_path, higgsfield_plan="plus", higgsfield_billing="annual")
    report = cr.rate_report("acme", repo_root=root)
    assert report["plan"] == "plus"
    assert report["billing"] == "annual"
    assert report["usd_per_credit"] == pytest.approx(35 / 1200)
    assert report["error"] is None
    assert report["verified_on"] == cr.HIGGSFIELD_PLANS_VERIFIED_ON


# --- CLI ------------------------------------------------------------------------------------------


def test_cli_prints_the_conversion(tmp_path, capsys):
    root = _profile(tmp_path, higgsfield_plan="ultra", higgsfield_billing="monthly")
    rc = cr.main(["--profile", "acme", "--credits", "405", "--repo-root", str(root)])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["cost_usd"] == pytest.approx(17.415)


def test_cli_exits_nonzero_when_no_rate_is_declared(tmp_path, capsys):
    root = _profile(tmp_path)
    rc = cr.main(["--profile", "acme", "--repo-root", str(root)])
    captured = capsys.readouterr()
    assert rc == 1
    assert json.loads(captured.out)["usd_per_credit"] is None
    assert "higgsfield_plan" in captured.err


def test_the_rate_costs_no_provider_call(tmp_path, monkeypatch):
    """Pure config arithmetic. If this ever needs the network it does not belong in a path that
    runs before every paid call."""
    import urllib.request

    def _boom(*args, **kwargs):  # pragma: no cover - the point is that it never runs
        raise AssertionError("the credit rate made a network call")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    root = _profile(tmp_path, higgsfield_plan="ultra", higgsfield_billing="monthly")
    assert cr.usd_per_credit("acme", repo_root=root) > 0
