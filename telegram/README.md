# telegram/ — the cockpit

The Telegram bot: your interface to the agent and the home of the three approval gates.

> **Implementation note:** the bot **code** lives in [`../cockpit/`](../cockpit/), not here. The package is named `cockpit/` (not `telegram/`) so it does not shadow the `python-telegram-bot` library's `telegram` import. This directory holds the gate/spec docs only.

**Gates (inline Approve / Edit / Reject buttons):**
- **Gate 1 — Plan:** approve/steer the weekly calendar before production.
- **Gate P — Podcast script/voice:** approve transcript (+ optional voice sample) before TTS/render spend.
- **Gate 2 — Publish:** approve exactly what posts where/when; **gate restates the company** (profile) to prevent cross-posting.

**Rules:**
- Only `TELEGRAM_ALLOWED_CHAT_ID` may talk to or approve (whitelist; reject all others).
- `Edit` → user replies with changes → agent revises → re-posts for approval.
- Active profile always shown in the chat header.
- Map `chat_id → Agent SDK session` for persistent context.
