"""Tests for the pre-load email compliance preflight (gtm_core.email_compliance)."""

from __future__ import annotations

import json

import pytest

from gtm_core import email_compliance as ec

SIG_GOOD = (
    "<div><br />Regards,<br />Jane Doe<br />Business Development<br />"
    "Example Pte. Ltd.  | UEN: 000000000X<br />15 Beach Road, #02-126,  <br />"
    "Singapore 189677 <br />www.example.com</div>"
)


def _accounts(*pairs):
    """Build a raw list_email_accounts-shaped payload from (email, signature) pairs."""
    return {
        "payload": {
            "emails": [
                {
                    "fromEmail": email,
                    "settings": [
                        {"code": "daily-sending-limit", "value": "10"},
                        {"code": "signature", "value": sig},
                    ],
                }
                for email, sig in pairs
            ]
        }
    }


def _settings(**codes):
    return {"payload": {"settings": [{"code": c, "value": v} for c, v in codes.items()]}}


# --------------------------------------------------------------------------- payload readers


def test_extract_signatures_reads_nested_payload():
    sigs = ec.extract_signatures(_accounts(("a@x.com", SIG_GOOD)))
    assert sigs == {"a@x.com": SIG_GOOD}


def test_extract_signatures_accepts_bare_list():
    bare = _accounts(("a@x.com", SIG_GOOD))["payload"]["emails"]
    assert ec.extract_signatures(bare) == {"a@x.com": SIG_GOOD}


def test_extract_signatures_missing_signature_is_empty_not_absent():
    payload = {"payload": {"emails": [{"fromEmail": "a@x.com", "settings": []}]}}
    assert ec.extract_signatures(payload) == {"a@x.com": ""}


def test_extract_settings_maps_code_to_value():
    assert ec.extract_settings(_settings(**{"1": "", "13": "1"})) == {1: "", 13: "1"}


# --------------------------------------------------------------------------- postal address


def test_address_passes_on_a_real_signature():
    assert ec.check_addresses({"a@x.com": SIG_GOOD}).status == "PASS"


def test_address_fails_on_empty_signature():
    r = ec.check_addresses({"a@x.com": ""})
    assert r.failed and "empty" in r.detail[0]


def test_address_fails_when_no_digits_so_no_street_number():
    r = ec.check_addresses({"a@x.com": "<div>Regards,<br />Jane Doe<br />Example Ltd</div>"})
    assert r.failed and "no digits" in r.detail[0]


def test_address_fails_on_a_bare_name():
    assert ec.check_addresses({"a@x.com": "Jane 1"}).failed


def test_address_fails_if_any_one_mailbox_is_blank():
    r = ec.check_addresses({"good@x.com": SIG_GOOD, "blank@x.com": ""})
    assert r.failed


def test_address_fails_when_no_mailboxes_at_all():
    assert ec.check_addresses({}).failed


# --------------------------------------------------------------------------- opt-out


def test_optout_passes_with_header_on_and_text_set():
    r = ec.check_optout({13: "1", 1: "", 2: "Reply 'stop' and I won't follow up."})
    assert r.status == "PASS"


def test_optout_fails_when_one_click_header_off():
    """The exact live state that prompted this gate: code 13 defaults to '0'."""
    r = ec.check_optout({13: "0", 2: "Reply 'stop'."})
    assert r.failed and any("code 13" in d for d in r.detail)


def test_optout_fails_when_no_link_and_no_text():
    r = ec.check_optout({13: "1", 1: "", 2: ""})
    assert r.failed and any("no visible opt-out" in d for d in r.detail)


def test_optout_missing_header_setting_is_a_failure_not_a_pass():
    assert ec.check_optout({2: "Reply 'stop'."}).failed


# --------------------------------------------------------------------------- markets


def test_markets_pass_when_every_lead_is_in_scope():
    rows = [
        {"email": "a@x.com", "country": "United States"},
        {"email": "b@x.com", "country": "Singapore"},
    ]
    assert ec.check_markets(rows, ["United States (primary)", "Singapore"]).status == "PASS"


