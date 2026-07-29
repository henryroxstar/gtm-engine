"""Unit tests for the ledger CLI (agent.ledger_cli) — SDK-INDEPENDENT.

The CLI is the skills' write path to the ledgers. It must shape the same
``content/<profile>/`` tree the Ledgers class does, stamp ``ts``, and report the
monthly total (with a scriptable over-cap exit code). We drive it with
``--repo-root tmp_path`` so all state lands under a throwaway dir.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("agent.ledger_cli", reason="agent.ledger_cli not built yet")

from agent.ledger_cli import main as ledger_main  # noqa: E402

PROFILE = "example"


def _run(tmp_path: Path, *argv: str) -> int:
    return ledger_main(["--repo-root", str(tmp_path), *argv])


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_append_history_and_cost_write_jsonl(tmp_path):
    rc = _run(
        tmp_path,
        "append-history",
        "--profile",
        PROFILE,
        "--json",
        json.dumps({"event": "asset_ready", "item_id": "ci-1"}),
    )
    assert rc == 0
    rc = _run(
        tmp_path,
        "append-cost",
        "--profile",
        PROFILE,
        "--json",
        json.dumps({"tool": "deepseek-worker", "cost_usd": 0.12}),
    )
    assert rc == 0

    base = tmp_path / "content" / PROFILE
    hist = _read_jsonl(base / "history.jsonl")
    costs = _read_jsonl(base / "costs.jsonl")
    assert hist[0]["event"] == "asset_ready"
    assert "ts" in hist[0], "CLI must stamp ts via Ledgers"
    assert costs[0]["cost_usd"] == 0.12


def test_write_run_manifest_lands_under_runs(tmp_path):
    manifest = {
        "run_id": "r-1",
        "trigger": "telegram",
        "stages": [{"name": "radar", "status": "ok", "outputs": ["d.md"]}],
    }
    rc = _run(tmp_path, "write-run-manifest", "--profile", PROFILE, "--json", json.dumps(manifest))
    assert rc == 0
    path = tmp_path / "content" / PROFILE / "runs" / "r-1.json"
    assert path.is_file()
    assert json.loads(path.read_text())["run_id"] == "r-1"


def test_month_total_over_cap_exit_code(tmp_path, capsys):
    _run(
        tmp_path,
        "append-cost",
        "--profile",
        PROFILE,
        "--json",
        json.dumps({"tool": "x", "cost_usd": 7.0}),
    )
    # Under cap → exit 0.
    assert _run(tmp_path, "month-total", "--profile", PROFILE, "--cap", "10") == 0
    # At/over cap → exit 2 (scriptable hard stop).
    rc = _run(tmp_path, "month-total", "--profile", PROFILE, "--cap", "5")
    assert rc == 2
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["over_cap"] is True
    assert payload["total_usd"] == pytest.approx(7.0)


def test_bad_json_payload_errors(tmp_path):
    with pytest.raises(SystemExit):
        _run(tmp_path, "append-history", "--profile", PROFILE, "--json", "{not json")


# ── record-manual-publish (durable idempotency for a hand-posted item) ────────


def test_record_manual_publish_stamps_matching_content_hash(tmp_path, capsys):
    """A manual publish must record the SAME content_sha256 the auto-publisher uses,
    so published_content_hashes() sees it and a later auto-publish dedupes."""
    from gtm_core.ledgers import Ledgers
    from gtm_core.paths import PathConfig
    from gtm_core.publish_hash import content_hash

    asset = tmp_path / "ci-1.asset.json"
    asset.write_text(json.dumps({"platform": "linkedin", "body": "Shipped a thing."}))

    rc = _run(
        tmp_path,
        "record-manual-publish",
        "--profile",
        PROFILE,
        "--asset",
        str(asset),
        "--item-id",
        "ci-1",
        "--source",
        "journey",
        "--url",
        "https://linkedin.com/p/abc",
    )
    assert rc == 0
    printed = capsys.readouterr().out.strip().splitlines()[-1]

    expected = content_hash("Shipped a thing.", ())
    assert printed == expected, "CLI prints the recorded content_sha256"

    base = tmp_path / "content" / PROFILE
    rec = _read_jsonl(base / "history.jsonl")[0]
    assert rec["event"] == "published"
    assert rec["content_sha256"] == expected
    assert rec["manual"] is True
    assert rec["source"] == "journey"
    assert rec["url"] == "https://linkedin.com/p/abc"
    assert "ts" in rec, "CLI must stamp ts via Ledgers"

    # The durable idempotency ledger must now see this hash.
    led = Ledgers(PathConfig.from_env(repo_root=tmp_path), PROFILE)
    assert expected in led.published_content_hashes()


def test_record_manual_publish_rejects_asset_without_body(tmp_path):
    asset = tmp_path / "ci-x.asset.json"
    asset.write_text(json.dumps({"platform": "x", "tweets": ["a"]}))  # no 'body'
    with pytest.raises(SystemExit):
        _run(
            tmp_path,
            "record-manual-publish",
            "--profile",
            PROFILE,
            "--asset",
            str(asset),
            "--item-id",
            "ci-x",
        )
