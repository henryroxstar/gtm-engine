"""R1.4 member check: a name counts only when it is in the page's own words, as a whole word."""

from __future__ import annotations

import pytest

from gtm_core.signal_obs import members

PAGE = "Our members\n\nNorthwind Traders\nCasanova Holdings\nBlue Harbour Partners\n"


@pytest.mark.parametrize("name", ["Abc", "Ab", "A", " Xyz "])
def test_a_name_of_three_characters_or_fewer_is_refused_even_when_present(name):
    assert members.member_in_capture(name, f"{PAGE}\nAbc Ab A Xyz\n") is False
    assert members.name_too_short(name) is True


@pytest.mark.parametrize(
    "name",
    ["Nova4", "Northwind Traders", "Blue Harbour Partners and Associates International"],
)
def test_names_of_four_to_twenty_plus_characters_are_checked_by_whole_words(name):
    text = f"{PAGE}\nNova4\nBlue Harbour Partners and Associates International\n"
    assert members.member_in_capture(name, text) is True


def test_a_name_inside_another_word_is_not_a_match():
    assert members.member_in_capture("Nova", "Casanova Holdings") is False
    assert members.member_in_capture("Nova", "The Nova group") is True
    assert members.member_in_capture("Harbour", "Harbourside Ltd") is False


def test_html_entities_nbsp_and_curly_quotes_are_folded_before_matching():
    assert members.member_in_capture("Smith & Sons", "Smith &amp; Sons Ltd") is True
    assert members.member_in_capture("Blue Harbour", "Blue Harbour") is True
    assert members.member_in_capture("O'Neil Freight", "O’Neil Freight") is True
    assert members.member_in_capture("Smith &amp; Sons", "Smith & Sons") is True


def test_json_unicode_escapes_in_the_capture_are_decoded():
    escaped = (
        "Caf" + chr(92) + "u00e9 Holdings"
    )  # the six characters backslash-u-0-0-e-9 in the text
    assert "é" not in escaped
    assert members.member_in_capture("Caf" + chr(233) + " Holdings", escaped) is True
    assert members.member_in_capture("Cafe Holdings", escaped) is False


def test_nfkc_and_casefold_variants_match():
    assert members.member_in_capture("ＮＯＲＴＨＷＩＮＤ Traders", "northwind traders") is True
    assert members.member_in_capture("STRASSE Labs", "Straße Labs") is True


def test_a_name_only_in_a_script_style_or_attribute_is_not_page_text():
    html = (
        '<script>var x = "Hidden Ventures";</script><style>.Hidden-Ventures{}</style>'
        '<a href="/m/hidden-ventures" title="Hidden Ventures">Open</a> Visible Co'
    )
    assert members.member_in_capture("Hidden Ventures", html) is False
    assert members.member_in_capture("Visible Co", html) is True


def test_a_name_only_in_a_markdown_link_target_is_not_page_text():
    md = "[profile](https://directory.example.test/members/quiet-holdings) and text"
    assert members.member_in_capture("Quiet Holdings", md) is False
    assert (
        members.member_in_capture("Quiet Holdings", "[Quiet Holdings](https://x.example.test)")
        is True
    )


def test_a_name_with_regex_characters_is_matched_literally():
    assert members.member_in_capture("Acme (Pty) Ltd.", "Acme (Pty) Ltd. joined") is True
    assert members.member_in_capture("A.B. Group", "AxBx Group") is False


def test_a_single_word_name_only_inside_a_link_target_is_not_page_text():
    assert (
        members.member_in_capture(
            "Quietworks", "[profile](https://directory.example.test/quietworks)"
        )
        is False
    )
    assert (
        members.member_in_capture("Quietworks", "![Quietworks](https://img.example.test/q.png)")
        is False
    )
    assert (
        members.member_in_capture("Quietworks", "see [Quietworks](https://x.example.test)") is True
    )


def test_a_huge_bracket_run_is_searched_in_linear_time():
    import time

    t0 = time.monotonic()
    members.member_in_capture(
        "Northwind Traders", "x" + "[" * 200000 + "<" * 200000 + "<script " * 50000
    )
    assert time.monotonic() - t0 < 5


def test_hidden_script_text_stays_hidden_after_characters_that_change_length_when_lowered():
    # U+0130 lowers to two characters, so offsets taken from a lowered copy drift by one per mark.
    page = "İ" * 100 + "<script>Hidden Member Corp</script> Visible Co"
    assert members.member_in_capture("Hidden Member Corp", page) is False
    assert members.member_in_capture("Visible Co", page) is True


def test_script_and_comment_blocks_are_matched_without_regard_to_case():
    page = (
        "<SCRIPT>Hidden Ventures</SCRIPT><Style>Hidden Ventures</STYLE><!-- Hidden Ventures --> ok"
    )
    assert members.member_in_capture("Hidden Ventures", page) is False
