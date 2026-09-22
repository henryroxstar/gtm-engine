"""Tests for the pre-load email compliance preflight (gtm_core.email_compliance)."""

from __future__ import annotations

import json

import pytest

from gtm_core import capability_preflight as cp
from gtm_core import email_compliance as ec

# Wholly invented (§R9). The only load-bearing properties are the ones check_addresses
# reads: at least one digit and six or more words once the markup is stripped. The
# doubled spaces and trailing blanks are deliberate — they exercise the whitespace
# collapse in _plain(). A real postal address here buys nothing and ships in the carve.
SIG_GOOD = (
    "<div><br />Regards,<br />Jane Doe<br />Business Development<br />"
    "Lantern Capital Ltd.  | UEN: 000000000X<br />12 Lantern Quay, #08-01,  <br />"
    "Singapore 000000 <br />www.example.com</div>"
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


# ------------------------------------------------------- SC2/SC4: capability assertions
#
# The ladder's five rungs, the two kill switches, and the one property that matters most:
# every rung's default is the refusing one, so a capability nobody has read cannot pass.

from pathlib import Path  # noqa: E402

REAL_SEQ_REGISTRY = Path(cp.__file__).resolve().parent / "sequencers.toml"

_CAP_ROW = 'source = "https://vendor.example/docs"\nverified_on = "2026-09-21"\n'


def _registry(tmp_path, **caps):
    """Build a registry with one provider `acme` and the given {name: extra-toml} rows."""
    body = ""
    for name, extra in caps.items():
        body += f"\n[providers.acme.capabilities.{name}]\n{extra}{_CAP_ROW}"
    p = tmp_path / "sequencers.toml"
    p.write_text(body, encoding="utf-8")
    return p


def _policy(monkeypatch, **policy):
    # Patch the OWNING module: SC2/SC4 live in gtm_core.capability_preflight and
    # email_compliance only re-exports the names, so patching the re-export would
    # leave the code under test reading the real policy.
    monkeypatch.setattr(cp, "CAPABILITY_POLICY", dict(policy))


# --- rung 1: the registry does not grant ------------------------------------------------


def test_rung1_ungranted_capability_blocks(tmp_path, monkeypatch):
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(tmp_path, stop_on_reply='supported = "unknown"\n')
    r = cp.check_capabilities("acme", None, registry_path=reg)
    assert r.status == "FAIL"
    assert "does not grant" in r.detail[0]


def test_rung1_absent_row_blocks(tmp_path, monkeypatch):
    """A provider with no row for this capability refuses exactly as `unknown` does."""
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(tmp_path, other_cap="supported = true\n")
    r = cp.check_capabilities("acme", None, registry_path=reg)
    assert r.status == "FAIL"


# --- rungs 2 & 3: the setting reads back ------------------------------------------------


def test_rung2_live_read_on_passes(tmp_path, monkeypatch):
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(tmp_path, stop_on_reply="supported = true\nreadable_via_api = true\n")
    r = cp.check_capabilities(
        "acme", _settings(**{"7": "1"}), setting_codes={"stop_on_reply": 7}, registry_path=reg
    )
    assert r.status == "PASS"
    assert "is ON" in r.detail[0]


def test_rung3_live_read_off_blocks_with_the_ui_path(tmp_path, monkeypatch):
    """The test plan's break-on-purpose case: finish-on-reply OFF must FAIL."""
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(
        tmp_path,
        stop_on_reply='supported = true\nreadable_via_api = true\nui_path = "Sequence > Safety"\n',
    )
    r = cp.check_capabilities(
        "acme", _settings(**{"7": "0"}), setting_codes={"stop_on_reply": 7}, registry_path=reg
    )
    assert r.status == "FAIL"
    assert "Sequence > Safety" in r.detail[0]


@pytest.mark.parametrize("live", ["0", "", "true", "True", "yes", "2"])
def test_only_the_literal_one_counts_as_on(tmp_path, monkeypatch, live):
    """`_SETTING_ON` is a closed list. An unrecognised live value blocks, never passes."""
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(tmp_path, stop_on_reply="supported = true\nreadable_via_api = true\n")
    r = cp.check_capabilities(
        "acme", _settings(**{"7": live}), setting_codes={"stop_on_reply": 7}, registry_path=reg
    )
    assert r.status == "FAIL"


def test_a_readable_capability_with_no_registered_code_blocks(tmp_path, monkeypatch):
    """readable_via_api says yes but nothing maps it to a code — a mismatch refuses."""
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(tmp_path, stop_on_reply="supported = true\nreadable_via_api = true\n")
    r = cp.check_capabilities("acme", _settings(**{"7": "1"}), setting_codes={}, registry_path=reg)
    assert r.status == "FAIL"
    assert "no provider setting code" in r.detail[0]


def test_a_code_absent_from_the_payload_blocks(tmp_path, monkeypatch):
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(tmp_path, stop_on_reply="supported = true\nreadable_via_api = true\n")
    r = cp.check_capabilities(
        "acme", _settings(**{"13": "1"}), setting_codes={"stop_on_reply": 7}, registry_path=reg
    )
    assert r.status == "FAIL"
    assert "not present in the settings payload" in r.detail[0]


# --- rung 4: attestation ------------------------------------------------------------------


def test_rung4_attested_is_warn_never_pass(tmp_path, monkeypatch):
    _policy(monkeypatch, ooo_auto_pause="blocking")
    reg = _registry(tmp_path, ooo_auto_pause="supported = true\nreadable_via_api = false\n")
    r = cp.check_capabilities("acme", None, attested={"ooo_auto_pause"}, registry_path=reg)
    assert r.status == "WARN"  # does not block...
    assert "ATTESTED" in r.detail[0]  # ...but never renders as a live-read PASS


def test_rung4_without_an_attestation_blocks(tmp_path, monkeypatch):
    _policy(monkeypatch, ooo_auto_pause="blocking")
    reg = _registry(tmp_path, ooo_auto_pause="supported = true\nreadable_via_api = false\n")
    r = cp.check_capabilities("acme", None, registry_path=reg)
    assert r.status == "FAIL"
    assert "--attest ooo_auto_pause" in r.detail[0]


def test_attest_is_refused_for_a_readable_capability(tmp_path, monkeypatch):
    """A human's say-so may never overwrite something the API can actually read."""
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(tmp_path, stop_on_reply="supported = true\nreadable_via_api = true\n")
    r = cp.check_capabilities(
        "acme",
        _settings(**{"7": "1"}),
        attested={"stop_on_reply"},
        setting_codes={"stop_on_reply": 7},
        registry_path=reg,
    )
    assert r.status == "FAIL"
    assert "attest-refused" in r.detail[0]


def test_attest_naming_an_unknown_capability_blocks(tmp_path, monkeypatch):
    _policy(monkeypatch, ooo_auto_pause="blocking")
    reg = _registry(tmp_path, ooo_auto_pause="supported = true\nreadable_via_api = false\n")
    r = cp.check_capabilities("acme", None, attested={"wharrgarbl"}, registry_path=reg)
    assert r.status == "FAIL"
    assert "nothing asserts" in r.detail[0]


def test_an_attestation_does_not_survive_into_the_next_run(tmp_path, monkeypatch):
    """Attestation is per-run by construction: it is an argument, never stored state."""
    _policy(monkeypatch, ooo_auto_pause="blocking")
    reg = _registry(tmp_path, ooo_auto_pause="supported = true\nreadable_via_api = false\n")
    assert (
        cp.check_capabilities("acme", None, attested={"ooo_auto_pause"}, registry_path=reg).status
        == "WARN"
    )
    assert cp.check_capabilities("acme", None, registry_path=reg).status == "FAIL"


# --- rung 5: unresolved readability ---------------------------------------------------------


@pytest.mark.parametrize(
    "readable", ['readable_via_api = "unknown"\n', "", 'readable_via_api = ""\n']
)
def test_rung5_unresolved_readability_blocks(tmp_path, monkeypatch, readable):
    """No attestation passed, so this reaches rung 5 itself rather than the attest guard."""
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(tmp_path, stop_on_reply=f"supported = true\n{readable}")
    r = cp.check_capabilities("acme", None, registry_path=reg)
    assert r.status == "FAIL"
    assert "readable_via_api is" in r.detail[0]


@pytest.mark.parametrize(
    "readable", ['readable_via_api = "unknown"\n', "", 'readable_via_api = ""\n']
)
def test_rung5_cannot_be_attested_away(tmp_path, monkeypatch, readable):
    """The other half: an operator cannot substitute their say-so for the missing evidence
    while nobody knows whether the setting is readable at all."""
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(tmp_path, stop_on_reply=f"supported = true\n{readable}")
    r = cp.check_capabilities("acme", None, attested={"stop_on_reply"}, registry_path=reg)
    assert r.status == "FAIL"
    assert "attest-refused" in r.detail[0]


def test_the_shipped_registry_blocks_stop_on_reply_today():
    """The PRD's intended state until SC2's live read lands: this is not a bug, and a change
    that makes it pass without a dated registry row is the defect."""
    r = cp.check_capabilities("saleshandy", None, registry_path=REAL_SEQ_REGISTRY)
    assert r.status == "FAIL"
    assert any("stop_on_reply" in d and "FAIL" in d for d in r.detail)


# --- policy: advisory never blocks -------------------------------------------------------


def test_an_advisory_capability_never_blocks(tmp_path, monkeypatch):
    _policy(monkeypatch, reply_categories="advisory")
    reg = _registry(tmp_path, reply_categories='supported = "unknown"\n')
    r = cp.check_capabilities("acme", None, registry_path=reg)
    assert r.status == "WARN"
    assert "does not block" in r.detail[0]


def test_the_policy_map_is_not_empty():
    """Instrument check: an empty policy would make every capability test pass vacuously."""
    assert cp.CAPABILITY_POLICY
    assert set(cp.CAPABILITY_POLICY.values()) <= {"blocking", "advisory"}
    assert "blocking" in cp.CAPABILITY_POLICY.values()


# --- auto-set is opt-in and allowlisted ----------------------------------------------------


def _autoset_registry(tmp_path):
    return _registry(tmp_path, stop_on_reply="supported = true\nreadable_via_api = true\n")


@pytest.mark.parametrize("env", [None, "", "false", "0", "no", "TRUEISH", "  "])
def test_autoset_is_off_unless_the_env_says_a_recognised_true(monkeypatch, env):
    if env is None:
        monkeypatch.delenv("GTM_CAPABILITY_AUTOSET_ENABLED", raising=False)
    else:
        monkeypatch.setenv("GTM_CAPABILITY_AUTOSET_ENABLED", env)
    assert cp.autoset_enabled() is False


@pytest.mark.parametrize("env", ["true", "1", "yes", "on", "TRUE", " On "])
def test_autoset_recognises_its_closed_list_of_true_values(monkeypatch, env):
    monkeypatch.setenv("GTM_CAPABILITY_AUTOSET_ENABLED", env)
    assert cp.autoset_enabled() is True


def test_no_autoset_plan_line_when_the_switch_is_off(tmp_path, monkeypatch):
    _policy(monkeypatch, stop_on_reply="blocking")
    r = cp.check_capabilities(
        "acme",
        _settings(**{"7": "0"}),
        setting_codes={"stop_on_reply": 7},
        autoset_enabled=False,
        autoset_allowlist=frozenset({7}),
        registry_path=_autoset_registry(tmp_path),
    )
    assert r.status == "FAIL"
    assert not any("AUTOSET PLAN" in d for d in r.detail)


def test_a_key_outside_the_allowlist_still_blocks_with_autoset_on(tmp_path, monkeypatch):
    _policy(monkeypatch, stop_on_reply="blocking")
    r = cp.check_capabilities(
        "acme",
        _settings(**{"7": "0"}),
        setting_codes={"stop_on_reply": 7},
        autoset_enabled=True,
        autoset_allowlist=frozenset({13}),  # 7 is NOT on it
        registry_path=_autoset_registry(tmp_path),
    )
    assert r.status == "FAIL"
    assert not any("AUTOSET PLAN" in d for d in r.detail)


def test_an_allowlisted_key_with_autoset_on_plans_but_does_not_pass(tmp_path, monkeypatch):
    """The mechanism is real (this is the positive control), but a PLAN is not a live read:
    it renders as ATTESTED/WARN, never PASS, because nothing has been written yet."""
    _policy(monkeypatch, stop_on_reply="blocking")
    r = cp.check_capabilities(
        "acme",
        _settings(**{"7": "0"}),
        setting_codes={"stop_on_reply": 7},
        autoset_enabled=True,
        autoset_allowlist=frozenset({7}),
        registry_path=_autoset_registry(tmp_path),
    )
    assert r.status == "WARN"
    assert any("AUTOSET PLAN" in d for d in r.detail)


def test_reading_a_setting_code_does_not_make_it_auto_settable():
    """READABLE AND AUTO-SETTABLE ARE DIFFERENT PERMISSIONS, and as of 2026-09-22 this test can
    finally tell them apart. It used to assert both collections were empty, which could not
    discriminate "we deliberately refuse to auto-set this" from "we have not read anything yet"
    (§R18). Now `stop_on_reply` = 3 is read and registered, and the allowlist is still empty —
    so the assertion has content: learning a code did not promote it."""
    assert cp.SETTING_CODES.get("stop_on_reply") == 3, "the read code must stay registered"
    assert cp.AUTOSET_ALLOWLIST == frozenset()
    for name, code in cp.SETTING_CODES.items():
        assert code not in cp.AUTOSET_ALLOWLIST, (
            f"{name} (code {code}) became auto-settable merely by being readable"
        )


# --- malformed payloads refuse (§R5 structured output) ---------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"payload": {"settings": {"code": 13}}},  # object where a list belongs
        {"payload": {"settings": ["13"]}},  # list of scalars
        {"payload": {"settings": [{"value": "1"}]}},  # item with no code
    ],
)
def test_a_malformed_settings_payload_refuses(tmp_path, monkeypatch, payload):
    _policy(monkeypatch, stop_on_reply="blocking")
    r = cp.check_capabilities(
        "acme",
        payload,
        setting_codes={"stop_on_reply": 7},
        registry_path=_autoset_registry(tmp_path),
    )
    assert r.status == "FAIL"
    assert any("not the documented shape" in d for d in r.detail)


