"""MM9 no-regression guard: ``classify_tool`` without confinement decides exactly as it did before.

``tests/fixtures/classify_tool_golden.json`` holds 252 decisions recorded from the policy at the commit
BEFORE path confinement was added (every built-in tool against secret and ordinary paths, ~70 Bash
shapes across the allow/deny/escalate classes, MCP tools, and ``Skill`` with and without a scope). The
single-tenant VPS and the cockpit never pass a confinement, so for them this file is the contract.

Omitting the keyword and passing ``None`` are the same default and could never disagree, so the golden —
not that equivalence — is what can actually go red when a branch changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.permissions import classify_tool

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "classify_tool_golden.json").read_text()
)["calls"]


def _id(i: int) -> str:
    c = GOLDEN[i]
    return f"{c['tool'] or '<empty>'}:{json.dumps(c['input'], sort_keys=True)[:50]}"


@pytest.mark.parametrize("i", range(len(GOLDEN)), ids=[_id(i) for i in range(len(GOLDEN))])
def test_the_unconfined_policy_decides_as_it_did_before_confinement_existed(i):
    c = GOLDEN[i]
    skills = frozenset(c["allowed_skills"]) if c["allowed_skills"] is not None else None
    assert classify_tool(c["tool"], c["input"], allowed_skills=skills) == c["decision"]
    assert (
        classify_tool(c["tool"], c["input"], allowed_skills=skills, confine=None) == c["decision"]
    )


def test_the_golden_is_not_vacuous():
    decisions = {c["decision"] for c in GOLDEN}
    assert decisions == {"allow", "deny", "escalate"}
    assert len(GOLDEN) >= 200
