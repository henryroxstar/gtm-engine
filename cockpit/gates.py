"""cockpit.gates — the two human gates' wire vocabulary and handlers.

Gate 1 (plan) and Gate 2 (publish) are the cockpit's permanent human-in-the-loop
approval points. This module owns their sentinels, follow-up directives, the
``callback_data`` prefixes that make up the inline-keyboard wire protocol
(including the ``/voice`` mode prefix — every prefix dispatched by
``on_callback`` lives here, next to its dispatcher), and the handlers that
stage, approve, and cancel gated actions. Publishing remains exactly as locked
in :mod:`agent.publish`: the operator approves the exact bytes, the destination
is pinned server-side, and the brain never holds the secret.
"""

from __future__ import annotations

import html
import logging

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import telegram
from agent.ledgers import Ledgers
from agent.publish import content_hash, parse_publish_block, validate_post
from agent.reply import content_hash as reply_content_hash
from agent.reply import parse_reply_block, validate_reply
from cockpit.base import CockpitComponent
from cockpit.limits import _TELEGRAM_MSG_LIMIT
from gtm_core.outcomes import append_outcome
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

logger = logging.getLogger("cockpit.bot")

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

# --------------------------------------------------------------------------- #
# Reply gate (inbound-triage) — the reply analogue of the publish gate
# --------------------------------------------------------------------------- #
# The inbound-triage skill ends its turn with ⟦GATE:reply⟧ and a delimited block
# carrying the EXACT reply text (+ optional thread id / display context). The
# cockpit parses it, re-displays the exact bytes for the operator to approve, and
# ONLY on an explicit Approve press does the Python reply sender act — which by
# default STAGES the reply for manual send (no auto-send transport configured) and
# logs a `reply` outcome. The brain never sends and never holds a send secret;
# approval is bound to the reply's content hash. callback_data carries a 16-hex token.
_GATE_REPLY_SENTINEL = "⟦GATE:reply⟧"
_REPLY_OK_PREFIX = "rep:ok:"
_REPLY_NO_PREFIX = "rep:no:"

# Reply-mode toggle (/voice): one-tap buttons set the chat's delivery mode.
_VOICE_MODE_PREFIX = "voice:mode:"


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


