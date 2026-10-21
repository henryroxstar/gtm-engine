"""cockpit.base — shared component base for the Cockpit composition root."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram.ext import Application

    from agent.config import Config
    from agent.publish import LinkedInPublisher
    from agent.reply import ReplySender
    from agent.session import SessionStore
    from cockpit.bot import Cockpit
    from telegram import Update


class CockpitComponent:
    """State view over the :class:`cockpit.bot.Cockpit` composition root.

    The root owns ALL mutable state; components hold only the root back-reference
    and read state through these properties at call time — so late rebinding
    (``_app`` is set in ``register()``) and test monkeypatching always observe
    the live objects. Nothing is captured at construction, and every dict below
    stays ONE dict shared by identity across all components.
    """

    def __init__(self, root: Cockpit) -> None:
        self._root = root

    @property
    def cfg(self) -> Config:
        return self._root.cfg

    @property
    def store(self) -> SessionStore:
        return self._root.store

    @property
    def _app(self) -> Application | None:
        return self._root._app

    @property
    def _publisher(self) -> LinkedInPublisher:
        return self._root._publisher

    @property
    def _awaiting_edit(self) -> dict[int, bool]:
        return self._root._awaiting_edit

    @property
    def _pending_publish(self) -> dict[str, dict]:
        return self._root._pending_publish

    @property
    def _reply_sender(self) -> ReplySender:
        return self._root._reply_sender

    @property
    def _pending_reply(self) -> dict[str, dict]:
        return self._root._pending_reply

    @property
    def _pending_gate_run_id(self) -> dict[int, str]:
        return self._root._pending_gate_run_id

    @property
    def _wizard_state(self) -> dict[tuple[int, str], object]:
        return self._root._wizard_state

    def _is_allowed(self, update: Update) -> bool:
        return self._root._is_allowed(update)
