"""cockpit.guard — the restart guard (F2, always-on hardening PRD).

Registered as the first handler group (``-1``) so it sees every update before any real handler.
It enforces the PRD's single rule about time:

* **Buttons are judged by boot epoch, never by age.** A ``callback_query`` carries no timestamp, and a
  legitimate approval can be tapped hours after its message. A press whose epoch is not this boot's is
  refused and the gate is re-sent (:mod:`cockpit.epoch`).
* **Everything else is judged by age, in one place, for every message type** (text, command, voice,
  photo, document, edited message), against ``COCKPIT_MAX_UPDATE_AGE_S`` (default 900):

  - an old *command* is refused ("send it again");
  - old *content* is **kept but not executed**: after a short collection window ONE digest is sent with
    ``[Run these now]`` / ``[Discard]``. The tap is the human gate, so nothing stale can start a paid run
    unattended — and the operator does not have to retype;
  - fresh content sent while the bot was down is delivered with an "(sent at HH:MM while I was offline)"
    prefix on the brain's *input*.

It also keeps the in-flight / handled ``update_id`` bookkeeping (dedupe, and the poison-update skip).
Every consumed update is recorded as handled, so nothing it swallowed can be replayed by a restart.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from telegram.ext import ApplicationHandlerStop

from agent.cockpit_state import CockpitSettings, CockpitState, PendingStore
from cockpit import copy, epoch
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

logger = logging.getLogger("cockpit.bot")

DIG_RUN = "dig:run"
DIG_DROP = "dig:drop"
# Prefix families that are NOT approvals and stay outside the epoch rule. Hook-library actions are
# reversible tweaks whose ids can be long enough that a stamp would break Telegram's 64-byte limit.
_EXEMPT_PREFIXES = ("hooks:",)
_DIGEST_PREVIEW_CHARS = 80


def digest_keyboard() -> InlineKeyboardMarkup:
    """The two digest buttons, stamped with the current boot epoch."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(copy.DIGEST_RUN_LABEL, callback_data=epoch.stamp(DIG_RUN)),
                InlineKeyboardButton(
                    copy.DIGEST_DISCARD_LABEL, callback_data=epoch.stamp(DIG_DROP)
                ),
            ]
        ]
    )


def _first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:_DIGEST_PREVIEW_CHARS]
    return ""


def _kind_and_preview(msg) -> tuple[str, str]:
    if msg.voice is not None:
        return "voice", "🎙 voice note"
    if msg.photo:
        cap = _first_line(msg.caption)
        return "photo", "📷 photo" + (f" — {cap}" if cap else "")
    if msg.document is not None:
        return "document", f"📎 {msg.document.file_name or 'file'}"
    return "text", _first_line(msg.text) or "(empty)"


