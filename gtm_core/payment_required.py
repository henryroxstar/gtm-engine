"""MM13: the payment-required answer, built once, the same at the origin as at the edge.

The Cloudflare Worker in front of the MCP endpoint (``deploy/mcp-gateway/src/index.ts``) refuses a caller
with no credits or no subscription from its cached entitlement, with
``{error: "payment_required", message, upgrade_url}`` — the link from its own ``BILLING_URL`` /
``PRICING_URL`` settings. When that cache is stale or absent the request reaches the origin, which used to
refuse with only "please top up your balance": true, and no way to do it. This module is where the origin
builds the SAME answer, so a developer sees the same words and the same link whichever layer said no.

``tests/contracts/test_payment_required_payload.py`` reads the Worker's source and pins this module to it
(error code, both messages, which setting feeds which link), so the two cannot drift.

The link comes from the setting and nowhere else. Unset, blank or not an http(s) URL: there is no link in the
text, no ``upgrade_url`` key in the payload (the Worker's JSON drops an undefined one too), and a warning in
the log — a link nobody configured is not a link to invent.

Stdlib only: this sits under the §R6 egress scan, and it never makes a request.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

__all__ = ["ERROR_CODE", "MESSAGES", "URL_SETTINGS", "Kind", "payload", "text", "with_link"]

Kind = Literal["credits", "subscription"]

ERROR_CODE = "payment_required"

#: The Worker's two messages, word for word.
MESSAGES: dict[str, str] = {
    "credits": "Insufficient MCP credits. Please top up your balance.",
    "subscription": "Active subscription required. Please subscribe or renew your plan.",
}

#: Which setting feeds which link — the Worker's own names. No credits → billing; no subscription → pricing.
URL_SETTINGS: dict[str, str] = {"credits": "BILLING_URL", "subscription": "PRICING_URL"}

_STATUS = "402 Payment Required:"
_log = logging.getLogger(__name__)


def _url(kind: str) -> str | None:
    setting = URL_SETTINGS[kind]
    raw = (os.getenv(setting) or "").strip()
    if raw.lower().startswith(("https://", "http://")) and not any(c.isspace() for c in raw):
        return raw
    _log.warning(
        "%s is unset or not an http(s) link: the %s answer carries no link", setting, ERROR_CODE
    )
    return None


def payload(kind: Kind) -> dict[str, str]:
    """The Worker's JSON body: ``error``, ``message`` and — only when a link is configured — ``upgrade_url``."""
    body = {"error": ERROR_CODE, "message": MESSAGES[kind]}
    if url := _url(kind):
        body["upgrade_url"] = url
    return body


def with_link(message: str, kind: Kind) -> str:
    """``message`` followed by the configured link; ``message`` unchanged when there is none."""
    url = _url(kind)
    return f"{message} {url}" if url else message


def text(kind: Kind, *, detail: str | None = None) -> str:
    """The readable form for a ``ValueError`` an MCP client shows: status, the Worker's message, an
    optional detail (the figures), then the link."""
    body = f"{_STATUS} {MESSAGES[kind]}" + (f" {detail}" if detail else "")
    return with_link(body, kind)
