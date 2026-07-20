"""agent.gate_notify — fire-and-forget Telegram push notifications from headless pipeline runs.

Used by ``agent/__main__.py`` to notify the operator when the cron pipeline
pauses at Gate 1. Unlike the interactive cockpit (which sends via the PTB
Application object), this module calls the Telegram Bot API directly via httpx
so it can be imported from headless contexts that have no running event loop or
PTB Application instance.

``httpx`` is imported lazily inside ``push_gate1`` — the same idiom
``agent/publish.py`` uses for the post-Gate-2 publish POST. It keeps the module
import-light for unit tests and satisfies the §R6 egress guard, which flags a
top-level ``import httpx`` in ``agent/**``. This is deterministic runner code
sending to the operator's own bot chat, not brain-initiated egress; do not hoist
the import back to module scope (it re-breaks the semgrep gate).

All sends are best-effort: a failure logs a warning but never re-raises so the
pipeline exit code is unaffected.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.config import Config

logger = logging.getLogger("agent.gate_notify")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT_S = 10


async def push_gate1(
    cfg: Config,
    profiles_root: Path,
    profile: str,
    run_id: str,
) -> None:
    """Send a Gate 1 notification to the profile's configured Telegram chat.

    Resolves the target chat ID from ``telegram_gate1_chat_id`` in
    ``profiles/<profile>/PROFILE.md``. No-ops silently when the field is absent,
    the bot token is unset, or the HTTP call fails.
    """
    from agent.profiles import load_gate1_chat_id

    token = cfg.telegram_bot_token
    if not token:
        logger.debug("push_gate1: TELEGRAM_BOT_TOKEN not set — skipping")
        return

    chat_id = load_gate1_chat_id(profiles_root, profile)
    if chat_id is None:
        logger.warning(
            "push_gate1: telegram_gate1_chat_id not set for profile=%s — "
            "Gate 1 notification suppressed. Add the field to profiles/%s/PROFILE.md.",
            profile,
            profile,
        )
        return

    text = (
        f"<b>[{profile}] Gate 1 — plan ready for review</b>\n"
        f"run_id: <code>{run_id}</code>\n\n"
        "Use the Telegram cockpit to Approve, Edit, or Reject.\n"
        "Reply /radar to trigger a manual scan if the plan looks thin."
    )

    import httpx  # lazy — keeps module + unit tests import-light (mirrors agent/publish.py)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.post(
                _TELEGRAM_API.format(token=token),
                data={"chat_id": str(chat_id), "text": text, "parse_mode": "HTML"},
            )
        if r.status_code != 200:
            logger.warning(
                "push_gate1: Telegram returned %s for profile=%s run_id=%s",
                r.status_code,
                profile,
                run_id,
            )
    except Exception:
        logger.warning(
            "push_gate1: HTTP error for profile=%s run_id=%s",
            profile,
            run_id,
            exc_info=True,
        )
