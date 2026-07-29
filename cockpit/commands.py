"""cockpit.commands — the simple slash commands: /start /help /profile /reset /radar /gate.

Session and profile housekeeping plus the manual radar trigger and the Gate-1
keyboard wiring test. The multi-step flows live elsewhere: /voice in
:mod:`cockpit.voice_mode`, /wizard + /onboard in :mod:`cockpit.onboarding`.
"""

from __future__ import annotations

import html
import logging

from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

import telegram
from cockpit.base import CockpitComponent
from cockpit.gates import _plan_gate_keyboard
from telegram import Update

logger = logging.getLogger("cockpit.bot")


class CommandHandlers(CockpitComponent):
    """One-shot slash commands over the shared session store."""

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/start`` — greet and show the active profile for this chat."""
        if not self._is_allowed(update):
            return
        chat_id = update.effective_chat.id  # type: ignore[union-attr]
        profile = self.store.active_profile(chat_id)
        text = (
            "👋 <b>GTM Engine online.</b>\n"
            f"Active profile: <b>{html.escape(profile)}</b>\n\n"
            "Just type to talk to the brain, or use a command:\n"
            "• <code>/help</code> — show all commands\n"
            "• <code>/profile &lt;name&gt;</code> — switch company profile\n"
            "• <code>/reset</code> — start a fresh session\n"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/help`` — show all available commands and usage tips."""
        if not self._is_allowed(update):
            return
        profile = self.store.active_profile(update.effective_chat.id)  # type: ignore[union-attr]
        text = (
            "🤖 <b>GTM Engine — Command Reference</b>\n\n"
            "<b>Session</b>\n"
            "• <code>/start</code> — show status + active profile\n"
            "• <code>/reset</code> — drop the current session and start fresh (profile kept)\n"
            "• <code>/voice</code> — toggle voice-only ⇄ text-only (persists). "
            "<code>/voice both</code> for text + voice; <code>/voice text|voice</code> to set directly.\n\n"
            "<b>Profile (company)</b>\n"
            "• <code>/profile</code> — show current profile\n"
            "• <code>/profile &lt;name&gt;</code> — switch to a different company profile\n"
            "  Available: see <code>profiles/</code> directory for configured profiles.\n\n"
            "<b>Brain</b>\n"
            "• Just type any message to run the brain against the active profile.\n"
            "• 🎙️ Send a voice note to talk to the brain — it transcribes and replies\n"
            "  in whatever <code>/voice</code> mode you've set.\n"
            "• Skills the brain knows: market-scan, content-radar, content-plan,\n"
            "  content-research, content-studio, build-deck, draft-outreach, and more.\n"
            '• Example: <i>"Run market-scan for this week"</i>\n'
            '• Example: <i>"Generate a LinkedIn carousel idea on AI agents"</i>\n\n'
            "<b>Approvals</b>\n"
            "• <b>Gate 1 (plan):</b> when content-plan proposes a plan, <b>Approve / Edit / Reject</b>\n"
            "  buttons appear. Approve writes the plan; Edit sends notes to revise; Reject discards.\n"
            "• <b>Gate 2 (publish):</b> when content-publish stages a LinkedIn post, the <b>exact</b>\n"
            "  text is shown with <b>Approve &amp; publish / Cancel</b>. Approving posts it to the one\n"
            "  pre-authorized account; nothing else is ever sent. Disabled unless the operator\n"
            "  turns the kill switch on (<code>HERMES_PUBLISH_ENABLED=true</code>).\n"
            "• <code>/gate</code> — render the plan gate keyboard on demand (wiring test)\n\n"
            f"Active profile: <b>{html.escape(profile)}</b>"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]

    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/profile <name>`` — switch the active profile for this chat.

        Validation (does the profile dir exist?) happens inside
        ``store.switch_profile`` → ``profiles.profile_dir``, which raises
        ``ValueError`` on an unknown profile. We surface that to the operator
        rather than silently no-op'ing.
        """
        if not self._is_allowed(update):
            return
        chat_id = update.effective_chat.id  # type: ignore[union-attr]

        # context.args is the whitespace-split tail after the command.
        if not context.args:
            current = self.store.active_profile(chat_id)
            await update.message.reply_text(  # type: ignore[union-attr]
                "Usage: <code>/profile &lt;name&gt;</code>\n"
                f"Current: <b>{html.escape(current)}</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        name = context.args[0].strip()
        try:
            # switch_profile validates via profiles.profile_dir, closes/drops any
            # existing session for this chat, and binds the new profile.
            await self.store.switch_profile(chat_id, name)
        except ValueError as exc:
            logger.info("Profile switch rejected for chat_id=%s: %s", chat_id, exc)
            await update.message.reply_text(  # type: ignore[union-attr]
                f"❌ Unknown profile: <b>{html.escape(name)}</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        await update.message.reply_text(  # type: ignore[union-attr]
            f"✅ Active profile: <b>{html.escape(name)}</b>",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/reset`` — drop this chat's agent session (profile binding kept)."""
        if not self._is_allowed(update):
            return
        chat_id = update.effective_chat.id  # type: ignore[union-attr]
        await self.store.reset(chat_id)
        profile = self.store.active_profile(chat_id)
        await update.message.reply_text(  # type: ignore[union-attr]
            f"🔄 Session reset. Active profile: <b>{html.escape(profile)}</b>",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_radar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/radar`` — trigger a manual radar scan for the active profile.

        Streams the result of the radar skill back into chat so the operator can
        see which items are above threshold without waiting for the Monday cron.
        """
        if not self._is_allowed(update):
            return
        chat_id = update.effective_chat.id  # type: ignore[union-attr]
        profile = self.store.active_profile(chat_id)
        header = f"[{profile}] "
        prompt = (
            "Run the radar skill for the active profile. Surface the top items above "
            "threshold with a brief signal summary and relevance score for each. "
            "If no items are above threshold, say so clearly and suggest adjusting "
            "the radar config or trying again after new content is available."
        )
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except telegram.error.TelegramError:
            pass
        placeholder = await update.message.reply_text(f"{header}Scanning radar…")  # type: ignore[union-attr]
        accumulated = await self._root._delivery._stream_into(placeholder, header, chat_id, prompt)
        if accumulated is None:
            return
        await self._root._delivery._finalize(placeholder, header, accumulated)

    async def cmd_gate_demo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/gate`` — render the real Gate-1 keyboard to test the callback round-trip.

        In normal use these buttons appear automatically under a proposed content
        plan (the content-plan skill emits the gate sentinel). This command renders
        the same Approve/Edit/Reject keyboard on demand; pressing a button runs the
        real directive against the chat's session (if there is no pending draft, the
        brain simply says so).
        """
        if not self._is_allowed(update):
            return
        await update.message.reply_text(  # type: ignore[union-attr]
            "Gate-1 test — Approve / Edit / Reject the (pending) content plan.",
            reply_markup=_plan_gate_keyboard(),
        )
