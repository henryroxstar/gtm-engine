"""The source-list kill switch: closed by default, one strict parse, one message.

While closed, nothing reads or writes observations and every ``signal_obs`` command exits 0 saying
so. The closed list of enabling values matches the other switches; anything else leaves it off.
"""

from __future__ import annotations

import os

SETTING = "GTM_SIGNAL_SOURCES_ENABLED"

DISABLED_MESSAGE = (
    f"Source lists are off, so this did nothing. {SETTING} is not set to a true value. "
    f"To use them, set {SETTING}=1 in the environment of the command (or of the run)."
)


class SwitchClosed(RuntimeError):
    """Raised by a library entry point that was reached while the switch is closed."""


def sources_enabled() -> bool:
    return (os.getenv(SETTING) or "").strip().lower() in {"true", "1", "yes", "on"}


def require_enabled() -> None:
    """The guard every library entry point calls first, so a caller that is not the CLI is held too."""
    if not sources_enabled():
        raise SwitchClosed(DISABLED_MESSAGE)


VIEW_SETTING = "GTM_SIGNAL_VIEW_ROUTING"

VIEW_DISABLED_MESSAGE = (
    f"Source lists are not used for choosing emails. {VIEW_SETTING} is not set to a true value. "
    f"To use them, set {VIEW_SETTING}=1 in the environment of the run."
)


def view_routing_enabled() -> bool:
    """Whether a source list may change which email a row gets. Closed unless set to a true value."""
    return (os.getenv(VIEW_SETTING) or "").strip().lower() in {"true", "1", "yes", "on"}
