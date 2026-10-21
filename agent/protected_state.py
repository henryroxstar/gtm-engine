"""agent.protected_state — files and commands only a HUMAN or the Python dispatchers may touch (review finding).

Two kinds of state exist so that the brain cannot talk itself past a safety control:

* the **dispatch-intent ledger** (``dispatch-intents.jsonl``) and the ``dispatch-intents`` operator subcommands
  that settle it (``resolve`` / ``removed`` / ``--restored`` / ``--reconcile``). Resolving is the act a human does
  to say "that send did / did not go out"; if the brain could run it, it could release its own "outcome unknown"
  and re-trigger a duplicate send. ``--unresolved`` (a read) stays allowed.
* the **cockpit's persisted state** (``_cockpit/pending.json``, ``_system/cockpit-state.json``,
  ``_system/cockpit-heartbeat``): pending approvals are re-presented to the operator after a restart, so a file
  the brain could write would be a way to put words in front of the human.

:func:`protected_path` is for the built-in file tools; :func:`segment_touches_protected_state` is for one shell
segment. Both are pure. Tenant runs are confined separately (:mod:`agent.path_confinement`); this applies to
EVERY run, including the unscoped VPS cockpit.
"""

from __future__ import annotations

import posixpath
import re

_PROTECTED = re.compile(
    r"(?:^|/)(?:dispatch-intents\.jsonl|_cockpit/pending\.json|_system/cockpit-(?:state\.json|heartbeat))$"
)
_PROTECTED_ANYWHERE = re.compile(
    r"(?:dispatch-intents\.jsonl|_cockpit/pending\.json|_system/cockpit-(?:state\.json|heartbeat))"
)
#: Shell redirect INTO a protected file, whatever the program.
_REDIRECT_INTO = re.compile(
    r">>?\s*\S*(?:dispatch-intents\.jsonl|_cockpit/pending\.json|_system/cockpit-(?:state\.json|heartbeat))"
)
#: Programs that only read what they are pointed at (an out-redirect is caught separately).
_READ_ONLY = frozenset(
    {
        "cat",
        "grep",
        "egrep",
        "head",
        "tail",
        "wc",
        "less",
        "more",
        "jq",
        "ls",
        "stat",
        "file",
        "diff",
        "cmp",
    }
)
_SETTLING_ACTIONS = frozenset({"resolve", "removed", "--restored", "--reconcile"})


def protected_path(path: str) -> bool:
    """True when ``path`` names protected state (``..`` segments are resolved first)."""
    if not path:
        return False
    return bool(_PROTECTED.search(posixpath.normpath(path.replace("\\", "/"))))


def segment_touches_protected_state(segment: str, program: str, tokens: list[str]) -> bool:
    """True when one shell segment settles an intent or writes protected state."""
    if "dispatch-intents" in tokens and _SETTLING_ACTIONS & set(tokens):
        return True
    if not _PROTECTED_ANYWHERE.search(segment):
        return False
    return bool(_REDIRECT_INTO.search(segment)) or program not in _READ_ONLY
