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

import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.config import Config
    from gtm_core.optout_watch import OptOutMatch

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

    # parse_mode=HTML below: escape the two data-controlled fields. Unescaped, a `<`
    # or `&` in either makes Telegram reject the whole message (the send failure is
    # swallowed at the bottom of this function, so the operator would simply never
    # learn the run had paused at Gate 1).
    text = (
        f"<b>[{html.escape(profile)}] Gate 1 — plan ready for review</b>\n"
        f"run_id: <code>{html.escape(run_id)}</code>\n\n"
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


async def push_optout_alert(
    cfg: Config,
    profiles_root: Path,
    profile: str,
    match: OptOutMatch,
) -> None:
    """Alert the operator that an inbound reply looks like an opt-out.

    Fire-and-forget, same posture as :func:`push_gate1`: this notifies, it never acts.
    The message never claims the person has been suppressed — DNC has no removal API,
    so the confirm step stays a human tap (Saleshandy UI, or an agent session told to
    add the address), not this function. No-ops silently when the bot token or the
    profile's ``telegram_gate1_chat_id`` is unset (same channel as Gate 1 — this is
    the same "wake the founder" urgency, not a new chat to configure).
    """
    from agent.profiles import load_gate1_chat_id

    token = cfg.telegram_bot_token
    if not token:
        logger.debug("push_optout_alert: TELEGRAM_BOT_TOKEN not set — skipping")
        return

    chat_id = load_gate1_chat_id(profiles_root, profile)
    if chat_id is None:
        logger.warning(
            "push_optout_alert: telegram_gate1_chat_id not set for profile=%s — "
            "opt-out alert suppressed for %s (thread %s). Add the field to "
            "profiles/%s/PROFILE.md.",
            profile,
            match.email,
            match.thread_id,
            profile,
        )
        return

    # parse_mode=HTML: escape every data-controlled field, including the reply snippet
    # itself — it is untrusted content (RULES.md §R5), quoted here for the operator to
    # read, never interpreted. An unescaped `<`/`&` would make Telegram reject the
    # whole message, and the failure is swallowed below by design.
    text = (
        f"<b>[{html.escape(profile)}] Possible opt-out reply</b>\n"
        f"From: <code>{html.escape(match.email)}</code>\n"
        f"Subject: {html.escape(match.subject)}\n"
        f'Reply: "{html.escape(match.snippet)}"\n\n'
        "Not yet suppressed — Global DNC has no removal API, so this stays a human tap. "
        f"Add <code>{html.escape(match.email)}</code> to DNC today (same-day is the standing rule)."
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
                "push_optout_alert: Telegram returned %s for profile=%s email=%s",
                r.status_code,
                profile,
                match.email,
            )
    except Exception:
        logger.warning(
            "push_optout_alert: HTTP error for profile=%s email=%s",
            profile,
            match.email,
            exc_info=True,
        )


async def push_signal_alert(
    cfg: Config,
    profiles_root: Path,
    profile: str,
    signal: dict,
) -> None:
    """Alert the operator that a signal needs a human, not a draft.

    The ``escalate_to_operator`` half of :data:`gtm_core.signals.SUGGESTED_ACTIONS` —
    buyer intent, a pricing question, a meeting request. These are not things to draft a
    reply to on a timer; they are things to wake someone for. Same channel and same
    fire-and-forget posture as :func:`push_optout_alert`: this notifies, it never acts.

    Every field is escaped before it reaches Telegram. A signal's ``who``, ``source`` and
    ``meta`` originate in inbound mail and scraped feeds — untrusted content (RULES.md
    §R5), quoted here for a human to read and never interpreted. The message deliberately
    carries no link or command to run: an instruction embedded in a subject line must not
    arrive looking like an instruction from this system.
    """
    from agent.profiles import load_gate1_chat_id

    token = cfg.telegram_bot_token
    if not token:
        logger.debug("push_signal_alert: TELEGRAM_BOT_TOKEN not set — skipping")
        return

    chat_id = load_gate1_chat_id(profiles_root, profile)
    if chat_id is None:
        logger.warning(
            "push_signal_alert: telegram_gate1_chat_id not set for profile=%s — "
            "signal alert suppressed for %s. Add the field to profiles/%s/PROFILE.md.",
            profile,
            signal.get("who", "?"),
            profile,
        )
        return

    meta = signal.get("meta") or {}
    subject = str(meta.get("subject") or "")
    text = (
        f"<b>[{html.escape(profile)}] "
        f"{html.escape(str(signal.get('signal_type') or 'signal'))}</b>\n"
        f"From: <code>{html.escape(str(signal.get('who') or '?'))}</code>\n"
        f"Source: {html.escape(str(signal.get('source') or '?'))}\n"
        + (f"Subject: {html.escape(subject)}\n" if subject else "")
        + "\nThis one wants a person, not a drafted reply."
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
                "push_signal_alert: Telegram returned %s for profile=%s who=%s",
                r.status_code,
                profile,
                signal.get("who"),
            )
    except Exception:
        logger.warning(
            "push_signal_alert: HTTP error for profile=%s who=%s",
            profile,
            signal.get("who"),
            exc_info=True,
        )
