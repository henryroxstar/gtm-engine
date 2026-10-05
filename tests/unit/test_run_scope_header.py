"""The run header speaks plain words: no path, no slug, no digit (PRD R-B1b).

PROSPECTING.md's contract is "never a path, never a count", and the lede is what an operator reads
on a phone. Everything machine-shaped (slugs, file names) lives in the record section instead.
The two patterns below are the ones ``tests/lint/test_operator_guides_stay_true.py`` applies to
the operator guides, so the same ban holds for the text this code prints.
"""

from __future__ import annotations

import re

from gtm_core import prospect_status_cli, run_scope

_CONTENT_PATH_RE = re.compile(r"content/[\w<>/.-]+")
_STALE_COUNT_RE = re.compile(
    r"\b\d{1,3},\d{3}\b|\b\d{2,}\s+(?:accounts|rows|contacts|emails|people|sends|replies)\b"
)


def _scope(root, product):
    got = run_scope.resolve("realshape", product=product, profiles_root=root)
    assert isinstance(got, run_scope.RunScope)
    return got


def test_the_lede_names_the_product_and_carries_no_path_slug_or_digit(one_product_profiles):
    lede = " ".join(run_scope.header_lines(_scope(one_product_profiles, "beta")))
    assert "Prospecting for Beta Ledger." in lede
    assert "/" not in lede and "\\" not in lede
    assert not re.search(r"\d", lede)
    assert not _CONTENT_PATH_RE.search(lede) and not _STALE_COUNT_RE.search(lede)
    assert not re.search(r"\.(toml|md|json)\b", lede)
    assert "beta" not in lede.replace("Beta Ledger", "")  # the slug never appears bare


def test_one_fallback_sentence_names_every_shared_file_that_fell_back(one_product_profiles):
    beta = one_product_profiles / "realshape" / "products" / "beta"
    lines = run_scope.header_lines(_scope(one_product_profiles, "beta"))
    assert len(lines) == 2  # the lede + ONE sentence, not one per file
    assert "seat pains" in lines[1] and "buyer personas" in lines[1]
    assert lines[1].endswith("written for Alpha Relay.")
    (beta / "role-vocabulary.toml").write_text(
        (one_product_profiles / "realshape" / "knowledge" / "role-vocabulary.toml").read_text()
    )
    after = " ".join(run_scope.header_lines(_scope(one_product_profiles, "beta")))
    assert "seat pains" not in after and "buyer personas" in after
    (beta / "icp-personas.md").write_text("# beta personas\n")
    assert (
        len(run_scope.header_lines(_scope(one_product_profiles, "beta"))) == 1
    )  # nothing fell back


def test_the_default_product_names_itself_and_falls_back_from_nothing(one_product_profiles):
    assert run_scope.header_lines(_scope(one_product_profiles, "alpha")) == [
        "Prospecting for Alpha Relay."
    ]


def test_a_profile_with_no_second_product_gets_no_header_at_all(one_product_profiles):
    got = run_scope.resolve("oneprod", product="solo", profiles_root=one_product_profiles)
    assert isinstance(got, run_scope.RunScope)
    assert run_scope.header_lines(got) == [] and run_scope.record_lines(got) == []


def test_the_record_section_holds_the_machine_detail(one_product_profiles):
    record = "\n".join(run_scope.record_lines(_scope(one_product_profiles, "beta")))
    assert "product: beta (Beta Ledger)" in record
    assert "claims.toml" in record and "role-vocabulary.toml" in record


def test_the_status_block_is_titled_all_products_under_a_second_product(
    one_product_profiles, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    prospect_status_cli.main(["--profile", "realshape", "--product", "beta"])
    assert capsys.readouterr().out.splitlines()[0] == "All products"
    prospect_status_cli.main(["--profile", "realshape", "--product", "alpha"])
    assert "All products" not in capsys.readouterr().out
    assert prospect_status_cli.main(["--profile", "realshape"]) != 2  # a label, never a refusal
