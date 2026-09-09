"""Denial-ledger tests (P0-1) — SDK-INDEPENDENT.

The sinks must produce hash-chained, secret-free records in
``content/<profile>/denials.jsonl``, never raise into a run, and be queryable via
``Ledgers.denials_summary`` / the ``ledger_cli denials`` subcommand.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agent.denial_log import denial_detail, make_denial_sink
from gtm_core.ledgers import GENESIS_HASH, Ledgers

PROFILE = "example"


@dataclass
class _Cfg:
    content_root: Path


def _rows(cfg: _Cfg) -> list[dict]:
    path = cfg.content_root / PROFILE / "denials.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_sink_appends_chained_records_with_source_and_outcome(tmp_path):
    cfg = _Cfg(tmp_path / "content")
    sink = make_denial_sink(cfg, PROFILE, "pipeline:studio")
    sink("Bash", {"command": "npm run export"}, "deny")
    sink("SlashCommand", {"command": "/x"}, "escalate")

    rows = _rows(cfg)
    assert len(rows) == 2
    assert rows[0]["prev_sha256"] == GENESIS_HASH
    assert rows[1]["prev_sha256"] != GENESIS_HASH  # chained to row 0
    assert rows[0]["tool"] == "Bash"
    assert rows[0]["decision"] == "deny"
    assert rows[0]["outcome"] == "denied"
    assert rows[0]["detail"] == "npm run export"
    assert rows[0]["source"] == "pipeline:studio"
    assert rows[0]["ts"]  # stamped by the appender


def test_mcp_tool_args_are_never_recorded(tmp_path):
    """MCP payloads can carry prospect PII — only the tool name identifies the call."""
    cfg = _Cfg(tmp_path / "content")
    sink = make_denial_sink(cfg, PROFILE, "cockpit")
    sink(
        "mcp__apollo__apollo_sequences_create",
        {"contact_email": "someone@acme.example"},
        "deny",
    )
    rows = _rows(cfg)
    assert rows[0]["detail"] == ""
    assert "acme.example" not in json.dumps(rows[0])


def test_detail_shapes_and_truncation():
    assert denial_detail("Bash", {"command": "  ls -la  "}) == "ls -la"
    assert denial_detail("Read", {"file_path": "/x/.env"}) == "/x/.env"
    assert denial_detail("Skill", {"skill": "prospect"}) == "prospect"
    assert denial_detail("Bash", None) == ""
    long = "x" * 400
    out = denial_detail("Bash", {"command": long})
    assert len(out) == 301 and out.endswith("…")


def test_sink_never_raises_on_unwritable_root(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file, not dir")
    sink = make_denial_sink(_Cfg(blocker), PROFILE, "one-shot")
    sink("Bash", {"command": "anything"}, "deny")  # must not raise


def test_denials_summary_buckets_and_window(tmp_path):
    cfg = _Cfg(tmp_path / "content")
    led = Ledgers(cfg, PROFILE)
    # ts is setdefault'd by the appender, so an explicit old ts survives → window test.
    led.append_denial(
        {"tool": "Bash", "decision": "deny", "outcome": "denied", "ts": "2020-01-01T00:00:00Z"}
    )
    led.append_denial({"tool": "Bash", "decision": "deny", "outcome": "denied"})
    led.append_denial({"tool": "SlashCommand", "decision": "escalate", "outcome": "approved"})

    summary = led.denials_summary(days=7)
    assert summary["total"] == 2  # the 2020 record falls outside the window
    assert summary["by_tool"] == {"Bash": 1, "SlashCommand": 1}
    assert summary["by_decision"] == {"deny": 1, "escalate": 1}
    assert summary["by_outcome"] == {"denied": 1, "approved": 1}
    assert len(summary["recent"]) == 2
    assert summary["recent"][-1]["tool"] == "SlashCommand"


def test_denials_summary_missing_file_and_junk_lines(tmp_path):
    cfg = _Cfg(tmp_path / "content")
    led = Ledgers(cfg, PROFILE)
    assert led.denials_summary()["total"] == 0
    base = cfg.content_root / PROFILE
    base.mkdir(parents=True)
    (base / "denials.jsonl").write_text('not json\n{"tool": "Bash", "ts": "junk"}\n')
    assert led.denials_summary()["total"] == 0  # junk skipped, never raises


def test_ledger_cli_denials_subcommand(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GTM_CONTENT_ROOT", raising=False)
    from gtm_core.ledger_cli import main
    from gtm_core.paths import PathConfig

    Ledgers(PathConfig.from_env(repo_root=tmp_path), PROFILE).append_denial(
        {"tool": "Bash", "decision": "deny", "outcome": "denied", "detail": "npm x"}
    )
    rc = main(["--repo-root", str(tmp_path), "denials", "--profile", PROFILE, "--days", "7"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["total"] == 1
    assert out["by_tool"] == {"Bash": 1}
