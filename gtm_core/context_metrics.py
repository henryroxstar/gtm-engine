"""What a run's context is made of, and how it grows (W4 measurement, PRD 2026-10-19a).

Pure helpers only: no I/O, no SDK import. ``summarize_context_usage`` reduces the CLI's own
``get_context_usage`` payload (the data behind ``/context``) to the figures the PRD asks for;
``TurnTally`` records per-turn usage for the first-cache-write and last-turn-input figures.

An absent figure is ``None`` ("unmeasured"), never ``0``: a zero would claim the thing was measured
and costs nothing, which is the exact conclusion this measurement exists to test.
"""

from __future__ import annotations

import asyncio
from typing import Any


def _tok(value: Any) -> int | None:
    """A usable token count, or None. ``bool`` is an int subclass and is refused on purpose."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _skills_tokens(resp: dict) -> tuple[int | None, int | None, int | None]:
    skills = resp.get("skills")
    tokens: int | None = None
    included = total = None
    if isinstance(skills, dict):
        included, total = _tok(skills.get("includedSkills")), _tok(skills.get("totalSkills"))
        tokens = _tok(skills.get("tokens"))
        detail = skills.get("skillFrontmatter")
        if tokens is None and isinstance(detail, list):
            parts = [_tok(e.get("tokens")) for e in detail if isinstance(e, dict)]
            tokens = (
                sum(p for p in parts if p is not None)
                if any(p is not None for p in parts)
                else None
            )
    if tokens is None:
        cats = resp.get("categories")
        if isinstance(cats, list):
            for c in cats:
                if isinstance(c, dict) and str(c.get("name", "")).strip().lower().startswith(
                    "skills"
                ):
                    tokens = _tok(c.get("tokens"))
                    break
    return tokens, included, total


def summarize_context_usage(resp: object) -> dict[str, Any]:
    """Reduce a ``get_context_usage`` payload to skills / CLAUDE.md / per-MCP-server token figures."""
    out: dict[str, Any] = {
        "available": False,
        "skills_tokens": None,
        "skills_included": None,
        "skills_total": None,
        "memory_files_tokens": None,
        "mcp_tokens_by_server": {},
        "mcp_tokens_total": None,
        "mcp_deferred_tokens_total": None,
        "total_tokens": None,
    }
    if not isinstance(resp, dict):
        return out
    out["available"] = True
    out["total_tokens"] = _tok(resp.get("totalTokens"))
    out["skills_tokens"], out["skills_included"], out["skills_total"] = _skills_tokens(resp)

    files = resp.get("memoryFiles")
    if isinstance(files, list):
        parts = [_tok(f.get("tokens")) for f in files if isinstance(f, dict)]
        if any(p is not None for p in parts):
            out["memory_files_tokens"] = sum(p for p in parts if p is not None)

    tools = resp.get("mcpTools")
    if isinstance(tools, list):
        by_server: dict[str, int] = {}
        deferred = 0
        seen_deferred = False
        for t in tools:
            if not isinstance(t, dict):
                continue
            n, server = _tok(t.get("tokens")), t.get("serverName")
            if n is None or not isinstance(server, str) or not server:
                continue
            if t.get("isLoaded", True) is False:
                deferred += n
                seen_deferred = True
            else:
                by_server[server] = by_server.get(server, 0) + n
        out["mcp_tokens_by_server"] = by_server
        out["mcp_tokens_total"] = sum(by_server.values()) if by_server else None
        out["mcp_deferred_tokens_total"] = deferred if seen_deferred else None
    return out


async def read_context_summary(client: Any, timeout_s: float) -> dict[str, Any]:
    """``summarize_context_usage`` of the live CLI's breakdown; unavailable (not raised) on any failure.

    Bounded by ``timeout_s`` so a measurement can never hold a run open.
    """
    try:
        return summarize_context_usage(
            await asyncio.wait_for(client.get_context_usage(), timeout_s)
        )
    except Exception:  # noqa: BLE001 — an old CLI, an error or a timeout all mean "unmeasured"
        return summarize_context_usage(None)


_CONTEXT_KEYS = (
    "skills_tokens",
    "skills_included",
    "skills_total",
    "memory_files_tokens",
    "mcp_tokens_by_server",
    "mcp_tokens_total",
    "mcp_deferred_tokens_total",
)


def run_context_record(
    *,
    run_id: str | None,
    workspace_id: str | None,
    agent_id: str | None,
    profile: str,
    warm_hit: bool,
    session_runs: int,
    tally: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """The one per-run record the backend logs: ids, the turn figures and the context breakdown."""
    ctx = context or summarize_context_usage(None)
    return {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "profile": profile,
        "warm_hit": warm_hit,
        "session_runs": session_runs,
        **tally,
        "context_available": ctx["available"],
        "context_total_tokens": ctx["total_tokens"],
        **{k: ctx[k] for k in _CONTEXT_KEYS},
    }


class TurnTally:
    """Per-run usage tally over the MAIN thread's model turns (a subagent's turns are not counted)."""

    def __init__(self) -> None:
        self.turns = 0
        self.first_turn_cache_creation: int | None = None
        self.last_turn_input_tokens: int | None = None

    def note(self, usage: Any, *, parent_tool_use_id: str | None = None) -> None:
        if parent_tool_use_id or not isinstance(usage, dict):
            return
        cache_creation = _tok(usage.get("cache_creation_input_tokens")) or 0
        total_in = (
            (_tok(usage.get("input_tokens")) or 0)
            + cache_creation
            + (_tok(usage.get("cache_read_input_tokens")) or 0)
        )
        if self.turns == 0:
            self.first_turn_cache_creation = cache_creation
        self.turns += 1
        self.last_turn_input_tokens = total_in

    def as_dict(self) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "first_turn_cache_creation_input_tokens": self.first_turn_cache_creation,
            "last_turn_input_tokens": self.last_turn_input_tokens,
        }
