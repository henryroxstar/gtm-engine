"""The Saleshandy worker MCP server (FastMCP, stdio).

A thin wrapper over the Saleshandy REST API for the ``email-sequence`` skill: it
lets the brain **stage** a cold-email sequence — create the sequence, add steps and
A/B variants, define a sending schedule, attach sending mailboxes, and enroll
leads/prospects into a step — plus read back the account's mailboxes, sequences, and
per-sequence stats.

CRITICAL SECURITY INVARIANT — this wrapper makes it **structurally impossible for the
brain to cause Saleshandy to send email.** It is the email analogue of the publish
gate: *build is a capability, send is not — and send is not even representable in this
tool surface.* In Saleshandy, actual sending is triggered by **resuming/activating** a
sequence; that capability simply does not exist here. There is deliberately NO tool
that activates, resumes, starts, launches, pauses, or otherwise changes a sequence's
status (no ``update_sequence_status``, no "resume", no "activate"), and NO
delete/revoke/destructive tool. A sequence built through this wrapper is **inert**: it
cannot send until a human deliberately resumes it in the Saleshandy UI. Staging is
safe; activation is deliberately unrepresentable.

Design doc: ``docs/prds/2026-07-13-email-sequence.md``.

Boundary (§R6 — all external I/O via MCP): the brain passes parameters in and gets
structured data back; it never sees ``SALESHANDY_API_KEY`` and never makes the HTTP
call. The key is read from the process env (Doppler-injected at spawn), never on the
command line, never echoed into a tool result, a log, or an error message.

No cost metering: Saleshandy is a flat subscription with no per-call finite unit, so —
unlike the RocketReach worker — this server writes NO cost ledger record. The
``GTM_CONTENT_ROOT`` / ``GTM_PROFILES_ROOT`` / ``GTM_PROFILE`` env vars are accepted for
spawn parity and otherwise ignored.

Robustness contract: every tool returns a string. On any failure (no key, HTTP error,
malformed body) it returns a ``[saleshandy-error] …`` string rather than raising — so
the brain gets a tool result it can react to, and the SDK ↔ MCP connection never breaks.

Run it with::

    python -m agent.mcp.saleshandy --transport stdio
"""

from __future__ import annotations

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

# --- Saleshandy wiring -------------------------------------------------------- #
# Base URL confirmed against the official docs (developer.saleshandy.com):
#   "All API requests should be made to: https://open-api.saleshandy.com/v1"
# Host ``open-api.saleshandy.com``, version prefix ``/v1``. Overridable for tests.
# Auth is the ``x-api-key`` header (docs: "Every API request must include your API
# key in the `x-api-key` header"). No secret in this module — the key is read at
# call time from the process env and never logged or returned.
SALESHANDY_BASE_URL = os.getenv("SALESHANDY_BASE_URL", "https://open-api.saleshandy.com/v1").rstrip(
    "/"
)
_API_KEY_ENV = "SALESHANDY_API_KEY"
_HTTP_TIMEOUT_S = 30.0

mcp = FastMCP("saleshandy")


def _headers(key: str) -> dict[str, str]:
    return {"x-api-key": key, "Content-Type": "application/json", "Accept": "application/json"}


def _compact(body: dict) -> dict:
    """Drop keys whose value is ``None`` or an empty string/list/dict.

    Keeps ``0`` and ``False`` (valid filter/flag values). Used for optional request
    fields so the brain can omit them by passing the default without sending blanks.
    """
    return {k: v for k, v in body.items() if v is not None and v != "" and v != [] and v != {}}


# Saleshandy's step ``type`` is an INTEGER channel code, not the channel name (the
# hosted connector accepts names and translates them; the raw REST API does not).
# Confirmed against the Swagger spec + live API (Email=1, payload {subject, content}).
_STEP_TYPE_CODES: dict[str, int] = {
    "Email": 1,
    "LinkedInConnectionRequest": 2,
    "LinkedInMessage": 3,
    "LinkedInInMail": 4,
    "LinkedInViewProfile": 5,
    "LinkedInPostInteraction": 6,
    "Custom": 9,
    "CallIntroduction": 11,
    "CallDemo": 12,
    "CallFollowUp": 13,
    "CallReminder": 14,
    "CallOther": 15,
    "WhatsappMessage": 16,
    "WhatsappVoiceMessage": 17,
    "WhatsappVoiceCall": 18,
}


