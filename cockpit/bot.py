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

import asyncio
import functools
import html
import logging
import secrets
from collections.abc import MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

import telegram
from agent import voice
from agent.cockpit_state import CockpitSettings, CockpitState, PendingStore
from agent.config import Config
from agent.denial_log import denial_detail, make_denial_sink, record_permission_event
from agent.permissions import make_cockpit_can_use_tool
from agent.publish import (
    LinkedInPublisher,
    PublishSettings,
)
from agent.reply import ReplySender, ReplySettings
from agent.session import SessionStore
from cockpit import epoch, health
from cockpit.chatqueue import ChatQueue, PerChatProcessor
from cockpit.commands import CommandHandlers
from cockpit.delivery import DeliveryPipeline
from cockpit.gates import GateHandlers
from cockpit.guard import UpdateGuard
from cockpit.hooks import HookUxHandlers
from cockpit.ingress import IngressHandlers
from cockpit.onboarding import OnboardHandlers, WizardHandlers
from cockpit.represent import GateRepresenter
from cockpit.restart import RestartSupport
from cockpit.voice_mode import VoiceModeHandlers
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

__all__ = ["Cockpit", "main", "build_application", "voice"]

logger = logging.getLogger("cockpit.bot")

# Approve-and-resume (P1-6) inline-keyboard wire protocol. Lives on the root because
# the permission ask is part of the security kernel, beside the blocked-tool notifier.
# callback_data carries a 16-hex token binding the press to exactly one pending ask.
_PERM_OK_PREFIX = "perm:ok:"
_PERM_NO_PREFIX = "perm:no:"


