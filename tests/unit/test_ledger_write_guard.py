"""A second product never changes a row the shared ledger already holds (PRD R-B2b, design A).

``latest.json`` holds one row per account and no product field. Every field on an existing row is
either the default product's decision (tier, verdict, lane, the contact it chose) or a company fact
that has its own blank-only writer (``firmographics apply``). So a run for a second product may only
ADD companies nobody has yet — identity fields only — and never touches a matched row. Status is
account-wide, so a reply or an opt-out (recorded with no product) still works.

Fictional data only. The seed is written as the default product on the two-product fixture, because
a write with no product refuses there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import prospects_state as ps
from gtm_core.prospects_item import CANONICAL_FIELDS, IDENTITY_FIELDS

ALPHA_ROW = {
    "company": "Fictional Alpha Co",
    "domain": "alpha-fictional.example",
    "segment": "enterprise",
    "market": "singapore",
    "contact_name": "Ada Example",
    "contact_title": "Head of Platform",
    "tier": "A",
    "score": "9",
    "verdict": "send",
    "lane": "champion",
    "why_now": "runs relays at scale",
}
DELTA = {
    "company": "Fictional Delta Co",
    "domain": "delta-fictional.example",
    "segment": "enterprise",
    "market": "singapore",
    "contact_name": "Bo Example",
    "contact_title": "Head of Data",
}
FIT_KEYS = {"tier", "score", "verdict", "lane", "why_now", "status"}


@pytest.fixture
def env(one_product_profiles, tmp_path, monkeypatch):
    content = tmp_path / "content"
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    ps.upsert_latest(
        "realshape",
        [ALPHA_ROW],
        "seed-run",
        generated_at="2026-01-01T00:00:00+00:00",
        product="alpha",
    )
    return content


def _latest(env) -> Path:
    return env / "realshape" / "prospects" / "latest.json"


def _row(company="Fictional Alpha Co"):
    return next(i for i in ps.load_latest("realshape")["items"] if i["company"] == company)


# --- a matched row is never changed by a second product -----------------------------------------


def test_beta_matching_an_existing_account_leaves_the_whole_row_deep_equal(env):
    before = _row()
    summary = ps.upsert_latest(
        "realshape",
        [
            {
                **{k: v for k, v in ALPHA_ROW.items() if k in IDENTITY_FIELDS},
                "segment": "smb",  # would overwrite the segment Gateway holds
                "contact_name": "Somebody Else",  # would overwrite the contact Gateway chose
                "contact_title": "Intern",
                "city": "Fictional City",  # a blank identity field: still not beta's to fill
            }
        ],
        "beta-run",
        product="beta",
    )
    assert _row() == before  # every key, including account_id and added_at
    assert summary["existing_untouched"] == 1
    assert summary["updated"] == 0 and summary["added"] == 0


def test_beta_fit_fields_on_a_matched_account_refuse_and_write_nothing(env):
    before = _latest(env).read_bytes()
    with pytest.raises(ps.LedgerFitRefused) as err:
        ps.upsert_latest(
            "realshape",
            [{**ALPHA_ROW, "tier": "C", "verdict": "drop"}],
            "beta-run",
            product="beta",
        )
    assert "tier" in str(err.value) and "verdict" in str(err.value)
    assert _latest(env).read_bytes() == before


def test_identity_only_drops_the_fit_and_still_leaves_the_row_deep_equal(env):
    before = _row()
    summary = ps.upsert_latest(
        "realshape",
        [{**ALPHA_ROW, "tier": "C", "verdict": "drop", "lane": "generic"}],
        "beta-run",
        product="beta",
        identity_only=True,
    )
    assert summary["fit_held_back"] == 5  # tier, score, verdict, lane, why_now
    assert _row() == before


# --- a company nobody has yet may be added, identity only ---------------------------------------


def test_beta_adds_a_new_company_with_identity_and_no_fit(env):
    summary = ps.upsert_latest(
        "realshape",
        [{**DELTA, "tier": "A", "score": "9"}],
        "beta-run",
        product="beta",
        identity_only=True,
    )
    row = _row("Fictional Delta Co")
    assert summary["added"] == 1
    assert not FIT_KEYS & set(row)
    assert row["contact_name"] == "Bo Example" and row["account_id"].startswith("a-")


def test_new_account_defaults_cannot_smuggle_fit_in_for_beta(env):
    ps.upsert_latest(
        "realshape",
        [DELTA],
        "beta-run",
        product="beta",
        new_account_defaults=lambda item: {**item, "tier": "C", "score": "0", "status": "new"},
    )
    assert not FIT_KEYS & set(_row("Fictional Delta Co"))


def test_blank_fit_values_are_not_a_write(env):
    ps.upsert_latest(
        "realshape", [{**DELTA, "tier": "", "verdict": None}], "beta-run", product="beta"
    )  # would raise if a blank counted


# --- who may write, and with which product ------------------------------------------------------


def test_no_product_refuses_on_a_two_product_profile_and_writes_nothing(env):
    before = _latest(env).read_bytes()
    with pytest.raises(ValueError, match="more than one product"):
        ps.upsert_latest("realshape", [DELTA], "run")
    with pytest.raises(ValueError, match="more than one product"):
        ps.mutate_account("realshape", "Fictional Alpha Co", {"contact_title": "CTO"})
    assert _latest(env).read_bytes() == before


def test_no_product_and_the_default_product_write_identically_on_a_one_product_profile(
    one_product_profiles, tmp_path, monkeypatch
):
    item = {**DELTA, "tier": "B", "score": "6"}
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    results = []
    for product in (None, "solo"):
        monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / f"c-{product}"))
        ps.upsert_latest(
            "oneprod", [item], "run", generated_at="2026-01-02T00:00:00+00:00", product=product
        )
        data = ps.load_latest("oneprod")
        for row in data["items"]:
            row.pop("account_id", None)
        results.append(data)
    assert results[0] == results[1]
    assert results[0]["items"][0]["tier"] == "B"


def test_the_default_product_updates_an_existing_row_as_it_always_did(env):
    ps.upsert_latest(
        "realshape",
        [{**ALPHA_ROW, "contact_title": "CTO"}],
        "alpha-run",
        product="alpha",
    )
    assert _row()["contact_title"] == "CTO"


def test_mutate_account_refuses_a_second_product_outright(env):
    before = _row()
    for updates in ({"verdict": "drop"}, {"contact_title": "Head of Platform"}):
        with pytest.raises(ps.LedgerFitRefused):
            ps.mutate_account("realshape", "Fictional Alpha Co", updates, product="beta")
    assert _row() == before
    ps.mutate_account("realshape", "Fictional Alpha Co", {"contact_title": "CTO"}, product="alpha")
    assert _row()["contact_title"] == "CTO"


def test_mutate_accounts_unattended_carries_the_refusal(env):
    with pytest.raises(ps.LedgerFitRefused):
        ps.mutate_accounts_unattended(
            "realshape", {"Fictional Alpha Co": {"contact_title": "CTO"}}, product="beta"
        )


def test_status_is_an_account_wide_fact_a_reply_can_record_with_no_product(env):
    key = f"a:{_row()['account_id']}"
    out = ps.set_status("realshape", {key: "disqualified"}, reason="opt-out", source="reply")
    assert out["changed"] == 1 and _row()["status"] == "disqualified"


def test_a_second_products_run_cannot_set_status_or_restore(env):
    before = _latest(env).read_bytes()
    key = f"a:{_row()['account_id']}"
    with pytest.raises(ps.LedgerFitRefused):
        ps.set_status("realshape", {key: "disqualified"}, product="beta")
    with pytest.raises(ps.LedgerFitRefused):
        ps.restore("realshape", product="beta")
    assert _latest(env).read_bytes() == before
    ps.set_status("realshape", {}, product="alpha")  # the default product is unguarded


def test_the_allowlist_holds_back_every_canonical_fit_field():
    fit = {"tier", "score", "heat", "why_now", "verdict", "verdict_reason", "lane", "lane_reason",
           "hook_cell", "signal_evidence", "signal_source_url", "signal_subject", "signal_observed",
           "signal_agent_kind", "category_relation", "signal_column", "qualification_path",
           "status", "priority", "intent_feeds", "new_in_role"}  # fmt: skip
    assert not fit & IDENTITY_FIELDS
    assert fit & set(CANONICAL_FIELDS) <= set(CANONICAL_FIELDS) - IDENTITY_FIELDS


#: Typed out on purpose. A test that derived its expectation from ``IDENTITY_FIELDS`` could not fail
#: when a fit field (tier, verdict, lane, status ...) was added to it, which is the one edit that
#: would let a second product overwrite the default product's judgment. Widening the allowlist is a
#: deliberate change, and it has to be made here too.
EXPECTED_IDENTITY = frozenset(
    {
        "account_id",
        "added_at",
        "city",
        "company",
        "company_domain",
        "contact_email",
        "contact_linkedin_url",
        "contact_name",
        "contact_note",
        "contact_phone",
        "contact_resolved_at",
        "contact_resolved_by",
        "contact_resolved_on",
        "contact_source",
        "contact_title",
        "contact_verified",
        "country",
        "description",
        "domain",
        "domain_note",
        "domain_source",
        "email",
        "email_grade",
        "email_resolved_at",
        "email_source",
        "email_status",
        "employees_range",
        "firmo_on",
        "firmo_source",
        "id",
        "industry",
        "market",
        "market_basis",
        "merged_aliases",
        "merged_domains",
        "region",
        "revenue_range",
        "secondary_contact_email",
        "secondary_contact_name",
        "secondary_contact_role",
        "secondary_contact_title",
        "segment",
        "segment_basis",
    }
)


def test_the_identity_allowlist_is_exactly_the_reviewed_set():
    assert IDENTITY_FIELDS == EXPECTED_IDENTITY
    judgments = {"tier", "score", "verdict", "lane", "status", "why_now", "hook_cell", "heat"}
    assert not judgments & EXPECTED_IDENTITY


# --- the CLI: each call is its own process ------------------------------------------------------


def _run(argv, capsys):
    code = ps._cli(argv)
    return code, capsys.readouterr()


def test_cli_merge_refuses_a_dropped_product_on_a_multi_product_profile(env, tmp_path, capsys):
    items = tmp_path / "items.json"
    items.write_text(json.dumps([ALPHA_ROW]))
    code, out = _run(
        ["merge", "--profile", "realshape", "--items", str(items), "--source-run", "r"], capsys
    )
    assert code == 2 and "more than one product" in out.err


def test_cli_merge_for_beta_needs_identity_only_when_it_carries_fit(env, tmp_path, capsys):
    items = tmp_path / "items.json"
    items.write_text(json.dumps([{**DELTA, "tier": "A"}]))
    base = [
        "merge", "--profile", "realshape", "--items", str(items), "--source-run", "r",
        "--product", "beta",
    ]  # fmt: skip
    code, out = _run(base, capsys)
    assert code == 2 and "identity-only" in out.err
    code, out = _run([*base, "--identity-only"], capsys)
    assert code == 0 and json.loads(out.out)["fit_held_back"] > 0


def test_cli_on_a_single_product_profile_needs_no_flag(
    one_product_profiles, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    items = tmp_path / "items.json"
    items.write_text(json.dumps([ALPHA_ROW]))
    code, out = _run(
        ["merge", "--profile", "oneprod", "--items", str(items), "--source-run", "r"], capsys
    )
    assert code == 0 and "fit_held_back" not in out.out


# --- finalize: the export a second product writes -----------------------------------------------

_UNSCORED = {**DELTA, "email": "bo@delta-fictional.example"}
# Gateway's company as a second product would meet it: identity only, no score to attribute.
ALPHA_UNSCORED = {
    **{k: v for k, v in ALPHA_ROW.items() if k in IDENTITY_FIELDS},
    "email": "ada@alpha-fictional.example",
}


def _read_csv(path: str):
    import csv

    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_finalize_for_beta_writes_beside_neither_pool_and_carries_the_account_id(env):
    from gtm_core import prospects_import as pi

    with pytest.raises(ps.LedgerFitRefused):
        pi.finalize("realshape", [{**_UNSCORED, "tier": "A"}], "run-b1", product="beta")
    assert "Fictional Delta Co" not in {i["company"] for i in ps.load_latest("realshape")["items"]}

    summary = pi.finalize(
        "realshape", [{**_UNSCORED, "tier": "A"}], "run-b2", product="beta", identity_only=True
    )
    csv_path = Path(summary["hubspot_csv"])
    assert csv_path.parent.name == "beta" and csv_path.parent.parent.name == "by-product"
    # consolidate folds in every prospects-*-hubspot.csv BESIDE latest.json; beta's is not one
    assert not list((env / "realshape" / "prospects").glob("prospects-*-hubspot.csv"))
    (row,) = _read_csv(summary["hubspot_csv"])
    assert row["GTM_Account_ID"] == _row("Fictional Delta Co")["account_id"] != ""


def test_a_beta_export_of_an_existing_account_joins_back_by_id_and_changes_nothing(env):
    from gtm_core import prospects_import as pi

    before = _row()
    summary = pi.finalize(
        "realshape",
        [ALPHA_UNSCORED],
        "run-b3",
        product="beta",
        identity_only=True,
    )
    (row,) = _read_csv(summary["hubspot_csv"])
    assert row["GTM_Account_ID"] == before["account_id"]
    assert _row() == before


def test_the_default_products_drop_verdict_does_not_exclude_a_beta_row(env):
    from gtm_core import prospects_import as pi

    ps.mutate_account("realshape", "Fictional Alpha Co", {"verdict": "drop"}, product="alpha")
    summary = pi.finalize(
        "realshape",
        [ALPHA_UNSCORED],
        "run-b4",
        product="beta",
        identity_only=True,
    )
    assert summary["excluded_retired"] == 0 and len(_read_csv(summary["hubspot_csv"])) == 1


def test_a_retired_account_is_excluded_from_a_beta_export_too(env):
    from gtm_core import prospects_import as pi

    ps.set_status("realshape", {f"a:{_row()['account_id']}": "disqualified"})
    summary = pi.finalize(
        "realshape",
        [ALPHA_UNSCORED],
        "run-b5",
        product="beta",
        identity_only=True,
    )
    assert summary["excluded_retired"] == 1 and _read_csv(summary["hubspot_csv"]) == []


def test_finalize_for_the_default_product_writes_where_it_always_did(env):
    from gtm_core import prospects_import as pi

    summary = pi.finalize("realshape", [{**_UNSCORED, "tier": "A"}], "run-a1", product="alpha")
    assert Path(summary["hubspot_csv"]).parent == env / "realshape" / "prospects"
    assert "GTM_Account_ID" not in _read_csv(summary["hubspot_csv"])[0]
    assert _row("Fictional Delta Co")["tier"] == "A"


def test_promote_is_refused_for_a_second_product_before_anything_is_written(env, tmp_path, capsys):
    from gtm_core import signal_backfill as sb

    list_csv = tmp_path / "list.csv"
    list_csv.write_text("email,company\nada@delta-fictional.example,Fictional Delta Co\n")
    records = tmp_path / "records.json"
    records.write_text("[]")
    argv = [
        "--list",
        str(list_csv),
        "--records",
        str(records),
        "--promote",
        "--profile",
        "realshape",
    ]
    assert sb.main([*argv, "--product", "beta"]) == 2
    assert "refused" in capsys.readouterr().err
    assert not (tmp_path / "list-recorded.csv").exists()
    assert sb.main(argv) == 2  # a dropped product refuses too
    assert "more than one product" in capsys.readouterr().err


def test_a_named_default_product_gets_the_summary_it_always_got(env):
    """The second-product keys (``existing_untouched``, ``fit_held_back``) appear only for a second
    product. A run that names the DEFAULT product must see exactly today's summary (mutation pass:
    a flipped condition put ``fit_held_back`` on it and no test noticed)."""
    summary = ps.upsert_latest("realshape", [DELTA], "alpha-run", product="alpha")
    assert not {"fit_held_back", "existing_untouched"} & set(summary)
    second = ps.upsert_latest("realshape", [DELTA], "beta-run", product="beta", identity_only=True)
    assert second["fit_held_back"] == 0 and second["existing_untouched"] == 1


def test_promote_for_the_default_product_still_reaches_the_ledger_on_a_two_product_company(
    env, tmp_path, monkeypatch
):
    """The guard refuses a second product; the DEFAULT product's --promote must still work. It
    called ``upsert_latest`` with no product, which refuses on a company with a second product, so
    the first default-product promote after a second product went live would have crashed after the
    recorded list was written (fresh audit, B1)."""
    from gtm_core import signal_backfill as sb

    list_csv = tmp_path / "list.csv"
    list_csv.write_text("email,company\nada@alpha-fictional.example,Fictional Alpha Co\n")
    records = tmp_path / "records.json"
    records.write_text("[]")
    monkeypatch.setattr(
        sb,
        "promote_records",
        lambda _records, _rows, _profile: ([{**ALPHA_UNSCORED, "why_now": "shipped a relay"}], []),
    )
    argv = [
        "--list",
        str(list_csv),
        "--records",
        str(records),
        "--promote",
        "--profile",
        "realshape",
    ]
    assert sb.main([*argv, "--product", "alpha"]) == 0
    assert _row()["why_now"] == "shipped a relay"


def test_a_second_product_leaves_a_legacy_row_undated_and_only_dates_what_it_creates(env):
    """A row written before ``added_at`` existed has none. The default product's next merge dates it;
    a second product's must not (it never changes a row that exists, and ``added_at`` is the row's
    retention age). The deep-equal test above seeds through ``upsert_latest`` and so cannot see this."""
    latest = env / "realshape" / "prospects" / "latest.json"
    data = json.loads(latest.read_text())
    for row in data["items"]:
        row.pop("added_at", None)
    latest.write_text(json.dumps(data))
    ps.upsert_latest("realshape", [DELTA], "beta-run", product="beta", identity_only=True)
    assert "added_at" not in _row()
    assert _row("Fictional Delta Co")["added_at"]
    ps.upsert_latest("realshape", [DELTA], "alpha-run", product="alpha")
    assert _row()["added_at"]  # the default product's merge dates it, as it always did


def test_finalize_writes_the_export_under_the_slug_whatever_name_the_caller_used(env):
    from gtm_core import prospects_import as pi

    summary = pi.finalize(
        "realshape", [_UNSCORED], "run-name", product="Beta Ledger", identity_only=True
    )
    assert Path(summary["hubspot_csv"]).parent.name == "beta"


def test_a_declared_product_with_no_files_cannot_write_fit_to_the_shared_ledger(
    one_product_profiles, tmp_path, monkeypatch
):
    """Red team F1: the guard used to depend on how many files a product folder held. A product
    named with NONE resolved as the default product and its fit fields overwrote the shared row."""
    path = one_product_profiles / "oneprod" / "PROFILE.md"
    path.write_text(
        path.read_text().replace(
            "  - { slug: solo, name: Solo Relay, capabilities: [solo] }\n",
            "  - { slug: solo, name: Solo Relay, capabilities: [solo] }\n"
            "  - { slug: extra, name: Extra Desk, capabilities: [extra] }\n",
        )
    )
    (one_product_profiles / "oneprod" / "products" / "extra").mkdir(parents=True)
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    ps.upsert_latest("oneprod", [ALPHA_ROW], "seed", product="solo")
    before = ps.load_latest("oneprod")["items"][0]
    with pytest.raises(ps.LedgerFitRefused):
        ps.upsert_latest("oneprod", [{**ALPHA_ROW, "tier": "C"}], "run", product="extra")
    with pytest.raises(ps.LedgerFitRefused):
        ps.mutate_account("oneprod", "Fictional Alpha Co", {"verdict": "drop"}, product="extra")
    assert ps.load_latest("oneprod")["items"][0] == before
    ps.upsert_latest("oneprod", [{**ALPHA_ROW, "tier": "B"}], "run", product="solo")  # the default
    assert ps.load_latest("oneprod")["items"][0]["tier"] == "B"
