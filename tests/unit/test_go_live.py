"""PS20 T1.10 — one liveness rule; evidence only, never a default."""

import pytest

from gtm_core.prospect_lede import GO_LIVE_WORDS, go_live


@pytest.mark.parametrize(
    "readable,statuses,contacted,on_record,expected",
    [
        (False, ["active"], 10, True, "unknown"),
        (True, [None, ""], 10, True, "started"),
        (True, [], 0, True, "staged"),
        (True, ["live"], 0, True, "active"),
        (True, ["RUNNING"], 0, False, "active"),
        (True, ["paused"], 10, True, "paused"),
        (True, [], None, True, "staged"),
        (True, [], "10", True, "staged"),  # non-numeric is not evidence
        (True, [], True, True, "staged"),  # a bool is not a count
        (True, [], 0, False, "none"),
        (True, [" Active "], 0, True, "active"),  # whitespace is stripped
        (True, ["paused", "active"], 0, True, "active"),  # a live status wins over paused
    ],
)
def test_go_live_table(readable, statuses, contacted, on_record, expected):
    assert go_live(statuses, contacted, on_record, readable=readable) == expected


def test_every_word_has_dashboard_wording():
    for word in ("none", "staged", "paused", "active", "started", "unknown"):
        assert GO_LIVE_WORDS[word]
    assert GO_LIVE_WORDS["started"] == "started, people have been contacted"
    assert GO_LIVE_WORDS["unknown"] == "unknown, the sending tool's figures couldn't be read"
