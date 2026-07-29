"""cockpit.limits — Telegram streaming/message tunables shared across cockpit modules."""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

# How often (seconds) we flush accumulated streamed text into the live Telegram
# message via edit_message_text. Telegram rate-limits edits to the same message
# (~1/sec is safe); coalescing avoids "Message is not modified" / 429 storms
# while still feeling live.
_STREAM_EDIT_INTERVAL_S = 0.7

# Telegram hard caps a single message at 4096 chars. We keep headroom for the
# profile header and the typing ellipsis we append while streaming.
_TELEGRAM_MSG_LIMIT = 4096
_STREAM_SOFT_LIMIT = 3800
