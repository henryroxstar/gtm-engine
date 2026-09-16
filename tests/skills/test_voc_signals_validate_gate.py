"""Contract: ``--validate`` is a gate — it can FAIL.

The first version printed only how many records SURVIVED validation and always exited 0. On
2026-09-10 a 76-record ``signals-2026-09-10.json`` holding 6 malformed records printed
``{"valid": 70}`` and exited 0, which at a glance is indistinguishable from a clean 70-record
file; a file where EVERY record was malformed would have printed ``{"valid": 0}`` and also
exited 0. The six errors were real (``disposition='open'`` on a ``verified: True`` record — the
schema forbids it, because if the primary was read there is no document left to open), and were
only found by calling ``from_dict`` on each record by hand.

So a survivor count is not a verdict. These tests pin the three properties that make it one:
the total in the file is reported alongside it, every reject names its id and its error, and
the exit code is non-zero whenever anything failed.

The forgiving read path is pinned here too, because it is what made the silent gate tolerable
in the first place and must not regress: ``load`` still drops bad records rather than crashing
issue generation.
"""

from __future__ import annotations

import json

from gtm_core.voc import signals as sig


def _raw(**overrides) -> dict:
    base = {
        "id": "sig-2026-09-10-example",
        "title": "Example signal",
        "date": "2026-09-10",
        "lane": "funding_and_ma",
        "speaker": "vendor-voice",
        "entity": "Example Inc",
        "url": "https://example.test/news",
        "direction": "neutral",
        "verified": True,
    }
    base.update(overrides)
    return base


def _write(tmp_path, records: list[dict], name: str = "signals-2026-09-10.json"):
    path = tmp_path / name
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return path


def _run(capsys, path) -> tuple[int, dict]:
    code = sig.main(["--profile", "template", "--validate", str(path)])
    return code, json.loads(capsys.readouterr().out)


#: The exact defect shape observed on 2026-09-10 — verified, yet still pointing at a document to
#: go and read.
def _observed_bad(n: int) -> dict:
    return _raw(id=f"sig-2026-09-10-bad-{n}", verified=True, disposition="open", settled_by="S-1")


# --- the gate ---------------------------------------------------------------------------------


def test_a_clean_file_passes_and_reports_the_whole_file(tmp_path, capsys):
    path = _write(tmp_path, [_raw(id=f"sig-2026-09-10-ok-{i}") for i in range(4)])

    code, out = _run(capsys, path)

    assert code == 0
    assert (out["total"], out["valid"], out["rejected"]) == (4, 4, 0)
    assert out["ok"] is True
    assert "rejects" not in out


def test_some_bad_records_fail_the_gate_and_are_named(tmp_path, capsys):
    """The 2026-09-10 case in miniature: survivors alone cannot distinguish this from a clean file."""
    path = _write(
        tmp_path,
        [_raw(id=f"sig-2026-09-10-ok-{i}") for i in range(4)]
        + [_observed_bad(1), _observed_bad(2)],
    )

    code, out = _run(capsys, path)

    assert code != 0, "a file with malformed records must not exit 0"
    assert (out["total"], out["valid"], out["rejected"]) == (6, 4, 2)
    assert out["ok"] is False

    rejected_ids = [r["id"] for r in out["rejects"]]
    assert rejected_ids == ["sig-2026-09-10-bad-1", "sig-2026-09-10-bad-2"]
    for reject in out["rejects"]:
        # The error must say what is wrong, not merely that something is.
        assert "disposition" in reject["error"] and "verified" in reject["error"]
    assert [r["index"] for r in out["rejects"]] == [4, 5]


def test_a_wholly_malformed_file_is_not_a_silent_zero(tmp_path, capsys):
    """`{"valid": 0}` + exit 0 was the worst case: total failure rendered as an empty success."""
    path = _write(tmp_path, [_observed_bad(1), _observed_bad(2), _observed_bad(3)])

    code, out = _run(capsys, path)

    assert code != 0
    assert out["valid"] == 0
    assert out["total"] == 3, "the count of records IN THE FILE, not the count that survived"
    assert out["rejected"] == 3
    assert len(out["rejects"]) == 3


def test_a_record_whose_id_is_missing_is_still_locatable(tmp_path, capsys):
    """A reject is reported by index too — a record whose id is the broken field has no id."""
    bad = _raw()
    del bad["id"]
    path = _write(tmp_path, [_raw(id="sig-2026-09-10-ok-0"), bad])

    code, out = _run(capsys, path)

    assert code != 0
    assert out["rejects"][0]["id"] == sig.UNKNOWN_ID
    assert out["rejects"][0]["index"] == 1


def test_an_unusable_file_fails_rather_than_reporting_zero_valid_records(tmp_path, capsys):
    """Missing / non-array files went down the same silent path as a wholly malformed one."""
    missing = tmp_path / "signals-2026-09-10.json"
    code, out = _run(capsys, missing)
    assert code != 0 and out["file_error"]

    not_an_array = tmp_path / "signals-2026-09-11.json"
    not_an_array.write_text(json.dumps({"id": "sig-1"}), encoding="utf-8")
    code, out = _run(capsys, not_an_array)
    assert code != 0 and "array" in out["file_error"]


# --- the read path stays forgiving -------------------------------------------------------------


def test_load_still_drops_bad_records_instead_of_crashing_the_issue(tmp_path):
    path = _write(tmp_path, [_raw(id="sig-2026-09-10-ok-0"), _observed_bad(1)])

    records = sig.load(path)

    assert [r.id for r in records] == ["sig-2026-09-10-ok-0"]


def test_load_survives_a_record_malformed_below_the_schema_checks(tmp_path):
    """``int(raw["decay_days"])`` raises a plain ValueError/TypeError, which the old
    ``except SignalValidationError`` never caught — so a hand-edited file COULD crash the issue,
    exactly what the forgiving path promised it would not."""
    path = _write(
        tmp_path,
        [
            _raw(id="sig-2026-09-10-ok-0"),
            _raw(id="sig-2026-09-10-str", decay_days="soon"),
            _raw(id="sig-2026-09-10-list", decay_days=[]),
        ],
    )

    assert [r.id for r in sig.load(path)] == ["sig-2026-09-10-ok-0"]

    report = sig.load_reporting(path)
    assert [r.id for r in report.rejects] == ["sig-2026-09-10-str", "sig-2026-09-10-list"]


def test_load_and_the_gate_read_the_same_file_the_same_way(tmp_path):
    """The CLI must not re-implement the loop — one loader, two verdicts."""
    path = _write(tmp_path, [_raw(id="sig-2026-09-10-ok-0"), _observed_bad(1)])

    report = sig.load_reporting(path)

    assert report.records == sig.load(path)
    assert report.total == 2 and report.ok is False
