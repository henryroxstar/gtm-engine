"""The interactive send-guard hook, executed the way Claude Code runs it.

`agent/permissions.py` only governs headless runs; an interactive session reaches a hosted
Saleshandy connector through this hook alone. It matched exact leaf names, so the hosted
connector's `import_prospects_to_sequence_step` / `import_prospects_with_field_name` /
`update_sequence_status` all passed (verified 2026-09-25).

Operator decision (2026-09-25): an enrolment VARIANT asks the person each time — wave A was
imported that way on purpose — while status changes and the two exact enrol verbs stay denied.

Operator decision (2026-10-15): `add_dnc_items` asks too. It is add-only and can only stop mail,
so a hard deny protected nothing in a session where the operator is present; every verb that
removes from or reshapes the Do Not Contact list stays denied. The headless brain is unchanged
(`tests/agent/test_permissions.py`): there a crafted reply could suppress a competitor.
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
        # the Do Not Contact list: only the ADD asks; everything that un-suppresses or
        # reshapes the list is a hard deny
        "remove_dnc_items",
        "delete_dnc_item",
        "delete_dnc_list",
        "clear_dnc_list",
        "update_dnc_list",
        "create_dnc_list",
    ],
)
def test_denied_leaves_exit_2(leaf):
    res = _run(_HOSTED + leaf)
    assert res.returncode == 2, res.stdout + res.stderr


def test_add_email_accounts_allowed():
    res = _run(_HOSTED + "add_email_accounts_to_sequence")
    assert res.returncode == 0, res.stdout + res.stderr


def test_add_dnc_items_asks_the_operator_with_a_dnc_specific_reason():
    res = _run(_HOSTED + "add_dnc_items")
    assert res.returncode == 0, res.stdout + res.stderr
    out = json.loads(res.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "ask"
    assert "Do Not Contact" in out["permissionDecisionReason"]
    assert "enrolls" not in out["permissionDecisionReason"]


def test_add_dnc_items_is_not_a_leaf_the_hook_denies():
    # The ask is a property of the generated lists, so pin where it lives: not in the deny list,
    # and in the prefix file as an exact (not prefix) ask.
    hooks = HOOK.parent
    assert "add_dnc_items" not in (hooks / "send-leaves.txt").read_text().split()
    assert "ask-exact add_dnc_items" in (hooks / "send-prefixes.txt").read_text().splitlines()


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


def test_message_to_own_claude_session_passes():
    # Claude Desktop's cross-session message stays on this machine — not outbound.
    res = _run("mcp__ccd_session_mgmt__send_message")
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize(
    "tool",
    [
        "mcp__00000000-0000-4000-8000-000000000000__send_message",  # a mail connector's leaf
        "mcp__ccd_session_mgmt_x__send_message",  # a look-alike server name
    ],
)
def test_other_send_message_tools_stay_denied(tool):
    res = _run(tool)
    assert res.returncode == 2, res.stdout + res.stderr
