"""agent — the Content OS brain (headless Claude Code via the claude-agent-sdk).

Resolves a company profile per Telegram chat (session-bound; no global mutable
ACTIVE_PROFILE), loads the de-branded plugin, and wires the infra MCP servers.

Public surface (per the shared interface): :class:`Config` and :class:`SessionStore`.
Other building blocks (``profiles``, ``ledgers``, ``mcp_config``, ``session``)
are importable as submodules.
"""

from __future__ import annotations

from .config import Config
from .session import SessionStore

__all__ = ["Config", "SessionStore"]
