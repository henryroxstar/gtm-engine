"""The pending-approvals store (F1) — ``content/<profile>/_cockpit/pending.json``.

Split out of :mod:`agent.cockpit_state` (the §R10 cap). Everything that carries content or prospect data lives
here, in the profile's own tree (tenant content, §R9 — and so inside the ``gtm-content`` backup): pending publish
and reply drafts (with the exact preview the operator approved), the plan-gate run id, the awaiting-edit flag,
the Telegram message ids of presented gates and the offline digest. The identifiers-only counterpart is
:class:`agent.cockpit_state.CockpitState`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator, MutableMapping
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.cockpit_state import (
    _STR_MAP,
    NOTICE_CORRUPT_PENDING,
    NOTICE_FOREIGN_PENDING,
    _atomic_write,
    _now,
    _read,
    _utc,
)
from gtm_core.paths import _safe_segment

logger = logging.getLogger("agent.cockpit_state")

_OBJ_MAP = {"type": "object", "additionalProperties": {"type": "object"}}
PENDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["version"],
    "properties": {
        "version": {"type": "integer"},
        "publish": _OBJ_MAP,
        "reply": _OBJ_MAP,
        "gate_run_id": _STR_MAP,
        "awaiting_edit": {"type": "object", "additionalProperties": {"type": "boolean"}},
        "presented": _OBJ_MAP,
        "digest": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "object"}},
        },
    },
}


_SECTIONS = ("publish", "reply", "gate_run_id", "awaiting_edit", "presented")


class PersistentMap(MutableMapping):
    """A dict that writes its owning profile's ``pending.json`` through on every mutation."""

    def __init__(
        self, store: PendingStore, section: str, owner_of: Callable[[Any, Any], str]
    ) -> None:
        self._store = store
        self._section = section
        self._owner_of = owner_of
        self._data: dict[Any, Any] = {}
        self._owner: dict[Any, str] = {}

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self) -> Iterator:
        return iter(list(self._data))

    def __len__(self) -> int:
        return len(self._data)

    def __setitem__(self, key, value) -> None:
        with self._store.lock:
            if isinstance(value, dict):
                value = dict(value)
                if self._section in ("publish", "reply"):
                    value.setdefault("staged_at", _utc(self._store.clock()))
            owner = self._owner_of(key, value)
            self._data[key] = value
            self._owner[key] = owner
            self._store.flush(owner)

    def __delitem__(self, key) -> None:
        with self._store.lock:
            owner = self._owner.pop(key, None)
            del self._data[key]
            if owner is not None:
                self._store.flush(owner)

    def _load(self, key, value, owner: str) -> None:
        self._data[key] = value
        self._owner[key] = owner

    def owned_by(self, profile: str) -> dict:
        return {k: v for k, v in self._data.items() if self._owner.get(k) == profile}


