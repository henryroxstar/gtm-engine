"""The sending-figures validator's boundaries, and the writer's two edge refusals (round-2 S02, S03,
S05, S10, N11, M6). Each case here was a surviving mutant or a message that said the wrong thing.
Every id is fictional.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from gtm_core import sequencer_snapshot as ss
from gtm_core import sequencer_snapshot_load as sl
from gtm_core.sequence_payload_check import MAX_DEPTH, MAX_ID_LEN, check_payload
from tests.unit.test_sequencer_snapshot_writer import (
    MON,
    NOW,
    PROFILE,
    _doc,
    _p,
    _payload,
    _payload_of,
    _refused,
    _row,
    _write,
)


@pytest.fixture(autouse=True)
def _the_profile_exists(tmp_path):
    (tmp_path / PROFILE).mkdir()


def _why(row_or_payload) -> str | None:
    payload = (
        row_or_payload
        if isinstance(row_or_payload, dict) and "sequences" in row_or_payload
        else {"sequences": [row_or_payload]}
    )
    return check_payload(payload)[1]


# --- S02: an id the page, the writer and the ledger could read differently ---------------------------


@pytest.mark.parametrize(
    "sid",
    [
        pytest.param("S\x01X", id="control-character"),
        pytest.param("S\x1b[2JX", id="escape"),
        pytest.param("S\nX", id="line-break"),
        pytest.param("S‮1", id="right-to-left-override"),
        pytest.param("S​1", id="zero-width-space"),
        pytest.param("S 1", id="non-breaking-space"),
        pytest.param("S　X", id="ideographic-space"),
        pytest.param("S X", id="line-separator"),
        pytest.param("S X", id="paragraph-separator"),
        pytest.param("S­X", id="soft-hyphen"),
        pytest.param("S﻿X", id="byte-order-mark"),
        pytest.param("SX", id="private-use"),
        pytest.param(" S1", id="leading-non-breaking-space"),
        pytest.param("S1​", id="trailing-zero-width-space"),
        pytest.param(" S1", id="leading-space"),
        pytest.param("S1 ", id="trailing-space"),
        pytest.param("\tS1", id="leading-tab"),
    ],
)
def test_an_id_with_an_invisible_or_control_character_is_refused(tmp_path, sid):
    why = _why(dict(_row("S9"), sequenceId=sid))
    assert why and "no usable sequenceId" in why
    assert "\x1b" not in why and "‮" not in why and "​" not in why, "echoed raw"
    f = _payload_of(tmp_path, "bad.json", {"sequences": [dict(_row("S9"), sequenceId=sid)]})
    _refused(tmp_path, f, "no usable sequenceId")


@pytest.mark.parametrize("sid", ["a b", "名前", "é-9", "S1:v2", "x" * MAX_ID_LEN])
def test_an_ordinary_id_is_accepted(sid):
    assert _why(dict(_row("S9"), sequenceId=sid)) is None


def test_two_ids_that_differ_only_in_edge_space_cannot_both_be_written(tmp_path):
    """After a strip they would be one key: the padded one is refused, never merged into the other."""
    assert _write(tmp_path, [_payload(tmp_path, "a.json", _row("S1"))], fetched=MON)[0]
    ok, lines = _write(tmp_path, [_payload(tmp_path, "b.json", _row("S1 "))], fetched=MON)
    assert not ok and "no usable sequenceId" in lines[0]
    assert list(_doc(tmp_path)["stamps"]) == ["S1"]


# --- S10: the id length cap -------------------------------------------------------------------------


def test_an_id_at_the_length_cap_is_accepted_and_one_over_is_refused(tmp_path):
    assert _why(dict(_row("S9"), sequenceId="i" * MAX_ID_LEN)) is None
    over = dict(_row("S9"), sequenceId="i" * (MAX_ID_LEN + 1))
    assert "no usable sequenceId" in (_why(over) or "")
    _refused(tmp_path, _payload_of(tmp_path, "bad.json", {"sequences": [over]}), "no usable")


def test_a_long_id_is_quoted_back_short():
    why = _why(dict(_row("S9"), sequenceId="i" * 5000)) or ""
    assert len(why) < 400


# --- S03: counters in digits other than 0-9 ---------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["²", "٣", "１２", "①", "1_0", " 5", "5 ", "+5", "5.0", "0x10", "1e3", "", "٣" * 3],
)
def test_a_counter_in_unicode_or_decorated_digits_is_refused(value):
    """``'²'.isdigit()`` is True and ``int('²')`` raises; the two readers of a counter disagree on
    such text, so accepting it would read some as 0. Blank is the one allowed non-number."""
    why = _why(_p(contacted=value))
    if value == "":
        assert why is None
    else:
        assert why and "prospects[0].contacted" in why and "whole number" in why


def test_an_ascii_digit_string_is_accepted():
    assert _why(_p(contacted="0012", total="8")) is None


# --- S05: the nesting cap ---------------------------------------------------------------------------


def _nested(levels: int) -> dict:
    node: list = []
    for _ in range(levels - 1):
        node = [node]
    return dict(_row("S9"), extra=node)


def test_a_row_nested_exactly_to_the_cap_is_accepted_and_one_level_more_is_refused(tmp_path):
    assert _why(_nested(MAX_DEPTH)) is None
    why = _why(_nested(MAX_DEPTH + 1)) or ""
    assert f"nested more than {MAX_DEPTH} levels deep" in why
    f = _payload_of(tmp_path, "bad.json", {"sequences": [_nested(MAX_DEPTH + 1)]})
    _refused(tmp_path, f, "levels deep")


def test_a_deeply_nested_dict_is_refused_the_same_way():
    node: dict = {}
    for _ in range(MAX_DEPTH + 2):
        node = {"k": node}
    assert "levels deep" in (_why(dict(_row("S9"), extra=node)) or "")


# --- N11: a file is not a profile -------------------------------------------------------------------


def test_a_file_named_like_the_profile_is_not_a_profile(tmp_path):
    root = tmp_path / "content"
    root.mkdir()
    (root / "ghost").write_text("not a folder", encoding="utf-8")
    f = _payload(tmp_path, "a.json", _row("S1"))
    ok, lines = ss.write("ghost", [f], content_root=root, now=NOW, profiles_root=tmp_path / "none")
    assert not ok and "no profile 'ghost'" in lines[0]
    assert (root / "ghost").read_text(encoding="utf-8") == "not a folder"


# --- M3/M6: a profile that exists only as profiles/<p> ----------------------------------------------


def test_a_profile_that_exists_only_under_profiles_is_accepted_as_the_dashboard_does(tmp_path):
    """The dashboard accepts either folder; a first-run tenant has no content folder yet, and the
    writer used to refuse it while the page rendered."""
    profiles = tmp_path / "profiles"
    (profiles / "fresh").mkdir(parents=True)
    root = tmp_path / "content"
    root.mkdir()
    f = _payload(tmp_path, "a.json", _row("S1"))
    ok, lines = ss.write(
        "fresh", [f], content_root=root, now=NOW, fetched=MON, profiles_root=profiles
    )
    assert ok, lines
    assert json.loads(ss.stats_path("fresh", root).read_text(encoding="utf-8"))["stamps"] == {
        "S1": MON
    }


def test_a_profile_in_neither_place_says_exactly_what_to_create(tmp_path):
    root = tmp_path / "content"
    root.mkdir()
    f = _payload(tmp_path, "a.json", _row("S1"))
    ok, lines = ss.write("acmee", [f], content_root=root, now=NOW, profiles_root=tmp_path / "p")
    assert not ok
    assert "no profile 'acmee'" in lines[0]
    assert "content/acmee/" in lines[0] and "profiles/acmee/" in lines[0]
    assert "nothing was written" in lines[0]
    assert sorted(p.name for p in root.rglob("*")) == []


def test_forget_and_ack_use_the_same_profile_rule(tmp_path):
    profiles = tmp_path / "profiles"
    (profiles / "fresh").mkdir(parents=True)
    root = tmp_path / "content"
    root.mkdir()
    ok, lines = ss.forget("fresh", "S1", content_root=root, profiles_root=profiles)
    assert not ok and "missing" in lines[0], "the profile is fine; there is just no file yet"
    ok, lines = ss.ack("nobody", None, content_root=root, profiles_root=profiles)
    assert not ok and "no profile 'nobody'" in lines[0]


# --- M6: the future-mtime message says what is wrong ------------------------------------------------


def test_a_payload_dated_ahead_of_the_clock_says_the_clock_is_the_problem(tmp_path):
    ahead = NOW + timedelta(seconds=sl.PAYLOAD_FUTURE_SKEW_S + 60)
    msg = _refused(
        tmp_path,
        _payload(tmp_path, "a.json", _row("S1"), mtime=ahead),
        "ahead of this machine's clock",
    )
    assert "check the clock or re-save the file" in msg
    assert "modified time" in msg
    assert "fetch the figures again" not in msg, "fetching again cannot fix a skewed storage clock"
    assert "file's age" in msg


def test_a_payload_file_exceeding_byte_limit_is_refused(tmp_path):
    from gtm_core import sequence_payload_check as rules

    p = tmp_path / "big.json"
    p.write_bytes(b" " * (rules.MAX_PAYLOAD_BYTES + 1))
    payload, data, why = sl.read_payload(p, NOW, None)
    assert payload is None
    assert "exceeds" in why and f"{rules.MAX_PAYLOAD_BYTES // (1024 * 1024)}MB" in why
