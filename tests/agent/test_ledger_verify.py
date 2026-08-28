"""Tests for the ledger hash-chain verifier CLI (``gtm_core.ledger_verify``).

Pure stdlib; exercises the verifier over raw JSONL files and the ``main`` exit codes. The append
side (chain construction) is covered in ``test_ledgers.py``; here we focus on the verifier's own
detection logic and CLI contract.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("gtm_core.ledger_verify", reason="gtm_core.ledger_verify not built yet")

from gtm_core.ledger_verify import main, verify_chain  # noqa: E402
from gtm_core.ledgers import _line_sha256  # noqa: E402


def _chain(records: list[dict]) -> list[str]:
    """Build a correctly-chained list of raw JSONL lines (genesis first)."""
    lines: list[str] = []
    prev = "GENESIS"
    for rec in records:
        rec = {**rec, "prev_sha256": prev}
        line = json.dumps(rec, ensure_ascii=False)
        lines.append(line)
        prev = _line_sha256(line)
    return lines


def test_valid_chain_ok(tmp_path):
    p = tmp_path / "history.jsonl"
    p.write_text("\n".join(_chain([{"e": 1}, {"e": 2}, {"e": 3}])) + "\n", encoding="utf-8")
    result = verify_chain(p)
    assert result.ok
    assert result.chained_from == 1
    assert result.total_lines == 3


def test_missing_file_reports_break(tmp_path):
    result = verify_chain(tmp_path / "nope.jsonl")
    assert not result.ok
    assert result.breaks[0][1] == "file not found"


def test_tamper_detected(tmp_path):
    lines = _chain([{"e": 1}, {"e": 2}, {"e": 3}])
    edited = json.loads(lines[0])
    edited["e"] = 999  # break line 1; line 2's prev_sha256 no longer matches
    lines[0] = json.dumps(edited, ensure_ascii=False)
    p = tmp_path / "history.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = verify_chain(p)
    assert not result.ok
    assert result.breaks[0][0] == 2


def test_legacy_prefix_skipped(tmp_path):
    legacy = ['{"e":"old1"}', '{"e":"old2"}']
    # The first chained record's prev_sha256 must hash the last legacy line.
    chained_first = {"e": "new1", "prev_sha256": _line_sha256(legacy[-1])}
    first_line = json.dumps(chained_first, ensure_ascii=False)
    second = {"e": "new2", "prev_sha256": _line_sha256(first_line)}
    p = tmp_path / "costs.jsonl"
    p.write_text(
        "\n".join([*legacy, first_line, json.dumps(second, ensure_ascii=False)]) + "\n",
        encoding="utf-8",
    )
    result = verify_chain(p)
    assert result.ok, result.breaks
    assert result.chained_from == 3


def test_cli_exit_codes(tmp_path, capsys):
    good = tmp_path / "good.jsonl"
    good.write_text("\n".join(_chain([{"e": 1}, {"e": 2}])) + "\n", encoding="utf-8")
    assert main([str(good)]) == 0

    lines = _chain([{"e": 1}, {"e": 2}])
    tampered = json.loads(lines[0])
    tampered["e"] = 42
    lines[0] = json.dumps(tampered, ensure_ascii=False)
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert main([str(bad)]) == 1

    assert main([str(tmp_path / "missing.jsonl")]) == 2


def test_cli_json_output(tmp_path, capsys):
    p = tmp_path / "h.jsonl"
    p.write_text("\n".join(_chain([{"e": 1}])) + "\n", encoding="utf-8")
    rc = main([str(p), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["total_lines"] == 1
