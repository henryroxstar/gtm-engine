"""R1.4 extractors: each one proposes names from a captured page; only verified names survive."""

from __future__ import annotations

import time

import pytest

from gtm_core.signal_obs import members

SRC = "https://members.example.test/list"


def _names(ex):
    return [m.name for m in ex.members]


def test_links_takes_link_text_and_the_outbound_domain_only():
    md = (
        "[Northwind Traders](https://www.northwind.example.test/about)\n"
        "[Join us](https://members.example.test/join)\n"
        "[Blue Harbour Partners](https://members.example.test/m/blue-harbour)\n"
    )
    ex = members.extract_members("links", {}, md, SRC)
    by = {m.name: m.domain for m in ex.members}
    assert by["Northwind Traders"] == "northwind.example.test"
    assert by["Blue Harbour Partners"] == ""  # an in-site profile link is not the member's domain
    assert by["Join us"] == ""


def test_links_href_contains_narrows_to_member_links():
    md = (
        "[Northwind Traders](https://members.example.test/m/northwind)\n"
        "[About](https://members.example.test/about)\n"
    )
    ex = members.extract_members("links", {"href_contains": "/m/"}, md, SRC)
    assert _names(ex) == ["Northwind Traders"]


def test_table_column_reads_the_named_column_under_its_header():
    md = (
        "| Member | Country |\n|---|---|\n"
        "| Northwind Traders | Fiji |\n"
        "| [Blue Harbour Partners](https://blueharbour.example.test) | Chile |\n"
        "\nunrelated paragraph\n"
        "| Quillon Freight | Peru |\n"
    )
    ex = members.extract_members("table_column", {"column": "Member"}, md, SRC)
    assert _names(ex) == ["Northwind Traders", "Blue Harbour Partners"]
    assert ex.members[1].domain == "blueharbour.example.test"


def test_table_column_with_no_such_header_yields_nothing():
    md = "| Name | Country |\n|---|---|\n| Northwind Traders | Fiji |\n"
    assert members.extract_members("table_column", {"column": "Member"}, md, SRC).members == []


def test_heading_list_takes_only_the_requested_level():
    md = "# Members\n\n## Northwind Traders\n\n### Sub note\n\n## [Blue Harbour Partners](https://x.example.test)\n"
    assert _names(members.extract_members("heading_list", {"level": 2}, md, SRC)) == [
        "Northwind Traders",
        "Blue Harbour Partners",
    ]


def test_a_name_twice_on_the_page_is_one_member():
    md = (
        "[Northwind Traders](https://a.example.test)\n[NORTHWIND TRADERS](https://a.example.test)\n"
    )
    assert _names(members.extract_members("links", {}, md, SRC)) == ["Northwind Traders"]


def test_short_names_are_counted_and_dropped():
    md = "[Abc](https://abc.example.test)\n[Northwind Traders](https://n.example.test)\n"
    ex = members.extract_members("links", {}, md, SRC)
    assert _names(ex) == ["Northwind Traders"] and ex.refused_short == ["Abc"]


def test_brain_list_keeps_only_names_the_page_itself_says():
    page = "# Members\n\nNorthwind Traders and Blue Harbour Partners are listed.\n"
    proposed = [
        members.Member("Northwind Traders"),
        members.Member("Blue Harbour Partners", "blueharbour.example.test"),
        members.Member("Quillon Freight"),  # the model's invention: not on the page
    ]
    ex = members.extract_members("brain_list", {}, page, SRC, proposed=proposed)
    assert _names(ex) == ["Northwind Traders", "Blue Harbour Partners"]
    assert ex.unverified == ["Quillon Freight"]


def test_parse_brain_list_validates_the_shape_with_minischema():
    good = members.parse_brain_list(
        '[{"name": "Northwind Traders"}, {"name": "B H P", "domain": "x.example.test"}]'
    )
    assert [m.name for m in good] == ["Northwind Traders", "B H P"] and good[
        1
    ].domain == "x.example.test"


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        '{"name": "Northwind Traders"}',
        '[{"title": "Northwind Traders"}]',
        '[{"name": 5}]',
        '[{"name": "Northwind Traders", "note": "hi"}]',
        '[{"name": ""}]',
    ],
)
def test_parse_brain_list_refuses_a_malformed_proposal(bad):
    with pytest.raises(ValueError, match="brain list"):
        members.parse_brain_list(bad)


