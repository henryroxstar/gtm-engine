"""Deterministic opt-out reply detector — the missing trigger, not a new judgment call.

``inbound-triage`` already classifies a reply containing "unsubscribe"/"stop"/"remove
me" as P3 and instructs suppression the same day (``inbound-triage-rubric.md``). What
was missing was anything that *looks*: the skill only runs when a human happens to
invoke it. On 2026-08-11, ``jordan.avery@brackenhealth.example`` replied "Unsubscribe"
verbatim to a cold-sequence email; Saleshandy tagged it "Negative sentiment" but left
``Unsubscribed: No`` (confirmed live — Saleshandy suppresses on the one-click *link*,
never on reply text, see ``docs/email-optimization.md`` §12.6). He sat un-suppressed
until an unrelated session six days later happened to check.

This module is the deterministic classifier half of the fix: a fixed keyword match
against untrusted reply text (RULES.md §R5 — classification must never be a model's
judgment call on content an adversary could phrase to evade or trigger it), plus a
watermark so a scheduled sweep (``agent/optout_sweep.py``) never re-scans a thread.

It does **not** write to the provider's Do Not Contact list. DNC is global, permanent,
and the connector exposes no removal tool (``gtm_core.suppression.PROVIDER_DNC_REASONS``
== ``{"dnc-optout"}`` only) — a false-positive auto-add would be unrecoverable. The
sweep detects and escalates (``agent.gate_notify.push_optout_alert``); a human taps
add-to-DNC, same as every other irreversible action this repo gates (publish, schedule).
Detection and escalation being reliable and same-day is the actual fix — the confirm
step was never the broken part.

Pure stdlib. No ``datetime.now()``/``random`` at import time (mirrors ``gtm_core.signals``
so importing has no side effects); the sweep script stamps timestamps at call time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "OptOutMatch",
    "is_optout",
    "find_optouts_in_thread",
    "load_watermark",
    "save_watermark",
    "new_threads",
    "record_optout_event",
    "main",
]

# Opt-out phrases: matched anywhere in the message, case-insensitive, word-boundary
# safe. Deliberately biased toward RECALL, not precision — ``inbound-triage-rubric``
# is explicit that "any unsubscribe / 'not interested' / 'stop' / 'remove me' reply
# counts — however phrased" and that "ambiguous reads as an opt-out". That bias is
# only affordable because this module escalates and never acts: a false positive costs
# the operator one glance at a Telegram message, while a miss costs a compliance
# deadline. Do NOT prune patterns here to quiet the alert volume — the same-day rule is
# the constraint, and the confirm step is what keeps a false positive harmless.
#
# The one carve-out is a phrase that is *unambiguously* not an opt-out: "please stop by
# our office" is a warm reply, not a suppression request.
_PHRASE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"\b{p}\b", re.IGNORECASE)
    for p in (
        r"unsubscribe",
        r"remove me",
        r"take me off",
        r"not interested",
        r"no longer interested",
        r"opt[- ]?out",
        r"opt\b.{0,10}\bout",
        r"please stop(?!\s+by\b)",
        r"stop (?:emailing|contacting|messaging|sending)",
    )
)

# A bare "stop" is the classic one-word opt-out convention (SMS/email list norms) but
# also a common English word ("let's stop and think") — only treat it as a signal when
# it is essentially the whole message (a bare "Unsubscribe" would already hit
# _PHRASE_PATTERNS; this branch is for a bare "Stop."/"STOP" reply).
_BARE_STOP_RE = re.compile(r"^\W*stop\W*$", re.IGNORECASE)
_BARE_STOP_MAX_WORDS = 3

# direction values observed/plausible for an inbound (prospect-sent) message. The
# in-repo Saleshandy wrapper's thread shape is unverified live (server.py `# VERIFY:`),
# so an unrecognized/missing direction is scanned anyway rather than silently skipped —
# see `direction_known` on OptOutMatch.
_INBOUND_DIRECTION_TOKENS = frozenset({"inbound", "in", "received", "reply", "prospect"})


def is_optout(text: str) -> bool:
    """True if ``text`` contains an opt-out phrase or is essentially a bare "stop"."""
    if not text:
        return False
    if any(p.search(text) for p in _PHRASE_PATTERNS):
        return True
    stripped = text.strip()
    if _BARE_STOP_RE.match(stripped) or (
        len(stripped.split()) <= _BARE_STOP_MAX_WORDS
        and re.search(r"\bstop\b", stripped, re.IGNORECASE)
    ):
        return True
    return False


@dataclass(frozen=True)
class OptOutMatch:
    thread_id: str
    email: str
    subject: str
    message_ts: str
    snippet: str
    direction_known: bool


def _snippet(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def find_optouts_in_thread(thread: dict) -> OptOutMatch | None:
    """Scan one ``get_thread`` payload for the first opt-out message.

    ``thread`` is the unwrapped ``payload`` object from ``get_thread`` (a dict with at
    least ``id``, ``subject``, and a ``messages`` list of ``{from, body, sentAt,
    direction}``). Returns the first match, or ``None``. Every message body is
    untrusted data (RULES.md §R5) — matched by fixed pattern only, never interpreted.
    """
    messages = thread.get("messages") or []
    for msg in messages:
        direction = str(msg.get("direction") or "").strip().lower()
        direction_known = bool(direction)
        if direction_known and direction not in _INBOUND_DIRECTION_TOKENS:
            continue
        body = msg.get("body") or ""
        if is_optout(body):
            return OptOutMatch(
                thread_id=str(thread.get("id") or ""),
                email=str(msg.get("from") or ""),
                subject=str(thread.get("subject") or ""),
                message_ts=str(msg.get("sentAt") or ""),
                snippet=_snippet(body),
                direction_known=direction_known,
            )
    return None


def first_inbound_message(thread: dict) -> dict | None:
    """The first INBOUND message in a ``get_thread`` payload, or ``None``.

    Distinct from :func:`find_optouts_in_thread`, which scans *every* inbound message for
    opt-out language: this answers the narrower "did this thread come back to us at all,
    and from whom". The sweep uses it to emit a ``reply_received`` signal for threads that
    are new but carry no opt-out — replies the sweep previously fetched, read and threw
    away (see :mod:`agent.optout_sweep`).

    Same §R5 posture as the rest of this module: the message body is untrusted data. This
    function reads only envelope fields (``from``, ``sentAt``, ``direction``) and never
    interprets the body.
    """
    for msg in thread.get("messages") or []:
        direction = str(msg.get("direction") or "").strip().lower()
        # Same fail-open bias as find_optouts_in_thread: an unlabelled direction is
        # treated as possibly-inbound rather than silently skipped.
        if direction and direction not in _INBOUND_DIRECTION_TOKENS:
            continue
        return msg
    return None


# --------------------------------------------------------------------------- watermark


def load_watermark(path: Path) -> dict[str, str]:
    """Read the watermark state file. Missing/corrupt file is an empty state (never raises)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_watermark(path: Path, state: dict[str, str]) -> None:
    """Write the watermark state file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def new_threads(
    threads_payload: dict, watermark: dict[str, str], *, key: str = "global"
) -> list[dict]:
    """Filter a ``get_inbox_threads`` payload's thread list to ones newer than the watermark.

    ``threads_payload`` is the unwrapped ``payload`` object (``{threads:[...], meta:{...}}``).
    A missing/unparseable ``lastMessageAt`` is treated as new (fail-open toward scanning,
    never toward silently skipping — matches this module's precision-over-recall stance
    on classification but the opposite bias for *coverage*, since a skipped thread never
    gets a second chance the way a false-positive escalation does).
    """
    last_seen = watermark.get(key, "")
    out = []
    for t in threads_payload.get("threads") or []:
        ts = str(t.get("lastMessageAt") or "")
        if not ts or not last_seen or ts > last_seen:
            out.append(t)
    return out


def advance_watermark(
    watermark: dict[str, str], threads_payload: dict, *, key: str = "global"
) -> dict[str, str]:
    """Return ``watermark`` updated to the max ``lastMessageAt`` seen in ``threads_payload``."""
    out = dict(watermark)
    seen_max = out.get(key, "")
    for t in threads_payload.get("threads") or []:
        ts = str(t.get("lastMessageAt") or "")
        if ts > seen_max:
            seen_max = ts
    if seen_max:
        out[key] = seen_max
    return out


# --------------------------------------------------------------------------- ledger


def record_optout_event(ledgers, match: OptOutMatch, *, escalated: bool) -> None:
    """Append an ``optout_detected`` audit record to ``history.jsonl``.

    Written unconditionally on every match, independent of whether the Telegram push
    in ``agent.gate_notify.push_optout_alert`` succeeds — this is the durable trail:
    even if the notification is swallowed (fire-and-forget by design), the finding is
    not lost, and a stale unconfirmed entry is what a later reconciliation pass reads.
    """
    ledgers.append_history(
        {
            "event": "optout_detected",
            "skill": "optout-watch",
            "thread_id": match.thread_id,
            "email": match.email,
            "subject": match.subject,
            "message_ts": match.message_ts,
            "snippet": match.snippet,
            "direction_known": match.direction_known,
            "escalated": escalated,
            "action_required": "operator confirms and adds to Global DNC — no auto-write "
            "(DNC has no removal API; see gtm_core.suppression.PROVIDER_DNC_REASONS)",
        }
    )


# --------------------------------------------------------------------------- CLI


def _cli_check(args) -> int:
    threads_payload = json.loads(args.threads_json.read_text(encoding="utf-8"))
    watermark = load_watermark(args.state)
    candidates = new_threads(threads_payload, watermark)
    matches: list[OptOutMatch] = []
    unreadable = 0
    for t in candidates:
        thread_detail = t.get("_thread") or t  # allow a pre-fetched detail to ride along
        if not thread_detail.get("messages"):
            # A bare `get_inbox_threads` summary row carries no message bodies, so there
            # is nothing to match on. Counting it as "checked" would print "clean" for a
            # dump this command never actually read — the silent miss this module exists
            # to prevent. Report it instead.
            unreadable += 1
            continue
        m = find_optouts_in_thread(thread_detail)
        if m:
            matches.append(m)
    if unreadable:
        print(
            f"warning: {unreadable} thread(s) had no message bodies and were NOT checked "
            "— pass a dump with per-thread `messages` (or a `_thread` detail on each row)."
        )
    watermark = advance_watermark(watermark, threads_payload)
    save_watermark(args.state, watermark)
    if matches:
        print(f"{len(matches)} opt-out reply(ies) found:")
        for m in matches:
            print(f"  - {m.email} (thread {m.thread_id}): {m.snippet!r}")
        return 1
    print(f"clean — {len(candidates) - unreadable} new thread(s) checked, 0 opt-outs")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="gtm_core.optout_watch",
        description="Deterministic opt-out-reply detector (manual/CI invocation).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    ck = sub.add_parser("check", help="scan a threads dump for opt-out replies")
    ck.add_argument("--threads-json", required=True, type=Path)
    ck.add_argument("--state", required=True, type=Path)
    ck.set_defaults(func=_cli_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