def _step_type_code(step_type: str) -> int | None:
    """Map a friendly channel name (or a numeric string) to Saleshandy's integer code.

    Returns ``None`` if the name is unknown. Accepts a raw integer string too ("1").
    """
    s = step_type.strip()
    if s in _STEP_TYPE_CODES:
        return _STEP_TYPE_CODES[s]
    if s.isdigit() and int(s) in _STEP_TYPE_CODES.values():
        return int(s)
    return None


async def _call(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
) -> str:
    """Make one Saleshandy REST call and return its JSON body as a string.

    Never raises: a missing key, HTTP error, or non-JSON body maps to a
    ``[saleshandy-error] …`` string. The API key is never echoed — errors carry only
    the HTTP status code or the exception type, never response bodies or headers.
    """
    key = os.environ.get(_API_KEY_ENV)
    if not key:
        return f"[saleshandy-error] {_API_KEY_ENV} is not set — wrapper cannot reach Saleshandy."
    url = f"{SALESHANDY_BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.request(
                method, url, params=params, json=json_body, headers=_headers(key)
            )
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPStatusError as exc:
        return f"[saleshandy-error] HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        return f"[saleshandy-error] request failed: {type(exc).__name__}"
    except ValueError:
        return "[saleshandy-error] non-JSON response"
    return json.dumps(body, ensure_ascii=False)


# --- Read tools --------------------------------------------------------------- #


@mcp.tool()
async def list_email_accounts(
    search: str = "",
    status: int | None = None,
    page: int = 1,
    page_size: int = 25,
    sort_by_key: str = "",
    sort: str = "",
) -> str:
    """List the workspace's sending mailboxes (read-only) — also the auth smoke test.

    Call this first to discover email-account IDs (the hashed ``id`` on each row) for
    ``create_sequence`` / ``add_email_accounts_to_sequence``. Because it is the
    cheapest read, it doubles as the "is the API key valid?" check.

    Args:
        search: Filter by email address, first name, or last name.
        status: Filter by status — 0=Disconnected, 1=Active, 2=In-progress, 3=Paused.
        page: 1-based page number.
        page_size: Items per page (max 100).
        sort_by_key: One of "created-date", "health-score", "remaining-quota",
            "clientFirstName".
        sort: "ASC" or "DESC".

    Returns a JSON string ``{message, payload:{emails:[…], meta:{…}, …}}``; each email
    row includes ``id`` (use it as an account ID), ``fromEmail``, ``status``, and
    ``healthScore``. On failure returns a ``[saleshandy-error] …`` string.
    """
    # VERIFY: docs describe this list/read as POST /v1/email-accounts (a POST verb for
    # a read is unusual); confirm GET-vs-POST live before relying on it.
    body = _compact(
        {
            "search": search,
            "status": status,
            "page": page,
            "pageSize": page_size,
            "sortByKey": sort_by_key,
            "sort": sort,
        }
    )
    return await _call("POST", "/email-accounts", json_body=body)


@mcp.tool()
async def list_sequences(
    sequence_name: str = "",
    page: int = 1,
    page_size: int = 100,
    sort: str = "",
    sort_by: str = "",
) -> str:
    """List the workspace's sequences (read-only) and their steps.

    Use this to discover sequence IDs (the hashed ``id`` on each row) for the staging
    tools and for ``get_sequence_stats``.

    Args:
        sequence_name: Filter by title (partial match).
        page: 1-based page number.
        page_size: Items per page (max 1000).
        sort: "ASC" or "DESC".
        sort_by: "sequence.createdAt" or "sequence.title".

    Returns a JSON string ``{message, payload:[{id, title, active, steps:[…]}, …]}``.
    On failure returns a ``[saleshandy-error] …`` string.
    """
    params = _compact(
        {
            "sequenceName": sequence_name,
            "page": page,
            "pageSize": page_size,
            "sort": sort,
            "sortBy": sort_by,
        }
    )
    return await _call("GET", "/sequences", params=params)