def test_markets_normalize_aliases_and_annotations():
    rows = [{"email": "a@x.com", "country": "USA"}, {"email": "b@x.com", "country": "SG"}]
    assert ec.check_markets(rows, ["United States (primary)", "Singapore"]).status == "PASS"


def test_markets_fail_on_an_out_of_market_lead():
    rows = [{"email": "a@x.com", "country": "United Kingdom"}]
    r = ec.check_markets(rows, ["United States", "Singapore"])
    assert r.failed and any("a@x.com" in d for d in r.detail)


def test_unknown_country_warns_by_default_and_is_never_silently_allowed():
    rows = [{"email": "a@x.com", "country": "", "city": "Denver"}]
    r = ec.check_markets(rows, ["United States"])
    assert r.status == "WARN" and not r.failed
    assert any("no country" in d for d in r.detail)


def test_unknown_country_fails_under_strict():
    rows = [{"email": "a@x.com", "country": ""}]
    assert ec.check_markets(rows, ["United States"], strict=True).failed


def test_ledger_suppressed_rows_are_excluded_from_the_market_verdict():
    """A row the ledger suppresses is not being loaded, so it cannot fail the jurisdiction gate.

    Without this the gate can never go green on a correctly-suppressed list — and a gate that
    always fails is one people stop running (2026-08-12: 32 out-of-market leads enrolled).
    """
    rows = [
        {"email": "keep@acme.example", "country": "United States"},
        {"email": "drop@bracken.example", "country": "United Kingdom"},
    ]
    r = ec.check_markets(rows, ["United States"], suppressed={"drop@bracken.example"})
    assert r.status == "PASS"
    assert any("1 row(s) excluded by the suppression ledger" in d for d in r.detail)
    assert any("1 lead(s) checked" in d for d in r.detail)


def test_unsuppressed_out_of_market_row_still_fails_alongside_a_suppressed_one():
    """Suppressing one lead must not launder the rest — the gate stays fail-closed."""
    rows = [
        {"email": "drop@bracken.example", "country": "United Kingdom"},
        {"email": "missed@dorn.example", "country": "Germany"},
    ]
    r = ec.check_markets(rows, ["United States"], suppressed={"drop@bracken.example"})
    assert r.failed
    assert any("missed@dorn.example" in d for d in r.detail)
    assert not any("drop@bracken.example" in d for d in r.detail)


def test_suppression_column_in_the_file_is_ignored_without_a_ledger():
    """The file's own column is a cache the next rebuild discards — it must not gate anything."""
    rows = [
        {
            "email": "drop@bracken.example",
            "country": "United Kingdom",
            "suppression": "out-of-market",
        }
    ]
    assert ec.check_markets(rows, ["United States"]).failed


def test_global_market_is_a_wildcard_that_passes_every_country():
    rows = [
        {"email": "a@x.com", "country": "France"},
        {"email": "b@x.com", "country": "United Arab Emirates"},
    ]
    r = ec.check_markets(rows, ["United States", "Global"])
    assert r.status == "PASS"


# ------------------------------------------------- city ↔ country cross-examination
#
# Regression cases for the 2026-08-11 finding: 34 rows declared an in-market country while
# their city said India / UAE / Australia / Canada / China / Belgium. The declared value is
# the one being policed, so it cannot also be the only witness.


@pytest.mark.parametrize(
    "city,expected",
    [
        ("bengaluru", ("india", False)),
        ("bengaluru south", ("india", False)),  # token fallback past a trailing qualifier
        ("Hyderābād", ("india", False)),  # diacritics folded
        ("south delhi campus", ("india", False)),  # enrichment noise word dropped
        ("jakarta jakarta", ("indonesia", False)),  # duplicated token
        ("new delhi", ("india", False)),  # longest match wins over bare "delhi"
        ("Toronto", ("canada", False)),
        ("gent", ("belgium", False)),
        ("singapore", ("singapore", False)),
        ("Henderson, NV", (None, False)),  # state suffix stripped; unlisted US city
        ("san francisco bay area", (None, False)),  # unlisted stays silent
        ("", (None, False)),
    ],
)
def test_city_country_resolves(city, expected):
    assert ec.city_country(city) == expected


