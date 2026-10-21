"""Durable cockpit state (always-on hardening PRD 2026-10-19, F1).

Two JSON stores, written atomically (temp file + rename) under one writer lock per store and validated
with :mod:`gtm_core.minischema` on read. JSON, not an object-serialization persistence class: those execute
code on load and are not inspectable (PRD D6).

* :class:`CockpitState` — ``content/_system/cockpit-state.json``. **Identifiers only, no content**: the
  chat→profile binding, ``(chat, profile) → session_id``, the handled / in-flight ``update_id`` set, the boot
  epoch and the boot timestamps used by safe mode. ``_system`` is cross-tenant, so nothing that names a
  prospect or carries post/reply text may ever be written here (tested with canary strings).
* :class:`PendingStore` — ``content/<profile>/_cockpit/pending.json``. Everything that carries content or
  prospect data: pending publish/reply drafts, the plan-gate run id, the awaiting-edit flag, the Telegram
  message ids of presented gates and the offline digest. It lives in the profile's tree because it is tenant
  content (§R9), and is therefore inside the ``gtm-content`` backup.

Both degrade, never crash: a missing file is an empty state; a corrupt or wrong-shape file is an empty state
**and the operator is told** (``NOTICE_*``). Nothing blocks the chat.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gtm_core.minischema import validate
from gtm_core.paths import _safe_segment

logger = logging.getLogger("agent.cockpit_state")

# --------------------------------------------------------------------------- #
# Operator-facing strings (contract: asserted verbatim in tests/unit/test_cockpit_state.py)
# --------------------------------------------------------------------------- #
NOTICE_CORRUPT_PENDING = (
    "I couldn't read my saved approvals — anything waiting may need to be re-run; check the plan"
)
NOTICE_FOREIGN_PENDING = (
    "I ignored a saved approval that did not belong to the profile it was stored under"
)
NOTICE_FAILED_RESUME = "I started a fresh conversation — I couldn't restore the old one"
NOTICE_EXPIRED_SESSION = (
    "fresh conversation — the old one was idle for more than 7 days or reached 500 turns"
)

SAFE_MODE_WINDOW = timedelta(minutes=10)
SAFE_MODE_BOOTS = 3
_HANDLED_KEEP = 200

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

_STR_MAP = {"type": "object", "additionalProperties": {"type": "string"}}
STATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["version"],
    "properties": {
        "version": {"type": "integer"},
        "bindings": _STR_MAP,
        "sessions": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["session_id", "turns", "last_used"],
                "properties": {
                    "session_id": {"type": "string", "minLength": 1},
                    "turns": {"type": "integer", "minimum": 0},
                    "last_used": {"type": "string"},
                },
            },
        },
        "handled": {"type": "array", "items": {"type": "integer"}},
        "last_update_id": {"type": "integer"},
        "in_flight": {"type": "array", "items": {"type": "integer"}},
        "epoch": {"type": "string"},
        "boots": {"type": "array", "items": {"type": "string"}},
        "volatile": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "integer"}},
        },
    },
}


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    return datetime.now(UTC)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path, schema: dict) -> tuple[dict | None, str]:
    """Return ``(data, status)``; status is ``ok`` | ``missing`` | ``corrupt``. Never raises."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        logger.warning("cockpit state unreadable: %s", path.name)
        return None, "corrupt"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        logger.warning("cockpit state is not valid JSON/UTF-8: %s", path.name)
        return None, "corrupt"
    errors = validate(data, schema)
    if errors:
        logger.warning("cockpit state failed its schema (%s): %s", path.name, errors[0])
        return None, "corrupt"
    return data, "ok"


# --------------------------------------------------------------------------- #
# System store (identifiers only)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SessionEntry:
    session_id: str
    turns: int
    last_used: datetime


@dataclass(frozen=True)
class ResumeDecision:
    session_id: str | None
    notice: str | None


@dataclass(frozen=True)
class BootInfo:
    epoch: str
    safe_mode: bool
    poison_ids: frozenset[int]


