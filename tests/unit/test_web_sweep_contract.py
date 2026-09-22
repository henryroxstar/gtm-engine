"""Step 6's normaliser, held to the prospect skill's own Step 7 rules.

Step 7 says an insurer's or staffing firm's "agents" are people, that `none` is the right answer
for a funding round, that `unclear` must block, and that `signal_subject` is the entity the fact is
genuinely ABOUT — "the mismatch is the finding, and it can only fire if the truth is on the row".
`tests/test_web_sweep.py` never fed a human-agent sentence, a stranger's news, or a mis-keyed
hit, so none of that was checked before. Fictional data; PSK-016/017/018/020 are fixed below —
each assertion in this file is now enforced, not xfailed.
"""

from __future__ import annotations

import pytest

from gtm_core import web_sweep as ws

AS_OF = "2026-09-21"


def _hit(snippet: str, **kw) -> dict:
    return {
        "url": "https://northwind.example/news/a",
        "date": "2026-09-10",
        "type": "newsroom",
        "snippet": snippet,
        **kw,
    }


def test_a_well_formed_fresh_hit_yields_a_clause() -> None:
    out = ws.normalize_sweep(
        "Northwind Robotics",
        [_hit("Northwind Robotics opened its agent platform to partner organisations.")],
        as_of=AS_OF,
    )
    assert out["why_now"] and out["signal_observed"] == "2026-09-10"


def test_an_empty_sweep_is_a_verdict_not_prose() -> None:
    out = ws.normalize_sweep("Northwind Robotics", [], as_of=AS_OF)
    assert (out["why_now"], out["verdict"]) == ("", "re-angle")


def test_search_result_and_plain_http_urls_are_refused() -> None:
    assert not ws.is_valid_source_url("https://www.google.com/search?q=northwind")
    assert not ws.is_valid_source_url("http://northwind.example/news")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Contoso Mutual appointed four hundred new insurance agents across the region.",
            {"human", "unclear"},
        ),
        ("Fabrikam Freight raised a growth round to expand its retail chain.", {"none"}),
        ("The chairman said the campaign would maintain email volumes.", {"none"}),
        (
            "Northwind Robotics launched an AI agent platform for enterprise operations.",
            {"ai"},
        ),
        (
            "Fabrikam Freight said its regional agents would be evaluated next quarter.",
            {"unclear"},
        ),
    ],
)
def test_agent_kind_is_not_a_substring_match(text: str, expected: set[str]) -> None:
    assert ws._determine_agent_kind(text) in expected


def test_a_strangers_news_is_not_attributed_to_the_account() -> None:
    stranger = _hit("Tailspin Credit Union launched a partner agent marketplace for member banks.")
    out = ws.normalize_sweep("Northwind Robotics", [stranger], as_of=AS_OF)
    assert out["signal_subject"] != "Northwind Robotics" or out["why_now"] == ""


def test_an_explicit_stranger_subject_is_reported_not_relabelled() -> None:
    """PSK-017: an explicit `subject` naming a different entity must not be silently
    swapped for the account's own name — the sweep keeps the truth and still re-angles."""
    stranger = _hit(
        "A partner agent marketplace opened for member banks.",
        subject="Tailspin Credit Union",
    )
    out = ws.normalize_sweep("Northwind Robotics", [stranger], as_of=AS_OF)
    assert out["signal_subject"] == "Tailspin Credit Union"
    assert out["why_now"] == ""
    assert out["verdict"] == "re-angle"
    assert out["rejected"] == [{"index": 0, "reason": "subject-mismatch"}]


def test_a_mis_keyed_hit_is_loud_not_a_silent_re_angle() -> None:
    """The `--hits` shape is documented nowhere, so an agent guessing `link`/`published_date`
    gets the byte-identical output of "we researched this account and found nothing"."""
    guessed = [{"link": "https://northwind.example/news/a", "published_date": "2026-09-10",
                "description": "Northwind Robotics opened its agent platform to partner organisations."}]  # fmt: skip
    genuine_empty = ws.normalize_sweep("Northwind Robotics", [], as_of=AS_OF)
    try:
        out = ws.normalize_sweep("Northwind Robotics", guessed, as_of=AS_OF)
    except (ValueError, KeyError, TypeError):
        return
    assert out != genuine_empty


def test_a_usable_second_hit_is_used_when_the_first_cannot_reduce() -> None:
    unusable = _hit(
        "Northwind Robotics — the agent platform company — announced a partner programme."
    )
    usable = _hit("Northwind Robotics opened its agent platform to partner organisations.", date="2026-09-05",
                  url="https://northwind.example/news/b")  # fmt: skip
    out = ws.normalize_sweep("Northwind Robotics", [unusable, usable], as_of=AS_OF)
    assert out["why_now"]
