"""Tests for gtm_core.refusal_copy."""

from __future__ import annotations

from gtm_core.refusal_copy import Refusal


def test_render_has_four_parts_in_order():
    r = Refusal(
        what="I haven't loaded anyone into the sending tool.",
        why="this list hasn't been sorted since it last changed",
        next_step="say 'sort my list' and I'll do it",
        alternative=None,
        cost="Nothing was spent.",
    )
    out = r.render()
    assert (
        out.index("I haven't")
        < out.index("because")
        < out.index("You can")
        < out.index("Nothing was spent")
    )


def test_render_with_alternative():
    r = Refusal(
        what="I haven't started sending.",
        why="the monthly budget cap is reached",
        next_step="raise the cap in your profile settings",
        alternative="wait until next month",
        cost="Nothing was spent.",
    )
    out = r.render()
    assert "or wait until next month" in out
    assert out.index("raise the cap") < out.index("or wait until next month")


def test_render_never_raises_and_the_lint_catches_jargon():
    # PRD §9-7: a refusal that crashes is a worse stop than a jargon one. render() never raises;
    # the identifier guard is a lint over the call sites plus this unit assertion.
    from tests.lint.operator_vocabulary import findings

    r = Refusal(
        what="x",
        why="evals/lanes-state.jsonl is missing",
        next_step="run lanes route",
        alternative=None,
        cost="",
    )
    out = r.render()  # must not raise
    assert findings(out), "the vocabulary lint must flag a path or a CLI verb in operator copy"


def test_render_never_raises_on_none_or_malformed():
    # Defensive: if fields are unexpected types, render() returns a safe fallback
    r = Refusal(what=None, why=None, next_step=None)  # type: ignore
    out = r.render()
    assert isinstance(out, str)
    assert len(out) > 0


def test_details_formatting():
    r = Refusal(
        what="I stopped.",
        why="checks failed",
        next_step="review findings",
        technical="exit code 1: 3 errors in preflight",
    )
    d = r.details()
    assert "<details><summary>Details</summary>" in d
    assert "exit code 1: 3 errors in preflight" in d
    assert d.endswith("</details>")


def test_details_empty_when_no_technical():
    r = Refusal(what="I stopped.", why="checks failed", next_step="review")
    assert r.details() == ""


def test_enrollment_gate_refusal_is_clean():
    from gtm_core.enrollment_gate import missing_lanes_state_refusal
    from tests.lint.operator_vocabulary import findings

    r = missing_lanes_state_refusal(empty=False)
    assert findings(r.render()) == []
    r_empty = missing_lanes_state_refusal(empty=True)
    assert findings(r_empty.render()) == []


def test_prospect_guards_refusals_are_clean():
    from gtm_core.prospect_guards import monthly_budget_refusal, per_run_cap_refusal
    from tests.lint.operator_vocabulary import findings

    r1 = per_run_cap_refusal(15.0, 10.0)
    assert findings(r1.render()) == []
    r2 = monthly_budget_refusal(8.0, 45.0, 53.0, 50.0)
    assert findings(r2.render()) == []


def test_account_folder_ambiguous_refusal_and_question_are_clean():
    from gtm_core.account_folder import (
        AmbiguousFolder,
        ambiguous_question,
        ambiguous_refusal,
    )
    from tests.lint.operator_vocabulary import findings

    q = ambiguous_question(["acme-robotics", "acme"])
    assert "I found two folders that could be this company: `acme-robotics` and `acme`." in q
    assert "Which one is it? (Say the name, or say 'neither' to make a new one.)" in q
    assert findings(q) == []

    r = ambiguous_refusal(AmbiguousFolder("prefix", ["acme-robotics", "acme"]))
    assert findings(r.render()) == []


def test_preflight_report_refusal_is_clean():
    from gtm_core.preflight_report import FAIL, CheckResult, PreflightReport, preflight_refusal
    from tests.lint.operator_vocabulary import findings

    rep = PreflightReport(
        profile="acme",
        ran_at="2026-09-25T00:00:00Z",
        csv_path="/path/to/ready-to-load.csv",
        rows=10,
        checks=[],
        acked=(),
        budget=None,
        readiness=None,
    )
    # No errors -> no refusal
    assert preflight_refusal(rep) is None

    rep_err = PreflightReport(
        profile="acme",
        ran_at="2026-09-25T00:00:00Z",
        csv_path="/path/to/ready-to-load.csv",
        rows=10,
        checks=[
            CheckResult(
                name="email_check",
                status=FAIL,
                detail="2 errors",
                errors=["row 1 missing domain", "row 2 bad email"],
            )
        ],
        acked=(),
        budget=None,
        readiness=None,
    )
    r = preflight_refusal(rep_err)
    assert r is not None
    assert findings(r.render()) == []


def test_render_with_alternative_and_empty_next_step():
    r = Refusal(what="Stopped", why="Denied", next_step="", alternative="Run X")
    rendered = r.render()
    assert "You can Run X." in rendered


def test_render_avoids_punctuation_stacking():
    r = Refusal(what="I stopped!", why="No key", next_step="Add key")
    rendered = r.render()
    assert "!." not in rendered
    assert "?." not in rendered
    assert rendered.startswith("I stopped! That's because No key.")
