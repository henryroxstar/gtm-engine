"""``eval_writeback`` on a two-product profile: a dropped product refuses, a second product's eval
cannot disqualify, and neither leaves the suppression ledger half-written.

Fictional data only.
"""

from __future__ import annotations

import pytest

from gtm_core import eval_writeback as ew
from gtm_core import prospects_state as ps
from gtm_core.eval_calibration import Label

ROW = {"company": "Fictional Alpha Co", "domain": "alpha-fictional.example", "tier": "A"}
LABELS = [
    Label(
        row_id="r1",
        spec_sha256="a" * 16,
        csv_sha256="b" * 16,
        send_it=False,
        account_fit=False,
        fact_earns_its_place=True,
        frame_fits_seat=True,
        right_person=True,
    )
]
INTERNAL = {
    "r1": {
        "row_id": "r1",
        "email": "chief@alpha-fictional.example",
        "company": "Fictional Alpha Co",
        "company_domain": "alpha-fictional.example",
    }
}


@pytest.fixture
def env(one_product_profiles, tmp_path, monkeypatch):
    content = tmp_path / "content"
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    ps.upsert_latest("realshape", [ROW], "seed", product="alpha")
    monkeypatch.setattr(ew, "read_labels", lambda _p: LABELS)
    monkeypatch.setattr(ew, "_read_internal", lambda _p: INTERNAL)
    return content


def _argv(tmp_path, cmd, *extra):
    return [
        cmd, "--profile", "realshape", "--labels", "x", "--internal", "y",
        "--ledger", str(tmp_path / "suppression.csv"), *extra,
    ]  # fmt: skip


def test_a_dropped_product_refuses_before_anything_is_read(env, tmp_path, capsys):
    assert ew.main(_argv(tmp_path, "apply")) == 2
    assert "more than one product" in capsys.readouterr().err
    assert not (tmp_path / "suppression.csv").exists()


def test_a_second_products_eval_refuses_before_the_suppression_ledger_is_touched(
    env, tmp_path, capsys
):
    assert ew.main(_argv(tmp_path, "apply", "--product", "beta", "--force")) == 2
    assert "may not apply" in capsys.readouterr().err
    assert not (tmp_path / "suppression.csv").exists()
    assert next(i for i in ps.load_latest("realshape")["items"])["tier"] == "A"


def test_the_default_products_eval_still_disqualifies(env, tmp_path):
    assert ew.main(_argv(tmp_path, "apply", "--product", "alpha", "--force")) == 0
    (row,) = ps.load_latest("realshape")["items"]
    assert row["status"] == "disqualified"


def test_a_second_products_plan_is_a_dry_run_and_allowed(env, tmp_path, capsys):
    assert ew.main(_argv(tmp_path, "plan", "--product", "beta")) == 0
    assert "DRY RUN" in capsys.readouterr().out
