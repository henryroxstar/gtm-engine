"""The interactive send-guard hook, executed the way Claude Code runs it.

`agent/permissions.py` only governs headless runs; an interactive session reaches a hosted
Saleshandy connector through this hook alone. It matched exact leaf names, so the hosted
connector's `import_prospects_to_sequence_step` / `import_prospects_with_field_name` /
`update_sequence_status` all passed (verified 2026-09-25).

Operator decision (2026-09-25): an enrolment VARIANT asks the person each time — wave A was
imported that way on purpose — while status changes and the two exact enrol verbs stay denied.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IS_CARVE = not (ROOT / "oss").is_dir()
HOOK = (
    ROOT / ".claude" / "hooks" / "send-guard.sh"
    if IS_CARVE
    else ROOT / "oss" / "overlays" / ".claude" / "hooks" / "send-guard.sh"
)
_HOSTED = "mcp__a84f5f15-b971-4074-8e7c-ca9385bd1cb1__"


def _run(tool_name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(HOOK)],
        input=json.dumps({"tool_name": tool_name, "tool_input": {}}),
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    "leaf",
    [
        "add_leads_to_sequence",
        "import_prospects_to_sequence",
        "update_sequence_status",
        "activate_sequence",
        "resume_sequence",
        "purchase_domain",
        "delete_sequence",
        "delete_domain",
        "revoke_domain",
        "add_email_accounts_to_sequence",
    ],
)
def test_denied_leaves_exit_2(leaf):
    res = _run(_HOSTED + leaf)
    assert res.returncode == 2, res.stdout + res.stderr


@pytest.mark.parametrize(
    "leaf",
    [
        "import_prospects_to_sequence_step",
        "import_prospects_with_field_name",
        "add_leads_to_sequence_step",
    ],
)
def test_enroll_variants_ask_the_operator(leaf):
    res = _run(_HOSTED + leaf)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "ask"
    assert out["permissionDecisionReason"]


@pytest.mark.parametrize("leaf", ["list_sequences", "create_sequence", "update_sequence_settings"])
def test_read_and_build_leaves_pass_silently(leaf):
    res = _run(_HOSTED + leaf)
    assert res.returncode == 0
    assert res.stdout.strip() == ""