class CockpitState:
    """``content/_system/cockpit-state.json`` — see the module docstring."""

    def __init__(self, content_root: Path, clock: Callable[[], datetime] | None = None) -> None:
        self._path = Path(content_root) / "_system" / "cockpit-state.json"
        self._clock = clock or _now
        self._lock = threading.RLock()
        self._poison: set[int] = set()
        data, self.read_status = _read(self._path, STATE_SCHEMA)
        d = data or {}
        self._bindings: dict[str, str] = dict(d.get("bindings", {}))
        self._sessions: dict[str, dict] = {k: dict(v) for k, v in d.get("sessions", {}).items()}
        self._handled: list[int] = list(d.get("handled", []))
        self._in_flight: list[int] = list(d.get("in_flight", []))
        self.epoch: str = d.get("epoch", "")
        self._boots: list[str] = list(d.get("boots", []))
        self._volatile: dict[str, list[int]] = {
            k: list(v) for k, v in d.get("volatile", {}).items()
        }

    # -- persistence --------------------------------------------------------
    def _flush(self) -> None:
        payload = {
            "version": 1,
            "bindings": self._bindings,
            "sessions": self._sessions,
            "handled": self._handled[-_HANDLED_KEEP:],
            "in_flight": self._in_flight,
            "epoch": self.epoch,
            "boots": self._boots,
            "volatile": {k: v for k, v in self._volatile.items() if v},
        }
        if self._handled:
            payload["last_update_id"] = max(self._handled)
        try:
            _atomic_write(self._path, payload)
        except OSError as exc:
            # In-memory state still applies for this process; only cross-restart durability is lost.
            logger.warning("could not persist cockpit state: %s", exc)

    # -- bindings -----------------------------------------------------------
    def binding(self, chat_id: int) -> str | None:
        with self._lock:
            return self._bindings.get(str(chat_id))

    def set_binding(self, chat_id: int, profile: str) -> None:
        with self._lock:
            self._bindings[str(chat_id)] = profile
            self._flush()

    # -- sessions -----------------------------------------------------------
    @staticmethod
    def _skey(chat_id: int, profile: str) -> str:
        return f"{chat_id}|{profile}"

    def session_for(self, chat_id: int, profile: str) -> SessionEntry | None:
        with self._lock:
            e = self._sessions.get(self._skey(chat_id, profile))
            if not e:
                return None
            used = _parse(e.get("last_used", "")) or datetime.fromtimestamp(0, UTC)
            return SessionEntry(e["session_id"], int(e["turns"]), used)

    @staticmethod
    def valid_session_id(session_id: str) -> bool:
        if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
            return False
        try:
            _safe_segment(session_id, "session_id")
        except (ValueError, TypeError):
            return False
        return True

    def record_session(
        self, chat_id: int, profile: str, session_id: str, turns_added: int = 1
    ) -> None:
        if not self.valid_session_id(session_id):
            logger.warning("refusing to store a malformed session id")
            return
        with self._lock:
            key = self._skey(chat_id, profile)
            prev = self._sessions.get(key)
            turns = (
                prev["turns"] if prev and prev["session_id"] == session_id else 0
            ) + turns_added
            self._sessions[key] = {
                "session_id": session_id,
                "turns": turns,
                "last_used": _utc(self._clock()),
            }
            self._flush()

    def drop_session(self, chat_id: int, profile: str) -> None:
        with self._lock:
            if self._sessions.pop(self._skey(chat_id, profile), None) is not None:
                self._flush()

    def resume_decision(
        self, chat_id: int, profile: str, max_idle_days: int, max_turns: int
    ) -> ResumeDecision:
        entry = self.session_for(chat_id, profile)
        if entry is None:
            return ResumeDecision(None, None)
        idle = self._clock() - entry.last_used
        if idle > timedelta(days=max_idle_days) or entry.turns >= max_turns:
            return ResumeDecision(None, NOTICE_EXPIRED_SESSION)
        return ResumeDecision(entry.session_id, None)

    # -- updates ------------------------------------------------------------
    @property
    def in_flight(self) -> set[int]:
        with self._lock:
            return set(self._in_flight)

    def should_skip(self, update_id: int) -> bool:
        with self._lock:
            return update_id in self._handled or update_id in self._poison

    def begin_update(self, update_id: int) -> None:
        with self._lock:
            if update_id not in self._in_flight:
                self._in_flight.append(update_id)
                self._flush()

    def end_update(self, update_id: int) -> None:
        with self._lock:
            if update_id in self._in_flight:
                self._in_flight.remove(update_id)
            if update_id not in self._handled:
                self._handled.append(update_id)
            self._handled = self._handled[-_HANDLED_KEEP:]
            self._flush()

    # -- memory-only state (permission asks, onboarding wizard) ----------------
    def set_volatile(self, kind: str, chat_id: int, on: bool) -> None:
        """Mark that ``chat_id`` has memory-only ``kind`` state, so a restart can tell the operator.

        Identifiers only. The state itself (a live future, a wizard transcript) stays in memory by design
        and dies with the process; this just remembers THAT it existed.
        """
        with self._lock:
            chats = self._volatile.setdefault(kind, [])
            if on and chat_id not in chats:
                chats.append(chat_id)
            elif not on and chat_id in chats:
                chats.remove(chat_id)
            else:
                return
            self._flush()

    def take_volatile(self) -> dict[str, list[int]]:
        """Return and clear the marks left by the previous process (what a restart destroyed)."""
        with self._lock:
            taken = {k: list(v) for k, v in self._volatile.items() if v}
            if taken:
                self._volatile = {}
                self._flush()
            return taken

    # -- boot ---------------------------------------------------------------
    def start_boot(self) -> BootInfo:
        """Mint this boot's epoch, count boots for safe mode, quarantine updates left in flight."""
        with self._lock:
            now = self._clock()
            self._poison = set(self._in_flight)  # died mid-handle last time: never replay these
            self._in_flight = []
            recent = [
                b
                for b in self._boots
                if (t := _parse(b)) is not None and now - t < SAFE_MODE_WINDOW
            ]
            recent.append(_utc(now))
            self._boots = recent
            self.epoch = secrets.token_hex(3)
            self._flush()
            return BootInfo(self.epoch, len(recent) >= SAFE_MODE_BOOTS, frozenset(self._poison))


