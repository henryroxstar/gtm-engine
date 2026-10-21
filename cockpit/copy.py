"""cockpit.copy — operator-facing strings introduced by the always-on hardening PRD.

One home, so wording cannot drift silently: every string here is asserted verbatim in
``tests/cockpit/`` (the PRD §6 "Copy" rule). Strings the PRD quotes are quoted exactly; the few it
leaves open (marked ``chosen``) were written here and are part of the same contract once shipped.
"""

from __future__ import annotations

from agent.cockpit_state import (  # noqa: F401 — re-exported: the state layer owns these three
    NOTICE_CORRUPT_PENDING,
    NOTICE_EXPIRED_SESSION,
    NOTICE_FAILED_RESUME,
)

# -- F2: buttons and commands judged against a restart ------------------------------------------
STALE_BUTTON = "that button is from before a restart — I've re-sent it below"
# chosen: a permission ask holds a live future that dies with the process, so nothing can be re-sent.
STALE_PERMISSION = "that approval request is from before a restart — it was denied; ask me again"
STALE_COMMAND = "that command is from before a restart — send it again"

# -- F2: messages that arrived while the bot was down ---------------------------------------------
OFFLINE_PREFIX = "(sent at {hhmm} while I was offline)"
DIGEST_RUN_LABEL = "Run these now"
DIGEST_DISCARD_LABEL = "Discard"
DIGEST_DISCARDED = "Discarded."  # chosen
DIGEST_RUNNING = "Running them now."  # chosen
DIGEST_NOTHING = "Nothing waiting — those were already handled."  # chosen
BATCH_MARK = "({n} messages from while I was offline)"  # chosen
BUSY_BATCH_OFFLINE = BATCH_MARK

# -- F1 / F2: restart behaviour -------------------------------------------------------------------
SAFE_MODE_ALERT = "I restarted 3 times in 10 minutes; skipped my backlog"
SUPERSEDED = "superseded — see below"
RESENT = "(re-sent after restart)"
# chosen: the PRD requires "one line" when a gate is dropped instead of re-presented.
DROPPED_GATE = (
    "I dropped a pending approval for {profile}: it was already decided, had expired, "
    "or was older than {days} days"
)
# chosen: _pending_permission futures and the onboarding wizard are memory-only by design (PRD F1).
RESTART_LOST_STATE = (
    "I restarted — a permission request or a profile-setup wizard that was in progress was "
    "cancelled; start it again if you still want it"
)
REPRESENT_INTRO = "I restarted — {n} approval(s) are still waiting on {profile}:"  # chosen


# -- F4: a send whose outcome nobody can prove -------------------------------------------------------
INTENT_SENT_LABEL = "It went out"
INTENT_NOT_LABEL = "It didn't"
INTENT_SENT_DONE = "Noted — it went out. I won't send it again."  # chosen
INTENT_NOT_DONE = "Noted — it didn't go out. Ask me to stage it again when you're ready."  # chosen
INTENT_ALREADY = "That one is already settled — nothing to do."  # chosen


# -- F3: /stop, batching, and a scheduled run that is using the profile ----------------------------------
STOP_NOTHING = "nothing is running"
STOP_STOPPED = "stopped"
TIMER_BUSY = "a scheduled run is working on {profile} — send this again when it finishes"
SEND_STILL_RUNNING = {
    "email": "the email send is still running; it can't be stopped from here",
    "post": "the post is still being published; it can't be stopped from here",
}
SEND_STILL_RUNNING_DEFAULT = "a send is still running; it can't be stopped from here"


def busy_batch(n: int) -> str:
    """The marker on the ONE follow-up turn that carries what arrived while the brain was busy."""
    return f"({n} message while you were busy)" if n == 1 else f"({n} messages while you were busy)"


BUSY_BATCH = "({n} messages while you were busy)"


def stop_dropped(n: int) -> str:
    return f"dropped {n} waiting message" + ("" if n == 1 else "s")


def digest_text(items: list[str], first_hhmm: str) -> str:
    """``N messages arrived while I was offline (from HH:MM): «first line», …`` (PRD F2)."""
    n = len(items)
    noun = "message" if n == 1 else "messages"
    quoted = ", ".join(f"«{t}»" for t in items[:5])
    more = f", … (+{n - 5} more)" if n > 5 else ""
    return f"{n} {noun} arrived while I was offline (from {first_hhmm}): {quoted}{more}"