# --- a domain the brain supplies counts only when the member's own entry carries it -------------

VICTIM = members.Member("Victim Corp", "victim.example.test")


def _brain(page, cand=VICTIM):
    return members.extract_members("brain_list", {}, page, SRC, proposed=[cand])


def _accepted(page, cand=VICTIM):
    ex = _brain(page, cand)
    assert _names(ex) == [cand.name]
    return ex.members[0].domain == cand.domain and not ex.domain_dropped


@pytest.mark.parametrize(
    "page",
    [
        "- Victim Corp (sales@notvictim.example.test)\n",
        "- Victim Corp at victim.example.test.evil.example.test\n",
        "- Victim Corp at evil-victim.example.test\n",
        "- Victim Corp\n<!-- victim.example.test -->\n",
        "- Victim Corp <!-- victim.example.test -->\n",
        "- Victim Corp\n<script>var d = 'victim.example.test';</script>\n",
        "- Victim Corp\n<style>/* victim.example.test */</style>\n",
        "- Victim Corp\n- [Other Member Ltd](https://victim.example.test)\n",
        "- Victim Corp\n- Other Member Ltd at victim.example.test\n",
        "- Victim Corp ![logo](https://victim.example.test/logo.png)\n",
        '<ul><li>Victim Corp</li><li><a href="https://victim.example.test">Other Member Ltd</a></li></ul>',
    ],
)
def test_a_brain_domain_is_dropped_when_the_members_own_entry_does_not_carry_it(page):
    ex = _brain(page)
    assert _names(ex) == ["Victim Corp"]
    assert ex.members[0].domain == "" and ex.members[0].reason == "domain-not-on-page"
    assert ex.domain_dropped == ["Victim Corp"]


@pytest.mark.parametrize(
    "page",
    [
        "- [Victim Corp](https://www.victim.example.test/about)\n",
        "- Victim Corp (https://victim.example.test/about)\n",
        "- Victim Corp, victim.example.test.\n",
        "- Victim Corp - VICTIM.example.test\n",
        "| Victim Corp | [site](https://victim.example.test) |\n",
        "## Victim Corp\n\nVisit victim.example.test for more.\n\n## Other Member Ltd\n",
        '<ul><li><a href="https://victim.example.test/x">Victim Corp</a></li></ul>',
        "- [Victim Corp](victim.example.test)\n",
    ],
)
def test_a_brain_domain_is_kept_when_the_members_own_entry_carries_it(page):
    assert _accepted(page)


def test_a_brain_domain_carried_by_a_different_entry_than_the_member_is_dropped_but_a_second_mention_counts():
    page = "- Other Member Ltd victim.example.test\n- Victim Corp\n- Victim Corp at victim.example.test\n"
    assert _accepted(page)


# --- hostile or broken input does not abort the source ------------------------------------------


def test_a_link_with_a_malformed_authority_is_skipped_and_the_rest_extracts():
    md = "[Broken Link Co](http://[bad)\n[Northwind Traders](https://northwind.example.test/)\n"
    ex = members.extract_members("links", {}, md, SRC)
    assert _names(ex) == ["Northwind Traders"] and ex.unverified == ["Broken Link Co"]


def test_a_table_link_with_a_malformed_authority_is_skipped_and_the_rest_extracts():
    md = "| Member |\n|---|\n| [Broken Link Co](http://[bad) |\n| Northwind Traders |\n"
    ex = members.extract_members("table_column", {"column": "Member"}, md, SRC)
    assert _names(ex) == ["Northwind Traders"] and ex.unverified == ["Broken Link Co"]


@pytest.mark.parametrize("args", [{}, {"column": ""}, {"column": "  "}])
def test_table_column_without_a_column_is_refused_not_read_from_an_empty_header(args):
    md = "| | Country |\n|---|---|\n| Northwind Traders | Fiji |\n"
    with pytest.raises(ValueError, match="column"):
        members.extract_members("table_column", args, md, SRC)


def _seconds(fn):
    t0 = time.perf_counter()
    out = fn()
    return time.perf_counter() - t0, out


def test_a_heading_with_a_long_run_of_spaces_is_read_in_linear_time():
    md = "##" + " " * 10_000 + "a" * 600
    took, _ = _seconds(lambda: members.extract_members("heading_list", {"level": 2}, md, SRC))
    assert took < 3


