"""Competitor matching is on an explicit identity: an exact registrable domain, or a name.

Until 2026-10-02 a domain was reduced to its first label and every ending was ignored, so one
entry convicted every company that shared that label. Three measured false positives, all of
one shape (§R9: the shapes, not the accounts):

* a hospital whose domain stem equals a software vendor's `direct` entry was EXCLUDED;
* a code-hosting company matched because an entry carried a path (`host/project`) and the loader
  dropped the path, leaving the whole host;
* an address on a legacy travel-tech domain matched an AI vendor with the same first label.

The fix is not a smarter stem. A stem stays available, but only for an entry that says
``match = "stem"``; the default is the exact registrable domain, its subdomains included.

Domains are RFC 2606 ``.example``; ``quarry.org.example`` has the shape of a `.org` sibling of
``quarry.example``.
"""

from __future__ import annotations

import logging

import pytest

from gtm_core.competitor_index import (
    CompetitorHit,
    competitor_entries,
    competitor_lookup,
    competitor_match,
    load_competitors,
)
from tests.unit.relation_fixtures import PROFILE, write_profile

_FILE = """
schema = 1

[[competitor]]
name = "Quarry Software"
tier = "direct"
domains = ["quarry.example"]
note = "Sells what we sell."

[[competitor]]
name = "Spire Registry"
tier = "adjacent"
domains = ["spire-registry.example", "codehost.example/spire-project"]
note = "Open-source project hosted on a shared code host."

[[competitor]]
name = "Lumen Systems"
tier = "direct"
domains = ["lumen.example"]
note = "AI vendor."

[[competitor]]
name = "Regional Rival"
tier = "direct"
domains = ["rival.example"]
match = "stem"
note = "Operates on a different ending in every country."

[[competitor]]
name = "Fabrikam Identity"
tier = "si-channel"
aliases = ["Fabrikam"]
domains = ["fabrikam.example"]
note = "Channel."
"""


@pytest.fixture
def comps(tmp_path):
    root = write_profile(tmp_path, regulators=None, competitors=_FILE)
    return load_competitors(PROFILE, root)


def test_the_listed_domain_and_its_subdomains_hit(comps):
    for host in ("quarry.example", "mail.quarry.example", "WWW.Quarry.Example"):
        hit = competitor_match("Anyco", host, comps)
        assert hit is not None and hit.direct and hit.entry == "Quarry Software", host


def test_a_sibling_on_another_ending_is_not_the_competitor(comps):
    """The measured hospital case: same first label, different ending, different company."""
    assert competitor_match("A Medical Center", "quarry.org.example", comps) is None
    assert competitor_match("A Medical Center", "", comps, email="a@quarry.org.example") is None


def test_the_parent_of_a_listed_subdomain_is_not_the_competitor(tmp_path):
    body = (
        '[[competitor]]\nname = "Docs Vendor"\ntier = "direct"\ndomains = ["docs.vendor.example"]\n'
    )
    comps = load_competitors(PROFILE, write_profile(tmp_path, regulators=None, competitors=body))
    assert competitor_match("Anyco", "docs.vendor.example", comps)
    assert competitor_match("Anyco", "a.docs.vendor.example", comps)
    assert competitor_match("Anyco", "vendor.example", comps) is None


def test_a_legacy_domain_sharing_a_first_label_is_not_the_competitor(comps):
    assert competitor_match("Travelco", "", comps, email="a@lumen.net.example") is None
    assert competitor_match("Travelco", "travelco.example", comps, email="a@lumen.example")


