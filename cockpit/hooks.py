"""cockpit.hooks — operator UX for the hook library.

Displays hook counts, fatigued hooks, and distiller promotion/demotion candidates
with inline-keyboard actions that mutate the profile's hooks.toml.
"""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from cockpit.base import CockpitComponent
from gtm_core import hooks as hk
from gtm_core.paths import resolve_content_root, resolve_profiles_root
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

logger = logging.getLogger("cockpit.bot")

_CALLBACK_PREFIX = "hooks:"
_CB_REVIVE = _CALLBACK_PREFIX + "revive:"
_CB_PROMOTE = _CALLBACK_PREFIX + "promote:"
_CB_DEMOTE = _CALLBACK_PREFIX + "demote:"


def _models_dir(cfg, profile: str) -> Path | None:
    """Return content/<profile>/models/ if it exists."""
    try:
        root = resolve_content_root()
    except RuntimeError:
        return None
    path = root / profile / "models"
    return path if path.is_dir() else None


def _load_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class HookUxHandlers(CockpitComponent):
    """Slash command + callbacks for hook-library operator UX."""

    async def cmd_hooks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/hooks`` — show hook counts, fatigue, and distiller candidates."""
        if not self._is_allowed(update):
            return
        chat_id = update.effective_chat.id  # type: ignore[union-attr]
        profile = self.store.active_profile(chat_id)

        profiles_root = resolve_profiles_root()
        content_root = resolve_content_root()

        try:
            bank = hk.load_hooks(profiles_root, profile, content_root=content_root)
        except FileNotFoundError:
            await update.message.reply_text(  # type: ignore[union-attr]
                f"[{html.escape(profile)}] No hooks.toml found. Run "
                "<code>python -m gtm_core.hooks migrate --profile &lt;profile&gt;</code> first.",
                parse_mode=ParseMode.HTML,
            )
            return

        counts: dict[str, int] = {}
        for h in bank.hooks:
            counts[h.status] = counts.get(h.status, 0) + 1

        fatigued: list[hk.Hook] = []
        for h in bank.hooks:
            if h.status == "candidate":
                continue
            if hk.is_fatigued(content_root, profile, h.id, bank):
                fatigued.append(h)

        models = _models_dir(self.cfg, profile)
        promote_candidates: list[dict] = []
        demote_candidates: list[dict] = []
        if models is not None:
            promote_candidates = _load_json(models / "promote_candidates.json") or []
            demote_candidates = _load_json(models / "demote_candidates.json") or []

        lines: list[str] = [
            f"📚 <b>Hook library — {html.escape(profile)}</b>",
            "",
            "<b>Counts</b>",
        ]
        for status in ("proven", "test", "candidate", "banned"):
            n = counts.get(status, 0)
            lines.append(f"• {status}: <b>{n}</b>")

        if fatigued:
            lines.extend(["", "<b>Fatigued</b> — click Revive to reset the window"])
            for h in fatigued:
                lines.append(
                    f"• <code>{html.escape(h.id)}</code> "
                    f"({h.max_impressions} impr / {h.fatigue_window_days}d)"
                )
        else:
            lines.extend(["", "<b>Fatigued</b>: none"])

        if promote_candidates:
            lines.extend(["", "<b>Promote candidates</b>"])
            for c in promote_candidates[:5]:
                rate = c.get("engagement_rate")
                base = c.get("baseline_rate")
                lines.append(
                    f"• <code>{html.escape(c.get('hook_id', '?'))}</code> "
                    f"ER {rate} vs baseline {base}"
                )

        if demote_candidates:
            lines.extend(["", "<b>Demote candidates</b>"])
            for c in demote_candidates[:5]:
                rate = c.get("engagement_rate")
                base = c.get("baseline_rate")
                lines.append(
                    f"• <code>{html.escape(c.get('hook_id', '?'))}</code> "
                    f"ER {rate} vs baseline {base}"
                )

        keyboard: list[list[InlineKeyboardButton]] = []
        for h in fatigued:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"Revive {h.id}",
                        callback_data=f"{_CB_REVIVE}{h.id}",
                    )
                ]
            )
        for c in promote_candidates[:5]:
            hid = c.get("hook_id")
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"Promote {hid}",
                        callback_data=f"{_CB_PROMOTE}{hid}",
                    )
                ]
            )
        for c in demote_candidates[:5]:
            hid = c.get("hook_id")
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"Demote {hid}",
                        callback_data=f"{_CB_DEMOTE}{hid}",
                    )
                ]
            )

        await update.message.reply_text(  # type: ignore[union-attr]
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
        )

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle revive / promote / demote button presses from ``/hooks``."""
        if not self._is_allowed(update):
            return
        query = update.callback_query
        if query is None:
            return
        data = query.data or ""
        if not data.startswith(_CALLBACK_PREFIX):
            await query.answer("Unknown action.")
            return

        chat_id = update.effective_chat.id  # type: ignore[union-attr]
        profile = self.store.active_profile(chat_id)
        profiles_root = resolve_profiles_root()
        content_root = resolve_content_root()

        try:
            if data.startswith(_CB_REVIVE):
                hook_id = data[len(_CB_REVIVE) :]
                hk.revive_hook(
                    profiles_root, content_root, profile, hook_id, "operator revived from cockpit"
                )
                await query.answer(f"Revived {hook_id}.")
                await query.edit_message_text(
                    f"✅ <code>{html.escape(hook_id)}</code> revived. "
                    "Its fatigue window resets now.",
                    parse_mode=ParseMode.HTML,
                )
            elif data.startswith(_CB_PROMOTE):
                hook_id = data[len(_CB_PROMOTE) :]
                hk.promote_hook(
                    profiles_root,
                    content_root,
                    profile,
                    hook_id,
                    "operator promoted from cockpit based on distiller candidate",
                )
                await query.answer(f"Promoted {hook_id}.")
                await query.edit_message_text(
                    f"✅ <code>{html.escape(hook_id)}</code> promoted to proven.",
                    parse_mode=ParseMode.HTML,
                )
            elif data.startswith(_CB_DEMOTE):
                hook_id = data[len(_CB_DEMOTE) :]
                hk.demote_hook(
                    profiles_root,
                    content_root,
                    profile,
                    hook_id,
                    "operator demoted from cockpit based on distiller candidate",
                )
                await query.answer(f"Demoted {hook_id}.")
                await query.edit_message_text(
                    f"✅ <code>{html.escape(hook_id)}</code> demoted to test.",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await query.answer("Unknown action.")
        except ValueError as exc:
            logger.warning("Hook UX action failed for profile=%s: %s", profile, exc)
            await query.answer(str(exc))
            await query.edit_message_text(
                f"❌ {html.escape(str(exc))}",
                parse_mode=ParseMode.HTML,
            )
