"""Lint test for the public .claude overlay (deny floor, hooks, settings).

R-2, R-3:
- oss/overlays/.claude/settings.json exists and is valid JSON.
- permissions.deny mirrors the shell deny floor from private .claude/settings.json.
- outputStyle is "gtm-operator" (PRD §8-B).
- Hooks map UserPromptSubmit -> secret-paste-guard.sh and PreToolUse(mcp__.*) -> send-guard.sh.
- Hook scripts are executable and enforce their contracts.
- send-leaves.txt matches agent.permissions._EXTERNAL_EFFECT_LEAVES.
- Private .claude/settings.json also wires both hooks (PRD §8-C).
- scripts/oss-export.sh allowlist includes settings.json and hooks.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent.permissions import _EXTERNAL_EFFECT_LEAVES

ROOT = Path(__file__).resolve().parents[2]
IS_CARVE = not (ROOT / "oss").is_dir()

OVERLAY_CLAUDE = ROOT / ".claude" if IS_CARVE else ROOT / "oss" / "overlays" / ".claude"
PRIVATE_SETTINGS = ROOT / ".claude" / "settings.json"
OVERLAY_SETTINGS = OVERLAY_CLAUDE / "settings.json"
SECRET_GUARD = OVERLAY_CLAUDE / "hooks" / "secret-paste-guard.sh"
SEND_GUARD = OVERLAY_CLAUDE / "hooks" / "send-guard.sh"
SEND_LEAVES = OVERLAY_CLAUDE / "hooks" / "send-leaves.txt"
EXPORT_SCRIPT = ROOT / "scripts" / "oss-export.sh"


def test_overlay_settings_json_exists_and_valid():
    assert OVERLAY_SETTINGS.is_file(), f"{OVERLAY_SETTINGS} must exist"
    data = json.loads(OVERLAY_SETTINGS.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("outputStyle") == "gtm-operator"


def test_overlay_settings_has_shell_deny_floor():
    overlay = json.loads(OVERLAY_SETTINGS.read_text(encoding="utf-8"))
    overlay_deny = set(overlay.get("permissions", {}).get("deny", []))
    if IS_CARVE:
        for r in ("Bash(curl:*)", "Bash(wget:*)", "Bash(rm:*)", "Bash(sudo:*)", "Read(**/.env)"):
            assert r in overlay_deny
        return

    private = json.loads(PRIVATE_SETTINGS.read_text(encoding="utf-8"))
    private_deny = set(private.get("permissions", {}).get("deny", []))

    # All private deny rules must be present in the public overlay
    missing = private_deny - overlay_deny
    assert not missing, f"Public overlay settings.json missing deny rules: {missing}"


def test_overlay_hooks_wired():
    overlay = json.loads(OVERLAY_SETTINGS.read_text(encoding="utf-8"))
    hooks = overlay.get("hooks", {})

    assert "UserPromptSubmit" in hooks
    ups_hooks = hooks["UserPromptSubmit"][0]["hooks"]
    assert any("secret-paste-guard.sh" in h.get("command", "") for h in ups_hooks)

    assert "PreToolUse" in hooks
    ptu_entry = next((e for e in hooks["PreToolUse"] if e.get("matcher") == "mcp__.*"), None)
    assert ptu_entry is not None, "PreToolUse hook with matcher 'mcp__.*' missing"
    assert any("send-guard.sh" in h.get("command", "") for h in ptu_entry["hooks"])


def test_private_settings_wires_both_hooks():
    if IS_CARVE:
        pytest.skip("Private settings not present in carve")
    private = json.loads(PRIVATE_SETTINGS.read_text(encoding="utf-8"))
    hooks = private.get("hooks", {})

    assert "UserPromptSubmit" in hooks
    ups_hooks = hooks["UserPromptSubmit"][0]["hooks"]
    assert any("secret-paste-guard.sh" in h.get("command", "") for h in ups_hooks)

    assert "PreToolUse" in hooks
    ptu_entry = next((e for e in hooks["PreToolUse"] if e.get("matcher") == "mcp__.*"), None)
    assert ptu_entry is not None, "Private PreToolUse hook with matcher 'mcp__.*' missing"
    assert any("send-guard.sh" in h.get("command", "") for h in ptu_entry["hooks"])


def test_send_leaves_file_matches_code_set():
    assert SEND_LEAVES.is_file(), f"{SEND_LEAVES} must exist"
    lines = [
        line.strip()
        for line in SEND_LEAVES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert set(lines) == _EXTERNAL_EFFECT_LEAVES
    assert lines == sorted(_EXTERNAL_EFFECT_LEAVES)


def test_hook_scripts_executable():
    assert SECRET_GUARD.is_file(), f"{SECRET_GUARD} must exist"
    assert os.access(SECRET_GUARD, os.X_OK), f"{SECRET_GUARD} must be executable"

    assert SEND_GUARD.is_file(), f"{SEND_GUARD} must exist"
    assert os.access(SEND_GUARD, os.X_OK), f"{SEND_GUARD} must be executable"


@pytest.mark.parametrize(
    "secret_token",
    [
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "AIzaSyD-abcdefghijklmnopqrstuvwxyz12345",  # gitleaks:allow — fake fixture for the paste-guard test
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDcSemACt8x4iTMCda8Yhe3iZaWbvV5XKSTbuAn0M",
        "password=supersecretpassword123",
    ],
)
def test_secret_paste_guard_blocks_secrets(secret_token: str):
    payload = json.dumps({"prompt": f"Here is the credential: {secret_token}"})
    res = subprocess.run(
        [str(SECRET_GUARD)],
        input=payload,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2, (
        f"Expected exit 2 for secret, got {res.returncode}. stderr: {res.stderr}"
    )
    assert "belongs in a settings file or a connector, not in chat" in res.stderr
    assert secret_token not in res.stderr, "Matched secret token must NEVER be echoed to stderr"
    assert secret_token not in res.stdout, "Matched secret token must NEVER be echoed to stdout"


def test_secret_paste_guard_allows_benign_prompt():
    payload = json.dumps({"prompt": "Please summarize our latest marketing strategy."})
    res = subprocess.run(
        [str(SECRET_GUARD)],
        input=payload,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"Expected exit 0 for benign prompt, got {res.returncode}. stderr: {res.stderr}"
    )


@pytest.mark.parametrize(
    "tool_name",
    [
        "mcp__5bd63f96-03c3-4b99-b913-fdddbc111549__send_message",
        "mcp__gmail__send_message",
        "mcp__slack__slack_send_message",
        "mcp__any_server__reply",
        "mcp__buffer__create_post",
        "mcp__reap__publish_clip",
    ],
)
def test_send_guard_blocks_denied_leaves(tool_name: str):
    payload = json.dumps({"tool_name": tool_name, "tool_input": {}})
    res = subprocess.run(
        [str(SEND_GUARD)],
        input=payload,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2, (
        f"Expected exit 2 for {tool_name}, got {res.returncode}. stderr: {res.stderr}"
    )
    assert (
        "The engine never sends, enrolls or posts from here. Draft it; the person sends it."
        in res.stderr
    )


@pytest.mark.parametrize(
    "tool_name",
    [
        "mcp__5bd63f96-03c3-4b99-b913-fdddbc111549__search_threads",
        "mcp__gmail__create_draft",
        "mcp__slack__conversations_history",
        "mcp__apollo__apollo_person_search",
    ],
)
def test_send_guard_allows_safe_tools(tool_name: str):
    payload = json.dumps({"tool_name": tool_name, "tool_input": {}})
    res = subprocess.run(
        [str(SEND_GUARD)],
        input=payload,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"Expected exit 0 for {tool_name}, got {res.returncode}. stderr: {res.stderr}"
    )


def test_oss_export_allowlist_contains_settings_and_hooks():
    if IS_CARVE:
        pytest.skip("Export script not present in carve")
    text = EXPORT_SCRIPT.read_text(encoding="utf-8")
    assert "settings.json)" in text
    assert "hooks)" in text
