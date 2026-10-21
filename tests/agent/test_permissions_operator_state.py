"""The brain cannot settle its own doubt, nor forge cockpit state (invariant-review findings, F4/F1).

`dispatch-intents resolve|removed|--restored|--reconcile` is how a HUMAN says "that send did / did not go out".
If the brain could run it (it is just `python -m …`), it could release its own block and re-trigger a duplicate
send. Likewise the persisted pending approvals and the intent ledger are written only by the cockpit/dispatchers —
never by a Write/Edit/shell redirect from the brain. `--unresolved` (a read) stays allowed.
"""

from __future__ import annotations

import pytest

from agent.permissions import classify_tool

CLI = "uv run python -m gtm_core.ledger_cli dispatch-intents --profile example"


@pytest.mark.parametrize(
    "cmd",
    [
        f"{CLI} resolve b-0123456789ab --not-sent",
        f"{CLI} resolve b-0123456789ab --sent",
        f"{CLI} removed --sequence s1 --recipient a@acme.example",
        f"{CLI} --restored",
        f"{CLI} --reconcile --sequence s1 --sequencer-export x.csv --record",
        "python -m gtm_core.ledger_cli dispatch-intents --profile example resolve b-0123456789ab --not-sent",
        "python -m agent.ledger_cli dispatch-intents --profile example resolve b-0123456789ab --not-sent",
        f"cd /app && {CLI} resolve b-0123456789ab --not-sent",
        f"echo hi && {CLI} --restored",
    ],
)
def test_the_brain_cannot_resolve_release_or_hold_dispatch_intents(cmd):
    assert classify_tool("Bash", {"command": cmd}) == "deny"


def test_reading_the_open_intents_is_still_allowed():
    assert classify_tool("Bash", {"command": f"{CLI} --unresolved"}) == "allow"


@pytest.mark.parametrize(
    "cmd",
    [
        "echo '{}' >> content/example/dispatch-intents.jsonl",
        "tee content/example/dispatch-intents.jsonl < x",
        "sed -i '' 's/unknown/dispatched/' content/example/dispatch-intents.jsonl",
        "rm content/example/dispatch-intents.jsonl",
        "echo {} > content/example/_cockpit/pending.json",
        "mv x content/_system/cockpit-state.json",
    ],
)
def test_the_brain_cannot_write_the_intent_ledger_or_cockpit_state_through_the_shell(cmd):
    assert classify_tool("Bash", {"command": cmd}) == "deny"


@pytest.mark.parametrize(
    "cmd",
    [
        "cat content/example/dispatch-intents.jsonl",
        "grep unknown content/example/dispatch-intents.jsonl",
        "wc -l content/example/dispatch-intents.jsonl",
    ],
)
def test_reading_the_intent_ledger_is_allowed(cmd):
    assert classify_tool("Bash", {"command": cmd}) == "allow"


@pytest.mark.parametrize("tool", ["Write", "Edit", "NotebookEdit"])
@pytest.mark.parametrize(
    "path",
    [
        "/app/content/example/dispatch-intents.jsonl",
        "content/example/dispatch-intents.jsonl",
        "/app/content/example/_cockpit/pending.json",
        "content/_system/cockpit-state.json",
        "content/_system/cockpit-heartbeat",
        "content/example/foo/../dispatch-intents.jsonl",
    ],
)
def test_the_brain_cannot_write_protected_state_with_the_file_tools(tool, path):
    assert classify_tool(tool, {"file_path": path}) == "deny"


def test_ordinary_content_writes_are_unaffected():
    for path in ("content/example/plans/2026-42-plan.md", "content/example/accounts/acme/notes.md"):
        assert classify_tool("Write", {"file_path": path}) == "allow"
