"""Optional Calendly meeting read-back via polling (PRD §3.1, §3.5).

Off by default. When a single-tenant operator sets ``CALENDLY_PAT`` (a Calendly Personal
Access Token, Doppler-injected), this polls Calendly's **list scheduled events** API and
appends a ``meeting`` outcome to ``content/<profile>/outcomes.jsonl`` for each new booking
— filling the ``MEETING_OUTCOMES`` bucket the learning loop reads.

Why polling, not a webhook (the decided transport):
  * **Pure outbound.** We only ever *call out* to Calendly's API — no inbound route, no
    new listener, nothing exposed (§R6). A webhook would add an inbound surface; polling
    does not. Self-host-portable for the same reason.
  * **Read-only.** This never books, cancels, or modifies anything. The token is used only
    to *read* events. There is no send/book path here at all.
  * **Deduped.** A booking already recorded (by its Calendly event uri as the outcome
    ``ref``) is never appended twice, so re-polling the same window is safe.

The ``CALENDLY_PAT`` is a secret: read from the process env at call time, sent only in the
``Authorization: Bearer`` header, never echoed into chat, logs, ledgers, or an outcome row.

Pure stdlib + a lazily-imported ``httpx`` (only inside the default transport), so the
module imports and unit-tests run without httpx present.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .outcomes import append_outcome, outcomes_path, read_outcomes

# VERIFY: Calendly API v2 base + list-events path taken from the public docs
# (GET https://api.calendly.com/scheduled_events?user=<uri>&min_start_time=<iso>).
# Confirm the exact param names + the token's user uri live before relying on it.
CALENDLY_BASE_URL = os.getenv("CALENDLY_BASE_URL", "https://api.calendly.com").rstrip("/")
_TIMEOUT_S = 20.0

# Transport: (url, headers, params, timeout) -> (status_code, body). Injectable for tests.
Transport = Callable[..., Awaitable[tuple[int, object]]]


async def _httpx_transport(
    url: str, *, headers: dict, params: dict, timeout: float
) -> tuple[int, object]:
    import httpx  # lazy — keeps the module + unit tests import-light

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        resp = await client.get(url, headers=headers, params=params)
        try:
            body: object = resp.json()
        except Exception:  # noqa: BLE001
            body = {"raw": resp.text[:500]}
        return resp.status_code, body


@dataclass(frozen=True)
class CalendlySettings:
    """Immutable Calendly read-back config. ``enabled`` iff a PAT is present."""

    pat: str | None = None
    user_uri: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.pat)

    @classmethod
    def from_env(cls) -> CalendlySettings:
        return cls(
            pat=os.getenv("CALENDLY_PAT") or None,
            user_uri=(os.getenv("CALENDLY_USER_URI") or "").strip() or None,
        )


def event_to_outcome(event: dict) -> dict | None:
    """Map one Calendly scheduled-event object to a ``meeting`` outcome record.

    The event uri is the dedup ``ref``. Any UTM params carried on the single-use booking
    link surface in the event's tracking block and are copied into ``meta`` for
    attribution — no lookup needed. Returns ``None`` for a canceled event (logged as a
    cancel signal instead) or a shapeless row.
    """
    uri = event.get("uri")
    if not isinstance(uri, str) or not uri:
        return None
    status = event.get("status")
    outcome = "meeting" if status != "canceled" else "meeting_canceled"
    tracking = event.get("tracking") if isinstance(event.get("tracking"), dict) else {}
    return {
        "channel": "calendly",
        "outcome": outcome,
        "ref": uri,
        "meta": {
            "name": event.get("name"),
            "start_time": event.get("start_time"),
            "status": status,
            "utm_campaign": tracking.get("utm_campaign"),
            "utm_source": tracking.get("utm_source"),
        },
    }


def _already_recorded_refs(content_root: Path, profile: str) -> set[str]:
    """Calendly event uris already in outcomes.jsonl (dedup across polls)."""
    refs: set[str] = set()
    for row in read_outcomes(content_root, profile):
        if row.get("channel") == "calendly" and isinstance(row.get("ref"), str):
            refs.add(row["ref"])
    return refs


async def poll(
    content_root: Path,
    profile: str,
    *,
    since: str,
    settings: CalendlySettings | None = None,
    transport: Transport = _httpx_transport,
) -> int:
    """Poll Calendly for events since ``since`` (ISO ts) and append new meeting outcomes.

    Returns the number of new outcomes appended. A no-op (returns 0) when disabled
    (no ``CALENDLY_PAT``) so a cron can call it unconditionally. Never raises on an API
    error — it logs nothing sensitive and returns 0.
    """
    s = settings or CalendlySettings.from_env()
    if not s.enabled:
        return 0

    headers = {"Authorization": f"Bearer {s.pat}", "Content-Type": "application/json"}
    params: dict = {"min_start_time": since, "count": 100}
    if s.user_uri:
        params["user"] = s.user_uri

    try:
        status_code, body = await transport(
            f"{CALENDLY_BASE_URL}/scheduled_events",
            headers=headers,
            params=params,
            timeout=_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001 — a poll failure must never break the caller
        return 0
    if not (200 <= status_code < 300) or not isinstance(body, dict):
        return 0

    events = body.get("collection")
    if not isinstance(events, list):
        return 0

    seen = _already_recorded_refs(content_root, profile)
    appended = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        record = event_to_outcome(ev)
        if record is None or record["ref"] in seen:
            continue
        append_outcome(content_root, profile, record)
        seen.add(record["ref"])
        appended += 1
    return appended


def outcomes_file(content_root: Path, profile: str) -> Path:
    """Where meeting outcomes land — the same ledger the reply path writes to."""
    return outcomes_path(content_root, profile)
