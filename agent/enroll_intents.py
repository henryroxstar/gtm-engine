"""agent.enroll_intents — the enrollment dispatcher's intent-ledger half (F4).

Split out of :mod:`agent.email_dispatch` (the §R10 ratchet caps it): which recipients a draft names, what a
Saleshandy response PROVES about whether anything was sent, the claim-before-call, and the one outbound call.
:func:`agent.email_dispatch.dispatch_approved_enrollment` stays the only entry point.
"""

from __future__ import annotations

import logging
from typing import Any

from agent import inflight
from agent.permissions import email_context
from gtm_core.dispatch_intents import recipient_key

log = logging.getLogger("agent.email_dispatch")


def enrollment_recipients(draft: dict) -> list[str]:
    """The recipient keys of an approved draft: lead ids as ``id:<n>``, prospect rows by ``Email``.

    A row without a usable email raises ``ValueError`` and the caller refuses — an unidentifiable
    recipient can never be proven not to be a duplicate.
    """
    if draft.get("tool") == "add_leads_to_sequence":
        return [recipient_key(lead_id=lead) for lead in draft.get("lead_ids") or []]
    keys = []
    for row in draft.get("prospect_list") or []:
        email = next((v for k, v in row.items() if str(k).strip().casefold() == "email"), None)
        keys.append(recipient_key(email))
    return keys


def classify_provider_result(raw: str) -> str:
    """What a Saleshandy response PROVES: ``dispatched`` | ``failed`` | ``unknown``.

    Never guesses toward "failed". An explicit 4xx (except 408 Request Timeout) or a pre-send validation
    refusal proves nothing was accepted; a timeout, connection error, 5xx, 408 or an unparseable body does
    not — the request may have been processed — so it is ``unknown`` and blocks a repeat.
    """
    if not raw.startswith("[saleshandy-error]"):
        return "dispatched"
    tail = raw[len("[saleshandy-error]") :].strip()
    if tail.startswith("HTTP "):
        code = tail[5:].split()[0]
        if code.isdigit() and 400 <= int(code) < 500 and int(code) != 408:
            return "failed"
        return "unknown"
    if tail.startswith(("request failed", "non-JSON response")):
        return "unknown"
    return "failed"  # a pre-send refusal from our own wrapper (missing key, empty list, …)


async def claim_enrollment(
    store: Any, draft: dict, tool: str
) -> tuple[Any, str | None, str | None, tuple]:
    """Write the enrollment's INTENT. Returns ``(claim, refusal, code, open_batches)``; ``refusal`` is a
    complete plain-words sentence when the batch must not go."""
    try:
        claim = await store.claim(
            str(draft.get("sequence_id", "")), enrollment_recipients(draft), tool=tool
        )
    except ValueError as exc:  # a recipient with no identity cannot be proven not to be a duplicate
        return None, f"Not sent: {exc}.", "unidentifiable_recipient", ()
    except Exception:  # noqa: BLE001 — an unreadable intent ledger cannot authorise a send
        log.exception("dispatch_approved_enrollment: the dispatch-intent ledger could not be read")
        return (
            None,
            "Not sent: I couldn't read the record of sends in progress, so I can't rule out a "
            "duplicate. Check that this profile's dispatch-intents.jsonl is readable, then try again.",
            "intent_ledger_unreadable",
            (),
        )
    if not claim.ok:
        return claim, claim.message, claim.code, claim.open_batches
    return claim, None, None, ()


async def call_provider(api_key: str, tool: str, draft: dict, rows: Any) -> tuple[str, str]:
    """The ONE outbound call. Returns ``(raw_response, intent_state)``; never raises.

    The email-context flag permits the two Saleshandy enroll verbs only during this approved dispatch
    window (mirrors ``publish_context()`` for Reap publish verbs). The request functions below don't go
    through the MCP tool-call surface at all — this is belt-and-suspenders — but it keeps the two paths
    symmetric. ``inflight.track()``: the cockpit watchdog and SIGTERM both wait for this window (F7), so a
    restart can never land between the provider call and its recorded outcome.
    """
    from agent.mcp.saleshandy.server import (
        _add_leads_to_sequence_request,
        _import_prospects_to_sequence_request,
    )

    try:
        with inflight.track("email"), email_context():
            if tool == "add_leads_to_sequence":
                raw = await _add_leads_to_sequence_request(
                    api_key,
                    draft["lead_ids"],
                    draft["sequence_id"],
                    draft["step_id"],
                    draft.get("tag_ids"),
                    draft.get("new_tags"),
                )
            else:  # import_prospects_to_sequence
                raw = await _import_prospects_to_sequence_request(
                    api_key,
                    rows,
                    draft.get("step_id", ""),
                    bool(draft.get("verify_prospects", False)),
                    draft.get("conflict_action", ""),
                )
        return raw, classify_provider_result(raw)
    except Exception as exc:  # noqa: BLE001 — blew up mid-call: we cannot know whether it sent
        log.exception("dispatch_approved_enrollment: the provider call raised")
        return f"[saleshandy-error] request failed: {type(exc).__name__}", "unknown"