class UpdateGuard:
    def __init__(
        self,
        *,
        state: CockpitState,
        pending: PendingStore,
        settings: CockpitSettings,
        send: Callable[..., Awaitable[int | None]],
        replay: Callable[[Update], Awaitable[None]],
        represent: Callable[[int], Awaitable[None]],
        is_allowed: Callable[[Update], bool],
        profile_of: Callable[[int], str],
        clock: Callable[[], datetime] | None = None,
        boot_time: datetime | None = None,
        collect_window_s: float = 10.0,
    ) -> None:
        self._state = state
        self._pending = pending
        self._settings = settings
        self._send = send
        self._replay = replay
        self._represent = represent
        self._is_allowed = is_allowed
        self._profile_of = profile_of
        self._clock = clock or (lambda: datetime.now(UTC))
        self._boot_time = boot_time or self._clock()
        self._window = collect_window_s
        self._bypass: set[int] = set()
        self._offline: dict[tuple[int, int], str] = {}
        self._announced: dict[int, int] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    # ------------------------------------------------------------------ #
    # PTB entry points
    # ------------------------------------------------------------------ #

    async def pre(self, update: Update, context) -> None:
        """Group ``-1``: raise :class:`ApplicationHandlerStop` when the guard consumed the update."""
        if not self._is_allowed(update):
            return  # the real handlers' own whitelist logs and drops it, exactly as before
        uid = update.update_id
        if uid in self._bypass:  # a digest replay: already vetted by the operator's tap
            self._bypass.discard(uid)
            return
        if self._state.should_skip(uid):
            raise ApplicationHandlerStop
        if update.callback_query is not None:
            await self._pre_callback(update)
        else:
            await self._pre_message(update)
        self._state.begin_update(uid)

    async def finish(self, update: Update) -> None:
        """Group ``100``: the update is done — record it as handled and no longer in flight."""
        self._state.end_update(update.update_id)

    def offline_prefix(self, message) -> str:
        """The "(sent at HH:MM while I was offline) " prefix for the brain's input, or ``""``."""
        key = (getattr(message, "chat_id", None), getattr(message, "message_id", None))
        return self._offline.pop(key, "")

    # ------------------------------------------------------------------ #
    # Buttons
    # ------------------------------------------------------------------ #

    async def _pre_callback(self, update: Update) -> None:
        q = update.callback_query
        data = q.data or ""
        if data.startswith(_EXEMPT_PREFIXES) or not epoch.current():
            return
        clean, got = epoch.split(data)
        chat_id = update.effective_chat.id if update.effective_chat else None
        if got != epoch.current():
            if clean.startswith("perm:"):
                await q.answer(copy.STALE_PERMISSION)
            else:
                await q.answer(copy.STALE_BUTTON)
                if chat_id is not None:
                    if clean.startswith("dig:"):
                        await self.flush_digest(chat_id, force=True)
                    else:
                        await self._represent(chat_id)
            self._consume(update)
        with q._unfrozen():
            q.data = clean
        if clean in (DIG_RUN, DIG_DROP):
            await self._handle_digest_button(update, run=clean == DIG_RUN)
            self._consume(update)

    async def _handle_digest_button(self, update: Update, *, run: bool) -> None:
        q = update.callback_query
        chat_id = update.effective_chat.id
        profile = self._profile_of(chat_id)
        items = self._pending.digest_clear(chat_id, profile)
        self._announced.pop(chat_id, None)
        if not items:
            await q.answer(copy.DIGEST_NOTHING)
            return
        if not run:
            await q.answer(copy.DIGEST_DISCARDED)
            return
        await q.answer(copy.DIGEST_RUNNING)
        for upd in self._replay_updates(items, q.get_bot()):
            self._bypass.add(upd.update_id)
            await self._replay(upd)

    def _replay_updates(self, items: list[dict], bot) -> list[Update]:
        """Texts collapse into ONE marked turn; media (voice/photo/document) replay on their own.

        Only a plain ``message`` / ``edited_message`` that matches the digest line the operator was SHOWN is
        replayed. A stored item is untrusted when read back (the brain's file tools could once have written
        it): a button press (``callback_query``), or an item whose text differs from its preview, is dropped.
        """
        texts: list[tuple[dict, str]] = []
        media: list[Update] = []
        for item in items:
            parsed = self._vetted(item, bot)
            if parsed is None:
                continue
            kind, body, upd = parsed
            if kind == "text":
                texts.append((item, body))
            else:
                media.append(upd)
        out: list[Update] = []
        if texts:
            last = dict(texts[-1][0]["update"])
            holder_key = "message" if "message" in last else "edited_message"
            msg = dict(last[holder_key])
            bodies = [b for _, b in texts]
            if len(bodies) == 1:
                body = copy.OFFLINE_PREFIX.format(hhmm=_hhmm(texts[0][0]["date"])) + " " + bodies[0]
            else:
                body = copy.BUSY_BATCH_OFFLINE.format(n=len(bodies)) + "\n" + "\n".join(bodies)
            msg.update(text=body, date=int(self._clock().timestamp()))
            msg.pop("entities", None)
            msg.pop("edit_date", None)
            out.append(Update.de_json({"update_id": last["update_id"], "message": msg}, bot))
        return out + media

    @staticmethod
    def _vetted(item: dict, bot) -> tuple[str, str, Update] | None:
        """``(kind, text, update)`` if this stored item is a plain message that matches its digest line."""
        try:
            upd = Update.de_json(item["update"], bot)
        except Exception:  # noqa: BLE001 — a malformed stored update is simply not replayed
            return None
        msg = upd.effective_message if upd is not None else None
        if (
            upd is None
            or msg is None
            or upd.callback_query is not None
            or upd.inline_query is not None
        ):
            return None
        kind, preview = _kind_and_preview(msg)
        if kind != item.get("kind") or preview != item.get("preview"):
            return None
        return kind, (msg.text or msg.caption or ""), upd

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #

    async def _pre_message(self, update: Update) -> None:
        msg = update.effective_message
        if msg is None or not self._settings.keep_pending_updates:
            return
        edited = update.edited_message is not None
        date = (update.edited_message.edit_date or msg.date) if edited else msg.date
        age = (self._clock() - date).total_seconds() if date is not None else float("inf")
        is_command = bool(msg.text and msg.text.startswith("/"))
        if age <= self._settings.max_update_age_s:
            if not is_command and date is not None and date < self._boot_time:
                self._offline[(msg.chat_id, msg.message_id)] = (
                    copy.OFFLINE_PREFIX.format(hhmm=date.astimezone(UTC).strftime("%H:%M")) + " "
                )
            return
        chat_id = msg.chat_id
        if is_command:
            await self._send(chat_id, copy.STALE_COMMAND)
            self._consume(update)
        kind, preview = _kind_and_preview(msg)
        profile = self._profile_of(chat_id)
        self._pending.digest_append(
            chat_id,
            profile,
            {
                "kind": kind,
                "uid": update.update_id,
                "date": (date.astimezone(UTC) if date else self._clock()).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "preview": preview,
                "text": msg.text or msg.caption or "",
                "update": update.to_dict(),
            },
        )
        self._schedule_digest(chat_id)
        self._consume(update)

    def _consume(self, update: Update) -> None:
        """The guard answered this update itself: record it handled, then stop the handler chain."""
        self._state.end_update(update.update_id)
        raise ApplicationHandlerStop

    # ------------------------------------------------------------------ #
    # The digest
    # ------------------------------------------------------------------ #

    def _schedule_digest(self, chat_id: int) -> None:
        task = self._tasks.get(chat_id)
        if task is not None and not task.done():
            return
        self._tasks[chat_id] = asyncio.get_running_loop().create_task(self._delayed_flush(chat_id))

    async def _delayed_flush(self, chat_id: int) -> None:
        await asyncio.sleep(self._window)
        await self.flush_digest(chat_id)

    async def flush_digest(self, chat_id: int, *, force: bool = False) -> None:
        """Send ONE digest for the chat's kept messages (a no-op if it already announced this exact set)."""
        profile = self._profile_of(chat_id)
        items = self._pending.digest_items(chat_id, profile)
        if not items:
            return
        if not force and self._announced.get(chat_id) == len(items):
            return
        self._announced[chat_id] = len(items)
        first_hhmm = _hhmm(items[0]["date"])
        text = copy.digest_text([i["preview"] for i in items], first_hhmm)
        await self._send(chat_id, text, reply_markup=digest_keyboard())


def _hhmm(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").strftime("%H:%M")
    except ValueError:
        return "??:??"
