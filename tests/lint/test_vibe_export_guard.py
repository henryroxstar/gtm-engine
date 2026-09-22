"""The Vibe export guard must block the wrong retrieval path — and nothing else.

`.claude/hooks/vibe-export-guard.sh` is a PreToolUse hook: a Vibe/Explorium export link
may only be read by `gtm_core.dataset_fetch` (host-pinned, §R6). It exists because on
2026-09-21 an ad-hoc session with no skill loaded tried a summarising fetch, tried a
shell transfer, concluded the CSV was unreachable, abandoned a paid export and silently
re-sourced the rows from another provider — while the fetcher existed and the bucket was
already allowlisted.

Hooks are otherwise untested in this repo, which is the exact shape of failure the guard
was written to prevent: a control nobody exercises. A guard that blocks everything, or
nothing, passes no test here — the MUST-ALLOW cases carry the weight, especially
"prose mentioning the host", because the sibling Doppler guard substring-matches and so
blocks its own documentation.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

# `.claude/hooks/` is withheld from the OSS carve by design (the carve's own
# must-not-ship invariant) — this whole module's premise, the hook FILE existing
# on disk, does not hold there. See tests/conftest.py's private_tree marker.
pytestmark = pytest.mark.private_tree

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "vibe-export-guard.sh"

BLOCK = 2
ALLOW = 0

EXPORT_URL = "https://share.explorium.ai/abc123"
S3_URL = "https://mcp-datasets-prod.s3.amazonaws.com/x/y.csv?sig=1"


def _run(event: str) -> int:
    return subprocess.run(  # noqa: S603
        ["bash", str(HOOK)],
        input=event,
        text=True,
        capture_output=True,
        timeout=30,
    ).returncode


def _bash(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def _fetch(url: str) -> str:
    return json.dumps({"tool_name": "WebFetch", "tool_input": {"url": url}})


def test_hook_exists():
    assert HOOK.is_file(), f"{HOOK} is wired in .claude/settings.json and must exist"


@pytest.mark.parametrize(
    ("name", "event"),
    [
        ("fetch of the share host", _fetch(EXPORT_URL)),
        ("fetch of the s3 redirect target", _fetch(S3_URL)),
        ("shell transfer of the share host", _bash(f"curl -sL {EXPORT_URL} -o out.csv")),
        (
            "python urllib against the bucket",
            _bash(f"python -c 'import urllib.request,sys;{S3_URL}'"),
        ),
    ],
)
def test_must_block(name, event):
    assert _run(event) == BLOCK, name


@pytest.mark.parametrize(
    ("name", "event"),
    [
        (
            "the sanctioned fetcher on the same URL",
            _bash(
                f"uv run python -m gtm_core.dataset_fetch --profile p --url {EXPORT_URL} --name x"
            ),
        ),
        # Prose cases: writing or searching docs that QUOTE the host must not be blocked,
        # or the guard blocks its own documentation.
        ("heredoc quoting the host", _bash(f"cat > doc.md <<'EOF'\nsee {EXPORT_URL}\nEOF")),
        ("grep for the host in the tree", _bash("grep -rn share.explorium.ai gtm_core/")),
        ("unrelated fetch", _fetch("https://example.com/docs")),
        ("transfer to an unrelated host", _bash("curl -s https://example.com/a.csv")),
        ("plain shell command", _bash("ls -la")),
    ],
)
def test_must_allow(name, event):
    assert _run(event) == ALLOW, name


@pytest.mark.parametrize(
    ("name", "event"),
    [
        ("malformed JSON", "not json at all"),
        ("empty event", ""),
        ("event with no command or url", json.dumps({"tool_name": "Bash", "tool_input": {}})),
    ],
)
def test_fails_open(name, event):
    """An unavailable guard must not wedge every Bash call in the session."""
    assert _run(event) == ALLOW, name


# ── other harnesses: Cursor and Antigravity send different payload shapes ───────
#
# Cursor's beforeShellExecution carries the command at the TOP LEVEL and sends no tool
# name at all. That unnamed case must fall to the conservative branch (require a transfer
# verb) — if an unknown tool were treated as a fetch, the guard would block Cursor users
# for merely grepping the host, which is how a guard gets switched off.

_XFER = "curl -sL " + EXPORT_URL + " -o out.csv"
_PROSE = "grep -rn share.explorium.ai gtm_core/"


@pytest.mark.parametrize(
    ("name", "event", "expected"),
    [
        ("cursor shell: transfer of the host", json.dumps({"command": _XFER}), BLOCK),
        ("cursor shell: prose, no transfer verb", json.dumps({"command": _PROSE}), ALLOW),
        (
            "cursor shell: the sanctioned fetcher",
            json.dumps({"command": f"uv run python -m gtm_core.dataset_fetch --url {EXPORT_URL}"}),
            ALLOW,
        ),
        (
            "antigravity: transfer of the host",
            json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": _XFER}}}),
            BLOCK,
        ),
        (
            "antigravity: prose, no transfer verb",
            json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": _PROSE}}}),
            ALLOW,
        ),
    ],
)
def test_other_harness_payload_shapes(name, event, expected):
    assert _run(event) == expected, name