@mcp.tool()
async def get_sequence_stats(sequence_id: str) -> str:
    """Get prospect + email statistics for ONE sequence (read-only).

    Args:
        sequence_id: The hashed sequence ID (from ``list_sequences``).

    Returns a JSON string ``{message, payload:{sequenceId, sequenceName, prospects:[…],
    emails:{status:{…}}, …}}``. On failure returns a ``[saleshandy-error] …`` string.
    """
    if not sequence_id.strip():
        return "[saleshandy-error] sequence_id is required."
    # VERIFY: analytics stats endpoint taken from the docs as POST /v1/analytics/stats
    # with body {sequenceId}; confirm exact path + body shape live.
    return await _call("POST", "/analytics/stats", json_body={"sequenceId": sequence_id.strip()})


@mcp.tool()
async def get_sequence_settings(sequence_id: str, code: int | None = None) -> str:
    """Read a sequence's configuration (read-only).

    Covers CC/BCC, open/click tracking, unsubscribe link/text and the one-click
    List-Unsubscribe header, text-only mode, ESP-matching, the assigned schedule, and
    the priority distribution.

    Args:
        sequence_id: The hashed sequence ID.
        code: Optional setting code (1-13) to fetch just one setting. Codes:
            1 unsubscribe-link (HTML, must contain ``{{link}}``), 2 unsubscribe-text,
            3 mark-as-finished, 4 track-link-clicks, 5 track-email-opens,
            6 email-risky-prospects, 7 bcc (JSON array string), 8 cc (JSON array
            string), 9 text-only-email, 10 show-text-only-option, 11 esp-matching,
            12 first-step-text-only-email, 13 unsubscribe-via-email-header.

    Returns a JSON string, or a ``[saleshandy-error] …`` string.
    """
    if not sequence_id.strip():
        return "[saleshandy-error] sequence_id is required."
    params = {"code": code} if code is not None else None
    return await _call("GET", f"/sequences/{sequence_id.strip()}/settings", params=params)


@mcp.tool()
async def update_sequence_settings(
    sequence_id: str,
    settings: list[dict],
    schedule_id: str = "",
) -> str:
    """Update a sequence's configuration (staging — does NOT send).

    Set CC / BCC, toggle open/click tracking, set an unsubscribe link/text or enable
    the one-click List-Unsubscribe header, switch text-only mode, or assign a schedule.
    **None of these settings send email** — sending stays gated on a human resuming the
    sequence in the Saleshandy UI.

    Args:
        sequence_id: The hashed sequence ID.
        settings: A list of ``{code, value}`` entries (see ``get_sequence_settings`` for
            codes). ALL values are STRINGS: ``"0"``/``"1"`` for toggles; a JSON-encoded
            ARRAY STRING for cc (code 8) and bcc (code 7), e.g. ``'["a@x.com"]'``; an
            HTML string containing ``{{link}}`` for unsubscribe-link (code 1).
        schedule_id: Optional hashed schedule ID to assign (top-level, not a code).

    Recipes: set CC → ``[{"code": 8, "value": '["you@work.com"]'}]``; BCC a CRM logging
    address → ``[{"code": 7, "value": '["12345@bcc.hubspot.com"]'}]``; enable one-click
    unsubscribe → ``[{"code": 13, "value": "1"}]``.

    NOTE: CC/BCC values are per-operator PII (a work email, a CRM address) — pass them at
    call time; never hardcode them into committed config.

    Returns a JSON string, or a ``[saleshandy-error] …`` string.
    """
    if not sequence_id.strip():
        return "[saleshandy-error] sequence_id is required."
    has_settings = isinstance(settings, list) and len(settings) > 0
    if not has_settings and not schedule_id.strip():
        return "[saleshandy-error] provide a non-empty `settings` list and/or a schedule_id."
    body: dict = {}
    if has_settings:
        body["settings"] = settings
    if schedule_id.strip():
        body["scheduleId"] = schedule_id.strip()
    return await _call("PATCH", f"/sequences/{sequence_id.strip()}/settings", json_body=body)