class _WizardMap(MutableMapping):
    """The onboarding wizard's state: memory-only by design, but a restart must be able to say it was lost.

    Behaves exactly like the plain dict it replaces; additionally records (identifiers only) which chats
    have a wizard open, so :meth:`Cockpit.boot_announce` can tell the operator it was cancelled.
    """

    def __init__(self, state: CockpitState | None) -> None:
        self._d: dict = {}
        self._state = state

    def __getitem__(self, k):
        return self._d[k]

    def __setitem__(self, k, v) -> None:
        self._d[k] = v
        if self._state is not None:
            self._state.set_volatile("wizard", k[0], True)

    def __delitem__(self, k) -> None:
        del self._d[k]
        if self._state is not None and not any(key[0] == k[0] for key in self._d):
            self._state.set_volatile("wizard", k[0], False)

    def __iter__(self):
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)


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
        # F1 (always-on hardening): durable state. With no content root at all (a bare test config) the
        # cockpit keeps the pre-hardening plain-dict, in-memory behaviour.
        self._settings = CockpitSettings.from_env()
        content_root = getattr(cfg, "content_root", None)
        self._state: CockpitState | None = CockpitState(content_root) if content_root else None
        self._boot = None
        self._lost: dict[str, list[int]] = {}
        self.drop_pending_updates = False
        self._hb_task: asyncio.Task | None = None
        self._watchdog: health.Watchdog | None = None
        self.store = SessionStore(
            cfg,
            can_use_tool_factory=self._make_can_use_tool,
            state=self._state,
            settings=self._settings,
            notice_sink=self._send_notice,
        )
        self._restart = RestartSupport(self)
        self._queue = ChatQueue()  # F3: per-chat ordering, busy state, buffered text
        self._pending: PendingStore | None = None
        if self._state is not None:
            self._pending = PendingStore(content_root, chat_profile=self.store.active_profile)
            self._pending.load_all()
        # Per-chat flag: the chat pressed "Edit" on a plan gate and the NEXT text
        # message is the edit notes (routed as a revise-and-re-present directive).
        self._awaiting_edit: MutableMapping[int, bool] = (
            self._pending.awaiting_edit if self._pending else {}
        )
        # Per-chat run_id extracted from the last Gate-1 sentinel response — used
        # by the dashboard /pending-run scan and for correlating approvals to runs.
        self._pending_gate_run_id: MutableMapping[int, str] = (
            self._pending.gate_run_id if self._pending else {}
        )
        # The single outbound publisher (account pinned server-side). Holds the
        # publish secret + idempotency/rate state; never handed to the brain.
        self._publisher = LinkedInPublisher(PublishSettings.from_env())
        # token -> pending publish draft awaiting an Approve/Cancel press.
        self._pending_publish: MutableMapping[str, dict] = (
            self._pending.publish if self._pending else {}
        )
        # The inbound-reply sender — inert by default (stages for manual send unless a
        # REPLY_SEND_* transport is deliberately configured). Never handed to the brain.
        self._reply_sender = ReplySender(ReplySettings.from_env())
        # token -> pending reply draft awaiting an Approve/Cancel press.
        self._pending_reply: MutableMapping[str, dict] = (
            self._pending.reply if self._pending else {}
        )
        # token -> asyncio.Future for a pending permission ask (approve-and-resume,
        # P1-6). Resolved by _on_permission_callback; timeout denies (fail closed).
        self._pending_permission: dict[str, asyncio.Future] = {}
        # Wizard state: (chat_id, profile) -> WizardState.  Active only while the
        # operator is mid-wizard; cleared on confirmation or /reset.
        self._wizard_state: MutableMapping[tuple[int, str], object] = _WizardMap(self._state)
        # Handler components — thin views over this root's state (cockpit.base);
        # the root stays the single owner of every dict above.
        self._voice_mode = VoiceModeHandlers(self)
        self._delivery = DeliveryPipeline(self)
        self._gates = GateHandlers(self)
        self._hooks = HookUxHandlers(self)
        self._wizard = WizardHandlers(self)
        self._onboard = OnboardHandlers(self)
        self._ingress = IngressHandlers(self)
        self._commands = CommandHandlers(self)
        # F2/F1-10: the restart guard and the gate re-presenter (only with durable state).
        self._guard: UpdateGuard | None = None
        self._representer: GateRepresenter | None = None
        if self._state is not None and self._pending is not None:
            self._guard = UpdateGuard(
                state=self._state,
                pending=self._pending,
                settings=self._settings,
                send=self._restart.guard_send,
                replay=self._restart.replay_update,
                represent=self._restart.represent_chat,
                is_allowed=self._is_allowed,
                profile_of=self.store.active_profile,
                boot_time=datetime.now(UTC),
            )
            self._representer = GateRepresenter(
                pending=self._pending,
                settings=self._settings,
                send=self._restart.gate_send,
                edit=self._restart.gate_edit,
                is_decided=self._restart.gate_is_decided,
            )

    # -- durable-state surface: thin delegators to cockpit.restart.RestartSupport -----------------------
    async def _send_notice(self, chat_id: int, text: str) -> None:
        await self._restart.send_notice(chat_id, text)

    def _begin_boot(self) -> None:
        self._restart.begin_boot()

    async def boot_announce(self) -> None:
        await self._restart.boot_announce()

    async def _post_init(self, application: Application) -> None:
        await self._restart.post_init(application)

    async def _post_stop(self, application: Application) -> None:
        await self._restart.post_stop(application)

    async def _guard_finish(self, update: Update, context) -> None:
        await self._restart.guard_finish(update, context)

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
        rather than silent. Every denial is also recorded in the bound profile's
        ``denials.jsonl`` (P0-1). When ``APPROVE_RESUME_ENABLED`` is set, an escalated call
        becomes an inline Approve/Deny ask instead (P1-6) — approve runs it, decline or
        timeout denies. The profile is resolved here, at session-build time, which is when
        the store (re)creates this chat's session — so a ``/profile`` switch rebinds both
        the ledger sink and the ask. See :mod:`agent.permissions`.
        """
        profile = self.store.active_profile(chat_id)
        ask = None
        if self.cfg.approval_ask_enabled:
            ask = functools.partial(self._ask_permission, chat_id, profile)
        return make_cockpit_can_use_tool(
            notify=functools.partial(self._notify_blocked_tool, chat_id),
            on_deny=make_denial_sink(self.cfg, profile, "cockpit"),
            ask=ask,
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

    async def _ask_permission(
        self, chat_id: int, profile: str, tool_name: str, tool_input: dict
    ) -> bool | None:
        """Ask THIS operator to approve one escalated tool call (approve-and-resume, P1-6).

        Sends an Approve/Deny inline keyboard showing exactly what the brain wants to run,
        then parks the permission callback on a future until the operator presses a button
        or the timeout lapses. Returns True (approved) / False (declined) / None (timeout,
        or the ask could not be delivered) — the caller treats False/None as deny, so every
        failure mode is fail-closed. Only the escalate class ever reaches here; the
        dangerous-program floor is not askable (see :mod:`agent.permissions`).
        """
        if self._app is None:
            return None
        token = secrets.token_hex(8)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_permission[token] = future
        if self._state is not None:
            self._state.set_volatile("permission", chat_id, True)  # a restart kills this future
        timeout = self.cfg.approval_ask_timeout_s
        detail = denial_detail(tool_name, tool_input)
        text = (
            "🔐 <b>Permission request</b> — the brain wants to run "
            f"<code>{html.escape(tool_name)}</code>:"
            + (f"\n<pre>{html.escape(detail)}</pre>" if detail else "")
            + f"\nAllow it this once? Auto-denies in {int(timeout)}s."
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Allow once", callback_data=epoch.stamp(f"{_PERM_OK_PREFIX}{token}")
                    ),
                    InlineKeyboardButton(
                        "🚫 Deny", callback_data=epoch.stamp(f"{_PERM_NO_PREFIX}{token}")
                    ),
                ]
            ]
        )
        try:
            message = await self._app.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
        except telegram.error.TelegramError:
            self._pending_permission.pop(token, None)
            if self._state is not None:
                self._state.set_volatile("permission", chat_id, False)
            return None  # could not reach the operator ⇒ deny (fail closed)
        try:
            approved: bool | None = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            approved = None
        finally:
            self._pending_permission.pop(token, None)
            if self._state is not None:
                self._state.set_volatile("permission", chat_id, False)
        outcome = (
            "approved" if approved is True else ("declined" if approved is False else "timeout")
        )
        # Audit the operator's decision. An approval exists ONLY in this record; a
        # decline/timeout additionally lands via the callback's on_deny sink
        # (source="cockpit"), distinguishable by the source field.
        record_permission_event(
            self.cfg,
            profile,
            tool_name=tool_name,
            tool_input=tool_input,
            decision="escalate",
            outcome=outcome,
            source="cockpit-ask",
        )
        status = {
            True: "✅ Approved by operator — running.",
            False: "🚫 Denied by operator.",
            None: "⏱ No response — denied (fail closed).",
        }[approved]
        try:
            await message.edit_text(f"{text}\n\n{status}", parse_mode=ParseMode.HTML)
        except telegram.error.TelegramError:
            pass  # cosmetic — the decision already stands
        return approved

    async def _on_permission_callback(self, update: Update) -> None:
        """Resolve a pending permission ask from an Approve/Deny button press."""
        query = update.callback_query
        if query is None:
            return
        data = query.data or ""
        approved = data.startswith(_PERM_OK_PREFIX)
        prefix = _PERM_OK_PREFIX if approved else _PERM_NO_PREFIX
        token = data[len(prefix) :]
        future = self._pending_permission.get(token)
        if future is None or future.done():
            await query.answer("Already handled (or expired).")
            return
        future.set_result(approved)
        await query.answer("Approved — running." if approved else "Denied.")

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

    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/stop`` — delegate to :class:`cockpit.commands.CommandHandlers`."""
        await self._commands.cmd_stop(update, context)

    async def cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/reset`` — delegate to :class:`cockpit.commands.CommandHandlers`."""
        await self._commands.cmd_reset(update, context)

    async def cmd_radar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/radar`` — delegate to :class:`cockpit.commands.CommandHandlers`."""
        await self._commands.cmd_radar(update, context)

    async def cmd_hooks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/hooks`` — delegate to :class:`cockpit.hooks.HookUxHandlers`."""
        await self._hooks.cmd_hooks(update, context)

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

    async def cmd_cost(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/cost`` — delegate to :class:`cockpit.commands.CommandHandlers`."""
        await self._commands.cmd_cost(update, context)

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Inline-keyboard dispatch — permission asks resolve here (security kernel on
        the root); hook-library actions go to :class:`cockpit.hooks.HookUxHandlers`;
        everything else delegates to :class:`cockpit.gates.GateHandlers`."""
        query = update.callback_query
        data = query.data if query is not None else None
        if data and data.startswith((_PERM_OK_PREFIX, _PERM_NO_PREFIX)):
            if not self._is_allowed(update):
                return
            await self._on_permission_callback(update)
            return
        if data and data.startswith("hooks:"):
            if not self._is_allowed(update):
                return
            await self._hooks.on_callback(update, context)
            return
        await self._gates.on_callback(update, context)

    # ------------------------------------------------------------------ #
    # Wiring
    # ------------------------------------------------------------------ #

    def registered_commands(self) -> list[tuple[str, Any]]:
        """Return the (command_name, handler_callback) pairs registered on the bot."""
        return [
            ("start", self.cmd_start),
            ("help", self.cmd_help),
            ("profile", self.cmd_profile),
            ("reset", self.cmd_reset),
            ("gate", self.cmd_gate_demo),
            ("voice", self.cmd_voice),
            ("radar", self.cmd_radar),
            ("hooks", self.cmd_hooks),
            ("wizard", self.cmd_wizard),
            ("wizard_cancel", self.cmd_wizard_cancel),
            ("onboard", self.cmd_onboard),
            ("onboard_confirm", self.cmd_onboard_confirm),
            ("onboard_cancel", self.cmd_onboard_cancel),
            ("cost", self.cmd_cost),
            ("stop", self.cmd_stop),
        ]

    def register(self, application: Application) -> None:
        """Register all handlers onto the PTB application."""
        # Keep a handle so the permission-policy notifier can message operators.
        self._app = application
        if self._state is not None and self._boot is None:
            self._begin_boot()  # mint the epoch BEFORE any keyboard is built
        if self._guard is not None:
            # F2: the guard sees every update first (group -1) and records it handled last (group 100).
            application.add_handler(TypeHandler(Update, self._guard.pre), group=-1)
            application.add_handler(TypeHandler(Update, self._guard_finish), group=100)
        for cmd, handler in self.registered_commands():
            application.add_handler(CommandHandler(cmd, handler))
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

    cockpit = Cockpit(cfg)
    builder = (
        Application.builder()
        .token(cfg.telegram_bot_token)
        .post_init(cockpit._post_init)
        .post_stop(cockpit._post_stop)
    )
    if cockpit._settings.per_chat_queue:
        # F3: one ordered lane per chat; buttons and /stop bypass it. COCKPIT_PER_CHAT_QUEUE=0 restores PTB's
        # default one-update-at-a-time processing (and with it, no batching and no /stop during a turn).
        builder = builder.concurrent_updates(PerChatProcessor(cockpit._queue))
    application = builder.build()
    cockpit.register(application)
    # run_polling is told whether to delete the backlog: only in safe mode (a crash loop), or when the
    # COCKPIT_KEEP_PENDING_UPDATES kill switch is off. Otherwise Telegram's own queue keeps messages
    # sent while we were down and the F2 guard decides what to do with each.
    application.bot_data["drop_pending_updates"] = cockpit.drop_pending_updates
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
    # allowed_updates left default. The backlog is KEPT (F2): old content is digested for a human tap and
    # old commands are refused by the guard; only safe mode (or the kill switch) discards it.
    application.run_polling(
        drop_pending_updates=bool(application.bot_data.get("drop_pending_updates", False))
    )


if __name__ == "__main__":
    main()
