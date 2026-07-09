"""cockpit.bot — python-telegram-bot (v21+) cockpit for the Content OS brain.

A thin, async-correct Telegram cockpit that fronts the headless Claude Agent SDK
brain. It does NOT hold agent state: every inbound message is routed to the
per-chat :class:`agent.session.SessionStore`, which binds a company *profile*
(e.g. ``acme`` / ``template`` / …) to each ``chat_id``. There is no global mutable
``ACTIVE_PROFILE`` — profile is per chat, switched with ``/profile <name>``.

Security boundary
-----------------
Every update is filtered through a whitelist of allowed chat ids
(``cfg.telegram_allowed_chat_ids``, env ``TELEGRAM_ALLOWED_CHAT_ID``). Updates
from any other chat are silently ignored (logged at WARNING, never answered) so
the bot cannot be enumerated or driven by strangers.

Handlers
--------
``/start``   greet + show the active profile for this chat.
``/profile`` ``<name>`` — switch the active profile for this chat (validated).
``/reset``   drop and recreate this chat's agent session.
text         default handler — streams ``store.run(chat_id, text)`` into one
             reply, edited in place as tokens arrive, prefixed with a
             ``[<active profile>] `` header.

Plus a single inline-keyboard ``Approve`` button demo + a
:class:`telegram.ext.CallbackQueryHandler` that answers the callback — the
skeleton for Phase 1 Gate 1 (the human-in-the-loop approval gate). The button is
wired to *answer* the callback (so the spinner clears) and acknowledge in the
message; the real gate logic (resume the gated run on approve) lands in Phase 1.

This module uses ``import telegram`` / ``from telegram.ext import ...`` — clean
because this package is ``cockpit``, not ``telegram``.
"""

from __future__ import annotations

import asyncio
import functools
import html
import logging
import re
from pathlib import Path