# --- Staging tools (build only — a built sequence is inert until a human resumes it) - #


@mcp.tool()
async def create_sequence(
    title: str,
    email_account_ids: list[str] | None = None,
    schedule_id: str = "",
) -> str:
    """Create a new, INERT email sequence (staging — it cannot send).

    The created sequence is paused by construction; nothing goes out until a human
    resumes it in the Saleshandy UI (not possible from this wrapper by design). You may
    optionally attach sending mailboxes and a schedule at creation time.

    Args:
        title: Sequence title (1–255 chars).
        email_account_ids: Optional hashed email-account IDs to attach as senders
            (from ``list_email_accounts``).
        schedule_id: Optional hashed schedule ID to assign (from ``create_schedule``).

    Returns a JSON string ``{message, payload:{sequenceId, title, …}}`` — use
    ``sequenceId`` with the other staging tools. On failure returns a
    ``[saleshandy-error] …`` string.
    """
    if not title.strip():
        return "[saleshandy-error] title is required (1-255 chars)."
    body = _compact(
        {
            "title": title.strip(),
            "emailAccountIds": email_account_ids,
            "scheduleId": schedule_id,
        }
    )
    return await _call("POST", "/sequences", json_body=body)


@mcp.tool()
async def add_sequence_step(
    sequence_id: str,
    step_type: str,
    absolute_days: int,
    variants: list[dict],
    priority: str = "",
    assignee_id: str = "",
) -> str:
    """Add a step to a sequence (staging — does not send).

    Each step defines an action taken on a given day of the sequence. Sending stays
    gated on a human resuming the sequence.

    Args:
        sequence_id: The hashed sequence ID.
        step_type: One of "Email", "LinkedInConnectionRequest", "LinkedInMessage",
            "LinkedInInMail", "LinkedInViewProfile", "LinkedInPostInteraction",
            "Custom", "CallIntroduction", "CallDemo", "CallFollowUp", "CallReminder",
            "CallOther", "WhatsappMessage", "WhatsappVoiceMessage", "WhatsappVoiceCall".
        absolute_days: Day number in the sequence when this step runs (1–999).
        step_type: A friendly channel name (mapped to Saleshandy's integer code
            internally) — "Email", "LinkedInMessage", "CallFollowUp", etc.
        variants: Array with (usually) one variant object. An Email variant's
            ``payload`` carries ``{subject, content}`` — ``content`` is the HTML body
            (NOT ``body``); optional ``preheader``. For Call / LinkedInViewProfile /
            LinkedInPostInteraction use an empty ``payload`` of ``{}`` and put any note
            in a top-level ``taskNote`` on the variant (never inside ``payload``).
            ``payload`` is passed through verbatim.
        priority: Optional task priority — "Urgent", "High", "Normal", "Low".
        assignee_id: Optional user ID to assign non-email tasks to.

    Returns a JSON string with the created step, or a ``[saleshandy-error] …`` string.
    """
    if not sequence_id.strip():
        return "[saleshandy-error] sequence_id is required."
    if not step_type.strip():
        return "[saleshandy-error] step_type is required."
    if not isinstance(variants, list) or not variants:
        return "[saleshandy-error] `variants` must be a non-empty list of variant objects."
    type_code = _step_type_code(step_type)
    if type_code is None:
        return f"[saleshandy-error] unknown step_type {step_type!r}; use one of: {', '.join(_STEP_TYPE_CODES)}."
    body = {"type": type_code, "absoluteDays": absolute_days, "variants": variants}
    body.update(_compact({"priority": priority, "assigneeId": assignee_id}))
    return await _call("POST", f"/sequences/{sequence_id.strip()}/steps", json_body=body)


