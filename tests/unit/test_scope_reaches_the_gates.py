"""The pre-send gates must not go quiet when a profile gains a second product.

``run_scope.ScopeError`` is a ``ValueError``, and several gates already treat a ``ValueError`` as
"not configured, skip". Left alone, adding a second product would have switched the hook-coverage
check off without a word (a gate that passes by finding nothing, the failure this repo has already
paid for). Each case below runs the real entry point on the ``realshape`` fixture.
"""

from __future__ import annotations

import pytest

from gtm_core import build_eval_sheet, preflight_report, run_scope


@pytest.fixture
def env(one_product_profiles, tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    return one_product_profiles


def _hook_check(profile, **kw):
    report = preflight_report.run_preflight(profile, **kw)
    return next(c for c in report.checks if c.name == "hook_coverage")


def test_preflight_fails_rather_than_skips_when_the_product_is_dropped(env):
    check = _hook_check("realshape")
    assert check.status == preflight_report.FAIL
    assert any("product-required" in e for e in check.errors)


def test_preflight_runs_the_check_when_the_product_is_named(env):
    check = _hook_check("realshape", product="alpha")
    assert not any("product-required" in e for e in check.errors)


def test_a_single_product_profile_is_still_measured_or_skipped_as_before(env):
    assert not any("product-required" in e for e in _hook_check("oneprod").errors)


def test_the_eval_sheet_refuses_a_dropped_product(env, capsys):
    assert build_eval_sheet.main(["--profile", "realshape"]) == 2
    assert "more than one product" in capsys.readouterr().err
    with pytest.raises(run_scope.ProductRequired):
        build_eval_sheet.all_live_rows("realshape")


# --- the two writers that would put a second product's work into the default product's files -----


def test_intake_staging_refuses_a_second_product_and_a_dropped_product(env, tmp_path):
    """Staged files promote into the company-wide knowledge folder, which is the default product's
    level; staging a second product's intake there would overwrite the default product's registry."""
    from gtm_core import messaging_intake

    intake = tmp_path / "intake.md"
    intake.write_text("# Intake\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not supported yet"):
        messaging_intake.stage_intake("realshape", intake, tmp_path / "content", product="beta")
    with pytest.raises(run_scope.ProductRequired):
        messaging_intake.stage_intake("realshape", intake, tmp_path / "content")
    assert not (tmp_path / "content" / "realshape" / "knowledge-staging").exists()


def test_lane_routing_refuses_a_second_product_and_a_dropped_product(env, tmp_path, capsys):
    """Routing writes one shared decisions file and one lane pool per company."""
    from gtm_core.lanes import cli as lanes_cli

    pool = tmp_path / "pool.csv"
    pool.write_text("email,company\nada@delta-fictional.example,Fictional Delta Co\n")
    base = ["route", "--profile", "realshape", "--csv", str(pool), "--dry-run"]
    assert lanes_cli.main([*base, "--product", "beta"]) == 2
    assert "waits for the per-product ledger" in capsys.readouterr().err
    assert lanes_cli.main(base) == 2
    assert "more than one product" in capsys.readouterr().err
