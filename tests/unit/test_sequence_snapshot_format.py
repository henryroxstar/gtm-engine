"""The shape of the per-sequence figures file, read by both its writer and the dashboard."""

import pytest

from gtm_core.sequence_snapshot_format import (
    FORMAT,
    body_digest,
    fall_sentence,
    figures_of,
    file_meta,
    row_id,
)


def _stamped(**over):
    raw = {"format": FORMAT, "fetched": "2026-09-30", "sequences": [{"id": "s1"}]}
    raw.update(over)
    raw["body_sha256"] = body_digest(raw)
    return raw


def test_row_id_prefers_sequence_id_and_trims():
    assert row_id({"sequenceId": " a1 ", "id": "b2"}) == "a1"
    assert row_id({"id": "b2"}) == "b2"
    assert row_id({"id": 7}) == "7"
    assert row_id({"id": ""}) == ""
    assert row_id("not a row") == ""


def test_figures_of_reads_a_payload_row_and_a_flat_row():
    payload = {"prospects": [{"contacted": "12.0"}], "emails": {"status": {"delivered": 9}}}
    assert figures_of(payload) == {"sent": 12, "delivered": 9}
    assert figures_of({"sent": "5", "delivered": None}) == {"sent": 5, "delivered": None}


def test_a_missing_figure_is_none_never_zero():
    assert figures_of({"prospects": []}) == {"sent": None, "delivered": None}
    assert figures_of({"prospects": [{}], "emails": {"status": {}}}) == {
        "sent": None,
        "delivered": None,
    }
    assert figures_of({}) == {"sent": None, "delivered": None}


def test_body_digest_ignores_key_order_and_its_own_field():
    assert body_digest({"a": 1, "b": 2}) == body_digest({"b": 2, "a": 1})
    assert body_digest({"a": 1}) == body_digest({"a": 1, "body_sha256": "x"})
    assert body_digest({"a": 1}) != body_digest({"a": 2})


def test_fall_sentence_names_each_fallen_counter():
    text = fall_sentence({"changes": [{"field": "sent", "was": 40, "now": 31}]})
    assert text == (
        "sent is 31 now, was 40 at the last snapshot. Check the sending tool if that is unexpected."
    )
    assert fall_sentence({}) == ""


def test_format_two_file_reports_its_own_stamps_and_catches_an_edit():
    raw = _stamped(stamps={"s1": "2026-09-29"}, inherited=["s2"], payload_sha256={"p": "ab"})
    meta = file_meta(raw)
    assert meta["tool_written"] and not meta["edited"]
    assert meta["stamps"] == {"s1": "2026-09-29"}
    assert meta["inherited"] == ["s2"]
    assert meta["payload_sha256"] == {"p": "ab"}
    raw["sequences"].append({"id": "s9"})
    assert file_meta(raw)["edited"] is True


def test_only_string_stamps_survive():
    meta = file_meta(_stamped(stamps={"s1": "2026-09-29", "s2": 5, "s3": ""}))
    assert meta["stamps"] == {"s1": "2026-09-29", "s3": ""}


def test_another_format_number_is_not_a_tool_written_file():
    raw = {"format": FORMAT + 1, "fetched": "2026-09-30", "sequences": [{"id": "s1"}]}
    meta = file_meta(raw)
    assert meta["tool_written"] is False
    assert meta["stamps"] == {"s1": "2026-09-30"}
    assert meta["inherited"] == ["s1"]


def test_a_legacy_file_gives_every_identified_row_the_file_date():
    raw = {"fetched": "2026-09-20", "sequences": [{"id": "s1"}, {"name": "no id"}]}
    meta = file_meta(raw)
    assert meta["stamps"] == {"s1": "2026-09-20"}
    assert meta["inherited"] == ["s1"]
    assert meta["file_date"] == "2026-09-20"
    assert meta["ids"] == ["s1", ""]


def test_an_empty_or_non_text_file_date_is_no_date():
    assert file_meta({"fetched": "", "sequences": [{"id": "s1"}]})["file_date"] is None
    assert file_meta({"fetched": 5, "sequences": [{"id": "s1"}]})["stamps"] == {}


def test_a_bare_list_is_unstamped():
    meta = file_meta([{"id": "s1"}, "junk"])
    assert meta["stamps"] == {} and meta["inherited"] == [] and meta["ids"] == ["s1"]
    assert meta["file_date"] is None and meta["tool_written"] is False


def test_two_fallen_counters_read_as_two_separate_sentences():
    text = fall_sentence(
        {
            "changes": [
                {"field": "sent", "was": 40, "now": 31},
                {"field": "delivered", "was": 30, "now": 20},
            ]
        }
    )
    assert "unexpected. delivered is 20 now" in text


# --- a fall the writer could not have written makes the file edited, never an exception -----------

