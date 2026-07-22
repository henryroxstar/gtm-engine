# cockpit/ — the Telegram cockpit

The Telegram bot: your interface to the agent (`../agent/`) and the home of the three
approval gates.

> **Why `cockpit/` and not `telegram/`?**
> The runtime depends on **python-telegram-bot**, whose import name is literally `telegram`
> (`from telegram import Update`, `from telegram.ext import Application`, …). A package named
> `telegram/` in this repo would **shadow** that library — `import telegram` would resolve to
> our empty package instead of the installed dependency, breaking the bot at import time.
> The bot package is therefore `cockpit/`. The legacy `../telegram/` directory holds docs only;
> see its README, which points here.

## What lives here

- `__init__.py` — package marker.
- `bot.py` — the Telegram application: command/handler wiring, the per-chat whitelist, and the
  three inline-button approval gates. Talks to the brain through `agent.session.SessionStore`
  (one Agent SDK session per `chat_id`).

## Gates (inline Approve / Edit / Reject buttons — spec §9)

- **Gate 1 — Plan:** approve/steer the weekly calendar before production.
- **Gate P — Podcast script/voice:** approve transcript (+ optional voice sample) before
  TTS/render spend.
- **Gate 2 — Publish:** approve exactly what posts where/when; the gate **restates the active
  company (profile)** to prevent cross-posting.

## Rules

- Only chat ids in `TELEGRAM_ALLOWED_CHAT_ID` (the `Config.telegram_allowed_chat_ids` whitelist)
  may talk to or approve — reject all others.
- `Edit` → user replies with changes → agent revises → re-posts for approval.
- The **active profile is always shown** in the chat header. Profile is bound **per `chat_id`**
  in the `SessionStore` — there is no global mutable active profile. `/profile <name>` rebinds
  the session for that chat only.

## Run

The bot reads its token and whitelist from the environment (Doppler-injected on the VPS) via
`agent.config.Config.from_env()`. Launch it as the long-running cockpit process; it owns the
Telegram long-poll / webhook loop and dispatches each authorized message into the per-chat
Agent SDK session.
