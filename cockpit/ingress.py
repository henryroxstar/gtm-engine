"""cockpit.ingress — the three inbound message surfaces: text, image, voice.

Every way an operator message reaches the brain. All three handlers share the
same tail (wizard interception → plan-gate edit-notes pop → the delivery
pipeline), so gates, file sentinels, and the ``/voice`` reply mode behave
identically however the message arrived. ``on_voice`` fronts that tail with an
ElevenLabs STT call; the OUTBOUND voice path lives in :mod:`cockpit.voice_mode`.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path

from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import telegram
from agent import voice
from cockpit.base import CockpitComponent
from cockpit.gates import _EDIT_DIRECTIVE
from gtm_core.uploads import (
    MAX_IMAGE_BYTES,
    build_image_prompt,
    normalize_suffix,
    save_inbound_image,
    suffix_for_media_type,
)
from telegram import Update

logger = logging.getLogger("cockpit.bot")

# Inbound voice-note ceiling (STT). Telegram voice notes are small; this just
# guards against an oversized upload before we spend an STT call.
_MAX_VOICE_BYTES = 25 * 1024 * 1024


class IngressHandlers(CockpitComponent):
    """Route inbound text / images / voice notes into the delivery pipeline."""

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Route free text to the brain and stream the reply into one message.

        We open one placeholder reply immediately, then edit it in place as the
        agent yields assistant ``TextBlock`` text. After the run we check for the
        Gate-1 sentinel and attach the Approve/Edit/Reject keyboard if present.

        If the chat just pressed "Edit" on a plan gate, this message is treated as
        edit notes and routed as a revise-and-re-present directive.
        """
        if not self._is_allowed(update):
            return
        if update.message is None or update.message.text is None:
            return

        chat_id = update.effective_chat.id  # type: ignore[union-attr]
        text = update.message.text
        profile = self.store.active_profile(chat_id)
        header = f"[{profile}] "

        # Wizard intercept: if this chat is mid-wizard, consume the message.
        if await self._root._wizard._handle_wizard_answer(chat_id, text, update):
            return

        # If the chat is mid-edit on a plan gate, this message is the edit notes.
        if self._awaiting_edit.pop(chat_id, False):
            prompt = _EDIT_DIRECTIVE.format(edit_notes=text)
        else:
            prompt = text

        # Show a "typing…" chat action so the operator knows we're working even
        # before the first token arrives.
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except telegram.error.TelegramError:  # noqa: BLE001 — best-effort UX hint
            pass

        await self._root._delivery._run_and_deliver(
            update.message, header, chat_id, prompt, context
        )

    async def on_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Download an inbound photo/image-document and route it to the brain.

        The brain has no image-ingestion capability of its own — it reads files on
        disk via the built-in ``Read`` tool (native vision) or the ``vision`` MCP
        worker (cheap OCR). So we persist the bytes under the active profile's
        content root (tenant boundary) and hand the brain the path. The reply then
        streams through the SAME path as text (gate detection, voice reply included).
        See docs/prds/2026-06-24-image-input-pipeline.md.
        """
        if not self._is_allowed(update):
            return
        msg = update.message
        if msg is None:
            return

        chat_id = update.effective_chat.id  # type: ignore[union-attr]
        profile = self.store.active_profile(chat_id)
        header = f"[{profile}] "

        # Resolve the Telegram file + a validated suffix. Photos are always JPEG;
        # image documents carry a filename and/or mime type.
        tg_file = None
        suffix = ".jpg"
        try:
            if msg.photo:
                tg_file = await msg.photo[-1].get_file()  # largest PhotoSize
                suffix = ".jpg"
            elif msg.document and (msg.document.mime_type or "").startswith("image/"):
                doc = msg.document
                raw_suffix = Path(doc.file_name or "").suffix or (
                    suffix_for_media_type(doc.mime_type) or ""
                )
                suffix = normalize_suffix(raw_suffix)  # raises ValueError if unsupported
                tg_file = await doc.get_file()
            else:
                return  # not an image we handle
        except ValueError as exc:
            await msg.reply_text(f"{header}⚠️ {exc}")
            return

        # Size guard before we commit the bytes to disk.
        if tg_file.file_size and tg_file.file_size > MAX_IMAGE_BYTES:
            await msg.reply_text(
                f"{header}⚠️ Image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB. "
                "Downscale it and resend."
            )
            return

        try:
            data = bytes(await tg_file.download_as_bytearray())
            dest = save_inbound_image(self.cfg.content_root, profile, data, suffix)
        except ValueError as exc:
            await msg.reply_text(f"{header}⚠️ Could not accept image: {exc}")
            return
        except (OSError, telegram.error.TelegramError) as exc:
            logger.exception("Image ingestion failed for chat_id=%s", chat_id)
            await msg.reply_text(
                f"{header}⚠️ Could not save image: {html.escape(type(exc).__name__)}."
            )
            return

        prompt = build_image_prompt(dest, msg.caption)

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except telegram.error.TelegramError:  # noqa: BLE001 — best-effort UX hint
            pass

        await self._root._delivery._run_and_deliver(msg, header, chat_id, prompt, context)

    async def on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Transcribe an inbound Telegram voice note and route it like typed text.

        Telegram voice notes are OGG/OPUS. We download the bytes, transcribe via
        ElevenLabs STT (the key lives with the server, never the brain — this is the
        same egress the dashboard /transcribe already uses), then feed the transcript
        through the SAME pipeline as :meth:`on_text` — so gates, file sentinels, and
        the ``/voice`` reply mode all apply.

        The transcript is NOT echoed on success (it would be noise); it surfaces only
        when STT yields nothing or a downstream step fails. See
        docs/prds/2026-06-27-telegram-voice-bridge.md.
        """
        if not self._is_allowed(update):
            return
        msg = update.message
        if msg is None or msg.voice is None:
            return

        chat_id = update.effective_chat.id  # type: ignore[union-attr]
        profile = self.store.active_profile(chat_id)
        header = f"[{profile}] "

        if not self.cfg.elevenlabs_api_key:
            await msg.reply_text(
                f"{header}⚠️ Voice input needs ELEVENLABS_API_KEY configured — send text instead."
            )
            return

        note = msg.voice
        if note.file_size and note.file_size > _MAX_VOICE_BYTES:
            await msg.reply_text(
                f"{header}⚠️ Voice note is larger than {_MAX_VOICE_BYTES // (1024 * 1024)} MB."
            )
            return

        # Show "transcribing" feedback while we fetch + STT the audio.
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except telegram.error.TelegramError:  # noqa: BLE001 — best-effort UX hint
            pass

        try:
            tg_file = await note.get_file()
            data = bytes(await tg_file.download_as_bytearray())
            transcript = await voice.transcribe(
                self.cfg,
                data,
                filename="voice.ogg",
                content_type=note.mime_type or "audio/ogg",
            )
        except voice.VoiceError as exc:
            await msg.reply_text(f"{header}⚠️ Could not transcribe: {html.escape(str(exc))}")
            return
        except (OSError, telegram.error.TelegramError):
            logger.exception("Voice ingestion failed for chat_id=%s", chat_id)
            await msg.reply_text(f"{header}⚠️ Could not fetch the voice note.")
            return

        if not transcript:
            await msg.reply_text(f"{header}🤔 I couldn't make out any words — try again?")
            return

        # Mirror on_text: a wizard answer or plan-gate edit can arrive by voice too.
        if await self._root._wizard._handle_wizard_answer(chat_id, transcript, update):
            return
        if self._awaiting_edit.pop(chat_id, False):
            prompt = _EDIT_DIRECTIVE.format(edit_notes=transcript)
        else:
            prompt = transcript

        await self._root._delivery._run_and_deliver(msg, header, chat_id, prompt, context)
