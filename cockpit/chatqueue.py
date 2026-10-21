"""cockpit.chatqueue — one ordered lane per chat, with `/stop` and buttons able to jump the line (F3).

Why this exists (E4/E5 in the always-on hardening PRD): PTB processes updates one at a time by default, so a
handler that awaits an operator's button press (approve-and-resume) holds the only slot — and the press that
would free it cannot be processed until the handler returns. The ask can only time out. And an operator who
sends three quick messages gets three separate turns, queued behind each other, with no way to say "stop".

:class:`PerChatProcessor` is a PTB ``BaseUpdateProcessor`` (a supported extension point, not a fork):

* a chat's updates run **in order** — one ``asyncio.Lock`` per chat;
* different chats run **concurrently**;
* **callback queries and ``/stop`` bypass the lock**, so a button press reaches the handler waiting on it and
  the operator can always stop a running turn;
* plain **text that arrives while that chat's turn is running is buffered** (in memory) instead of queued as
  its own turn; when the turn ends, :mod:`cockpit.delivery` sends the buffer as ONE marked follow-up turn.
  A restart drops the buffer — old content is the offline digest's job (:mod:`cockpit.guard`).

:class:`ChatQueue` is the small shared state (who is busy, what is buffered); it carries no data beyond
message text and is never persisted.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from telegram.ext import BaseUpdateProcessor

from telegram import Update

_STOP_RE = re.compile(r"^/stop(?:@\w+)?\s*$")


class ChatQueue:
    """Per-chat turn state: the ordering lock, whether a turn is running, and the buffered text."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._busy: set[int] = set()
        self._buffer: dict[int, list[str]] = {}

    def lock(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    def begin(self, chat_id: int) -> None:
        self._busy.add(chat_id)

    def end(self, chat_id: int) -> None:
        self._busy.discard(chat_id)

    def is_busy(self, chat_id: int) -> bool:
        return chat_id in self._busy

    def buffer_text(self, chat_id: int, text: str) -> bool:
        """Hold ``text`` for the follow-up turn. False (nothing held) if the chat has no turn running."""
        if chat_id not in self._busy:
            return False
        self._buffer.setdefault(chat_id, []).append(text)
        return True

    def take(self, chat_id: int) -> list[str]:
        """Return and clear everything buffered for ``chat_id`` (in arrival order)."""
        return self._buffer.pop(chat_id, [])

    def discard(self, chat_id: int) -> int:
        """Drop the buffer (``/stop``); returns how many messages were waiting. The turn is not ended here."""
        return len(self._buffer.pop(chat_id, []))


def classify(update: Update) -> tuple[int | None, str]:
    """``(chat_id, kind)``: ``button`` | ``stop`` | ``command`` | ``text`` | ``other`` (voice/photo/document)."""
    chat = update.effective_chat
    chat_id = chat.id if chat is not None else None
    if update.callback_query is not None:
        return chat_id, "button"
    msg = update.effective_message
    if msg is None:
        return chat_id, "other"
    text = msg.text
    if text and text.startswith("/"):
        return chat_id, "stop" if _STOP_RE.match(text) else "command"
    if text:
        return chat_id, "text"
    return chat_id, "other"


class PerChatProcessor(BaseUpdateProcessor):
    def __init__(self, queue: ChatQueue, max_concurrent_updates: int = 32) -> None:
        super().__init__(max_concurrent_updates)
        self._queue = queue

    async def initialize(self) -> None:  # nothing to set up
        return None

    async def shutdown(self) -> None:
        return None

    async def do_process_update(self, update: object, coroutine: Any) -> None:
        if not isinstance(update, Update):
            await coroutine
            return
        chat_id, kind = classify(update)
        if chat_id is None or kind in ("button", "stop"):
            await coroutine  # never waits behind the chat's running turn
            return
        if kind == "text" and self._queue.is_busy(chat_id):
            text = update.effective_message.text  # type: ignore[union-attr]
            self._queue.buffer_text(chat_id, text)
            getattr(coroutine, "close", lambda: None)()  # not run: the follow-up turn carries it
            return
        async with self._queue.lock(chat_id):
            await coroutine
