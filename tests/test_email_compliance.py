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


def test_global_market_is_a_wildcard_that_passes_every_country():
    rows = [
        {"email": "a@x.com", "country": "France"},
        {"email": "b@x.com", "country": "United Arab Emirates"},
    ]
    r = ec.check_markets(rows, ["United States", "Global"])
    assert r.status == "PASS"


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
