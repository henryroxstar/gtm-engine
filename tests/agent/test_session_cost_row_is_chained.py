"""The brain's own cost row joins the costs.jsonl hash-chain (NIST AU-9).

``AgentSession._log_usage`` wrote its VPS/cockpit row with a bare ``open("a")``, so every brain
cost row reached ``costs.jsonl`` with no ``prev_sha256`` — and ``gtm_core.ledger_verify`` reports
an unchained row after the chain has started as a break. Every other writer goes through
``Ledgers``; this one now does too.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent.ledgers import Ledgers
from agent.session import AgentSession
from gtm_core.ledger_verify import verify_chain

_USAGE = {
    "input_tokens": 1200,
    "output_tokens": 300,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 800,
}


def test_brain_cost_rows_carry_prev_sha256_and_verify(tmp_path):
    cfg = SimpleNamespace(content_root=tmp_path)
    # An MCP worker metered first, so the brain row must chain onto a row it did not write.
    Ledgers(cfg, "fixtureco").append_cost({"tool": "worker", "cost_usd": 0.01})

    session = AgentSession(cfg=cfg, profile="fixtureco")
    session._log_usage(_USAGE)
    session._log_usage(_USAGE)

    path = tmp_path / "fixtureco" / "costs.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["tool"] for r in rows] == ["worker", "brain", "brain"]
    assert all(r.get("prev_sha256") for r in rows), "a brain row reached costs.jsonl unchained"
    assert rows[1]["cost_usd"] > 0  # the cap still counts it

    result = verify_chain(path)
    assert result.ok, result.breaks
    assert result.chained_from == 1