class PendingStore:
    """All profiles' ``_cockpit/pending.json``, exposed as write-through mappings."""

    def __init__(
        self,
        content_root: Path,
        chat_profile: Callable[[int], str],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._root = Path(content_root)
        self._chat_profile = chat_profile
        self.clock = clock or _now
        self.lock = threading.RLock()
        self.notices: list[str] = []
        self._digest: dict[str, dict[str, list[dict]]] = {}  # profile -> chat -> items
        by_profile_field = lambda _k, v: str(v["profile"])  # noqa: E731
        by_chat = lambda k, _v: str(self._chat_profile(int(k)))  # noqa: E731
        self.publish = PersistentMap(self, "publish", by_profile_field)
        self.reply = PersistentMap(self, "reply", by_profile_field)
        self.gate_run_id = PersistentMap(self, "gate_run_id", by_chat)
        self.awaiting_edit = PersistentMap(self, "awaiting_edit", by_chat)
        self.presented = PersistentMap(self, "presented", lambda _k, v: str(v["profile"]))

    # -- paths / io ---------------------------------------------------------
    def _path(self, profile: str) -> Path:
        return self._root / _safe_segment(profile, "profile") / "_cockpit" / "pending.json"

    def flush(self, profile: str) -> None:
        with self.lock:
            payload: dict[str, Any] = {"version": 1}
            for name in _SECTIONS:
                m: PersistentMap = getattr(self, name)
                payload[name] = {str(k): v for k, v in m.owned_by(profile).items()}
            digest = self._digest.get(profile, {})
            payload["digest"] = {c: items for c, items in digest.items() if items}
            try:
                _atomic_write(self._path(profile), payload)
            except OSError as exc:
                logger.warning("could not persist pending state for %s: %s", profile, exc)

    def load_all(self) -> None:
        """Populate the maps from every profile's pending.json (boot-time)."""
        with self.lock:
            if not self._root.is_dir():
                return
            for child in sorted(self._root.iterdir()):
                if not child.is_dir() or child.name.startswith(("_", ".")):
                    continue
                path = child / "_cockpit" / "pending.json"
                data, status = _read(path, PENDING_SCHEMA)
                if status == "corrupt":
                    self._quarantine(path)
                    if NOTICE_CORRUPT_PENDING not in self.notices:
                        self.notices.append(NOTICE_CORRUPT_PENDING)
                    continue
                if data is None:
                    continue
                profile = child.name
                self._load_entries(data, profile)
                # chat-keyed state only comes back for the profile the chat is bound to NOW
                for name in ("gate_run_id", "awaiting_edit"):
                    for k, v in data.get(name, {}).items():
                        try:
                            chat = int(k)
                        except ValueError:
                            continue
                        if str(self._chat_profile(chat)) == profile:
                            getattr(self, name)._load(chat, v, profile)
                if data.get("digest"):
                    self._digest[profile] = {c: list(items) for c, items in data["digest"].items()}

    def _load_entries(self, data: dict, profile: str) -> None:
        """Load publish/reply/presented entries, refusing any that names a profile other than the directory
        that holds it: such an entry would run Ledgers/dispatch against another tenant (review finding)."""
        for name in ("publish", "reply", "presented"):
            for k, v in data.get(name, {}).items():
                if str(v.get("profile")) != profile:
                    if NOTICE_FOREIGN_PENDING not in self.notices:
                        self.notices.append(NOTICE_FOREIGN_PENDING)
                    continue
                getattr(self, name)._load(k, v, profile)

    @staticmethod
    def _quarantine(path: Path) -> None:
        try:
            path.replace(path.with_name("pending.json.corrupt"))
        except OSError:
            pass

    # -- presented gates ----------------------------------------------------
    def record_presented(
        self,
        kind: str,
        token: str,
        *,
        chat_id: int,
        message_id: int,
        profile: str,
        epoch: str,
        extra: dict | None = None,
    ) -> None:
        now = _utc(self.clock())
        prev = self.presented.get(f"{kind}:{token}") or {}
        rec = {
            "kind": kind,
            "token": token,
            "chat_id": chat_id,
            "message_id": message_id,
            "profile": profile,
            "epoch": epoch,
            "presented_at": now,
            # first_at survives re-presentation (a gate's age is measured from when it first appeared);
            # resent_at is set only by a RE-presentation (the 30-minute spacing never counts the original).
            "first_at": prev.get("first_at", now),
        }
        if extra:
            rec.update(extra)
        self.presented[f"{kind}:{token}"] = rec

    def presented_for(self, kind: str, token: str) -> dict | None:
        return self.presented.get(f"{kind}:{token}")

    def clear_presented(self, kind: str, token: str) -> None:
        self.presented.pop(f"{kind}:{token}", None)

    # -- offline digest (F2) -------------------------------------------------
    def digest_append(self, chat_id: int, profile: str, item: dict) -> None:
        with self.lock:
            self._digest.setdefault(profile, {}).setdefault(str(chat_id), []).append(item)
            self.flush(profile)

    def digest_items(self, chat_id: int, profile: str) -> list[dict]:
        with self.lock:
            return list(self._digest.get(profile, {}).get(str(chat_id), []))

    def digest_clear(self, chat_id: int, profile: str) -> list[dict]:
        with self.lock:
            items = self._digest.get(profile, {}).pop(str(chat_id), [])
            self.flush(profile)
            return items