def test_new_london_is_connecticut_not_the_united_kingdom():
    """The bug a substring match would introduce: exact match must win before token fallback."""
    assert ec.city_country("new london") == ("united states", False)


def test_gentry_does_not_match_gent():
    """Token containment is whole-token, never a character-level prefix."""
    assert ec.city_country("gentry") == (None, False)


@pytest.mark.parametrize("city", ["London", "Birmingham", "Cambridge", "Manchester"])
def test_cities_shared_across_jurisdictions_are_ambiguous_not_guessed(city):
    assert ec.city_country(city) == (None, True)


@pytest.mark.parametrize("city", ["New York", "new york", "York Township"])
def test_ambiguity_is_judged_on_the_whole_name_not_a_token(city):
    """Regression: token-level matching read "york" inside "New York" and flagged all 14
    New York rows — noise that teaches the operator to skim past the market section."""
    assert ec.city_country(city) == (None, False)


def test_city_contradicting_an_in_market_country_fails():
    rows = [{"email": "a@x.com", "country": "United States", "city": "Bengaluru"}]
    r = ec.check_markets(rows, ["United States", "Singapore"])
    assert r.failed
    assert any("contradicts the declared country" in d for d in r.detail)
    assert any("implies India" in d for d in r.detail)


def test_city_conflict_is_reported_even_when_the_implied_country_is_also_in_market():
    """A US-declared row in Bengaluru is still a data defect once India is in scope —
    the row is sendable, but the record is wrong and the next sweep re-inherits it."""
    rows = [{"email": "a@x.com", "country": "United States", "city": "Bengaluru"}]
    r = ec.check_markets(rows, ["United States", "Singapore", "India"])
    assert r.failed
    assert any("in market" in d for d in r.detail)


def test_matching_city_and_country_passes_clean():
    rows = [{"email": "a@x.com", "country": "Singapore", "city": "Singapore"}]
    assert ec.check_markets(rows, ["United States", "Singapore"]).status == "PASS"


def test_unlisted_city_never_contradicts_a_good_country():
    rows = [{"email": "a@x.com", "country": "United States", "city": "Peabody"}]
    assert ec.check_markets(rows, ["United States"]).status == "PASS"


def test_ambiguous_city_warns_by_default_and_fails_under_strict():
    rows = [{"email": "a@x.com", "country": "United States", "city": "London"}]
    assert ec.check_markets(rows, ["United States"]).status == "WARN"
    assert ec.check_markets(rows, ["United States"], strict=True).failed


def test_wildcard_market_disables_the_city_cross_check_too():
    rows = [{"email": "a@x.com", "country": "United States", "city": "Shanghai"}]
    assert ec.check_markets(rows, ["Global"]).status == "PASS"


def test_out_of_market_listing_is_truncated_but_the_count_is_not():
    rows = [{"email": f"p{i}@x.com", "country": "France"} for i in range(25)]
    r = ec.check_markets(rows, ["United States"])
    assert r.failed
    assert any("25 lead(s) outside" in d for d in r.detail)
    assert any("and 5 more" in d for d in r.detail)


# --------------------------------------------------------------------------- target_markets parsing


def _write_profile(tmp_path, body: str) -> None:
    (tmp_path / "acme").mkdir(parents=True, exist_ok=True)
    (tmp_path / "acme" / "PROFILE.md").write_text(body, encoding="utf-8")


def test_read_target_markets_strips_comment_and_brackets(tmp_path):
    _write_profile(tmp_path, "target_markets:  [United States (primary), Singapore]   # narrowed\n")
    assert ec.read_target_markets("acme", tmp_path) == ["United States (primary)", "Singapore"]


def test_read_target_markets_ignores_prose_mentions(tmp_path):
    _write_profile(
        tmp_path,
        "> Adding to `target_markets:` is a compliance decision.\ntarget_markets:  [Singapore]\n",
    )
    assert ec.read_target_markets("acme", tmp_path) == ["Singapore"]


def test_read_target_markets_strips_single_quoted_items(tmp_path):
    _write_profile(
        tmp_path,
        "target_markets:  ['United States', 'United Kingdom', 'Singapore']\n",
    )
    assert ec.read_target_markets("acme", tmp_path) == [
        "United States",
        "United Kingdom",
        "Singapore",
    ]


