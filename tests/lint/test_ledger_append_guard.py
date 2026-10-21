"""The ledger append guard blocks hand-written ledger rows — and nothing else.

`.claude/hooks/ledger-append-guard.sh` exists because interactive sessions wrote 11 unchained rows
into the hash-chained ledgers (a one-off Python ``open(p, "a")`` into history.jsonl three times, a
shell ``>>`` into costs.jsonl once), each a permanent break ``gtm_core.ledger_verify`` cannot tell
from tampering. The BLOCK cases are those incident shapes (paths fictional, record bodies elided);
the ALLOW cases carry the weight — a guard that blocked reading a ledger would be switched off.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

# `.claude/hooks/` is withheld from the OSS carve by design (see tests/conftest.py).
pytestmark = pytest.mark.private_tree

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "hooks" / "ledger-append-guard.sh"
TWIN = ROOT / ".agents" / "hooks" / "ledger-append-guard.sh"
HIST = "content/fixtureco/history.jsonl"
COSTS = "content/fixtureco/costs.jsonl"

BLOCK, ALLOW = 2, 0


def _run(event: dict) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        ["bash", str(HOOK)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.mark.parametrize(
    "command",
    [
        # 2026-08-12: path in a variable, then open(p, 'a') in a heredoc.
        f"uv run python - <<'EOF'\nimport json\np='{HIST}'\nwith open(p,'a') as f:\n"
        "    for e in evs: f.write(json.dumps(e)+'\\n')\nEOF",
        # 2026-08-18: pathlib, p.open('a', encoding=...).
        f"uv run python -c \"import json,pathlib; p=pathlib.Path('{HIST}'); "
        "p.open('a',encoding='utf-8').write(json.dumps(ev)+'\\n')\"",
        # 2026-09-11: shell append of a cost row.
        f'echo \'{{"tool": "x", "cost_usd": 0.1}}\' >> {COSTS}',
        f'printf "%s\\n" "$ROW" >> "{HIST}"',
        f"echo row | tee -a {HIST}",
        f"sed -i '' '3d' {HIST}",
        f"cat rows.jsonl > {HIST}",
    ],
)
def test_blocks_the_incident_shapes(command):
    proc = _run(_bash(command))
    assert proc.returncode == BLOCK, proc.stderr
    assert "ledger_cli append-history" in proc.stderr


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_blocks_a_file_tool_on_a_ledger(tool):
    proc = _run({"tool_name": tool, "tool_input": {"file_path": f"/repo/{HIST}"}})
    assert proc.returncode == BLOCK


@pytest.mark.parametrize(
    "event",
    [
        _bash(f"uv run python -m gtm_core.ledger_verify {HIST}"),
        _bash(f"grep -c sequence_staged {HIST} 2>/dev/null"),
        _bash(f"tail -n 5 {COSTS} > /tmp/tail.jsonl"),
        _bash(f"wc -l {HIST} >> notes.txt"),
        _bash(f"cp {HIST} {HIST}.bak"),
        # The sanctioned path, including from Python.
        _bash(
            "uv run python -m gtm_core.ledger_cli append-history --profile fixtureco "
            '--json \'{"event": "x"}\''
        ),
        _bash(
            "uv run python - <<'EOF'\nfrom agent.ledgers import Ledgers\n"
            "Ledgers(cfg, 'fixtureco').append_history({'event': 'x'})\nEOF"
        ),
        # An append to some other file, in a command that never names a ledger.
        _bash("uv run python -c \"open('notes.txt','a').write('x')\""),
        # Reading a ledger with a file tool, and writing a test fixture.
        {"tool_name": "Read", "tool_input": {"file_path": f"/repo/{HIST}"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/repo/tests/fixtures/history.jsonl"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/repo/content/fixtureco/notes.md"}},
        {"tool_name": "Bash", "tool_input": {}},
    ],
)
def test_allows_reads_the_sanctioned_path_and_unrelated_writes(event):
    proc = _run(event)
    assert proc.returncode == ALLOW, proc.stderr
    assert proc.stderr == ""


def test_unparseable_event_fails_open():
    proc = subprocess.run(  # noqa: S603
        ["bash", str(HOOK)], input="not json", text=True, capture_output=True, check=False
    )
    assert proc.returncode == ALLOW


@pytest.mark.parametrize(
    ("payload", "decision"),
    [
        ({"command": f"echo row >> {HIST}"}, "deny"),  # Cursor beforeShellExecution
        ({"toolCall": {"name": "write_to_file", "args": {"TargetFile": f"/p/{COSTS}"}}}, "deny"),
        ({"toolCall": {"name": "run_command", "args": {"CommandLine": f"tail {HIST}"}}}, "allow"),
    ],
)
def test_twin_wrapper_emits_one_json_decision(payload, decision):
    proc = subprocess.run(  # noqa: S603
        ["bash", str(TWIN)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert json.loads(proc.stdout)["decision"] == decision, proc.stdout
