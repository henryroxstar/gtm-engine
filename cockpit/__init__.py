"""cockpit — the Telegram operator cockpit for the Content OS runtime.

This package is deliberately named ``cockpit`` (NOT ``telegram``) so it does not
shadow the ``python-telegram-bot`` library, whose import name is ``telegram``.
Importing ``from telegram.ext import ...`` from inside this package therefore
resolves to the real library, never to this package.

Public surface:
    - :func:`cockpit.bot.main` — entrypoint that builds the PTB ``Application``
      and runs long-polling.
    - :class:`cockpit.bot.Cockpit` — the handler-bearing object that wires the
      bot to :class:`agent.session.SessionStore`.

The cockpit owns NO agent state of its own. It is a thin presentation layer:
every message is routed to the per-chat session in ``SessionStore`` (which holds
the per-``chat_id`` profile binding — there is no global mutable profile).
"""

from __future__ import annotations

__all__ = ["main", "Cockpit"]


def __getattr__(name: str):
    # Lazy re-export so that merely importing the package does not pull in
    # python-telegram-bot (and thus require the dependency) until the bot is
    # actually used. Keeps `import cockpit` cheap for tooling / introspection.
    if name in {"main", "Cockpit"}:
        from cockpit import bot

        return getattr(bot, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
