"""Is this process inside a Claude Code session a person is driving (desktop Code tab / CLI)?

Claude Code exports ``CLAUDECODE=1`` and ``CLAUDE_CODE_ENTRYPOINT`` into its Bash tool.
The headless brain (agent/session.py) also runs under the SDK, so it sets ``GTM_RUNTIME=headless``
in the child env so the desktop-only paths (TIER 0 bypass, .active-profile marker) are never
honoured on the VPS.
"""

from __future__ import annotations

import os


def is_desktop_session() -> bool:
    """True when running inside Claude Code interactively, not in headless Agent SDK mode."""
    if os.getenv("GTM_RUNTIME") == "headless":
        return False
    return bool(os.getenv("CLAUDECODE") or os.getenv("CLAUDE_CODE_ENTRYPOINT"))
