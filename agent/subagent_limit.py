"""A counted limit on helper (subagent) launches per run (W5, PRD 2026-10-19a).

Starting a helper used to be unlimited: one fan-out run spent about 93M subagent tokens to produce two
sendable emails. This bounds the NUMBER of helpers a run may start. It does not bound what each costs; the
per-run budget stays the SDK's ``max_budget_usd`` and the §R2 cap, and a per-run spend ceiling is a separate
follow-up.

**Why a ``PreToolUse`` hook, not the permission callback.** A probe through a real session on both the pinned
(2.1.76) and the bundled (2.1.301) CLI, and again under the repo's real options, found that the model starts a
helper as ``Agent`` and that the launch never reaches ``can_use_tool`` in default mode; only a ``PreToolUse``
hook sees it (``tests/fixtures/subagent_tool_probe.json``). A cap written into the callback would never fire.
``permissions.py`` is therefore unchanged: it still allows ``Task`` and still denies ``Agent`` if the CLI ever
does ask, so the failure direction there is closed.

**The window.** One run on the backend (including a warm session's next run), one pack-node dispatch, one
cockpit turn, one one-shot invocation. ``AgentSession.run`` resets its counter when it starts; a pack node
builds its own. A limit of 8 is the default, set per profile by ``max_subagents`` in
``content/<profile>/settings.json`` and clamped to 32. A missing, corrupt, negative or non-integer value is the
default, never unlimited.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: The names a helper launch arrives under. ``Agent`` is what the model calls on every CLI probed;
#: ``Task`` is the name the CLI still lists at init (a legacy alias), counted so a CLI that uses it is covered.
#: A test ties this set to the probe fixture, so a CLI that launches under a new name fails the build.
SUBAGENT_TOOLS = frozenset({"Agent", "Task"})
#: The regex ``HookMatcher`` takes. Anchored by the SDK; ``TaskCreate``/``TaskOutput`` etc. do not match.
HOOK_MATCHER = "|".join(sorted(SUBAGENT_TOOLS))

DEFAULT_MAX = 8
MAX_CLAMP = 32


def resolve_max(cfg: Any, profile: str) -> int:
    """The profile's ``max_subagents`` clamped to ``0..32``; anything unusable is :data:`DEFAULT_MAX`.

    Read as defensively as ``agent.signal_dispatch.daily_cap``: a missing or corrupt file falls back to the
    default (with a warning when it exists but cannot be read), never to unlimited.
    """
    try:
        from gtm_core.paths import _safe_segment

        path = cfg.content_root / _safe_segment(profile, "profile") / "settings.json"
        if not path.is_file():
            return DEFAULT_MAX
        stored = json.loads(path.read_text(encoding="utf-8"))
        raw = stored.get("max_subagents") if isinstance(stored, dict) else None
    except Exception:  # noqa: BLE001 — a bad settings file must not stop a run or lift the limit
        logger.warning("max_subagents unreadable for profile %r; using %d", profile, DEFAULT_MAX)
        return DEFAULT_MAX
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return DEFAULT_MAX
    return min(raw, MAX_CLAMP)


class SubagentCounter:
    """Helpers started in the current window, and how many were refused."""

    def __init__(self, limit: int = DEFAULT_MAX) -> None:
        self.limit = self._clean(limit)
        self.started = 0
        self.denied = 0
        self._seen: set[str] = set()

    @staticmethod
    def _clean(limit: Any) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            return DEFAULT_MAX
        return min(limit, MAX_CLAMP)

    def reset(self, limit: int | None = None) -> None:
        """Open a new window (optionally re-reading the limit)."""
        if limit is not None:
            self.limit = self._clean(limit)
        self.started = 0
        self.denied = 0
        self._seen = set()

    def admit(self, tool_use_id: str | None) -> bool:
        """Count one launch. The same ``tool_use_id`` seen twice is one launch."""
        if tool_use_id and tool_use_id in self._seen:
            return True
        if self.started >= self.limit:
            self.denied += 1
            return False
        self.started += 1
        if tool_use_id:
            self._seen.add(tool_use_id)
        return True

    @property
    def note(self) -> str | None:
        """The one line a run's output carries when it was cut short (``None`` if it was not)."""
        if not self.denied:
            return None
        return f"stopped starting helpers after {self.limit}; the result may be less thorough"


def with_note(text: str, counter: SubagentCounter) -> str:
    """``text`` with the cut-short note appended when the window denied a helper; else ``text`` itself."""
    return f"{text}\n\n{counter.note}" if counter.note else text


def make_subagent_hook(
    counter: SubagentCounter, on_deny: Callable[[str, dict, str], None] | None = None
):
    """A ``PreToolUse`` hook: allow a helper launch within the limit, deny it past the limit.

    The deny is in the CLI's own vocabulary, and its reason is read by the model, so it says what to do
    instead. A sink that raises never turns a denial into an allow.
    """

    async def _hook(input_data: Any, tool_use_id: str | None, context: Any) -> dict:
        data = input_data if isinstance(input_data, dict) else {}
        name = data.get("tool_name")
        if name not in SUBAGENT_TOOLS:
            return {}
        if counter.admit(tool_use_id):
            return {}
        if on_deny is not None:
            try:
                on_deny(str(name), {"limit": counter.limit}, "subagent_cap")
            except Exception:  # noqa: BLE001 — observability must not change the decision
                logger.debug("subagent_cap sink failed", exc_info=True)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Helper limit reached ({counter.limit} per run). Do the remaining work yourself, "
                    "or return what you have; do not start more helpers."
                ),
            }
        }

    return _hook


def denial_sink(sink: Callable[[str, dict, str], None]) -> Callable[[str, dict, str], None]:
    """Adapt a ``denials.jsonl`` sink (``agent.denial_log.make_denial_sink``) to the hook's callback.

    The ledger row is a normal denial (``decision: deny``) whose detail reads ``subagent_cap (limit N)``.
    """
    from .denial_log import SubagentCapDenial

    def _adapted(tool_name: str, tool_input: dict, reason: str) -> None:
        sink(tool_name, SubagentCapDenial(reason=reason, limit=tool_input.get("limit")), "deny")

    return _adapted
