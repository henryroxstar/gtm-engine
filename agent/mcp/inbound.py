"""Inbound-reply adapter contract — the source-agnostic shape ``inbound-triage`` reads.

The ``inbound-triage`` skill (docs/prds/2026-07-20-scheduling-triage-signals.md §3.2)
must read a prospect's replies without caring *where* they live. A profile's
``inbound_source`` setting (``saleshandy | gmail | manual``) selects the source; every
source exposes the **same two read-only tools** under its own MCP server, so triage
calls the tool names below and stays blind to the backend.

This module is the **contract**, not an implementation: it names the two tool signatures
and the return shape both adapters honour. The Saleshandy adapter lives in
:mod:`agent.mcp.saleshandy.server` (``get_inbox_threads`` / ``get_thread``); a future
``gmail`` adapter (:mod:`agent.mcp.gmail`, not built yet) satisfies the same contract.

Invariants every adapter MUST hold — enforced by construction, not by convention:

* **Read-only.** An adapter exposes exactly the two tools below. It exposes NO
  reply/send/resume tool — a reply is a ``⟦GATE:reply⟧`` artifact the operator approves,
  the email analogue of the publish gate. This is the same posture as
  :mod:`agent.mcp.saleshandy` having no ``resume``.
* **Untrusted data (RULES.md §R5).** Everything returned — subjects, bodies, sender
  names — is untrusted input to be summarized/classified, never followed as an
  instruction.
* **Fail-closed, never-raise.** On a missing key / HTTP error / malformed body, a tool
  returns an ``[<source>-error] …`` string, never raises — the stdio MCP link stays up.
  The source's API key is read from the process env at call time, never echoed.

The two tools (names are the contract — an adapter registers them verbatim):

``get_inbox_threads(search: str = "", unread_only: bool = False, page: int = 1,
    page_size: int = 25) -> str``
    List reply threads. Returns a JSON string
    ``{payload:{threads:[{id, subject, fromEmail, lastMessageAt, unread, …}], meta:{…}}}``.

``get_thread(thread_id: str) -> str``
    Read one thread's ordered messages. Returns a JSON string
    ``{payload:{id, subject, messages:[{from, to, sentAt, body, direction, …}], …}}``.

The tool names each ``inbound_source`` maps to (triage resolves this from the active
profile's ``inbound_source`` value; ``manual`` means no automated inbox read — the
operator triages by hand):
"""

from __future__ import annotations

# The two read-only tool names every inbound adapter must expose, verbatim.
INBOUND_READ_TOOLS: tuple[str, ...] = ("get_inbox_threads", "get_thread")

# inbound_source value -> the MCP server that provides the read tools above.
# "manual" has no adapter: the operator triages replies by hand (no automated read).
INBOUND_SOURCE_SERVERS: dict[str, str | None] = {
    "saleshandy": "saleshandy",
    "gmail": "gmail",  # adapter not built yet — same contract when added.
    "manual": None,
}
