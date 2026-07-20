"""cockpit.bot — composition root for the python-telegram-bot (v21+) cockpit.

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
the bot cannot be enumerated or driven by strangers. The whitelist guard and the
least-privilege blocked-tool notifier live HERE, on the root — they are the
security kernel every handler module reads through :class:`cockpit.base.
CockpitComponent`.

Composition root
----------------
:class:`Cockpit` owns all mutable state (the session store, the publisher, and
the per-chat gate/edit/wizard dicts) and wires one component per concern; the
handler bodies live in sibling modules and read the root's live state at call
time (see :mod:`cockpit.base`):

- :mod:`cockpit.commands`   — /start /help /profile /reset /radar /gate
- :mod:`cockpit.voice_mode` — TTS replies + the /voice reply-mode picker
- :mod:`cockpit.onboarding` — the /wizard Q&A and /onboard staging flows
- :mod:`cockpit.ingress`    — inbound text / image / voice (STT) surfaces
- :mod:`cockpit.delivery`   — streaming, gate-aware finalize, ⟦FILE:…⟧ delivery
- :mod:`cockpit.gates`      — Gate 1 (plan) + Gate 2 (publish) vocabulary,
  ``on_callback`` dispatch, and the approve/cancel handlers

The registered handlers stay two-line delegators on :class:`Cockpit`, so the
public surface (``cockpit.bot.Cockpit``, ``cockpit.bot.main``, ``python -m
cockpit.bot``, the ``gtm-cockpit`` entry point) is unchanged by the split.

This module uses ``import telegram`` / ``from telegram.ext import ...`` — clean
because this package is ``cockpit``, not ``telegram``.
"""

from __future__ import annotations

import functools
import html
import logging
from pathlib import Path

from telegram.constants import ParseMode
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
from agent.permissions import make_cockpit_can_use_tool
from agent.publish import (
    LinkedInPublisher,
    PublishSettings,
)
from agent.session import SessionStore
from cockpit.commands import CommandHandlers
from cockpit.delivery import DeliveryPipeline
from cockpit.gates import GateHandlers
from cockpit.ingress import IngressHandlers
from cockpit.onboarding import OnboardHandlers, WizardHandlers
from cockpit.voice_mode import VoiceModeHandlers
from telegram import Update

__all__ = ["Cockpit", "main", "build_application", "voice"]

logger = logging.getLogger("cockpit.bot")

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
        # Handler components — thin views over this root's state (cockpit.base);
        # the root stays the single owner of every dict above.
        self._voice_mode = VoiceModeHandlers(self)
        self._delivery = DeliveryPipeline(self)
        self._gates = GateHandlers(self)
        self._wizard = WizardHandlers(self)
        self._onboard = OnboardHandlers(self)
        self._ingress = IngressHandlers(self)
        self._commands = CommandHandlers(self)

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
    # Slash commands (cockpit.commands) + /voice (cockpit.voice_mode)
    # ------------------------------------------------------------------ #

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/start`` — delegate to :class:`cockpit.commands.CommandHandlers`."""
        await self._commands.cmd_start(update, context)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/help`` — delegate to :class:`cockpit.commands.CommandHandlers`."""
        await self._commands.cmd_help(update, context)

    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/profile`` — delegate to :class:`cockpit.commands.CommandHandlers`."""
        await self._commands.cmd_profile(update, context)

    async def cmd_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/voice`` — delegate to :class:`cockpit.voice_mode.VoiceModeHandlers`."""
        await self._voice_mode.cmd_voice(update, context)

    async def cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/reset`` — delegate to :class:`cockpit.commands.CommandHandlers`."""
        await self._commands.cmd_reset(update, context)

    async def cmd_radar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/radar`` — delegate to :class:`cockpit.commands.CommandHandlers`."""
        await self._commands.cmd_radar(update, context)

    # ------------------------------------------------------------------ #
    # /wizard + /onboard — profile creation (cockpit.onboarding)
    # ------------------------------------------------------------------ #

    async def cmd_wizard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/wizard`` — delegate to :class:`cockpit.onboarding.WizardHandlers`."""
        await self._wizard.cmd_wizard(update, context)

    async def cmd_wizard_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/wizard_cancel`` — delegate to :class:`cockpit.onboarding.WizardHandlers`."""
        await self._wizard.cmd_wizard_cancel(update, context)

    async def cmd_onboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/onboard`` — delegate to :class:`cockpit.onboarding.OnboardHandlers`."""
        await self._onboard.cmd_onboard(update, context)

    async def cmd_onboard_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/onboard_confirm`` — delegate to :class:`cockpit.onboarding.OnboardHandlers`."""
        await self._onboard.cmd_onboard_confirm(update, context)

    async def cmd_onboard_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/onboard_cancel`` — delegate to :class:`cockpit.onboarding.OnboardHandlers`."""
        await self._onboard.cmd_onboard_cancel(update, context)

    # ------------------------------------------------------------------ #
    # Message ingress — text / image / voice (cockpit.ingress)
    # ------------------------------------------------------------------ #

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Text ingress — delegate to :class:`cockpit.ingress.IngressHandlers`."""
        await self._ingress.on_text(update, context)

    async def on_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Image ingress — delegate to :class:`cockpit.ingress.IngressHandlers`."""
        await self._ingress.on_image(update, context)

    async def on_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """CSV document ingress — delegate to :class:`cockpit.ingress.IngressHandlers`."""
        await self._ingress.on_document(update, context)

    async def on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Voice ingress (STT) — delegate to :class:`cockpit.ingress.IngressHandlers`."""
        await self._ingress.on_voice(update, context)

    # ------------------------------------------------------------------ #
    # Inline approval gate (Phase 1, Gate 1 skeleton)
    # ------------------------------------------------------------------ #

    async def cmd_gate_demo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/gate`` — delegate to :class:`cockpit.commands.CommandHandlers`."""
        await self._commands.cmd_gate_demo(update, context)

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Inline-keyboard dispatch — delegate to :class:`cockpit.gates.GateHandlers`."""
        await self._gates.on_callback(update, context)

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
        # Inbound CSV exports (bulk-mode prospect discovery, Phase 1.5 receiver) →
        # download to disk under prospects/imports/, let the skill ingest it.
        # Registered before the text catch-all, same as images.
        application.add_handler(
            MessageHandler(
                filters.Document.FileExtension("csv") | filters.Document.MimeType("text/csv"),
                self.on_document,
            )
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