from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import telegram
from agent import voice
from agent.config import Config
from agent.ledgers import Ledgers
from agent.permissions import make_cockpit_can_use_tool
from agent.publish import (
    LinkedInPublisher,
    PublishSettings,
    content_hash,
    parse_publish_block,
    validate_post,
)
from agent.session import SessionStore
from gtm_core.uploads import (
    MAX_IMAGE_BYTES,
    build_image_prompt,
    normalize_suffix,
    save_inbound_image,
    suffix_for_media_type,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

logger = logging.getLogger("cockpit.bot")

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

# --------------------------------------------------------------------------- #
# Gate 1 (content-plan approval) — conversational gate
# --------------------------------------------------------------------------- #
# The content-plan skill ends its turn with this sentinel when a plan DRAFT is
# waiting for approval. The cockpit detects it in the streamed text, strips it,
# and attaches the Approve / Edit / Reject keyboard. A button press injects a
# follow-up prompt into the SAME persistent session (the brain still remembers
# the draft it just proposed) — no SDK permission-callback machinery needed.
_GATE_PLAN_SENTINEL = "⟦GATE:plan⟧"

_GATE_PLAN_APPROVE = "gate:plan:approve"
_GATE_PLAN_EDIT = "gate:plan:edit"
_GATE_PLAN_REJECT = "gate:plan:reject"

# Follow-up prompts injected on a button press. The brain resolves "the active
# profile" / the ISO week from its system prompt + the conversation context.
_APPROVE_DIRECTIVE = (
    "Approved. Promote the pending content plan draft to the final plan for the active profile: "
    "validate each idea as a ContentItem (status 'planned'), write "
    "content/<active>/plans/<YYYY-WW>-plan.md and the matching <YYYY-WW>-plan.json (the ContentItem[] "
    "machine contract), append a history entry, write the plan run-manifest stage via the ledger CLI, "
    "then confirm exactly what you wrote. Resolve <YYYY-WW> to the current ISO week. "
    "After the plan is written and confirmed, immediately run content-research for all approved items "
    "in the plan, then run content-studio for each researched item — producing a linted LinkedIn draft "
    "for each one. Deliver each draft for review."
)
_REJECT_DIRECTIVE = (
    "Rejected. Discard the pending content plan draft (delete the .pending draft file) and confirm. "
    "Do not write a final plan."
)
# Edit directive: loaded as a format-string so edit_notes can be injected safely.
_EDIT_DIRECTIVE = (
    "Revise the pending content plan draft per these edit notes. Load the draft from "
    "content/<active>/plans/.pending/<YYYY-WW>.draft.json, update the ContentItem[] per the notes "
    "(angle, hook_direction, key_points, tone, avoid may all change), rewrite the .pending draft, "
    "then re-present the plan for approval ending with the gate marker line exactly: "
    f"{_GATE_PLAN_SENTINEL}\n\nEdit notes: {{edit_notes}}"
)

# --------------------------------------------------------------------------- #
# Gate 2 (publish) — the ONLY outbound-posting path
# --------------------------------------------------------------------------- #
# The content-publish skill ends its turn with ⟦GATE:publish⟧ and a delimited
# block carrying the EXACT post text (+ optional https media). The cockpit parses
# it, re-displays the exact bytes for the operator to approve, and ONLY on an
# explicit Approve press does the Python publisher POST to the account-pinned n8n
# webhook. The agent never makes the HTTP call and never sees the publish secret;
# approval is bound to the content hash so approving one draft can't publish a
# different one. callback_data carries a 16-hex token (Telegram's 64-byte cap).
_GATE_PUBLISH_SENTINEL = "⟦GATE:publish⟧"
_PUB_OK_PREFIX = "pub:ok:"
_PUB_NO_PREFIX = "pub:no:"

# Reply-mode toggle (/voice): one-tap buttons set the chat's delivery mode.
_VOICE_MODE_PREFIX = "voice:mode:"

# Inbound voice-note ceiling (STT). Telegram voice notes are small; this just
# guards against an oversized upload before we spend an STT call.
_MAX_VOICE_BYTES = 25 * 1024 * 1024

# --------------------------------------------------------------------------- #
# File delivery — ⟦FILE:/absolute/path/to/file.ext⟧
# --------------------------------------------------------------------------- #
# The agent emits this sentinel at the end of a response when it has written a
# deliverable (PDF, DOCX, PPTX, CSV, …) that the operator should receive.
# The cockpit validates the path (must exist; must be inside content_root — the
# only directory the agent is permitted to write to per CLAUDE.md), strips the
# sentinel from the visible text, and sends the file via send_document().
# Multiple ⟦FILE:…⟧ blocks in one response are all sent in order.
_FILE_SENTINEL_RE = re.compile(r"⟦FILE:([^⟧\n]+)⟧")


# --------------------------------------------------------------------------- #
# Cockpit
# --------------------------------------------------------------------------- #


class Cockpit:
    """Holds the PTB handlers and the shared :class:`SessionStore`.

    One :class:`Cockpit` per process. It owns the :class:`SessionStore` (which
    in turn owns one :class:`~agent.session.AgentSession` per chat) and registers
    all handlers onto a PTB :class:`~telegram.ext.Application`.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # Set in register(); used by the permission-policy notifier to message the operator.
        self._app: Application | None = None
        # SessionStore is the single source of per-chat profile + session state.
        # The cockpit never tracks profiles itself. We hand it a per-chat permission-policy
        # factory so a blocked tool call (least-privilege policy, agent/permissions.py) is
        # surfaced to that operator instead of failing silently.
        self.store = SessionStore(cfg, can_use_tool_factory=self._make_can_use_tool)
        # Per-chat flag: the chat pressed "Edit" on a plan gate and the NEXT text
        # message is the edit notes (routed as a revise-and-re-present directive).
        self._awaiting_edit: dict[int, bool] = {}
        # Per-chat run_id extracted from the last Gate-1 sentinel response — used
        # by the dashboard /pending-run scan and for correlating approvals to runs.
        self._pending_gate_run_id: dict[int, str] = {}
        # The single outbound publisher (account pinned server-side). Holds the
        # publish secret + idempotency/rate state; never handed to the brain.
        self._publisher = LinkedInPublisher(PublishSettings.from_env())
        # token -> pending publish draft awaiting an Approve/Cancel press.
        self._pending_publish: dict[str, dict] = {}
        # Wizard state: (chat_id, profile) -> WizardState.  Active only while the
        # operator is mid-wizard; cleared on confirmation or /reset.
        self._wizard_state: dict[tuple[int, str], object] = {}

    # ------------------------------------------------------------------ #
    # Whitelist
    # ------------------------------------------------------------------ #

    def _is_allowed(self, update: Update) -> bool:
        """Return True iff this update's chat is on the whitelist.

        If the whitelist is empty (``TELEGRAM_ALLOWED_CHAT_ID`` unset), we treat
        the bot as locked down and reject everything — fail closed, never open.
        """
        chat = update.effective_chat
        if chat is None:
            return False
        allowed = self.cfg.telegram_allowed_chat_ids
        if not allowed:
            logger.warning(
                "Rejecting update from chat_id=%s: no whitelist configured "
                "(TELEGRAM_ALLOWED_CHAT_ID unset) — failing closed.",
                chat.id,
            )
            return False
        if chat.id not in allowed:
            logger.warning("Rejecting update from non-whitelisted chat_id=%s", chat.id)
            return False
        return True

    # ------------------------------------------------------------------ #
    # Least-privilege permission policy (per chat)
    # ------------------------------------------------------------------ #

    def _make_can_use_tool(self, chat_id: int):
        """Build the chat-bound ``can_use_tool`` callback for this operator's session.

        Allows known-safe tools, denies the dangerous-shell floor, and on an unrecognised
        (escalated) tool denies it AND notifies this operator — so a blocked call is visible
        rather than silent. See :mod:`agent.permissions`.
        """
        return make_cockpit_can_use_tool(
            notify=functools.partial(self._notify_blocked_tool, chat_id)
        )

    async def _notify_blocked_tool(
        self, chat_id: int, tool_name: str, tool_input: dict, final: bool = False
    ) -> None:
        """Tell the operator that the brain tried an unrecognised tool and was blocked.

        Called at most twice per distinct blocked call: once on the first escalation, and once
        (``final=True``) when the brain crosses the strike limit and has been told to stop. The
        permission callback handles the de-dupe — see :func:`agent.permissions.make_cockpit_can_use_tool`.
        """
        if self._app is None:
            return
        tool = html.escape(tool_name)
        # Surface the actual command/input so the operator can see exactly what was blocked.
        # Without this the notice just says "Bash", which makes a denied step impossible to
        # diagnose from Telegram. Truncate so a huge command can't blow the message size.
        detail = ""
        if isinstance(tool_input, dict):
            raw = tool_input.get("command") or tool_input.get("file_path") or ""
            raw = str(raw).strip()
            if raw:
                if len(raw) > 300:
                    raw = raw[:300] + "…"
                detail = f"\n<pre>{html.escape(raw)}</pre>"
        if final:
            text = (
                f"⛔ The brain repeatedly tried <code>{tool}</code> and was denied each time — "
                "I've now instructed it to stop retrying (nothing ran). Run the step yourself or "
                "point me at an approved tool/MCP."
                f"{detail}"
            )
        else:
            text = (
                "🛡️ Blocked a tool the brain tried to use: "
                f"<code>{tool}</code>.{detail}\n"
                "It was denied by the least-privilege policy (nothing ran). For web pages "
                "use the Firecrawl MCP tool or WebFetch; for deck/carousel rendering use "
                "mcp__deck__export_deck; otherwise run the step yourself or point me at an approved tool/MCP."
            )
        try:
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except telegram.error.TelegramError:  # noqa: BLE001 — best-effort notice
            pass

    # ------------------------------------------------------------------ #
    # ElevenLabs voice reply
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # Command handlers
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # /radar — manual radar trigger
    # ------------------------------------------------------------------ #

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
        accumulated = await self._stream_into(placeholder, header, chat_id, prompt)
        if accumulated is None:
            return
        await self._finalize(placeholder, header, accumulated)

    # ------------------------------------------------------------------ #
    # /wizard — profile onboarding wizard
    # ------------------------------------------------------------------ #

    async def cmd_wizard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/wizard [profile]`` — guided profile onboarding flow.

        Runs the 6-question wizard for ``profile`` (defaults to the active profile).
        Each answer is collected via follow-up text messages (routed through
        ``_handle_wizard_answer``), not through the brain session — so the active
        profile binding is unaffected for the duration of the wizard.
        """
        from agent.wizard import WizardState

        if not self._is_allowed(update):
            return
        chat_id = update.effective_chat.id  # type: ignore[union-attr]

        target_profile = (
            context.args[0].strip() if context.args else self.store.active_profile(chat_id)
        )
        from agent import profiles as _profiles

        try:
            _profiles.profile_dir(self.cfg.profiles_root, target_profile)
        except ValueError:
            await update.message.reply_text(  # type: ignore[union-attr]
                f"❌ Unknown profile: <b>{html.escape(target_profile)}</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        key = (chat_id, target_profile)
        if key not in self._wizard_state:
            self._wizard_state[key] = WizardState(profile=target_profile)
        state = self._wizard_state[key]  # type: ignore[assignment]

        intro = (
            f"🧙 <b>Profile wizard — {html.escape(target_profile)}</b>\n\n"
            f"I'll ask you 6 questions to bootstrap the knowledge files for this profile. "
            f"Your answers feed directly into voice.md, icp-personas.md, competitors.md, and pillars.md.\n\n"
            f"Reply /wizard_cancel at any time to abort.\n\n"
        )
        question = state.current_question()
        if question:
            await update.message.reply_text(  # type: ignore[union-attr]
                intro + question["prompt"],
                parse_mode=ParseMode.HTML,
            )

    async def _handle_wizard_answer(self, chat_id: int, text: str, update: Update) -> bool:
        """Handle a wizard answer. Returns True if the message was consumed by the wizard."""
        from agent.wizard import WizardState, build_summary, patch_profile_md, write_knowledge_files

        # Find any active wizard for this chat, regardless of which profile it's for.
        state: WizardState | None = next(
            (s for k, s in self._wizard_state.items() if k[0] == chat_id),
            None,
        )
        if state is None:
            return False
        key = (chat_id, state.profile)

        stripped = text.strip()

        # Handle /wizard_cancel (also routed here since it arrives as plain text
        # after CommandHandler filtering — belt-and-suspenders check).
        if stripped.lower() in ("/wizard_cancel", "cancel", "/cancel"):
            del self._wizard_state[key]
            await update.message.reply_text("🚫 Wizard cancelled.")  # type: ignore[union-attr]
            return True

        # At confirmation step: handle "yes" or "edit N".
        if state.is_complete:
            if stripped.lower() == "yes":
                write_knowledge_files(self.cfg.profiles_root, state)
                patched = patch_profile_md(self.cfg.profiles_root, state)
                del self._wizard_state[key]
                note = ""
                if not patched:
                    note = (
                        "\n\n⚠️ PROFILE.md has no wizard anchor markers — "
                        "<code>icp:</code> and <code>content_pillars:</code> were not auto-updated. "
                        "Edit them manually."
                    )
                await update.message.reply_text(  # type: ignore[union-attr]
                    f"✅ Knowledge files written for <b>{html.escape(state.profile)}</b>.{note}",
                    parse_mode=ParseMode.HTML,
                )
                return True

            edit_match = re.match(r"^edit\s+(\d+)$", stripped.lower())
            if edit_match:
                n = int(edit_match.group(1))
                from agent.wizard import QUESTIONS

                if 1 <= n <= len(QUESTIONS):
                    state.step = n - 1
                    q = state.current_question()
                    await update.message.reply_text(  # type: ignore[union-attr]
                        f"Re-answering question {n}:\n\n{q['prompt']}",
                        parse_mode=ParseMode.HTML,
                    )
                    return True

            # Unrecognised input at confirm step — re-show summary.
            await update.message.reply_text(  # type: ignore[union-attr]
                build_summary(state) + "\n\nReply *yes* or *edit N*.",
                parse_mode=ParseMode.HTML,
            )
            return True

        # Normal answer: record and advance.
        state.record_answer(stripped)

        if state.is_complete:
            await update.message.reply_text(  # type: ignore[union-attr]
                build_summary(state),
                parse_mode=ParseMode.HTML,
            )
        else:
            next_q = state.current_question()
            await update.message.reply_text(  # type: ignore[union-attr]
                next_q["prompt"],
                parse_mode=ParseMode.HTML,
            )
        return True

    async def cmd_wizard_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/wizard_cancel`` — abort an in-progress wizard."""
        if not self._is_allowed(update):
            return
        chat_id = update.effective_chat.id  # type: ignore[union-attr]
        keys = [k for k in self._wizard_state if k[0] == chat_id]
        if keys:
            for k in keys:
                del self._wizard_state[k]
            await update.message.reply_text("🚫 Wizard cancelled.")  # type: ignore[union-attr]
        else:
            await update.message.reply_text("No wizard in progress.")  # type: ignore[union-attr]

    # ------------------------------------------------------------------ #
    # /onboard — URL/file/text ingestion → staged profile draft
    # ------------------------------------------------------------------ #

    async def cmd_onboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /onboard <url|text> — ingest and stage a new profile."""
        if not self._is_allowed(update):
            return
        chat_id = update.effective_chat.id  # type: ignore[union-attr]

        args = context.args or []
        source = " ".join(args).strip()
        if not source:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "Usage: /onboard <url> or /onboard <pasted text>\n\n"
                    "Example:\n/onboard https://acme.com"
                ),
            )
            return

        cfg = self.cfg
        source_type = (
            "url" if source.startswith("http://") or source.startswith("https://") else "text"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Ingesting source ({source_type})... this may take 30-60 seconds.",
        )

        try:
            from agent.onboard import extract, ingest, render, slugify, stage

            raw_text = await asyncio.to_thread(ingest, source, source_type, cfg)
            draft = await extract(raw_text, cfg)
            slug = slugify(draft["company"]["name"])
            files = render(draft)
            draft_id, staged_root = await asyncio.to_thread(
                stage, slug, files, cfg, draft["company"]["name"]
            )

        except (ValueError, RuntimeError) as exc:
            await context.bot.send_message(chat_id=chat_id, text=f"Onboarding failed: {exc}")
            return

        company = draft["company"]
        gaps = draft.get("gaps", [])
        confidence = draft.get("confidence", "unknown")
        gap_text = "\n".join(f"  • {g}" for g in gaps) if gaps else "  _none_"

        summary = (
            f"*Profile draft ready*\n\n"
            f"Company: *{company['name']}*\n"
            f"Slug: `{slug}`\n"
            f"Confidence: {confidence}\n"
            f"Files staged: {len(files)}\n\n"
            f"*Gaps:*\n{gap_text}\n\n"
            f"Draft ID: `{draft_id}`\n\n"
            f"To promote:\n`/onboard_confirm {draft_id} {company['name']}`\n\n"
            f"To discard:\n`/onboard_cancel {draft_id}`"
        )
        await context.bot.send_message(chat_id=chat_id, text=summary, parse_mode="Markdown")

    async def cmd_onboard_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /onboard_confirm <draft_id> <Company Name> — promote staged profile."""
        if not self._is_allowed(update):
            return
        chat_id = update.effective_chat.id  # type: ignore[union-attr]

        args = context.args or []
        if len(args) < 2:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Usage: /onboard_confirm <draft_id> <Company Name>",
            )
            return

        draft_id = args[0]
        confirmed_name = " ".join(args[1:])

        try:
            import json  # noqa: PLC0415

            from agent.onboard import _staged_root_for_draft_id, promote  # noqa: PLC0415

            staged_root = _staged_root_for_draft_id(draft_id, self.cfg)
            meta = json.loads((staged_root / ".onboard-meta.json").read_text())
            slug = meta["slug"]

            # Tenant safeguard: verify the operator's confirmed name matches the extracted name
            expected_name = meta.get("company_name", "")
            if expected_name and confirmed_name.strip().lower() != expected_name.strip().lower():
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=(
                        f"Company name mismatch.\n"
                        f"Expected: {expected_name}\n"
                        f"Got: {confirmed_name}\n\n"
                        f"Use: /onboard_confirm {draft_id} {expected_name}"
                    ),
                )
                return

            # Minimal draft stub for audit logging (Telegram has no in-memory registry)
            draft_stub = {
                "company": {"name": confirmed_name},
                "source": {},
                "confidence": "unknown",
                "products": [],
                "gaps": [],
            }

            await asyncio.to_thread(promote, slug, draft_id, staged_root, draft_stub, self.cfg)
        except (ValueError, FileNotFoundError) as exc:
            await context.bot.send_message(chat_id=chat_id, text=f"Promote failed: {exc}")
            return

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Profile *{slug}* promoted to `profiles/{slug}/`.\n\n"
                f"Use `/profile {slug}` to activate."
            ),
            parse_mode="Markdown",
        )

    async def cmd_onboard_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /onboard_cancel <draft_id> — discard staged profile."""
        if not self._is_allowed(update):
            return
        chat_id = update.effective_chat.id  # type: ignore[union-attr]

        args = context.args or []
        if not args:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Usage: /onboard_cancel <draft_id>",
            )
            return

        draft_id = args[0]
        try:
            from agent.onboard import _staged_root_for_draft_id, cancel

            staged_root = _staged_root_for_draft_id(draft_id, self.cfg)
            cancel(staged_root)
        except FileNotFoundError as exc:
            await context.bot.send_message(chat_id=chat_id, text=f"Cancel failed: {exc}")
            return

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Draft `{draft_id}` cancelled.",
            parse_mode="Markdown",
        )

    # ------------------------------------------------------------------ #
    # Default text handler — stream the brain's reply
    # ------------------------------------------------------------------ #

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
        if await self._handle_wizard_answer(chat_id, text, update):
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

        await self._run_and_deliver(update.message, header, chat_id, prompt, context)

    # ------------------------------------------------------------------ #
    # Streaming + gate-aware finalization (shared by on_text and on_callback)
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Image handler — store inbound photo to disk, let the brain read it
    # ------------------------------------------------------------------ #

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

        await self._run_and_deliver(msg, header, chat_id, prompt, context)

    # ------------------------------------------------------------------ #
    # Voice handler — transcribe an inbound voice note, route it like text
    # ------------------------------------------------------------------ #

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
        if await self._handle_wizard_answer(chat_id, transcript, update):
            return
        if self._awaiting_edit.pop(chat_id, False):
            prompt = _EDIT_DIRECTIVE.format(edit_notes=transcript)
        else:
            prompt = transcript

        await self._run_and_deliver(msg, header, chat_id, prompt, context)

    async def _run_and_deliver(
        self, message, header: str, chat_id: int, prompt: str, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Stream ``prompt`` to the brain, finalize, and deliver voice per the chat's mode.

        The shared tail of :meth:`on_text`, :meth:`on_image`, and :meth:`on_voice`:
        open one placeholder reply to ``message``, stream into it (suppressed in
        voice-only mode), gate-aware finalize, then a mode-aware voice reply. Keeps
        the three inbound surfaces identical so gates / file sentinels / the
        ``/voice`` mode behave the same however the message arrived.
        """
        mode = self.store.reply_mode(chat_id)
        placeholder = await message.reply_text(f"{header}…")
        accumulated = await self._stream_into(
            placeholder, header, chat_id, prompt, show_text=mode != "voice"
        )
        if accumulated is None:  # the run errored; the error was already shown
            return
        await self._finalize(placeholder, header, accumulated, voice_only=mode == "voice")
        # Voice reply: fire-and-forget after text is delivered (mode-aware; skips
        # publish-gate readbacks and the text-only mode).
        self._maybe_voice_reply(chat_id, accumulated, context)

    async def _stream_into(
        self, placeholder, header: str, chat_id: int, prompt: str, show_text: bool = True
    ) -> str | None:
        """Stream ``store.run(chat_id, prompt)`` into ``placeholder``, coalescing edits.

        Returns the accumulated assistant text, or ``None`` if the brain run
        errored (in which case a sanitized error line has already been rendered).
        Does NOT do the final clean render — the caller's :meth:`_finalize` does,
        so it can strip the gate sentinel and attach the keyboard.

        When ``show_text`` is False (voice-only mode) the streamed text is NOT
        painted into the placeholder — it stays as the "…" thinking indicator —
        but the full reply is still accumulated and returned for TTS and gate
        detection. Errors and gates always render regardless (the caller's
        :meth:`_finalize` re-renders a gate in full even in voice-only mode).
        """
        accumulated = ""
        last_edit = 0.0
        last_rendered = ""
        truncated = False

        async def _flush() -> None:
            nonlocal last_rendered
            body = accumulated if accumulated else "…"
            rendered = (header + body + " …")[:_TELEGRAM_MSG_LIMIT]
            if rendered == last_rendered:
                return
            try:
                await placeholder.edit_text(rendered)
                last_rendered = rendered
            except telegram.error.BadRequest as exc:
                if "not modified" not in str(exc).lower():
                    logger.debug("edit_text BadRequest: %s", exc)
            except telegram.error.RetryAfter as exc:
                logger.debug("edit_text RetryAfter %ss", exc.retry_after)
                await asyncio.sleep(exc.retry_after)

        try:
            async for chunk in self.store.run(chat_id, prompt):
                if not chunk:
                    continue
                accumulated += chunk
                if len(accumulated) >= _STREAM_SOFT_LIMIT:
                    accumulated = accumulated[:_STREAM_SOFT_LIMIT]
                    truncated = True
                now = asyncio.get_running_loop().time()
                if show_text and now - last_edit >= _STREAM_EDIT_INTERVAL_S:
                    await _flush()
                    last_edit = now
                if truncated:
                    break
        except Exception as exc:  # noqa: BLE001 — surface ANY brain error to the operator
            logger.exception("Brain run failed for chat_id=%s", chat_id)
            err_text = (
                f"{header}⚠️ The run failed: "
                f"{html.escape(type(exc).__name__)}. Check the server logs."
            )
            try:
                await placeholder.edit_text(err_text[:_TELEGRAM_MSG_LIMIT])
            except telegram.error.TelegramError:
                pass
            return None

        if truncated:
            accumulated += "\n\n…(truncated)"
        return accumulated

    def _validate_sendable_path(self, path_str: str) -> Path | None:
        """Return the resolved Path iff it is safe to send to Telegram.

        A path is sendable iff:
        - It resolves to an absolute path with no ``..`` traversal.
        - The resolved path is inside ``cfg.content_root`` (the only directory the
          agent is permitted to write, per CLAUDE.md tenant boundary).
        - The file exists on disk.

        Returns ``None`` on any violation so callers can log-and-skip safely.
        """
        try:
            resolved = Path(path_str.strip()).resolve()
        except Exception:
            logger.warning("FILE sentinel: unparseable path %r — skipping", path_str)
            return None

        content_root = self.cfg.content_root.resolve()
        try:
            resolved.relative_to(content_root)
        except ValueError:
            logger.warning(
                "FILE sentinel: path %r is outside content_root %s — blocked",
                str(resolved),
                content_root,
            )
            return None

        if not resolved.is_file():
            logger.warning("FILE sentinel: path %r does not exist — skipping", str(resolved))
            return None

        return resolved

    async def _send_file_attachments(self, placeholder, paths: list[Path]) -> None:
        """Send each path as a Telegram document reply. Best-effort — errors are logged, not raised."""
        for p in paths:
            try:
                with p.open("rb") as fh:
                    await placeholder.reply_document(document=fh, filename=p.name)
                logger.info("FILE sentinel: sent %s (%d bytes)", p.name, p.stat().st_size)
            except Exception:
                logger.exception("FILE sentinel: failed to send %s", p.name)

    async def _finalize(self, placeholder, header: str, raw: str, voice_only: bool = False) -> None:
        """Render the final reply; if it carries a gate sentinel, attach the gate.

        Publish (Gate 2) takes precedence over plan (Gate 1): a publish draft must
        be re-displayed exactly and gated behind Approve/Cancel. Otherwise the
        plan sentinel is stripped and replaced by the Approve/Edit/Reject keyboard.
        File sentinels (⟦FILE:…⟧) are always processed last — they are independent
        of the plan/publish gates and are stripped from the visible text in all paths.

        When ``voice_only`` is True the spoken clip carries the content, so a normal
        (non-gated, non-error) reply collapses to a compact 🎙️ marker instead of the
        full text bubble. Gates and error fallbacks always render in full — you must
        read the plan / tap the buttons / see the failure, so they override voice-only.
        """
        # Extract and validate any file attachments before gate routing.
        file_paths = [
            p
            for m in _FILE_SENTINEL_RE.finditer(raw)
            if (p := self._validate_sendable_path(m.group(1))) is not None
        ]
        # Strip file sentinels from the raw text in all branches.
        raw = _FILE_SENTINEL_RE.sub("", raw).strip()

        if _GATE_PUBLISH_SENTINEL in raw:
            await self._finalize_publish_gate(placeholder, header, raw)
            if file_paths:
                await self._send_file_attachments(placeholder, file_paths)
            return

        gated = _GATE_PLAN_SENTINEL in raw
        body = raw.replace(_GATE_PLAN_SENTINEL, "").strip()
        if not body:
            # When gated, the plan-gate keyboard is the deliverable — keep "…" as the
            # body so the keyboard attaches cleanly (different reply_markup = edit succeeds).
            # When NOT gated, "…" is identical to the placeholder, so Telegram silently
            # rejects the edit and the user sees the placeholder stuck forever. Show a
            # diagnostic message instead — this happens when a tool-denial loop burned the
            # run budget without producing any assistant text.
            body = (
                "…"
                if gated
                else "⚠️ The brain completed without producing text — a tool call may have been blocked. Check the server logs or rephrase your request."
            )
        # Voice-only: a normal reply is delivered as audio, so collapse the text
        # bubble to a marker. Gates (gated) and the diagnostic "⚠️" fallback keep
        # their full text — they are not voiced as the sole channel.
        if voice_only and not gated and not body.startswith("⚠️"):
            body = "🎙️"
        rendered = (header + body)[:_TELEGRAM_MSG_LIMIT]
        keyboard = self._plan_gate_keyboard() if gated else None
        if gated:
            # Extract run_id (format: r-YYYYMMDD-HHMM) from the brain's output
            # so the dashboard can correlate approvals to the right manifest.
            m = re.search(r"\br-(20\d{6}-\d{4})\b", raw)
            if m:
                self._pending_gate_run_id[placeholder.chat_id] = f"r-{m.group(1)}"
        try:
            await placeholder.edit_text(rendered, reply_markup=keyboard)
        except telegram.error.BadRequest as exc:
            if "not modified" not in str(exc).lower():
                logger.debug("finalize edit_text BadRequest: %s", exc)

        if file_paths:
            await self._send_file_attachments(placeholder, file_paths)

    async def _finalize_publish_gate(self, placeholder, header: str, raw: str) -> None:
        """Re-display the EXACT post for human approval, then attach Approve/Cancel.

        Prompt-injection defense: the post is treated purely as data. We strip the
        whole gate block from the agent's visible prose, then send a SEPARATE,
        clearly-labelled preview showing the exact bytes that will be sent — so the
        operator approves precisely what gets published. The destination is not in
        the draft at all (the server pins the account), so no scraped/model text can
        redirect it. Approval is bound to the content hash via a short token.
        """
        chat_id = placeholder.chat_id
        profile = self.store.active_profile(chat_id)
        draft = parse_publish_block(raw)

        # Strip the entire gate block from the agent's prose for the cleaned reply.
        visible = raw.split(_GATE_PUBLISH_SENTINEL, 1)[0].strip() or "Drafted a LinkedIn post."
        try:
            await placeholder.edit_text((header + visible)[:_TELEGRAM_MSG_LIMIT])
        except telegram.error.BadRequest as exc:
            if "not modified" not in str(exc).lower():
                logger.debug("publish finalize edit_text BadRequest: %s", exc)

        if draft is None:
            await placeholder.reply_text(
                f"{header}⚠️ A publish gate was emitted but no valid post block was found — "
                "nothing staged. Ask me to redraft."
            )
            return

        # Validate BEFORE staging so the operator never approves invalid content
        # (blank post, over-length, or non-https media) only to have it bounce.
        reason = validate_post(draft.post, draft.media_urls, self._publisher.settings.max_chars)
        if reason:
            await placeholder.reply_text(
                f"{header}⚠️ Not staged for publish — {reason}. Ask me to redraft."
            )
            return

        media_line = (
            "\n".join(f"• {html.escape(u)}" for u in draft.media_urls)
            if draft.media_urls
            else "<i>none</i>"
        )
        preview = (
            f"{header}📤 <b>Ready to publish to LinkedIn</b> (account pinned server-side):\n\n"
            f"<b>Post (exact):</b>\n<pre>{html.escape(draft.post)}</pre>\n"
            f"<b>Media:</b>\n{media_line}\n\n"
            "⚠️ Review the <b>exact</b> text above. Approving publishes it as-is to the one "
            "pre-authorized account. Nothing else is sent."
        )
        # Integrity: the operator must see the FULL exact content before approving.
        # If the preview would be truncated, refuse to stage — never let unseen
        # trailing media/text ride along on an approval. (In practice the linter
        # caps the body well under this; this is the hard backstop.)
        if len(preview) > _TELEGRAM_MSG_LIMIT:
            await placeholder.reply_text(
                f"{header}⚠️ The post + media is too long to show in full for approval. "
                "Shorten it so the exact content fits one message before publishing — nothing staged."
            )
            return

        key = content_hash(draft.post, draft.media_urls)
        token = key[:16]
        self._pending_publish[token] = {
            "post": draft.post,
            "media_urls": list(draft.media_urls),
            "hash": key,
            "chat_id": chat_id,
            "profile": profile,
        }
        await placeholder.reply_text(
            preview,
            parse_mode=ParseMode.HTML,
            reply_markup=self._publish_keyboard(token),
        )

    @staticmethod
    def _publish_keyboard(token: str) -> InlineKeyboardMarkup:
        """Approve / Cancel keyboard for a staged publish (token binds to the draft)."""
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Approve & publish", callback_data=f"{_PUB_OK_PREFIX}{token}"
                    ),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"{_PUB_NO_PREFIX}{token}"),
                ]
            ]
        )

    @staticmethod
    def _plan_gate_keyboard() -> InlineKeyboardMarkup:
        """The Gate-1 Approve / Edit / Reject inline keyboard."""
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=_GATE_PLAN_APPROVE),
                    InlineKeyboardButton("✏️ Edit", callback_data=_GATE_PLAN_EDIT),
                    InlineKeyboardButton("❌ Reject", callback_data=_GATE_PLAN_REJECT),
                ]
            ]
        )

    # ------------------------------------------------------------------ #
    # Inline approval gate (Phase 1, Gate 1 skeleton)
    # ------------------------------------------------------------------ #

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
            reply_markup=self._plan_gate_keyboard(),
        )

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Drive the Gate-1 round-trip from an inline-keyboard press.

        Always ``answer()`` the callback first so Telegram clears the button's
        spinner. Approve / Reject inject a follow-up directive into the chat's
        persistent session and stream the result; Edit parks the chat so its next
        message becomes the edit notes.
        """
        if not self._is_allowed(update):
            return
        query = update.callback_query
        if query is None:
            return
        # Clearing the spinner is mandatory — an unanswered callback leaves the
        # client hanging for ~30s.
        await query.answer()

        chat_id = update.effective_chat.id  # type: ignore[union-attr]
        header = f"[{self.store.active_profile(chat_id)}] "
        data = query.data

        if data and data.startswith(_VOICE_MODE_PREFIX):
            await self._set_voice_mode(query, chat_id, data[len(_VOICE_MODE_PREFIX) :])
        elif data and data.startswith(_PUB_OK_PREFIX):
            await self._do_publish(query, chat_id, header, data[len(_PUB_OK_PREFIX) :])
        elif data and data.startswith(_PUB_NO_PREFIX):
            await self._cancel_publish(query, header, data[len(_PUB_NO_PREFIX) :])
        elif data == _GATE_PLAN_APPROVE:
            await self._run_gate_directive(query, chat_id, header, _APPROVE_DIRECTIVE)
        elif data == _GATE_PLAN_REJECT:
            self._awaiting_edit.pop(chat_id, None)  # cancel any pending edit
            await self._run_gate_directive(query, chat_id, header, _REJECT_DIRECTIVE)
        elif data == _GATE_PLAN_EDIT:
            # Park the chat: the next text message is the edit notes (see on_text).
            self._awaiting_edit[chat_id] = True
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except telegram.error.BadRequest:
                pass
            await query.message.reply_text(  # type: ignore[union-attr]
                f"{header}✏️ Send your edit notes and I'll revise the plan, then re-present it."
            )
        else:
            logger.debug("Unhandled callback_data=%r", data)

    async def _run_gate_directive(self, query, chat_id: int, header: str, directive: str) -> None:
        """Drop the gate keyboard, then stream a gate directive into a fresh reply."""
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except telegram.error.BadRequest:
            pass
        placeholder = await query.message.reply_text(f"{header}…")  # type: ignore[union-attr]
        accumulated = await self._stream_into(placeholder, header, chat_id, directive)
        if accumulated is None:
            return
        await self._finalize(placeholder, header, accumulated)

    # ------------------------------------------------------------------ #
    # Gate 2 — publish (the only outbound-posting action)
    # ------------------------------------------------------------------ #

    async def _do_publish(self, query, chat_id: int, header: str, token: str) -> None:
        """Approve press: fire the account-pinned publish for the staged draft.

        The draft is popped (so a second click finds nothing — and the publisher's
        idempotency would block a re-send anyway). We recompute the content hash and
        verify it matches what was staged: an approval is bound to its exact draft.
        The HTTP call happens here in Python — the brain is not involved and never
        held the publish secret.
        """
        pending = self._pending_publish.pop(token, None)
        if pending is None:
            await query.message.reply_text(  # type: ignore[union-attr]
                f"{header}↩️ That publish was already handled or expired — nothing sent."
            )
            return

        # Approval-binding check: the staged hash must match the staged content.
        recomputed = content_hash(pending["post"], tuple(pending["media_urls"]))
        if recomputed != pending["hash"]:
            logger.warning(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
                "Publish hash mismatch for token=%s — refusing.", token
            )
            await query.message.reply_text(  # type: ignore[union-attr]
                f"{header}⚠️ Draft integrity check failed — not published."
            )
            return

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except telegram.error.BadRequest:
            pass

        # Durable, cross-restart idempotency: block re-publishing content this profile has
        # already published (the in-memory set in the publisher resets on restart). Read the
        # per-profile history ledger; tolerate any error (fall back to in-memory-only).
        try:
            published = Ledgers(self.cfg, pending["profile"]).published_content_hashes()
            is_published = published.__contains__
        except Exception:  # noqa: BLE001 — never let a ledger read block a human-approved publish
            is_published = None

        result = await self._publisher.publish(
            pending["post"], tuple(pending["media_urls"]), is_published=is_published
        )

        # Audit: record the outcome in the per-profile history ledger (written by the
        # component that actually published — not trusted to the LLM).
        try:
            Ledgers(self.cfg, pending["profile"]).append_history(
                {
                    "event": "published" if result.ok else "publish_failed",
                    "platform": "linkedin",
                    "skill": "content-publish",
                    "status": result.status,
                    "post_id": result.post_id,
                    "content_sha256": pending["hash"],
                    "chars": len(pending["post"]),
                    "media_count": len(pending["media_urls"]),
                }
            )
        except Exception:  # noqa: BLE001 — ledger write must never break the reply
            logger.exception("Failed to write publish history for profile=%s", pending["profile"])

        await query.message.reply_text(f"{header}{result.operator_line()}")  # type: ignore[union-attr]

    async def _cancel_publish(self, query, header: str, token: str) -> None:
        """Cancel press: drop the staged draft without sending anything."""
        self._pending_publish.pop(token, None)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except telegram.error.BadRequest:
            pass
        await query.message.reply_text(f"{header}❌ Publish cancelled — nothing sent.")  # type: ignore[union-attr]

    # ------------------------------------------------------------------ #
    # Wiring
    # ------------------------------------------------------------------ #

    def register(self, application: Application) -> None:
        """Register all handlers onto the PTB application."""
        # Keep a handle so the permission-policy notifier can message operators.
        self._app = application
        application.add_handler(CommandHandler("start", self.cmd_start))
        application.add_handler(CommandHandler("help", self.cmd_help))
        application.add_handler(CommandHandler("profile", self.cmd_profile))
        application.add_handler(CommandHandler("reset", self.cmd_reset))
        application.add_handler(CommandHandler("gate", self.cmd_gate_demo))
        application.add_handler(CommandHandler("voice", self.cmd_voice))
        application.add_handler(CommandHandler("radar", self.cmd_radar))
        application.add_handler(CommandHandler("wizard", self.cmd_wizard))
        application.add_handler(CommandHandler("wizard_cancel", self.cmd_wizard_cancel))
        application.add_handler(CommandHandler("onboard", self.cmd_onboard))
        application.add_handler(CommandHandler("onboard_confirm", self.cmd_onboard_confirm))
        application.add_handler(CommandHandler("onboard_cancel", self.cmd_onboard_cancel))
        application.add_handler(CallbackQueryHandler(self.on_callback))
        # Inbound images (photos + image documents) → download to disk, let the
        # brain Read them. Registered before the text catch-all so a captioned
        # photo is handled as an image, not as text.
        application.add_handler(
            MessageHandler(filters.PHOTO | filters.Document.IMAGE, self.on_image)
        )
        # Inbound voice notes → transcribe (STT) → route like text. Registered
        # before the text catch-all, same as images.
        application.add_handler(MessageHandler(filters.VOICE, self.on_voice))
        # Catch-all for plain text (not commands) → stream the brain.
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))


