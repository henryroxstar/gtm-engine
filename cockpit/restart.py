"""cockpit.restart — durable state across a restart: the adapters, the boot sequence, the PTB hooks (F1/F2/F7).

Split out of :mod:`cockpit.bot` (which the §R10 ratchet caps) as a :class:`~cockpit.base.CockpitComponent`:
the root still OWNS the state (the stores, the guard, the re-presenter, the epoch); this component holds the
behaviour that reads and drives it — the small adapters the guard and re-presenter call, the boot-time notices
and gate re-presentation, and the PTB ``post_init`` / ``post_stop`` hooks that start the heartbeat and watchdog.
Design: the always-on hardening PRD (2026-10-19).
"""

from __future__ import annotations

import asyncio
import json
import logging

from telegram.ext import Application

import telegram
from agent import inflight
from agent.ledgers import Ledgers
from cockpit import copy, epoch, health
from cockpit.base import CockpitComponent
from telegram import Update

logger = logging.getLogger("cockpit.bot")


class RestartSupport(CockpitComponent):
    """Durable-state adapters + boot sequence for :class:`cockpit.bot.Cockpit`."""

    def offline_prefix(self, message) -> str:
        return self._root._guard.offline_prefix(message) if self._root._guard is not None else ""

    def record_presented(
        self, kind: str, token: str, chat_id: int, profile: str, sent, text: str | None = None
    ) -> None:
        """Remember which Telegram message shows a gate, so a restart can supersede it."""
        message_id = getattr(sent, "message_id", None)
        if self._root._pending is None or message_id is None:
            return
        self._root._pending.record_presented(
            kind,
            token,
            chat_id=chat_id,
            message_id=message_id,
            profile=profile,
            epoch=epoch.current(),
            extra={"text": text} if text else None,
        )

    def clear_presented(self, kind: str, token: str) -> None:
        if self._root._pending is not None:
            self._root._pending.clear_presented(kind, token)

    async def send_notice(self, chat_id: int, text: str) -> None:
        if self._app is None:
            return
        try:
            await self._app.bot.send_message(chat_id=chat_id, text=text)
        except telegram.error.TelegramError:  # noqa: BLE001 — best-effort notice
            logger.debug("could not deliver notice to chat_id=%s", chat_id, exc_info=True)

    async def guard_send(self, chat_id: int, text: str, reply_markup=None) -> int | None:
        if self._app is None:
            return None
        msg = await self._app.bot.send_message(
            chat_id=chat_id, text=text, reply_markup=reply_markup
        )
        return getattr(msg, "message_id", None)

    async def gate_send(
        self, chat_id: int, text: str, reply_markup=None, parse_mode: str | None = None
    ) -> int | None:
        if self._app is None:
            return None
        try:
            msg = await self._app.bot.send_message(
                chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode
            )
        except telegram.error.TelegramError:
            logger.warning("could not re-present a gate to chat_id=%s", chat_id, exc_info=True)
            return None
        return getattr(msg, "message_id", None)

    async def gate_edit(
        self, chat_id: int, message_id: int, text: str, parse_mode: str | None = None
    ) -> None:
        if self._app is None:
            return
        await self._app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=None,
        )

    async def replay_update(self, update: Update) -> None:
        if self._app is not None:
            await self._app.process_update(update)

    async def represent_chat(self, chat_id: int) -> None:
        """A stale button was pressed: re-send that chat's still-valid gates now."""
        if self._root._representer is None:
            return
        report = await self._root._representer.represent_chat(chat_id)
        for profile in report.dropped:
            await self.send_notice(chat_id, self._root._representer.dropped_notice(profile))

    def gate_is_decided(self, kind: str, entry: dict, profile: str) -> bool:
        """Has this pending gate already been decided elsewhere? (a ledger read; any failure → keep it)"""
        try:
            ledgers = Ledgers(self.cfg, profile)
            if self._fails_staging_validation(kind, entry, profile):
                return True  # restored state must still pass what staging enforced
            if kind == "publish":
                from agent.publish import content_hash

                digest = content_hash(entry["post"], tuple(entry.get("media_urls") or ()))
                return digest in ledgers.published_content_hashes()
            if kind == "reply":
                return any(
                    r.get("event") in ("reply_sent", "reply_staged")
                    and r.get("content_sha256") == entry.get("hash")
                    for r in ledgers.iter_history()
                )
            if kind == "plan":
                run_id = self._pending_gate_run_id.get(entry.get("chat_id"))
                if not run_id:
                    return False
                manifest = self.cfg.content_root / profile / "runs" / f"{run_id}.json"
                if not manifest.is_file():
                    return False
                stages = json.loads(manifest.read_text(encoding="utf-8")).get("stages", [])
                return not any(
                    isinstance(st, dict) and st.get("status") == "awaiting_approval"
                    for st in stages
                )
        except Exception:  # noqa: BLE001 — cannot prove it is decided, so it stays pending
            logger.debug("could not check whether a %s gate was decided", kind, exc_info=True)
        return False

    def _fails_staging_validation(self, kind: str, entry: dict, profile: str) -> bool:
        """Re-run the validators staging applied (length, media scheme, disclosure): a restored gate that
        would not be staged today is dropped rather than shown."""
        from agent.publish import candidate_disclosure_lines, validate_disclosure, validate_post
        from agent.reply import validate_reply

        if kind == "publish":
            root = self._root
            if validate_post(
                entry.get("post", ""),
                tuple(entry.get("media_urls") or ()),
                root._publisher.settings.max_chars,
            ):
                return True
            ident = tuple(entry.get("identity_used") or ())
            return bool(
                ident
                and validate_disclosure(
                    entry.get("post", ""),
                    ident,
                    candidate_disclosure_lines(self.cfg.profiles_root, profile),
                )
            )
        if kind == "reply":
            return bool(
                validate_reply(entry.get("body", ""), self._root._reply_sender.settings.max_chars)
            )
        return False

    # ------------------------------------------------------------------ #
    # Boot: epoch, notices, re-presentation (called from PTB post_init)
    # ------------------------------------------------------------------ #

    def begin_boot(self) -> None:
        """Mint this boot's epoch and read what the previous process left (safe mode, lost state)."""
        if self._root._state is None:
            return
        self._root._boot = self._root._state.start_boot()
        epoch.set_epoch(self._root._boot.epoch)
        self._root._lost = self._root._state.take_volatile()
        self._root.drop_pending_updates = (
            self._root._boot.safe_mode or not self._root._settings.keep_pending_updates
        )

    async def boot_announce(self) -> None:
        """Tell the operator what a restart changed, then re-present still-valid pending gates."""
        if self._root._state is None or self._root._pending is None:
            return
        if self._root._boot is None:
            self.begin_boot()
        chats = sorted(self.cfg.telegram_allowed_chat_ids)
        notices = list(self._root._pending.notices)
        self._root._pending.notices.clear()
        for chat in chats:
            for text in notices:
                await self.send_notice(chat, text)
        lost_chats = {c for chat_list in self._root._lost.values() for c in chat_list}
        self._root._lost = {}
        for chat in sorted(lost_chats):
            await self.send_notice(chat, copy.RESTART_LOST_STATE)
        if self._root._boot is not None and self._root._boot.safe_mode:
            for chat in chats:
                await self.send_notice(chat, copy.SAFE_MODE_ALERT)
            return  # a crash loop must not also flood the chat with re-presented gates
        if self._root._representer is not None:
            report = await self._root._representer.represent_all()
            for profile in report.dropped:
                for chat in chats:
                    await self.send_notice(chat, self._root._representer.dropped_notice(profile))
        if self._root._guard is not None:
            for chat in chats:
                await self._root._guard.flush_digest(chat, force=True)

    async def post_init(self, application: Application) -> None:
        """PTB post-init hook (runs before polling starts): liveness machinery, then boot notices."""
        self._app = application
        if self._root._state is not None:
            hb = health.heartbeat_path(self.cfg.content_root)
            self._root._hb_task = asyncio.get_running_loop().create_task(
                health.heartbeat_loop(application, hb)
            )
            if self._root._settings.watchdog_s > 0:
                self._root._watchdog = health.Watchdog(hb, limit_s=self._root._settings.watchdog_s)
                self._root._watchdog.start()
        await self.boot_announce()

    async def post_stop(self, application: Application) -> None:
        """PTB post-stop hook (SIGTERM): let any in-flight publish/enroll finish before the process ends."""
        if self._root._watchdog is not None:
            self._root._watchdog.stop()
        if not await asyncio.to_thread(inflight.wait_idle, 60.0):
            logger.warning("shutting down with an outbound dispatch still in flight")
        if self._root._hb_task is not None:
            self._root._hb_task.cancel()

    async def guard_finish(self, update: Update, context) -> None:
        if self._root._guard is not None:
            await self._root._guard.finish(update)
