"""cockpit.delivery — streaming, gate-aware finalization, and file delivery.

The shared reply pipeline behind every brain run: stream ``store.run`` into one
edited-in-place Telegram message, then finalize — route gate sentinels to the
gate handlers, validate and send ⟦FILE:…⟧ deliverables, and honour the
voice-only reply mode. Ingress handlers, ``/radar``, and the gate directives all
terminate here so gates and file sentinels behave identically however the run
was started.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from pathlib import Path

from telegram.ext import ContextTypes

import telegram
from cockpit.base import CockpitComponent
from cockpit.gates import _GATE_PLAN_SENTINEL, _GATE_PUBLISH_SENTINEL, _plan_gate_keyboard
from cockpit.limits import _STREAM_EDIT_INTERVAL_S, _STREAM_SOFT_LIMIT, _TELEGRAM_MSG_LIMIT

logger = logging.getLogger("cockpit.bot")

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


class DeliveryPipeline(CockpitComponent):
    """Stream → finalize → deliver: the shared tail of every brain run."""

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
        self._root._voice_mode._maybe_voice_reply(chat_id, accumulated, context)

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
            await self._root._gates._finalize_publish_gate(placeholder, header, raw)
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
        keyboard = _plan_gate_keyboard() if gated else None
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