@mcp.tool()
async def add_step_variant(
    sequence_id: str,
    step_id: str,
    step_type: str,
    payload: dict,
    attachment_ids: list[str] | None = None,
    task_note: str = "",
    absolute_days: int | None = None,
    assignee_id: str = "",
    priority: str = "",
) -> str:
    """Add an A/B variant to an existing sequence step (staging — does not send).

    The variant's channel (``step_type``) must match the parent step. Max 26 variants
    per step.

    Args:
        sequence_id: The hashed sequence ID.
        step_id: The hashed step ID (from ``list_sequences`` / the step's step list).
        step_type: Channel — must match the parent step (same values as
            ``add_sequence_step``).
        payload: Per-channel content, passed through verbatim. Email: ``{subject,
            content}`` (``content`` = HTML body, optional ``preheader``). For Call /
            LinkedInViewProfile / LinkedInPostInteraction use ``{}``.
        attachment_ids: Optional hashed attachment IDs (Email variants only).
        task_note: Optional note for non-Email variants (≤3000 chars).
        absolute_days: Optional override of the step's day number (1–999).
        assignee_id: Optional override of the step assignee.
        priority: Optional "Urgent", "High", "Normal", or "Low".

    Returns a JSON string with the new variant ID, or a ``[saleshandy-error] …`` string.
    """
    if not sequence_id.strip() or not step_id.strip():
        return "[saleshandy-error] sequence_id and step_id are required."
    if not step_type.strip():
        return "[saleshandy-error] step_type is required."
    type_code = _step_type_code(step_type)
    if type_code is None:
        return f"[saleshandy-error] unknown step_type {step_type!r}; use one of: {', '.join(_STEP_TYPE_CODES)}."
    body: dict = {"type": type_code, "payload": payload if payload is not None else {}}
    body.update(
        _compact(
            {
                "attachmentIds": attachment_ids,
                "taskNote": task_note,
                "absoluteDays": absolute_days,
                "assigneeId": assignee_id,
                "priority": priority,
            }
        )
    )
    return await _call(
        "POST",
        f"/sequences/{sequence_id.strip()}/steps/{step_id.strip()}/variants",
        json_body=body,
    )


@mcp.tool()
async def create_schedule(
    name: str,
    timezone: str,
    time_slots: list[dict],
    is_default: bool = False,
) -> str:
    """Create a reusable sending schedule (staging — a schedule alone sends nothing).

    Args:
        name: Human-readable label (e.g. "Weekdays 9-5 EST").
        timezone: IANA timezone identifier (e.g. "America/New_York", "Asia/Kolkata",
            "UTC").
        time_slots: Exactly 7 entries, one per day-of-week (0=Sunday … 6=Saturday).
            Each entry is ``{day, slots}`` where ``slots`` is a list of active windows,
            each ``{start:{hour,minute}, end:{hour,minute}}``; use ``slots: []`` for
            inactive days. Passed through verbatim.
        is_default: If true, mark this as the account's default schedule.

    Returns a JSON string ``{message, payload:{id, name, timezone, …}}`` — use ``id``
    as ``schedule_id`` for ``create_sequence``. On failure returns a
    ``[saleshandy-error] …`` string.
    """
    if not name.strip():
        return "[saleshandy-error] name is required."
    if not timezone.strip():
        return "[saleshandy-error] timezone is required (IANA identifier)."
    if not isinstance(time_slots, list) or len(time_slots) != 7:
        return "[saleshandy-error] time_slots must be exactly 7 entries (one per day-of-week)."
    body: dict = {"name": name.strip(), "timezone": timezone.strip(), "timeSlots": time_slots}
    if is_default:
        body["isDefault"] = True
    return await _call("POST", "/schedules", json_body=body)