def test_a_malformed_payload_is_never_read_as_the_setting_being_off_or_on(tmp_path, monkeypatch):
    """The dangerous middle case: a payload we could not parse must not silently become
    'code 7 absent => off => FAIL for the wrong reason' or, worse, a pass."""
    _policy(monkeypatch, stop_on_reply="blocking")
    r = cp.check_capabilities(
        "acme",
        {"payload": {"settings": {"nope": True}}},
        setting_codes={"stop_on_reply": 7},
        registry_path=_autoset_registry(tmp_path),
    )
    assert r.status == "FAIL"
    assert any("not the documented shape" in d for d in r.detail)


# --- provider resolution + CLI wiring ---------------------------------------------------------


def test_read_email_tool_reads_the_profile(tmp_path):
    _write_profile(tmp_path, "company: Acme\nemail_tool:       saleshandy    # which sequencer\n")
    assert cp.read_email_tool("acme", tmp_path) == "saleshandy"


def test_read_email_tool_raises_when_absent(tmp_path):
    _write_profile(tmp_path, "company: Acme\n")
    with pytest.raises(ValueError):
        cp.read_email_tool("acme", tmp_path)


def test_cli_without_a_provider_runs_no_capability_check(tmp_path, capsys):
    """Backwards compatible: a run that names no sequencer asserts no capabilities."""
    assert ec.main(_cli_files(tmp_path)) == 0
    assert "sequencer capabilities" not in capsys.readouterr().out