# --------------------------------------------------------------------------- #
# Settings (env-driven kill switches + limits; every switch defaults to "on once shipped")
# --------------------------------------------------------------------------- #


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "off", "no"}


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class CockpitSettings:
    """The ``COCKPIT_*`` environment (PRD §5 kill switches). Each switch flips back to today's behaviour."""

    session_resume: bool = True
    keep_pending_updates: bool = True
    per_chat_queue: bool = True
    watchdog_s: int = 120
    max_update_age_s: int = 900
    session_max_idle_days: int = 7
    session_max_turns: int = 500
    pending_max_age_days: int = 7

    @classmethod
    def from_env(cls) -> CockpitSettings:
        return cls(
            session_resume=_env_flag("COCKPIT_SESSION_RESUME", True),
            keep_pending_updates=_env_flag("COCKPIT_KEEP_PENDING_UPDATES", True),
            per_chat_queue=_env_flag("COCKPIT_PER_CHAT_QUEUE", True),
            watchdog_s=_env_int("COCKPIT_WATCHDOG_S", 120),
            max_update_age_s=_env_int("COCKPIT_MAX_UPDATE_AGE_S", 900),
            session_max_idle_days=_env_int("COCKPIT_SESSION_MAX_IDLE_DAYS", 7),
            session_max_turns=_env_int("COCKPIT_SESSION_MAX_TURNS", 500),
            pending_max_age_days=_env_int("COCKPIT_PENDING_MAX_AGE_DAYS", 7),
        )


# The pending-approvals store lives in its own module (the §R10 file cap); re-exported so every importer of
# `agent.cockpit_state` is unchanged. Imported LAST: it reads the helpers and notices defined above.
from agent.cockpit_pending import PENDING_SCHEMA, PendingStore, PersistentMap  # noqa: E402,F401