# --------------------------------------------------------------------------- #
# Application factory + entrypoint
# --------------------------------------------------------------------------- #


def build_application(cfg: Config) -> Application:
    """Build a PTB :class:`Application` with all cockpit handlers registered.

    Raises:
        RuntimeError: if ``TELEGRAM_BOT_TOKEN`` is not configured.
    """
    if not cfg.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set — cannot start the cockpit. Inject it via Doppler / env."
        )
    if not cfg.telegram_allowed_chat_ids:
        # Not fatal (the whitelist also fails closed at request time), but the
        # operator almost certainly forgot to set it — warn loudly.
        logger.warning(
            "TELEGRAM_ALLOWED_CHAT_ID is empty — the cockpit will reject ALL "
            "updates (fail-closed). Set it to your chat id to use the bot."
        )

    application = Application.builder().token(cfg.telegram_bot_token).build()
    Cockpit(cfg).register(application)
    return application


def main() -> None:
    """Console entrypoint: build the cockpit and run long-polling.

    ``run_polling`` is PTB v21's synchronous wrapper — it creates and owns the
    asyncio event loop, runs ``initialize → start → updater.start_polling``, and
    blocks until SIGINT/SIGTERM, then shuts everything down cleanly. We do NOT
    wrap this in ``asyncio.run`` — that would nest event loops.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # Quiet PTB's very chatty HTTP logs at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)

    cfg = Config.from_env(repo_root=Path(__file__).resolve().parent.parent)
    application = build_application(cfg)

    logger.info(
        "Cockpit starting (default profile=%s, allowed chats=%s)",
        cfg.default_profile,
        sorted(cfg.telegram_allowed_chat_ids) or "<none>",
    )
    # allowed_updates left default; drop_pending_updates avoids replaying a
    # backlog accumulated while the bot was down.
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
