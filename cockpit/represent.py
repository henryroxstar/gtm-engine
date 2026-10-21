"""cockpit.represent — re-present pending gates after a restart (F1-10, always-on hardening PRD).

F1 makes pending approvals survive a restart; F2 refuses any button issued before it. So a pending gate is
re-presented once, with fresh current-epoch buttons, and the old message is edited to say it was
superseded. But only a gate that is still *real* is re-sent — its authoritative record is checked first:

* older than ``COCKPIT_PENDING_MAX_AGE_DAYS`` (7), or a schedule that has already passed → **dropped**;
* already decided elsewhere (the content hash is in the published ledger / a reply was logged / the plan's
  run manifest no longer awaits approval) → **dropped**;
* otherwise re-sent — at most once per boot epoch, and (at boot) no more than once per 30 minutes per gate,
  so a crash loop cannot flood the chat. Every drop is a one-line notice, never a silent disappearance.

The re-sent preview is the stored HTML from staging, so the operator approves the *same exact bytes*.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agent.cockpit_state import CockpitSettings, PendingStore
from cockpit import copy, epoch

logger = logging.getLogger("cockpit.bot")

_RESPACING = timedelta(minutes=30)
_TS = "%Y-%m-%dT%H:%M:%SZ"


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, _TS).replace(tzinfo=UTC)
    except ValueError:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None


@dataclass
class RepresentReport:
    sent: int = 0
    dropped: list[str] = field(default_factory=list)  # profile names, in order, de-duplicated

    def _drop(self, profile: str) -> None:
        if profile not in self.dropped:
            self.dropped.append(profile)


def _utc_now(now: datetime) -> str:
    return now.astimezone(UTC).strftime(_TS)


def _keyboard(kind: str, token: str):
    # Imported lazily: cockpit.gates imports this package's siblings at module load.
    from cockpit.gates import _plan_gate_keyboard, _publish_keyboard, _reply_keyboard

    if kind == "publish":
        return _publish_keyboard(token)
    if kind == "reply":
        return _reply_keyboard(token)
    return _plan_gate_keyboard()


class GateRepresenter:
    def __init__(
        self,
        *,
        pending: PendingStore,
        settings: CockpitSettings,
        send: Callable[..., Awaitable[int | None]],
        edit: Callable[..., Awaitable[None]],
        is_decided: Callable[[str, dict, str], bool],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._pending = pending
        self._settings = settings
        self._send = send
        self._edit = edit
        self._is_decided = is_decided
        self._clock = clock or (lambda: datetime.now(UTC))

    def dropped_notice(self, profile: str) -> str:
        return copy.DROPPED_GATE.format(profile=profile, days=self._settings.pending_max_age_days)

    # ------------------------------------------------------------------ #

    def _gates(self, chat_id: int | None):
        """Yield ``(kind, token, entry, text, mode)`` for every pending gate (optionally one chat's)."""
        for token, e in list(self._pending.publish.items()):
            if chat_id is None or e.get("chat_id") == chat_id:
                yield "publish", token, e, e.get("preview"), "HTML"
        for token, e in list(self._pending.reply.items()):
            if chat_id is None or e.get("chat_id") == chat_id:
                yield "reply", token, e, e.get("preview"), "HTML"
        for _key, rec in list(self._pending.presented.items()):
            if rec.get("kind") == "plan" and (chat_id is None or rec.get("chat_id") == chat_id):
                yield "plan", rec["token"], rec, rec.get("text"), None

    def _forget(self, kind: str, token: str) -> None:
        if kind == "publish":
            self._pending.publish.pop(token, None)
        elif kind == "reply":
            self._pending.reply.pop(token, None)
        elif kind == "plan":
            pass  # the presented record IS the plan gate; cleared below
        self._pending.clear_presented(kind, token)

    @staticmethod
    def _shows_the_bytes(kind: str, entry: dict) -> bool:
        """The stored preview must actually contain what approving would send.

        Persisted pending state is untrusted when read back (the brain's file tools could once have written
        it). The operator approves what the preview SHOWS, so a preview that does not contain the entry's post
        (and every media url that would ride along) is never re-sent.
        """
        preview = entry.get("preview") or ""
        if kind == "publish":
            needles = [entry.get("post") or "", *(entry.get("media_urls") or [])]
        elif kind == "reply":
            needles = [entry.get("body") or ""]
        else:
            return True
        return bool(needles[0]) and all(html.escape(str(n)) in preview for n in needles if n)

    def _drop_reason(self, kind: str, entry: dict, profile: str, now: datetime) -> bool:
        """True when the gate must be dropped rather than re-presented."""
        if not self._shows_the_bytes(kind, entry):
            return True
        max_age = timedelta(days=self._settings.pending_max_age_days)
        born = _parse(entry.get("staged_at") if kind != "plan" else entry.get("first_at"))
        if born is not None and now - born > max_age:
            return True
        if kind == "publish":
            when = _parse(entry.get("scheduled_at"))
            if when is not None and when < now:
                return True
        return bool(self._is_decided(kind, entry, profile))

    async def _present(
        self, kind: str, token: str, entry: dict, text: str, mode: str | None
    ) -> bool:
        chat_id = entry["chat_id"]
        profile = entry["profile"]
        old = self._pending.presented_for(kind, token)
        if old and old.get("message_id") and old.get("epoch") != epoch.current():
            try:
                await self._edit(chat_id, old["message_id"], f"{text}\n\n{copy.SUPERSEDED}", mode)
            except Exception:  # noqa: BLE001 — cosmetic; the new message below is what matters
                logger.debug("could not mark the old gate message superseded", exc_info=True)
        message_id = await self._send(
            chat_id,
            f"{copy.RESENT}\n{text}",
            reply_markup=_keyboard(kind, token),
            parse_mode=mode,
        )
        if message_id is not None:
            extra = {"resent_at": _utc_now(self._clock())}
            if kind == "plan":
                extra["text"] = text
            self._pending.record_presented(
                kind,
                token,
                chat_id=chat_id,
                message_id=message_id,
                profile=profile,
                epoch=epoch.current(),
                extra=extra,
            )
        return True

    async def _run(self, chat_id: int | None, *, respace: bool) -> RepresentReport:
        report = RepresentReport()
        now = self._clock()
        todo: list[tuple[str, str, dict, str, str | None]] = []
        for kind, token, entry, text, mode in self._gates(chat_id):
            profile = entry.get("profile", "")
            if not text or self._drop_reason(kind, entry, profile, now):
                # no stored preview = we cannot show the exact bytes again, so we never invent them
                self._forget(kind, token)
                report._drop(profile)
                continue
            prev = self._pending.presented_for(kind, token)
            if prev is not None:
                if prev.get("epoch") == epoch.current():
                    continue  # already re-presented in this epoch
                last = _parse(prev.get("resent_at"))
                if respace and last is not None and now - last < _RESPACING:
                    continue
            todo.append((kind, token, entry, text, mode))

        introduced: set[tuple[int, str]] = set()
        for kind, token, entry, text, mode in todo:
            key = (entry["chat_id"], entry["profile"])
            if key not in introduced:
                introduced.add(key)
                n = sum(1 for t in todo if (t[2]["chat_id"], t[2]["profile"]) == key)
                await self._send(
                    entry["chat_id"], copy.REPRESENT_INTRO.format(n=n, profile=entry["profile"])
                )
            if await self._present(kind, token, entry, text, mode):
                report.sent += 1
        return report

    async def represent_all(self) -> RepresentReport:
        """Boot-time pass over every pending gate (respects the 30-minute spacing)."""
        return await self._run(None, respace=True)

    async def represent_chat(self, chat_id: int) -> RepresentReport:
        """A stale button was pressed: re-send that chat's gates now (still at most once per epoch)."""
        return await self._run(chat_id, respace=False)