def test_cli_with_a_provider_blocks_on_the_capability_gate(tmp_path, capsys):
    assert ec.main(_cli_files(tmp_path) + ["--provider", "saleshandy"]) == 1
    out = capsys.readouterr().out
    assert "sequencer capabilities" in out
    assert "DO NOT LOAD" in out


def test_manual_is_not_a_sequencer_to_assert_against(tmp_path, capsys):
    assert ec.main(_cli_files(tmp_path) + ["--provider", "manual"]) == 0
    assert "sequencer capabilities" not in capsys.readouterr().out


def test_the_capability_gate_shares_the_one_exit_status(tmp_path, capsys):
    """Not a second gate: the capability result joins the same list and the same exit code,
    so there is exactly one thing to run and one thing to read."""
    rc = ec.main(_cli_files(tmp_path, header="0") + ["--provider", "saleshandy"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "DO NOT LOAD — 2 check(s) failed" in out


# --- the preflight stays a pure judging tool ------------------------------------------------


def test_the_capability_check_makes_no_network_call(tmp_path, monkeypatch):
    """§R2/§R6: the preflight judges piped payloads. Any socket here is a defect."""
    import socket

    def _boom(*a, **k):  # pragma: no cover - only runs if the property breaks
        raise AssertionError("the preflight opened a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    cp.check_capabilities("saleshandy", None, registry_path=REAL_SEQ_REGISTRY)


def test_the_capability_check_writes_no_cost_row(tmp_path, monkeypatch):
    """Asserted, not assumed (test plan §3.B): nothing here is a metered call."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    cp.check_capabilities("saleshandy", None, registry_path=REAL_SEQ_REGISTRY)
    assert not list(tmp_path.rglob("costs.jsonl"))


def test_capability_work_does_not_scale_with_the_lead_list(tmp_path, monkeypatch):
    """One resolution per policy capability, whatever the list size."""
    calls = []
    real = cp.resolve_capability

    def _counted(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(cp, "resolve_capability", _counted)
    cp.check_capabilities("saleshandy", None, registry_path=REAL_SEQ_REGISTRY)
    assert len(calls) == len(cp.CAPABILITY_POLICY)


# --- ledger rows --------------------------------------------------------------------------


def test_sequence_id_writes_a_capability_asserted_row(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    rc = ec.main(
        _cli_files(tmp_path)
        + ["--provider", "saleshandy", "--profile", "acme", "--sequence-id", "seq-1"]
    )
    assert rc == 1
    rows = [
        json.loads(line) for line in (tmp_path / "acme" / "history.jsonl").read_text().splitlines()
    ]
    assert rows[0]["event"] == "capability_asserted"
    assert rows[0]["sequence_id"] == "seq-1"
    assert rows[0]["status"] == "FAIL"


def test_record_autoset_refuses_when_the_reread_does_not_show_the_flip(tmp_path, capsys):
    (tmp_path / "before.json").write_text(json.dumps(_settings(**{"13": "0"})), encoding="utf-8")
    (tmp_path / "after.json").write_text(json.dumps(_settings(**{"13": "0"})), encoding="utf-8")
    rc = ec.main(
        [
            "record-autoset",
            "--profile",
            "acme",
            "--provider",
            "saleshandy",
            "--capability",
            "stop_on_reply",
            "--code",
            "13",
            "--before-json",
            str(tmp_path / "before.json"),
            "--after-json",
            str(tmp_path / "after.json"),
        ]
    )
    assert rc == 1
    assert "not on" in capsys.readouterr().err


def test_record_autoset_refuses_a_code_outside_the_allowlist(tmp_path, capsys):
    (tmp_path / "before.json").write_text(json.dumps(_settings(**{"13": "0"})), encoding="utf-8")
    (tmp_path / "after.json").write_text(json.dumps(_settings(**{"13": "1"})), encoding="utf-8")
    rc = ec.main(
        [
            "record-autoset",
            "--profile",
            "acme",
            "--provider",
            "saleshandy",
            "--capability",
            "stop_on_reply",
            "--code",
            "13",
            "--before-json",
            str(tmp_path / "before.json"),
            "--after-json",
            str(tmp_path / "after.json"),
        ]
    )
    assert rc == 1
    assert "AUTOSET_ALLOWLIST" in capsys.readouterr().err


def test_record_autoset_row_can_reverse_the_change(tmp_path, monkeypatch):
    """§3.E reconstructability: the row alone carries both values."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setattr(cp, "AUTOSET_ALLOWLIST", frozenset({13}))
    (tmp_path / "before.json").write_text(json.dumps(_settings(**{"13": "0"})), encoding="utf-8")
    (tmp_path / "after.json").write_text(json.dumps(_settings(**{"13": "1"})), encoding="utf-8")
    rc = ec.main(
        [
            "record-autoset",
            "--profile",
            "acme",
            "--provider",
            "saleshandy",
            "--capability",
            "stop_on_reply",
            "--code",
            "13",
            "--before-json",
            str(tmp_path / "before.json"),
            "--after-json",
            str(tmp_path / "after.json"),
        ]
    )
    assert rc == 0
    row = json.loads((tmp_path / "acme" / "history.jsonl").read_text().splitlines()[0])
    assert row["event"] == "capability_autoset"
    assert row["value_before"] == "0"
    assert row["value_after"] == "1"
    assert row["setting_code"] == 13


# --- one object, three surfaces (§4.5) --------------------------------------------------------


def test_every_surface_renders_the_same_resolved_capabilities():
    """Terminal, gate preview and dashboard all derive from `capability_rows`; assert the
    three renderings agree on content rather than each re-deriving its own."""
    from gtm_core.sequencers import render_summary

    rows = cp.capability_rows("saleshandy", registry_path=REAL_SEQ_REGISTRY)
    text = render_summary(rows, fmt="text")
    html = render_summary(rows, fmt="html")
    for cap in rows:
        assert cap.name in text
        assert cap.name in html
    assert text.count("GRANTED") == html.count("GRANTED")


# --- gaps found by the mutation sweep (2026-09-21) ---------------------------------------


def test_autoset_defaults_to_off_when_the_caller_says_nothing(tmp_path, monkeypatch):
    """The safety default itself. Every other auto-set test passes `autoset_enabled=`
    explicitly, so the sweep could flip the default to True and nothing noticed."""
    _policy(monkeypatch, stop_on_reply="blocking")
    r = cp.check_capabilities(
        "acme",
        _settings(**{"7": "0"}),
        setting_codes={"stop_on_reply": 7},
        autoset_allowlist=frozenset({7}),
        registry_path=_autoset_registry(tmp_path),
    )
    assert r.status == "FAIL"
    assert not any("AUTOSET PLAN" in d for d in r.detail)


def test_a_malformed_payload_fails_even_when_every_capability_would_pass(tmp_path, monkeypatch):
    """Isolates the payload-error contribution. Today every capability FAILs anyway, so a
    bug that dropped the payload error entirely was invisible — the sweep found exactly
    that. Here the one capability PASSES on a live read, so the only thing that can make
    the result FAIL is the malformed payload itself."""
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(tmp_path, stop_on_reply="supported = true\nreadable_via_api = true\n")

    good = cp.check_capabilities(
        "acme", _settings(**{"7": "1"}), setting_codes={"stop_on_reply": 7}, registry_path=reg
    )
    assert good.status == "PASS"  # the control: this passes when the payload is readable

    bad = cp.check_capabilities(
        "acme",
        {"payload": {"settings": {"not": "a list"}}},
        setting_codes={"stop_on_reply": 7},
        registry_path=reg,
    )
    assert bad.status == "FAIL"
    assert any("not the documented shape" in d for d in bad.detail)


def test_an_unknown_attestation_fails_even_when_capabilities_would_pass(tmp_path, monkeypatch):
    """The other half of the same OR: `--attest` naming nothing must fail on its own."""
    _policy(monkeypatch, stop_on_reply="blocking")
    reg = _registry(tmp_path, stop_on_reply="supported = true\nreadable_via_api = true\n")
    r = cp.check_capabilities(
        "acme",
        _settings(**{"7": "1"}),
        attested={"wharrgarbl"},
        setting_codes={"stop_on_reply": 7},
        registry_path=reg,
    )
    assert r.status == "FAIL"
    assert any("nothing asserts" in d for d in r.detail)


# --- test plan §3.D / §3.E — the surfaces an operator actually reads -----------------------


def test_a_three_failure_result_renders_as_one_block_with_every_ui_path(tmp_path, monkeypatch):
    """§3.D. A per-capability notification would be 6+ Telegram messages per staging
    attempt, which is a cockpit the operator mutes. One block, and it must carry the UI
    path for every capability that BLOCKS — a failure that does not say where to fix it
    sends the operator hunting."""
    _policy(monkeypatch, stop_on_reply="blocking", ooo_auto_pause="blocking", webhooks="blocking")
    reg = _registry(
        tmp_path,
        stop_on_reply='supported = true\nui_path = "Sequence > Safety > Finished on Reply"\n',
        ooo_auto_pause='supported = true\nreadable_via_api = false\nui_path = "Settings > Out of Office"\n',
        webhooks='supported = "unknown"\nui_path = "Settings > Webhooks"\n',
    )
    r = cp.check_capabilities("acme", None, registry_path=reg)
    assert r.status == "FAIL"

    # ONE Result — one block — not one per capability.
    assert isinstance(r.detail, list)
    blocking = [d for d in r.detail if "BLOCKS" in d]
    assert len(blocking) == 3, f"expected three blocking lines in one block: {r.detail}"

    # every UI path present, so the operator can act from the message alone
    joined = "\n".join(r.detail)
    for ui in ("Sequence > Safety > Finished on Reply", "Settings > Out of Office"):
        assert ui in joined, f"missing the UI path for a blocked capability: {ui}"


def test_the_gate_preview_escapes_every_provider_supplied_field():
    """§3.D. The registry is committed config, but the renderer is shared with surfaces
    that show provider text, so it must escape unconditionally rather than rely on its
    caller's provenance."""
    from gtm_core.sequencers import Capability, render_summary

    hostile = Capability(
        provider="acme",
        name="cap",
        supported=True,
        readable_via_api=None,
        settable_via_api=None,
        source="https://vendor.example",
        verified_on="2026-09-21",
        ui_path=None,
        granted=False,
        reason='refused: <img src=x onerror="alert(1)"> & "quoted"',
    )
    html = render_summary([hostile], fmt="html")
    assert "<img" not in html
    assert "&lt;img" in html
    assert "&amp;" in html


def test_the_preflight_adds_no_telegram_sender():
    """§3.D, asserted rather than assumed: the capability check reports through the
    existing Result/render path. A second notification sender here would be a second
    thing to rate-limit, and a second way to wake somebody at 3am."""
    import ast
    from pathlib import Path

    for mod in ("gtm_core/capability_preflight.py", "gtm_core/email_compliance.py"):
        src = Path(mod).read_text(encoding="utf-8")
        assert "api.telegram.org" not in src, f"{mod} hardcodes the Telegram API"
        assert "sendMessage" not in src, f"{mod} calls the Telegram send endpoint"
        tree = ast.parse(src)
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not any("gate_notify" in m for m in imported), (
            f"{mod} imports a notifier — the preflight reports through its Result, "
            "and the cockpit decides what to send"
        )


def test_capability_asserted_answers_was_this_checked_with_no_provider_call(tmp_path, monkeypatch):
    """§3.E Outcome Syncing. 'Was this sequence checked, and when?' must be answerable from
    the ledger alone — the whole point is not having to ask the provider."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    from gtm_core.capability_ledger import record_asserted

    record_asserted(
        "acme",
        provider="saleshandy",
        sequence_id="seq-42",
        status="FAIL",
        attested=["ooo_auto_pause"],
        detail=["FAIL [BLOCKS] saleshandy/stop_on_reply: ..."],
    )

    # Read it back the way an operator would: the ledger, no network.
    import socket

    def _boom(*a, **k):  # pragma: no cover - only runs if the property breaks
        raise AssertionError("reading the answer opened a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    import dataclasses

    from gtm_core.ledgers import Ledgers

    @dataclasses.dataclass
    class _Cfg:
        content_root: object

    rows = [
        r
        for r in Ledgers(_Cfg(content_root=tmp_path), "acme").iter_history()
        if r.get("event") == "capability_asserted"
    ]
    assert len(rows) == 1
    assert rows[0]["sequence_id"] == "seq-42"
    assert rows[0]["status"] == "FAIL"
    assert rows[0]["attested"] == ["ooo_auto_pause"]
    assert rows[0]["ts"].endswith("Z")  # when, not just whether
