"""cockpit.voice_mode — outbound voice: TTS replies + the ``/voice`` mode picker.

The inbound voice note (STT) path lives with the other message ingress handlers
in :mod:`cockpit.ingress`; this module owns only how replies are DELIVERED —
synthesizing speech, honouring the per-chat reply mode, and the one-tap mode
keyboard driven by ``/voice`` and the ``voice:mode:`` callback prefix.
"""

from __future__ import annotations

import asyncio
import html
import logging

from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

import telegram
from agent import voice
from agent.session import SessionStore
from cockpit.base import CockpitComponent
from cockpit.gates import _GATE_PLAN_SENTINEL, _GATE_PUBLISH_SENTINEL, _VOICE_MODE_PREFIX
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

logger = logging.getLogger("cockpit.bot")


class VoiceModeHandlers(CockpitComponent):
    """ElevenLabs voice replies + the per-chat reply-mode picker."""

    async def _send_voice_reply(
        self,
        chat_id: int,
        text: str,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Synthesize the assistant text to speech and send it as a Telegram voice note.

        Fire-and-forget — callers use create_task(). Uses the shared streaming TTS
        helper (low time-to-first-byte; one complete clip) and speaks the full
        reply up to :data:`voice.TTS_MAX_CHARS` rather than the old 400-char snippet
        (logged when clamped — never silently truncated). Shows the "recording
        audio…" presence so the synthesis pause reads as the brain about to speak.

        Silently skips if the API key is absent, the TTS call fails, or the Telegram
        send fails — voice is best-effort, never a blocking gate.
        """
        if not self.cfg.elevenlabs_api_key:
            return
        if not text.strip():
            return
        if len(text) > voice.TTS_MAX_CHARS:
            logger.info(
                "Voice reply: clamping %d chars to TTS limit %d", len(text), voice.TTS_MAX_CHARS
            )
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
        except telegram.error.TelegramError:  # noqa: BLE001 — best-effort UX hint
            pass
        try:
            audio = await voice.synthesize(self.cfg, text)
            await context.bot.send_voice(chat_id=chat_id, voice=audio)
        except voice.VoiceError as exc:
            logger.debug("Voice reply skipped (non-fatal): %s", exc)
        except Exception:  # noqa: BLE001 — best-effort, never break the chat flow
            logger.debug("Voice reply failed (non-fatal)", exc_info=True)

    def _maybe_voice_reply(
        self, chat_id: int, accumulated: str, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Fire a voice reply iff the chat's mode wants one and there's text to speak.

        Honours the per-chat reply mode (``text`` → never voiced) and the standing
        rule that publish-gate readbacks are never spoken (the operator reads the
        exact staged bytes). Fire-and-forget — TTS never blocks the chat flow.
        """
        if not self.cfg.elevenlabs_api_key:
            return
        if self.store.reply_mode(chat_id) == "text":
            return
        if _GATE_PUBLISH_SENTINEL in accumulated:
            return
        clean = accumulated.replace(_GATE_PLAN_SENTINEL, "").strip()
        if clean:
            asyncio.create_task(self._send_voice_reply(chat_id, clean, context))

    _VOICE_MODE_LABELS = {
        "both": "📝🎙️ Text + voice",
        "text": "📝 Text only",
        "voice": "🎙️ Voice only",
    }

    def _voice_mode_keyboard(self, current: str) -> InlineKeyboardMarkup:
        """One-tap mode picker; the active mode is marked with a ●."""
        row = [
            InlineKeyboardButton(
                f"{'● ' if m == current else ''}{self._VOICE_MODE_LABELS[m]}",
                callback_data=f"{_VOICE_MODE_PREFIX}{m}",
            )
            for m in SessionStore.REPLY_MODES
        ]
        return InlineKeyboardMarkup([[b] for b in row])

    async def cmd_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/voice`` toggles voice-only ⇄ text-only; ``/voice [both|text|voice]`` sets it.

        Bare ``/voice`` is a binary toggle so one tap flips the spoken reply on or
        off: from voice-only it goes to text-only, from anything else (text, or the
        ``both`` default) it goes to voice-only — so the first tap on a fresh chat
        gives the spoken-only reply the operator expects. An explicit mode argument
        sets that exact mode (``both`` = text bubble + voice clip). Either way the
        choice persists across redeploys. Gates always render text + buttons
        regardless of mode — you can't approve a publish from an audio clip.
        """
        if not self._is_allowed(update):
            return
        chat_id = update.effective_chat.id  # type: ignore[union-attr]

        arg = context.args[0].strip().lower() if context.args else ""
        if arg in SessionStore.REPLY_MODES:
            new_mode = arg
        else:
            # Bare (or unrecognised) `/voice` toggles the spoken reply on/off.
            new_mode = "text" if self.store.reply_mode(chat_id) == "voice" else "voice"
        self.store.set_reply_mode(chat_id, new_mode)
        await update.message.reply_text(  # type: ignore[union-attr]
            f"✅ Reply mode: <b>{html.escape(self._VOICE_MODE_LABELS[new_mode])}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=self._voice_mode_keyboard(new_mode),
        )

    async def _set_voice_mode(self, query, chat_id: int, mode: str) -> None:
        """Apply a voice-mode button press and refresh the picker in place."""
        try:
            self.store.set_reply_mode(chat_id, mode)
        except ValueError:
            logger.debug("Ignoring unknown voice mode %r from callback", mode)
            return
        try:
            await query.edit_message_text(
                f"✅ Reply mode: <b>{html.escape(self._VOICE_MODE_LABELS[mode])}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=self._voice_mode_keyboard(mode),
            )
        except telegram.error.BadRequest as exc:
            if "not modified" not in str(exc).lower():
                logger.debug("voice-mode edit BadRequest: %s", exc)
