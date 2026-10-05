"""``prospects status`` is a read of the shared ledger: it never refuses for want of a product.

Every other reader on a two-product profile refuses a run with no product. The status page is the
exception the PRD names — it reads no knowledge file and its numbers are every product's — so it
carries a label instead of a refusal, and only a product that IS named must be a real one.
"""

from __future__ import annotations

import pytest

from gtm_core import prospect_status_cli as cli

NOTHING_YET = 1  # no lanes-state.jsonl seeded: the page's own "nothing to show yet", not a refusal


@pytest.fixture
def env(one_product_profiles, tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))


def test_no_product_on_a_two_product_profile_is_labelled_not_refused(env, capsys):
    assert cli.main(["--profile", "realshape"]) == NOTHING_YET
    out = capsys.readouterr()
    assert out.out.splitlines()[0] == "All products"


def test_a_named_second_product_is_labelled_the_same_way(env, capsys):
    assert cli.main(["--profile", "realshape", "--product", "beta"]) == NOTHING_YET
    assert capsys.readouterr().out.splitlines()[0] == "All products"


def test_a_single_product_profile_prints_no_label(env, capsys):
    assert cli.main(["--profile", "oneprod"]) == NOTHING_YET
    assert "All products" not in capsys.readouterr().out


def test_an_unknown_product_still_refuses(env, capsys):
    assert cli.main(["--profile", "realshape", "--product", "gamma"]) == 2
    assert "not one of this company's products" in capsys.readouterr().err
