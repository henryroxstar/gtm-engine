"""Static verification and parity tests for scripts/stack.ps1 and scripts/stack.sh.

Verifies:
1. LD-09: scripts/stack.ps1 does not contain bare native command stderr redirection
   that terminates execution on Windows PowerShell 5.1 with $ErrorActionPreference = "Stop".
2. scripts/stack.ps1 defines Invoke-NativeQuiet with proper try/finally error action preference restoration.
3. Feature parity between stack.sh and stack.ps1 subcommands.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STACK_PS1 = REPO_ROOT / "scripts" / "stack.ps1"
STACK_SH = REPO_ROOT / "scripts" / "stack.sh"


def test_stack_ps1_no_bare_stderr_redirection():
    """LD-09: scripts/stack.ps1 must not have bare 2>$null or *>$null outside Invoke-NativeQuiet."""
    assert STACK_PS1.exists(), f"missing {STACK_PS1}"
    content = STACK_PS1.read_text(encoding="utf-8")

    # Extract body of Invoke-NativeQuiet
    match = re.search(r"function\s+Invoke-NativeQuiet\s*\{(?P<body>.*?)\n\}", content, re.DOTALL)
    assert match is not None, "Invoke-NativeQuiet function not found in stack.ps1"
    func_body = match.group("body")

    # Strip Invoke-NativeQuiet from content
    outside_func = content.replace(func_body, "")

    # Assert no bare redirections exist outside the function
    bare_redirects = re.findall(r"(\d*\s*>\s*\$null)", outside_func)
    assert not bare_redirects, (
        f"Found bare native stderr redirection in stack.ps1 outside Invoke-NativeQuiet: {bare_redirects}"
    )


def test_stack_ps1_invoke_native_quiet_restores_error_action_preference():
    """LD-09: Invoke-NativeQuiet must restore $ErrorActionPreference in a finally block."""
    content = STACK_PS1.read_text(encoding="utf-8")
    match = re.search(r"function\s+Invoke-NativeQuiet\s*\{(?P<body>.*?)\n\}", content, re.DOTALL)
    assert match is not None, "Invoke-NativeQuiet function not found in stack.ps1"
    func_body = match.group("body")

    assert "$prev = $ErrorActionPreference" in func_body, (
        "Invoke-NativeQuiet does not save previous $ErrorActionPreference"
    )
    assert '$ErrorActionPreference = "SilentlyContinue"' in func_body, (
        "Invoke-NativeQuiet does not set SilentlyContinue"
    )
    assert "finally" in func_body, "Invoke-NativeQuiet does not contain a finally block"
    assert "$ErrorActionPreference = $prev" in func_body, (
        "Invoke-NativeQuiet does not restore previous $ErrorActionPreference in finally"
    )


def test_stack_subcommand_parity():
    """stack.ps1 and stack.sh must support the exact same set of subcommands."""
    assert STACK_SH.exists() and STACK_PS1.exists()

    sh_content = STACK_SH.read_text(encoding="utf-8")
    ps1_content = STACK_PS1.read_text(encoding="utf-8")

    expected_commands = {"start", "stop", "restart", "status", "logs", "seed", "reset"}

    # Verify switch arms in stack.ps1
    for cmd in expected_commands:
        assert f'"{cmd}"' in ps1_content, f'Subcommand "{cmd}" missing from stack.ps1'

    # Verify case arms in stack.sh
    for cmd in expected_commands:
        assert f"{cmd})" in sh_content, f'Subcommand "{cmd}" missing from stack.sh'