def test_read_target_markets_strips_double_quoted_items(tmp_path):
    _write_profile(tmp_path, 'target_markets:  ["United States", "Singapore"]\n')
    assert ec.read_target_markets("acme", tmp_path) == ["United States", "Singapore"]


def test_read_target_markets_raises_on_region_name(tmp_path):
    """Confirmed 2026-09-14: a tenant's PROFILE.md declared `target_markets` with the region
    "Southeast Asia" alongside real countries. Compared literally against a lead's `country`,
    a region name never matches, so it must be rejected at parse time rather than silently
    failing every real lead in it (or, if ever auto-expanded, silently opening a whole region's
    worth of jurisdictions at once)."""
    _write_profile(
        tmp_path,
        "target_markets:  [United States (primary), Southeast Asia, Hong Kong]\n",
    )
    with pytest.raises(ValueError, match="Southeast Asia"):
        ec.read_target_markets("acme", tmp_path)


def test_normalize_market_resolves_sar_alias_to_hong_kong():
    assert ec.normalize_market("Hong Kong SAR") == "hong kong"
    assert ec.normalize_market("HKSAR") == "hong kong"


def test_check_markets_region_value_fails_every_real_lead_in_it():
    """If a region name ever reaches `check_markets` directly (e.g. via the CLI's `--market`
    override, which bypasses `read_target_markets`'s region guard), the pre-existing failure
    mode still holds: no lead's `country` is ever literally "southeast asia", so every real
    lead in the region is reported out-of-market rather than silently accepted."""
    rows = [
        {"email": "a@example.com", "country": "Singapore"},
        {"email": "b@example.com", "country": "Malaysia"},
    ]
    r = ec.check_markets(rows, ["Southeast Asia"])
    assert r.failed
    assert any("2 lead(s) outside target_markets" in d for d in r.detail)


def test_read_target_markets_raises_when_key_absent(tmp_path):
    _write_profile(tmp_path, "company: Acme\n")
    with pytest.raises(ValueError):
        ec.read_target_markets("acme", tmp_path)


def test_read_target_markets_raises_when_profile_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        ec.read_target_markets("nope", tmp_path)


# --------------------------------------------------------------------------- CLI gate


def _cli_files(tmp_path, *, sig=SIG_GOOD, header="1", country="United States"):
    (tmp_path / "accounts.json").write_text(
        json.dumps(_accounts(("a@x.com", sig))), encoding="utf-8"
    )
    (tmp_path / "settings.json").write_text(
        json.dumps(_settings(**{"13": header, "2": "Reply 'stop'."})), encoding="utf-8"
    )
    (tmp_path / "leads.csv").write_text(f"email,country\na@x.com,{country}\n", encoding="utf-8")
    return [
        "preflight",
        "--accounts-json",
        str(tmp_path / "accounts.json"),
        "--settings-json",
        str(tmp_path / "settings.json"),
        "--leads-csv",
        str(tmp_path / "leads.csv"),
        "--market",
        "United States",
    ]


def test_cli_exits_zero_when_everything_passes(tmp_path, capsys):
    assert ec.main(_cli_files(tmp_path)) == 0
    assert "not legal advice" in capsys.readouterr().out


def test_cli_exits_one_when_the_unsubscribe_header_is_off(tmp_path, capsys):
    assert ec.main(_cli_files(tmp_path, header="0")) == 1
    assert "DO NOT LOAD" in capsys.readouterr().out


def test_cli_exits_one_when_a_lead_is_out_of_market(tmp_path, capsys):
    assert ec.main(_cli_files(tmp_path, country="United Kingdom")) == 1
    assert "DO NOT LOAD" in capsys.readouterr().out


def test_cli_exits_two_when_given_nothing_to_check(tmp_path):
    assert ec.main(["preflight"]) == 2


def test_cli_markdown_emits_a_spec_table(tmp_path, capsys):
    ec.main(_cli_files(tmp_path) + ["--markdown"])
    assert "| Check | Verdict | Detail |" in capsys.readouterr().out