@mcp.tool()
async def add_email_accounts_to_sequence(sequence_id: str, email_account_ids: list[str]) -> str:
    """Attach sending mailboxes to a sequence (staging — attaching sends nothing).

    Args:
        sequence_id: The hashed sequence ID.
        email_account_ids: Hashed email-account IDs to attach (from
            ``list_email_accounts``). Must be active and not already attached.

    Returns a JSON string with the API's confirmation message, or a
    ``[saleshandy-error] …`` string.
    """
    if not sequence_id.strip():
        return "[saleshandy-error] sequence_id is required."
    if not isinstance(email_account_ids, list) or not email_account_ids:
        return "[saleshandy-error] `email_account_ids` must be a non-empty list of account IDs."
    return await _call(
        "POST",
        f"/sequences/{sequence_id.strip()}/email-accounts/add",
        json_body={"emailAccountIds": email_account_ids},
    )


@mcp.tool()
async def add_leads_to_sequence(
    lead_ids: list[int],
    sequence_id: str,
    step_id: str,
    tag_ids: list[str] | None = None,
    new_tags: list[str] | None = None,
) -> str:
    """Enroll Saleshandy Lead Finder leads into a sequence step (staging — does not send).

    Enrolling leads into a sequence does not itself send anything: the sequence must
    still be resumed by a human before any email goes out. Use ``import_prospects_to_
    sequence`` instead when you are enrolling raw email prospects (not Lead Finder
    lead IDs).

    Args:
        lead_ids: Saleshandy Lead Finder lead IDs (numeric). Max 10000 per call.
        sequence_id: The hashed destination sequence ID (from ``list_sequences``).
        step_id: The hashed step ID to enroll the leads into (from the sequence's
            step list).
        tag_ids: Optional hashed IDs of existing tags to assign.
        new_tags: Optional names of new tags to create and assign.

    Returns a JSON string with the enrollment result, or a ``[saleshandy-error] …``
    string.
    """
    if not sequence_id.strip() or not step_id.strip():
        return "[saleshandy-error] sequence_id and step_id are required."
    if not isinstance(lead_ids, list) or not lead_ids:
        return "[saleshandy-error] `lead_ids` must be a non-empty list of Lead Finder lead IDs."
    body = _compact(
        {
            "leadIds": lead_ids,
            "sequenceId": sequence_id.strip(),
            "stepId": step_id.strip(),
            "tagIds": tag_ids,
            "newTags": new_tags,
        }
    )
    # VERIFY: this Lead Finder path (POST /v1/leads/bulk-actions/add-to-sequence) comes
    # from the hosted connector's description; it is not in the public prospects REST
    # docs. Confirm the path + body live before relying on it.
    return await _call("POST", "/leads/bulk-actions/add-to-sequence", json_body=body)


@mcp.tool()
async def import_prospects_to_sequence(
    prospect_list: list[dict],
    step_id: str = "",
    verify_prospects: bool = False,
    conflict_action: str = "",
) -> str:
    """Import raw email prospects and enroll them at a sequence step (staging — no send).

    The prospect-import counterpart to ``add_leads_to_sequence`` (which needs Lead
    Finder lead IDs): use this to enroll prospects you already have as email records.
    Importing does not send — the sequence stays gated on a human resume.

    Args:
        prospect_list: Prospect objects (e.g. ``{"email": …, "firstName": …,
            "lastName": …, "company": …}``), passed through verbatim.
        step_id: Optional step to enroll the imported prospects into.
        verify_prospects: If true, run Saleshandy email verification on import.
        conflict_action: How to handle prospects that already exist (per the API's
            conflict-action values).

    Returns a JSON string with the import request result (typically a request ID to
    poll), or a ``[saleshandy-error] …`` string.
    """
    if not isinstance(prospect_list, list) or not prospect_list:
        return "[saleshandy-error] `prospect_list` must be a non-empty list of prospect objects."
    body: dict = {"prospectList": prospect_list}
    body.update(_compact({"stepId": step_id, "conflictAction": conflict_action}))
    if verify_prospects:
        body["verifyProspects"] = True
    # VERIFY: POST /v1/prospects/import — confirm stepId semantics (hashed ID vs numeric
    # step position) and the exact prospect object schema live.
    return await _call("POST", "/prospects/import", json_body=body)
