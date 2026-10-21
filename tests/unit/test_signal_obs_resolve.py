"""R1.4 resolution: a member becomes an observation only through a domain that names one account.

Names are fictional shapes from ``gtm_core.fictionalize``'s family (legal suffix, article,
ampersand, casing, subdomain). No case here may turn a name alone into an observation.
"""

from __future__ import annotations

import json

import pytest

from gtm_core.signal_obs import resolve
from unit.conftest import page

NW = ("Northwind Traders", "northwind.example.test")
PAGE_NW = page(NW[0], domains={NW[0]: NW[1]})


def _queue(world):
    p = world.obs_dir / "unresolved.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []


def test_a_domain_the_page_gives_that_names_one_ledger_account_is_an_observation(signal_world):
    signal_world.ledger(NW)
    signal_world.capture(PAGE_NW, "2026-10-01T08:00:00+00:00")
    signal_world.run()
    assert [o["account_key"] for o in signal_world.observations()] == [NW[1]]
    assert _queue(signal_world) == []


def test_www_and_case_do_not_hide_the_domain(signal_world):
    signal_world.ledger(NW)
    text = f"# Members\n\n- [{NW[0]}](https://WWW.Northwind.Example.TEST/about)\n"
    signal_world.capture(text, "2026-10-01T08:00:00+00:00")
    signal_world.run()
    assert [o["account_key"] for o in signal_world.observations()] == [NW[1]]


def test_a_domain_outside_the_ledger_goes_to_the_queue_with_the_candidate(signal_world):
    signal_world.ledger()
    signal_world.capture(PAGE_NW, "2026-10-01T08:00:00+00:00")
    report = signal_world.run()
    assert signal_world.observations() == [] and report.unresolved == 1
    (entry,) = _queue(signal_world)
    assert entry["name"] == NW[0] and entry["candidate_domain"] == NW[1]
    assert entry["source_id"] == "north-directory" and entry["first_seen"] == "2026-10-01"


def test_two_ledger_rows_on_one_domain_is_ambiguous_and_goes_to_the_queue(signal_world):
    signal_world.ledger(NW, ("Northwind Traders Holdings", NW[1]))
    signal_world.capture(PAGE_NW, "2026-10-01T08:00:00+00:00")
    signal_world.run()
    assert signal_world.observations() == []
    assert _queue(signal_world)[0]["reason"] == "ambiguous-domain"


@pytest.mark.parametrize(
    "ledger_name",
    [
        "Northwind Traders Ltd",  # legal-form variant
        "NORTHWIND TRADERS",  # casing
        "The Northwind Traders",  # leading article
        "Northwind & Traders",  # ampersand
        "Northwind Trading (formerly Northwind Traders)",  # former name
    ],
)
def test_a_matching_name_with_no_domain_never_makes_an_observation(signal_world, ledger_name):
    signal_world.registry(extractor="heading_list", args="{level = 2}")
    signal_world.ledger((ledger_name, NW[1]))
    signal_world.capture(f"# Members\n\n## {NW[0]}\n", "2026-10-01T08:00:00+00:00")
    signal_world.run()
    assert signal_world.observations() == []
    (entry,) = _queue(signal_world)
    assert entry["candidate_domain"] == "" and entry["reason"] == "no-domain"


def test_a_subdomain_is_not_folded_into_the_ledger_domain(signal_world):
    signal_world.ledger(NW)
    text = f"# Members\n\n- [{NW[0]}](https://app.northwind.example.test/)\n"
    signal_world.capture(text, "2026-10-01T08:00:00+00:00")
    signal_world.run()
    assert signal_world.observations() == []
    assert _queue(signal_world)[0]["candidate_domain"] == "app.northwind.example.test"


def test_a_member_linking_back_to_the_source_site_has_no_domain_of_its_own(signal_world):
    signal_world.ledger(NW)
    text = f"# Members\n\n- [{NW[0]}](https://members.example.test/m/northwind)\n"
    signal_world.capture(text, "2026-10-01T08:00:00+00:00")
    signal_world.run()
    assert signal_world.observations() == []
    assert _queue(signal_world)[0]["reason"] == "no-domain"


def test_a_candidate_from_the_firmographics_step_resolves_only_through_the_ledger(signal_world):
    signal_world.registry(extractor="heading_list", args="{level = 2}")
    signal_world.ledger(NW)
    signal_world.capture(
        f"# Members\n\n## {NW[0]}\n## Quillon Freight\n", "2026-10-01T08:00:00+00:00"
    )
    signal_world.run(candidates={NW[0]: NW[1], "Quillon Freight": "quillonfreight.example.test"})
    assert [o["account_key"] for o in signal_world.observations()] == [NW[1]]
    (entry,) = _queue(signal_world)
    assert entry["name"] == "Quillon Freight" and entry["reason"] == "not-in-ledger"
    assert entry["candidate_domain"] == "quillonfreight.example.test"


def test_the_queue_is_deduplicated_by_source_and_name_and_keeps_the_first_seen_day(signal_world):
    signal_world.ledger()
    signal_world.capture(PAGE_NW, "2026-10-01T08:00:00+00:00")
    signal_world.run()
    signal_world.capture(PAGE_NW + "\nmore\n", "2026-10-22T08:00:00+00:00")
    signal_world.run(day="2026-10-22", run_id="run2", rebaseline=True)
    (entry,) = _queue(signal_world)
    assert entry["first_seen"] == "2026-10-01"


def test_a_name_too_short_to_trust_is_counted_and_never_resolved(signal_world):
    signal_world.ledger(("Abc", "abc.example.test"))
    text = "# Members\n\n- [Abc](https://abc.example.test/)\n- [Northwind Traders](https://northwind.example.test/)\n"
    signal_world.capture(text, "2026-10-01T08:00:00+00:00")
    signal_world.ledger(("Abc", "abc.example.test"), NW)
    report = signal_world.run()
    assert report.refused_short == 1
    assert [o["account_key"] for o in signal_world.observations()] == [NW[1]]


def test_the_ledger_index_normalises_domains_and_skips_rows_without_one():
    index = resolve.ledger_index(
        [
            {"company": "A", "domain": "WWW.Alpha.Example.Test "},
            {"company": "B", "domain": ""},
            {"company": "C"},
            "not a row",
        ]
    )
    assert list(index) == ["alpha.example.test"]