def test_a_page_of_empty_script_blocks_is_read_in_linear_time():
    md = "<script></script>" * 100_000 + "\n- [Northwind Traders](https://n.example.test/)\n"
    took, ex = _seconds(lambda: members.extract_members("links", {}, md, SRC))
    assert took < 3 and _names(ex) == ["Northwind Traders"]


def test_unclosed_script_and_comment_openers_are_read_in_linear_time():
    md = "<script " * 100_000 + "<!--" * 100_000
    took, _ = _seconds(lambda: members.extract_members("links", {}, md, SRC))
    assert took < 3


def test_a_page_of_fifty_thousand_unique_links_is_refused_quickly_as_too_many():
    md = "\n".join(f"[Member Number {i}](https://m{i}.example.test/)" for i in range(50_000))
    took, raised = _seconds(
        lambda: pytest.raises(ValueError, members.extract_members, "links", {}, md, SRC)
    )
    assert took < 3 and "candidate" in str(raised.value)


def test_a_few_thousand_links_are_verified_against_an_index_not_a_rescan():
    md = "\n".join(f"[Member Number {i}](https://m{i}.example.test/)" for i in range(4_000))
    took, ex = _seconds(lambda: members.extract_members("links", {}, md, SRC))
    assert took < 3 and len(ex.members) == 4_000 and not ex.unverified


# A logo link whose text runs over several lines is how real directory pages are written: the name
# sits in the middle of the link and the address closes it a few lines later. That link is the
# member's own entry.
_LOGO_LINKS = (
    "[![Northwind Traders logo](https://img.example.test/a.png)\\\n\\\n"
    "**Northwind Traders**\\\n(opens in new tab)](https://www.northwind.example.test/sg/)\n\n"
    "[![Blue Harbour Partners logo](https://img.example.test/b.png)\\\n\\\n"
    "**Blue Harbour Partners**\\\n(opens in new tab)](https://blueharbour.example.test/)\n"
)


def test_a_domain_in_the_link_that_wraps_the_name_is_the_members_own():
    nw = members.Member("Northwind Traders", "northwind.example.test")
    ex = members.extract_members("brain_list", {}, _LOGO_LINKS, SRC, proposed=[nw])
    assert ex.members[0].domain == "northwind.example.test" and not ex.domain_dropped


def test_the_other_members_link_does_not_carry_this_members_domain():
    wrong = members.Member("Northwind Traders", "blueharbour.example.test")
    ex = members.extract_members("brain_list", {}, _LOGO_LINKS, SRC, proposed=[wrong])
    assert ex.members[0].domain == "" and ex.domain_dropped == ["Northwind Traders"]


def test_a_link_left_open_does_not_swallow_the_page():
    text = "[unclosed\n" * 200 + "- Victim Corp\n- Other Co (other.example.test)\n"
    victim = members.Member("Victim Corp", "other.example.test")
    ex = members.extract_members("brain_list", {}, text, SRC, proposed=[victim])
    assert ex.members[0].domain == ""


def test_a_page_of_many_open_links_is_read_in_linear_time():
    import time

    text = "[open\n" * 100_000 + "- Victim Corp\n"
    started = time.perf_counter()
    members.extract_members("brain_list", {}, text, SRC, proposed=[VICTIM])
    assert time.perf_counter() - started < 3


_CHAINED = (
    "[![Northwind Traders logo](https://img.example.test/a.png)\\\n\\\n"
    "**Northwind Traders**\\\n(opens in new tab)](https://www.northwind.example.test/) "
    "[![Blue Harbour Partners logo](https://img.example.test/b.png)\\\n\\\n"
    "**Blue Harbour Partners**\\\n(opens in new tab)](https://blueharbour.example.test/)\n"
)


@pytest.mark.parametrize(
    ("name", "domain", "kept"),
    [
        ("Northwind Traders", "northwind.example.test", True),
        ("Blue Harbour Partners", "blueharbour.example.test", True),
        ("Blue Harbour Partners", "northwind.example.test", False),
        ("Northwind Traders", "blueharbour.example.test", False),
    ],
)
def test_links_chained_on_one_line_each_carry_only_their_own_address(name, domain, kept):
    ex = members.extract_members(
        "brain_list", {}, _CHAINED, SRC, proposed=[members.Member(name, domain)]
    )
    assert bool(ex.members[0].domain) is kept