def _reply_keyboard(token: str) -> InlineKeyboardMarkup:
    """Approve / Cancel keyboard for a staged reply (token binds to the draft)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Approve reply", callback_data=f"{_REPLY_OK_PREFIX}{token}"
                ),
                InlineKeyboardButton("❌ Cancel", callback_data=f"{_REPLY_NO_PREFIX}{token}"),
            ]
        ]
    )


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


class GateHandlers(CockpitComponent):
    """Stage, approve, and cancel the two human gates (plan + publish)."""

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
            reply_markup=_publish_keyboard(token),
        )

    async def _finalize_reply_gate(self, placeholder, header: str, raw: str) -> None:
        """Re-display the EXACT reply for human approval, then attach Approve/Cancel.

        The reply analogue of :meth:`_finalize_publish_gate`. The reply text is treated
        purely as data (it was drafted in response to an untrusted inbound message): the
        gate block is stripped from the agent's visible prose and the exact bytes are
        re-shown in a labelled preview so the operator approves precisely what will be
        logged/sent. Approval is bound to the reply's content hash via a short token.
        """
        chat_id = placeholder.chat_id
        profile = self.store.active_profile(chat_id)
        draft = parse_reply_block(raw)

        visible = raw.split(_GATE_REPLY_SENTINEL, 1)[0].strip() or "Drafted a reply."
        try:
            await placeholder.edit_text((header + visible)[:_TELEGRAM_MSG_LIMIT])
        except telegram.error.BadRequest as exc:
            if "not modified" not in str(exc).lower():
                logger.debug("reply finalize edit_text BadRequest: %s", exc)

        if draft is None:
            await placeholder.reply_text(
                f"{header}⚠️ A reply gate was emitted but no valid reply block was found — "
                "nothing staged. Ask me to redraft."
            )
            return

        reason = validate_reply(draft.body, self._reply_sender.settings.max_chars)
        if reason:
            await placeholder.reply_text(
                f"{header}⚠️ Not staged for reply — {reason}. Ask me to redraft."
            )
            return

        to_line = html.escape(draft.to) if draft.to else "<i>the reply thread</i>"
        preview = (
            f"{header}✉️ <b>Ready to reply</b> to {to_line}:\n\n"
            f"<b>Reply (exact):</b>\n<pre>{html.escape(draft.body)}</pre>\n\n"
            "⚠️ Review the <b>exact</b> text above. Approving logs it and (if an auto-send "
            "transport is configured) sends it as-is. By default nothing auto-sends — you send it."
        )
        if len(preview) > _TELEGRAM_MSG_LIMIT:
            await placeholder.reply_text(
                f"{header}⚠️ The reply is too long to show in full for approval. "
                "Shorten it so the exact content fits one message — nothing staged."
            )
            return

        key = reply_content_hash(draft.body, draft.thread_id)
        token = key[:16]
        self._pending_reply[token] = {
            "body": draft.body,
            "thread_id": draft.thread_id,
            "to": draft.to,
            "hash": key,
            "chat_id": chat_id,
            "profile": profile,
        }
        await placeholder.reply_text(
            preview,
            parse_mode=ParseMode.HTML,
            reply_markup=_reply_keyboard(token),
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
            await self._root._voice_mode._set_voice_mode(
                query, chat_id, data[len(_VOICE_MODE_PREFIX) :]
            )
        elif data and data.startswith(_PUB_OK_PREFIX):
            await self._do_publish(query, chat_id, header, data[len(_PUB_OK_PREFIX) :])
        elif data and data.startswith(_PUB_NO_PREFIX):
            await self._cancel_publish(query, header, data[len(_PUB_NO_PREFIX) :])
        elif data and data.startswith(_REPLY_OK_PREFIX):
            await self._do_reply(query, chat_id, header, data[len(_REPLY_OK_PREFIX) :])
        elif data and data.startswith(_REPLY_NO_PREFIX):
            await self._cancel_reply(query, header, data[len(_REPLY_NO_PREFIX) :])
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
        accumulated = await self._root._delivery._stream_into(
            placeholder, header, chat_id, directive
        )
        if accumulated is None:
            return
        await self._root._delivery._finalize(placeholder, header, accumulated)

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

    async def _do_reply(self, query, chat_id: int, header: str, token: str) -> None:
        """Approve press: stage (default) or send the reply, then log a `reply` outcome.

        The reply analogue of :meth:`_do_publish`. The draft is popped (double-click safe)
        and its content hash re-verified so an approval is bound to its exact draft. By
        default no send transport is configured, so :meth:`agent.reply.ReplySender.send`
        returns ``staged`` — the operator sends it by hand — and either way a ``reply``
        outcome is recorded by Python, never trusted to the brain.
        """
        pending = self._pending_reply.pop(token, None)
        if pending is None:
            await query.message.reply_text(  # type: ignore[union-attr]
                f"{header}↩️ That reply was already handled or expired — nothing sent."
            )
            return

        recomputed = reply_content_hash(pending["body"], pending["thread_id"])
        if recomputed != pending["hash"]:
            logger.warning(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
                "Reply hash mismatch for token=%s — refusing.", token
            )
            await query.message.reply_text(  # type: ignore[union-attr]
                f"{header}⚠️ Draft integrity check failed — reply not handled."
            )
            return

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except telegram.error.BadRequest:
            pass

        result = await self._reply_sender.send(pending["body"], pending["thread_id"])

        # Capture the outcome (learning loop) — a reply outcome regardless of whether the
        # transport sent it or it was staged for manual send; both mean "we replied".
        if result.ok:
            try:
                append_outcome(
                    self.cfg.content_root,
                    pending["profile"],
                    {
                        "channel": "email",
                        "outcome": "reply",
                        "ref": pending["thread_id"] or None,
                        "meta": {"status": result.status, "chars": len(pending["body"])},
                    },
                )
            except Exception:  # noqa: BLE001 — an outcome-log failure must never break the reply
                logger.exception("Failed to write reply outcome for profile=%s", pending["profile"])

        # Audit trail in history.jsonl too (same pattern as publish).
        try:
            Ledgers(self.cfg, pending["profile"]).append_history(
                {
                    "event": "reply_staged" if result.status == "staged" else "reply_sent",
                    "skill": "inbound-triage",
                    "status": result.status,
                    "thread_id": pending["thread_id"] or None,
                    "content_sha256": pending["hash"],
                    "chars": len(pending["body"]),
                }
            )
        except Exception:  # noqa: BLE001 — ledger write must never break the reply
            logger.exception("Failed to write reply history for profile=%s", pending["profile"])

        await query.message.reply_text(f"{header}{result.operator_line()}")  # type: ignore[union-attr]

    async def _cancel_reply(self, query, header: str, token: str) -> None:
        """Cancel press: drop the staged reply without sending anything."""
        self._pending_reply.pop(token, None)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except telegram.error.BadRequest:
            pass
        await query.message.reply_text(f"{header}❌ Reply cancelled — nothing sent.")  # type: ignore[union-attr]
