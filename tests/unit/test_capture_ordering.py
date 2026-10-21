"""R0.2: "latest capture" is the maximum fetched_at, never the last line of the index."""

from __future__ import annotations

import json

import pytest

from gtm_core import signal_sources as ss

URL = "https://example.test/registry/list"


def _write_index(sources_dir, rows, *, extra_lines=()):
    sources_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in rows] + list(extra_lines)
    (sources_dir / "index.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _row(text, fetched_at, url=URL):
    import hashlib

    sha = hashlib.sha256(text.encode()).hexdigest()
    return sha, {"url_norm": ss.url_norm(url), "fetched_at": fetched_at, "sha256": sha, "tool": "t"}


def _store(sources_dir, text, fetched_at):
    sha, row = _row(text, fetched_at)
    (sources_dir / f"{sha}.txt").write_text(text, encoding="utf-8")
    return row


def test_out_of_order_index_returns_newest_by_fetched_at(tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
    new = _store(d, "newer page", "2026-09-20T00:00:00+00:00")
    old = _store(d, "older page", "2026-09-01T00:00:00+00:00")
    # A union merge can leave the newer capture BEFORE the older one.
    _write_index(d, [new, old])
    cap = ss.get_latest_capture(URL, sources_dir=d)
    assert cap is not None and cap.text == "newer page"


def test_get_captures_is_time_ordered_oldest_first(tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
    a = _store(d, "a", "2026-09-03T00:00:00+00:00")
    b = _store(d, "b", "2026-09-01T00:00:00+00:00")
    c = _store(d, "c", "2026-09-02T00:00:00+00:00")
    _write_index(d, [a, b, c])
    assert [x.text for x in ss.get_captures(URL, sources_dir=d)] == ["b", "c", "a"]


def test_equal_fetched_at_ties_break_by_sha(tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
    r1 = _store(d, "tie one", "2026-09-05T00:00:00+00:00")
    r2 = _store(d, "tie two", "2026-09-05T00:00:00+00:00")
    _write_index(d, [r1, r2])
    _write_index_rev = list(reversed([r1, r2]))
    expected = max((r1, r2), key=lambda r: r["sha256"])["sha256"]
    assert ss.get_latest_capture(URL, sources_dir=d).sha256 == expected
    _write_index(d, _write_index_rev)
    assert ss.get_latest_capture(URL, sources_dir=d).sha256 == expected


def test_offset_timestamps_compare_as_instants(tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
    # 10:00+08:00 is 02:00Z, which is EARLIER than 05:00Z although it sorts later as a string.
    later = _store(d, "really later", "2026-09-05T05:00:00+00:00")
    earlier = _store(d, "really earlier", "2026-09-05T10:00:00+08:00")
    _write_index(d, [later, earlier])
    assert ss.get_latest_capture(URL, sources_dir=d).text == "really later"


def test_conflict_marker_is_reported_by_line_and_never_crashes(tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
    row = _store(d, "kept", "2026-09-05T00:00:00+00:00")
    _write_index(d, [row], extra_lines=["<<<<<<< HEAD", "=======", ">>>>>>> theirs"])
    assert [c.text for c in ss.get_captures(URL, sources_dir=d)] == ["kept"]
    problems = ss.index_problems(sources_dir=d)
    assert [p["line"] for p in problems] == [2, 3, 4]
    assert all(p["kind"] == "conflict-marker" for p in problems)


def test_corrupt_non_marker_line_is_reported_not_raised(tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
    row = _store(d, "kept", "2026-09-05T00:00:00+00:00")
    _write_index(d, [row], extra_lines=["{not json"])
    assert [c.text for c in ss.get_captures(URL, sources_dir=d)] == ["kept"]
    assert [p["kind"] for p in ss.index_problems(sources_dir=d)] == ["corrupt"]


def test_missing_or_unparseable_fetched_at_never_beats_a_dated_capture(tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
    dated = _store(d, "dated", "2026-09-01T00:00:00+00:00")
    undated = _store(d, "undated", "")
    _write_index(d, [dated, undated])
    assert ss.get_latest_capture(URL, sources_dir=d).text == "dated"


def test_no_matching_capture_still_returns_none(tmp_path):
    d = tmp_path / "sources"
    _write_index(d, [])
    assert ss.get_latest_capture(URL, sources_dir=d) is None
    assert ss.get_captures(URL, sources_dir=d) == []


@pytest.mark.parametrize("fname", ["index.jsonl"])
def test_store_capture_round_trips_through_get_captures(tmp_path, fname):
    d = tmp_path / "sources"
    ss.store_capture(URL, "one", sources_dir=d, fetched_at="2026-09-01T00:00:00+00:00")
    ss.store_capture(URL, "two", sources_dir=d, fetched_at="2026-09-02T00:00:00+00:00")
    assert [c.text for c in ss.get_captures(URL, sources_dir=d)] == ["one", "two"]


def test_the_evidence_reader_refuses_a_damaged_index_rather_than_skip_a_line(tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
    row = _store(d, "kept", "2026-09-05T00:00:00+00:00")
    _write_index(d, [row], extra_lines=["{not json"])
    with pytest.raises(ValueError, match="Corrupt capture index"):
        ss.get_latest_capture(URL, sources_dir=d)


@pytest.mark.parametrize("sha", ["../outside", "a" * 63, "A" * 64, ""])
def test_a_sha_that_is_not_a_digest_never_names_a_file(tmp_path, sha):
    d = tmp_path / "sources"
    d.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    row = {"url_norm": ss.url_norm(URL), "fetched_at": "2026-09-05T00:00:00+00:00", "sha256": sha}
    _write_index(d, [row])
    assert ss.get_captures(URL, sources_dir=d) == []


def test_when_the_newest_row_has_no_page_there_is_no_latest_capture_not_an_older_one(tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
    older = _store(d, "older page", "2026-09-01T00:00:00+00:00")
    _, newest = _row("newest page", "2026-09-05T00:00:00+00:00")  # its text file is never written
    _write_index(d, [older, newest])
    assert ss.get_latest_capture(URL, sources_dir=d) is None
    assert [c.text for c in ss.get_captures(URL, sources_dir=d)] == ["older page"]