def test_a_path_bearing_domain_is_refused_loudly_and_never_truncated(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        comps = load_competitors(
            PROFILE, write_profile(tmp_path, regulators=None, competitors=_FILE)
        )
    assert "codehost.example/spire-project" in caplog.text and "path" in caplog.text
    assert competitor_match("CodeHost", "codehost.example", comps) is None
    assert competitor_match("Anyco", "spire-registry.example", comps).entry == "Spire Registry"
    entries = {
        e.name: e
        for e in competitor_entries(PROFILE, write_profile(tmp_path / "again", competitors=_FILE))
    }
    assert entries["Spire Registry"].rejected == ("codehost.example/spire-project",)


def test_a_stem_match_is_kept_only_for_an_entry_that_asks_for_it(comps):
    for host in (
        "rival.example",
        "rival.co.example",
        "rival.org.example",
        "mail.rival.net.example",
    ):
        hit = competitor_match("Anyco", host, comps)
        assert hit is not None and hit.entry == "Regional Rival", host
    assert competitor_lookup("Anyco", "rival.org.example", comps)[1] == "stem:rival"
    # the control: the same ending swap on an entry that did not ask
    assert competitor_match("Anyco", "quarry.org.example", comps) is None


def test_an_unknown_match_value_falls_back_to_exact_and_says_so(tmp_path, caplog):
    body = '[[competitor]]\nname = "Quarry Software"\ntier = "direct"\ndomains = ["quarry.example"]\nmatch = "fuzzy"\n'
    with caplog.at_level(logging.WARNING):
        comps = load_competitors(
            PROFILE, write_profile(tmp_path, regulators=None, competitors=body)
        )
    assert "match" in caplog.text and "fuzzy" in caplog.text
    assert competitor_match("Anyco", "quarry.org.example", comps) is None
    assert competitor_match("Anyco", "quarry.example", comps)


def test_a_name_and_an_alias_still_hit_on_any_domain(comps):
    assert competitor_match("Quarry Software", "unrelated.example", comps)
    assert competitor_match("Fabrikam", "", comps).entry == "Fabrikam Identity"
    assert competitor_match("Fabrikam Identity Ltd", "x.example", comps)
    assert competitor_match("Fabrikam Freight Lines", "", comps) is None


def test_a_domain_never_matches_a_company_name_by_its_first_label(comps):
    """A bare name equal to a listed domain's first label is not that company."""
    assert competitor_match("Quarry", "", comps) is None
    assert competitor_match("Lumen", "", comps) is None


def test_the_rows_email_host_is_an_identity_and_free_mail_never_is(comps):
    assert competitor_match("Anyco", "", comps, email="a@eu.quarry.example")
    assert competitor_match("Anyco", "", comps, email="a@gmail.com") is None
    assert "gmail.com" not in comps


def test_direct_outranks_adjacent_and_lookup_says_what_matched(comps):
    hit, how = competitor_lookup("Fabrikam", "quarry.example", comps)
    assert hit.direct and how == "domain:quarry.example"
    hit, how = competitor_lookup("Fabrikam", "", comps)
    assert not hit.direct and how.startswith("name:")


def test_the_summary_is_the_string_the_router_has_always_used(comps):
    hit = competitor_match("Quarry Software", "", comps)
    assert hit.summary == "Quarry Software (direct) — Sells what we sell."
    assert isinstance(hit, CompetitorHit) and hit.tier == "direct"


def test_a_hand_built_index_keeps_working(comps):
    """Tests and the router build `{key: CompetitorHit}` by hand. A dotted key is a domain."""
    hand = {
        "vertex.example": CompetitorHit("direct", "Vertex (direct)"),
        "vertexsystems": CompetitorHit("adjacent", "x"),
    }
    assert competitor_match("Anyco", "mail.vertex.example", hand).direct
    assert competitor_match("Vertex Systems", "", hand) is not None
    assert competitor_match("Anyco", "vertex.org.example", hand) is None


def test_an_entry_with_no_domains_is_found_by_name_alone(tmp_path):
    body = '[[competitor]]\nname = "Nameonly Labs"\ntier = "direct"\n'
    comps = load_competitors(PROFILE, write_profile(tmp_path, regulators=None, competitors=body))
    assert competitor_match("Nameonly Labs", "", comps).direct