_GOOD = {"on": "2026-09-29T08:00:00Z", "changes": [{"field": "sent", "was": 40, "now": 31}]}
_BAD_FALLS = {
    "falls-is-a-list": ["s1"],
    "falls-is-text": "s1",
    "entry-is-text": {"s1": "fell"},
    "entry-is-a-list": {"s1": [1]},
    "no-changes-key": {"s1": {"on": "x"}},
    "changes-is-text": {"s1": {"on": "x", "changes": "abc"}},
    "changes-is-an-object": {"s1": {"on": "x", "changes": {"field": "sent"}}},
    "changes-is-empty": {"s1": {"on": "x", "changes": []}},
    "item-lacks-keys": {"s1": {"on": "x", "changes": [{"x": 1}]}},
    "item-is-text": {"s1": {"on": "x", "changes": ["sent"]}},
    "field-is-not-a-counter": {
        "s1": {"on": "x", "changes": [{"field": "<script>1</script>", "was": 2, "now": 1}]}
    },
    "figures-as-text": {
        "s1": {"on": "x", "changes": [{"field": "sent", "was": "40", "now": "31"}]}
    },
    "figures-negative": {"s1": {"on": "x", "changes": [{"field": "sent", "was": 4, "now": -1}]}},
    "figures-boolean": {"s1": {"on": "x", "changes": [{"field": "sent", "was": 4, "now": True}]}},
    "no-date": {"s1": {"changes": _GOOD["changes"]}},
}


def test_the_shape_the_writer_produces_is_not_edited():
    meta = file_meta(_stamped(stamps={"s1": "2026-09-29"}, falls={"s1": _GOOD}))
    assert meta["edited"] is False and meta["falls_invalid"] is False
    assert meta["falls"] == {"s1": _GOOD}


def test_a_file_with_no_falls_key_is_not_edited():
    assert file_meta(_stamped())["edited"] is False


@pytest.mark.parametrize("falls", _BAD_FALLS.values(), ids=_BAD_FALLS.keys())
def test_a_fall_in_a_shape_the_writer_never_writes_reads_as_an_edit(falls):
    """Even with the body hash recomputed to match — the shape is what gives it away."""
    meta = file_meta(_stamped(stamps={"s1": "2026-09-29"}, falls=falls))
    assert meta["edited"] is True and meta["falls_invalid"] is True
    assert meta["falls"] == {}, "nothing from a malformed fall is kept to be printed"


@pytest.mark.parametrize(
    "fall",
    [
        v
        for name, f in _BAD_FALLS.items()
        if isinstance(f, dict) and name != "no-date"  # a missing date is the loader's concern
        for v in f.values()
    ],
)
def test_fall_sentence_never_raises_on_a_malformed_fall(fall):
    assert fall_sentence(fall) == ""


def test_a_good_fall_still_reads_after_a_bad_one_is_refused():
    assert "sent is 31 now, was 40" in fall_sentence(_GOOD)


def test_a_delivered_only_row_still_reports_delivered():
    row = {"sequenceId": "s1", "emails": {"status": {"delivered": "40"}}}
    assert figures_of(row) == {"sent": None, "delivered": 40}


def test_a_flat_delivered_wins_over_the_status_block():
    assert figures_of({"delivered": 7, "emails": {"status": {"delivered": 9}}})["delivered"] == 7


def test_an_overflowing_counter_reads_as_absent_not_a_crash():
    assert figures_of({"prospects": [{"contacted": 1e999}]})["sent"] is None
    assert figures_of({"sent": float("inf"), "delivered": float("nan")}) == {
        "sent": None,
        "delivered": None,
    }


def test_the_body_digest_of_text_a_file_cannot_hold_is_a_hash_not_a_crash():
    assert len(body_digest({"name": "Launch \ud83d"})) == 64
    assert body_digest({"name": "Launch \ud83d"}) != body_digest({"name": "Launch"})


def test_a_restamped_record_in_the_shape_the_writer_makes_is_not_edited():
    meta = file_meta(_stamped(stamps={"s1": "2026-09-29"}, restamped={"s1": "2026-09-28"}))
    assert meta["edited"] is False and meta["falls_invalid"] is False
    assert meta["restamped"] == {"s1": "2026-09-28"}


def test_a_file_with_no_restamped_key_has_none_recorded_and_is_not_edited():
    meta = file_meta(_stamped())
    assert meta["restamped"] == {} and meta["edited"] is False


@pytest.mark.parametrize(
    "bad",
    [["s1"], "s1", 5, {"s1": 5}, {"s1": ""}, {"s1": None}, {1: "2026-09-28"}, {"s1": ["x"]}],
    ids=[
        "list",
        "text",
        "number",
        "int-value",
        "empty-value",
        "null-value",
        "int-key",
        "list-value",
    ],
)
def test_a_restamped_record_in_a_shape_the_writer_never_makes_reads_as_an_edit(bad):
    """Rehashed to match, so only the shape gives it away; nothing from it is kept."""
    meta = file_meta(_stamped(stamps={"s1": "2026-09-29"}, restamped=bad))
    assert meta["edited"] is True and meta["falls_invalid"] is True
    assert meta["restamped"] == {}
