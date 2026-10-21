"""MM8: the typed first-run error, and which MCP tools may answer without a profile.

A developer who has bought access and pointed their IDE at the MCP endpoint has a workspace and a key but
no ``profiles`` row yet — onboarding happens in the portal. Every profile-scoped tool used to answer
``Profile 'x' is not bound to caller workspace`` (it reads like a typo) and ``describe`` answered
``{"packs": []}`` (it reads like "nothing available"). :class:`SetupRequiredError` says what is true and
what to do, with a stable leading ``setup_required:`` code an IDE or a script can match on.

It is a ``ValueError`` on purpose: every existing handler keeps working and the IDE shows an error. It is
raised ONLY when the workspace has no profile at all; a workspace that has one and names a different one
keeps the generic refusal, and a workspace that finished onboarding never sees this.

The portal link comes from a setting (``SETUP_URL``), never from the code. With no usable setting the text
names no link and a warning is logged — a link nobody configured is not a link to invent.

``SETUP_AWARE`` and ``PROFILE_FREE`` are the closed classification of every tool the server registers
(``tests/mcp_server/test_setup_required.py`` enumerates the live registry against their union): a new tool
fails that test until someone decides which it is.
"""

from __future__ import annotations

import logging
import os

SETUP_REQUIRED = "setup_required"
SETUP_URL_ENV = "SETUP_URL"

#: Tools that need a profile and so answer a workspace with none with the typed error.
SETUP_AWARE = frozenset(
    {"radar_check", "profile_context", "draft_post", "draft_outreach", "describe", "dispatch"}
)

#: Tools that work on a run or on the workspace's own database rows and need no profile: an empty answer
#: from them is accurate, not a symptom of missing setup.
PROFILE_FREE = frozenset(
    {
        "status",
        "artifacts",
        "read_artifact",
        "cost",
        "get_prospect_account",
        "list_prospect_accounts",
        "check_suppression",
    }
)

_log = logging.getLogger(__name__)


def _setup_url() -> str | None:
    """The configured portal link, only if it is a plain http(s) URL — it is shown to a developer to click."""
    raw = (os.getenv(SETUP_URL_ENV) or "").strip()
    if raw.lower().startswith(("https://", "http://")) and not any(c.isspace() for c in raw):
        return raw
    return None


def setup_message() -> str:
    url = _setup_url()
    if url is None:
        _log.warning(
            "%s is unset or not an http(s) link: the %s error names no portal link",
            SETUP_URL_ENV,
            SETUP_REQUIRED,
        )
        where = "in the portal"
    else:
        where = f"at {url}"
    return (
        f"{SETUP_REQUIRED}: this workspace has not finished setup, so it has no profile yet. "
        f"Finish setup {where}, then retry."
    )


class SetupRequiredError(ValueError):
    """The workspace has no profile at all. ``code`` is the stable machine-readable half."""

    code = SETUP_REQUIRED

    def __init__(self) -> None:
        super().__init__(setup_message())
