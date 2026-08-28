"""Structured permission-event ledger sinks (assessment P0-1).

Every run path already had an ``on_deny`` hook on its permission callback, but the sinks
only printed to stderr — a denial was grep-able in journald and nowhere else. These
helpers turn each denied/escalated tool call (and each operator approve/deny on the
cockpit ask path) into one secret-free record in ``content/<profile>/denials.jsonl`` via
:class:`gtm_core.ledgers.Ledgers` (hash-chained like the other ledgers). The ledger is the
feedback loop that interactive Claude Code gets for free by putting a human in front of
every borderline call.

Best-effort by construction: a ledger failure must never change a permission decision or
break a run, so every write path swallows exceptions (logged at debug).

Record shape (``ts`` + ``prev_sha256`` stamped by the ledger appender)::

    {"tool": "Bash", "decision": "deny", "outcome": "denied",
     "detail": "npm run export …", "source": "pipeline"}

``detail`` is deliberately narrow: the Bash command head, the Read/Grep target path, or
the Skill name — never a full MCP payload (MCP args can carry prospect PII; the
``mcp__<server>__<leaf>`` tool name already identifies the call). Truncated to 300 chars,
matching the cockpit's blocked-tool notice.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

_MAX_DETAIL = 300

#: Outcomes a permission event can record. "denied" = the policy denied it outright;
#: "approved"/"declined"/"timeout" = the cockpit ask path consulted the operator.
OUTCOMES = ("denied", "approved", "declined", "timeout")


def denial_detail(tool_name: str, tool_input: dict | None) -> str:
    """A short, secret-free description of what was attempted (for ledgers + notices)."""
    if not isinstance(tool_input, dict):
        return ""
    if tool_name == "Bash":
        raw = str(tool_input.get("command", ""))
    elif tool_name == "Skill":
        raw = str(tool_input.get("skill") or tool_input.get("name") or "")
    elif tool_name.startswith("mcp__"):
        raw = ""  # the tool name already carries server+leaf; args may hold payload/PII
    else:
        raw = str(
            tool_input.get("file_path") or tool_input.get("path") or tool_input.get("pattern") or ""
        )
    raw = raw.strip()
    if len(raw) > _MAX_DETAIL:
        return raw[:_MAX_DETAIL] + "…"
    return raw


def record_permission_event(
    cfg,
    profile: str,
    *,
    tool_name: str,
    tool_input: dict | None,
    decision: str,
    outcome: str,
    source: str,
) -> None:
    """Append one permission event to the profile's ``denials.jsonl``; never raises."""
    try:
        from gtm_core.ledgers import Ledgers

        Ledgers(cfg, profile).append_denial(
            {
                "tool": tool_name,
                "decision": decision,
                "outcome": outcome,
                "detail": denial_detail(tool_name, tool_input),
                "source": source,
            }
        )
    except Exception:  # noqa: BLE001 — observability must never break the run
        logger.debug("denials.jsonl append failed (profile=%s)", profile, exc_info=True)


def make_denial_sink(cfg, profile: str, source: str) -> Callable[[str, dict, str], None]:
    """Build an ``on_deny`` sink for the permission callbacks → ``denials.jsonl``.

    ``source`` labels the run path ("cockpit", "pipeline", "one-shot", "backend",
    "headless-default") so a weekly review can tell a cron denial from a chat one.
    """

    def _sink(tool_name: str, tool_input: dict, decision: str) -> None:
        record_permission_event(
            cfg,
            profile,
            tool_name=tool_name,
            tool_input=tool_input,
            decision=decision,
            outcome="denied",
            source=source,
        )

    return _sink
