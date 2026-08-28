"""cockpit.onboarding — profile creation flows: the /wizard Q&A and /onboard ingestion.

Two ways a new company profile is born from Telegram: the 6-question wizard
(answers collected as plain follow-up messages, intercepted by
``_handle_wizard_answer`` before they reach the brain) and the ``/onboard``
URL/text ingestion that stages a draft for explicit ``/onboard_confirm``
promotion. Neither flow touches the brain session — the active profile binding
is unaffected until the operator switches.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from cockpit.base import CockpitComponent
from telegram import Update

logger = logging.getLogger("cockpit.bot")


class WizardHandlers(CockpitComponent):
    """The 6-question onboarding wizard and its message interception."""

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


class OnboardHandlers(CockpitComponent):
    """URL/text ingestion → staged profile draft → confirm/cancel promotion."""

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
                    "Example:\n/onboard https://acme.com\n\n"
                    "Other ways to onboard: see docs/onboarding-surfaces.md"
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
