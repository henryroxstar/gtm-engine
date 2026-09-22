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

This module still writes nothing itself. Since SC9 (2026-09-21) there IS a route to the
provider's Do Not Contact list, and it is a gate, not a tool: a detected opt-out raises a
``suppress_on_provider`` signal, the one-node ``inbound/optout-suppress`` pack drafts the
addresses with their dated ledger evidence and stops, and ``agent/dnc_dispatch.py`` makes
the call after the operator approves — inside a ``dnc_context()`` window, narrowed against
this profile's own open opt-out rows, with a read-back before anything is recorded.

**Add only.** No removal path exists anywhere in that vertical: not here, not in the
dispatcher, not on the connector, and not in the loader's effect set. The five removal
verbs are denied to the brain in every context. DNC is global and permanent, so a
false-positive add is a human's to undo in the provider UI — and an automated removal
would un-suppress someone who asked to be left alone, which nothing here may do.

Detection and escalation being reliable and same-day was the actual fix; the confirm step
was never the broken part, and it is still a human's.

**Known coverage gap.** Every phrase below is English. A reply in another SCRIPT is caught
by :func:`is_unreadable` and escalated as ambiguous (SC6); a non-English opt-out in LATIN
script ("désinscrire", "abmelden") is not caught at all. Per-language patterns are unbuilt.

Pure stdlib. No ``datetime.now()``/``random`` at import time (mirrors ``gtm_core.signals``
so importing has no side effects); the sweep script stamps timestamps at call time.
"""

from __future__ import annotations

import json
import re
import unicodedata
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
    "is_unreadable",
    "record_unreadable_event",
    "main",
]

# Opt-out phrases: matched anywhere in the message, case-insensitive, word-boundary
# safe. Deliberately biased toward RECALL, not precision — ``inbound-triage-rubric``
# is explicit that "any unsubscribe / 'stop' / 'remove me' reply counts — however
# phrased" and that "ambiguous reads as an opt-out". That bias is
# only affordable because this module escalates and never acts: a false positive costs
# the operator one glance at a Telegram message, while a miss costs a compliance
# deadline. Do NOT prune patterns here to quiet the alert volume — the same-day rule is
# the constraint, and the confirm step is what keeps a false positive harmless.
#
# The one carve-out is a phrase that is *unambiguously* not an opt-out: "please stop by
# our office" is a warm reply, not a suppression request.
#
# SC12 (2026-09-21) removed "not interested" / "no longer interested" from this list, and
# the removal is the point rather than a tightening for its own sake. A soft no is not a
# suppression request: it carries no legal deadline, it does not belong on a Do Not Contact
# list, and routing it here spent the same-day opt-out alert — the one with an actual
# deadline attached — on a reply that has none. Treating every soft no as an opt-out also
# permanently removed people who had only said "not right now".
# They are classified as `not_now` instead (``gtm_core.reply_classify``): recorded, no
# draft, nobody woken, and a `reshow_after` date to re-approach. An explicit request to
# stop, however it is worded — "not interested, remove me" — still matches the patterns
# below and is still an opt-out, because "remove me" is in the list on its own merits.
_PHRASE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"\b{p}\b", re.IGNORECASE)
    for p in (
        r"unsubscribe",
        r"remove me",
        r"take me off",
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

#: Line markers after which the text is no longer what the sender TYPED — a signature
#: block or the quoted original. Live read 2026-09-22: setting code 2 on all seven live
#: sequences instructs the recipient to reply with the single word "stop", and the
#: bare-stop branch above only fires when that word is essentially the whole message. A
#: phone appends "Sent from my iPhone"; a desktop client quotes the original underneath.
#: Either one pushes the body past `_BARE_STOP_MAX_WORDS`, so the reply our own copy asks
#: for reads as ordinary prose. Six of eight realistic shapes missed before this existed.
_REPLY_END_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^-{2,}\s*$",  # RFC 3676 sigdashes, with or without the trailing space
        r"^_{5,}\s*$",  # the Outlook reply divider
        r"^>",  # a quoted line
        r"^on\b.*\bwrote:",  # Gmail/Apple Mail attribution
        r"^-+\s*original message\s*-+",
        r"^from:\s",  # Outlook quoted header
        r"^sent from my\b",
        r"^get outlook for\b",
    )
)


def _reply_head(text: str) -> str:
    """The part of a reply the sender actually typed, above any signature or quote.

    Used ONLY to widen the bare-stop branch, never to narrow a match: `is_optout` tries the
    whole body first and falls back to this, so trimming can add a detection and can never
    remove one. That direction matters — the opposite mistake silently un-suppresses
    somebody, and the quoted original commonly contains our own "reply 'stop'" instruction,
    which must not opt out the person who quoted it back.
    """
    head: list[str] = []
    for line in (text or "").splitlines():
        if any(m.match(line.strip()) for m in _REPLY_END_MARKERS):
            break
        head.append(line)
    return "\n".join(head)


# direction values observed/plausible for an inbound (prospect-sent) message. The
# in-repo Saleshandy wrapper's thread shape is unverified live (server.py `# VERIFY:`),
# so an unrecognized/missing direction is scanned anyway rather than silently skipped —
# see `direction_known` on OptOutMatch.
_INBOUND_DIRECTION_TOKENS = frozenset({"inbound", "in", "received", "reply", "prospect"})


#: Characters that are invisible in a rendered email but split a word for a regex.
#: Unicode category Cf (ZERO WIDTH SPACE/JOINER, SOFT HYPHEN, WORD JOINER, BOM) and Cc
#: (control). Real mail carries these — HTML rendering, tracking pixels, copy-paste from a
#: web page — and `un\u200bsubscribe` reads as "unsubscribe" to the person who sent it and
#: as nothing at all to a `\bunsubscribe\b` match. A missed opt-out is the failure this
#: whole module exists to prevent, so they are removed before matching rather than
#: enumerated as patterns.
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc"})


def strip_invisibles(text: str) -> str:
    """Drop zero-width/format/control characters, keeping ordinary whitespace.

    Tabs and newlines are Cc but are real separators, so they survive as spaces; anything
    else in Cc/Cf is removed outright rather than replaced, because it sits INSIDE a word.
    """
    out = []
    for ch in text or "":
        if ch in ("\t", "\n", "\r"):
            out.append(" ")
        elif unicodedata.category(ch) not in _INVISIBLE_CATEGORIES:
            out.append(ch)
    return "".join(out)


def is_optout(text: str) -> bool:
    """True if ``text`` contains an opt-out phrase or is essentially a bare "stop"."""
    if not text:
        return False
    full = strip_invisibles(text)
    if any(p.search(full) for p in _PHRASE_PATTERNS):
        return True
    # The bare-stop branch runs on the whole body AND on just what the sender typed. Two
    # passes rather than one on the trimmed text, so the trim can only ever ADD a match.
    for candidate in (full, strip_invisibles(_reply_head(text))):
        stripped = candidate.strip()
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


# --- per-message field readers -------------------------------------------------------
#
# VERIFIED LIVE 2026-09-22 against a real account, which is how these were found to be
# wrong. `get_thread` returns a bare LIST of messages whose fields are `content`,
# `fromEmail`, `sentAt` and `fromProspectId` — not the `body`/`senderEmail`/`timestamp`/
# `direction` this module assumed. The 2026-09-21 fix corrected the PATHS and left the
# field names as guesses, so the sweep read nothing from every thread it fetched.
#
# Both spellings are accepted: the live one first, the previously-assumed one second. The
# old names are kept because the recorded fixtures and every existing test use them, and
# because a provider that renames a field back should not silently stop matching.


def message_body(msg: dict) -> str:
    """The message text. Live: ``content``; previously assumed: ``body``."""
    return str(msg.get("content") or msg.get("body") or "")


def message_sender(msg: dict) -> str:
    """Who sent it. Live: ``fromEmail``; previously assumed: ``senderEmail``/``from``."""
    return str(msg.get("fromEmail") or msg.get("senderEmail") or msg.get("from") or "")


def message_ts(msg: dict) -> str:
    """When. Live: ``sentAt``; previously assumed: ``timestamp``."""
    return str(msg.get("sentAt") or msg.get("timestamp") or "")


def message_is_inbound(msg: dict) -> tuple[bool, bool]:
    """``(is_inbound, direction_known)``.

    Live payloads carry no ``direction`` at all: a message the PROSPECT sent has a
    ``fromProspectId``, and one we sent has ``None``. That is a positive signal, so when it
    is present the direction IS known. Falls back to the old ``direction`` token, and — when
    neither is available — scans the message anyway rather than skipping it, because a
    missed opt-out costs more than an extra match on our own outbound copy.
    """
    if "fromProspectId" in msg:
        return msg.get("fromProspectId") is not None, True
    direction = str(msg.get("direction") or "").strip().lower()
    if direction:
        return direction in _INBOUND_DIRECTION_TOKENS, True
    return True, False


def find_optouts_in_thread(thread: dict) -> OptOutMatch | None:
    """Scan one ``get_thread`` payload for the first opt-out message.

    ``thread`` is the unwrapped ``payload`` object from ``get_thread`` (a dict with at
    least ``id``, ``subject``, and a ``messages`` list of ``{from, body, sentAt,
    direction}``). Returns the first match, or ``None``. Every message body is
    untrusted data (RULES.md §R5) — matched by fixed pattern only, never interpreted.
    """
    # Field names below try the provider's DOCUMENTED shape first, the ORIGINAL
    # assumed shape second — see the 2026-09-21 `# CORRECTED` comment on
    # `agent.mcp.saleshandy.server.get_thread` for why: that endpoint's real path was
    # wrong outright (404, never hit live before that day), and its per-message field
    # names are confirmed for `senderEmail`/`timestamp` but NOT confirmed to exclude
    # `direction`/`from`/`sentAt` — the doc excerpt that fixed the path may simply not
    # have mentioned them. Two names checked is cheap; a silently-dropped opt-out
    # because a field was read under the wrong key is the failure this whole module
    # exists to prevent.
    thread_id = str(thread.get("threadId") or thread.get("id") or "")
    subject = str(thread.get("subject") or "")
    messages = thread.get("messages") or []
    for msg in messages:
        inbound, direction_known = message_is_inbound(msg)
        if not inbound:
            continue
        body = message_body(msg)
        if is_optout(body):
            return OptOutMatch(
                thread_id=thread_id,
                email=message_sender(msg),
                subject=subject,
                message_ts=message_ts(msg),
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
    function reads only envelope fields (``senderEmail``/``from``, ``timestamp``/``sentAt``,
    ``direction``) and never
    interprets the body.
    """
    for msg in thread.get("messages") or []:
        # Same fail-open bias as find_optouts_in_thread: an unlabelled direction is
        # treated as possibly-inbound rather than silently skipped.
        inbound, _known = message_is_inbound(msg)
        if not inbound:
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


def thread_ts(item: dict) -> str:
    """A list item's timestamp. VERIFIED LIVE 2026-09-22: the field is ``sentAt``.

    `lastMessageTimestamp` was the assumed name and is absent from every live item, so the
    watermark saw an empty string for every thread — it could neither select new threads
    nor advance. Both older spellings are still accepted; the live one wins.
    """
    return str(
        item.get("sentAt") or item.get("lastMessageTimestamp") or item.get("lastMessageAt") or ""
    )


def thread_id_of(item: dict) -> str:
    """A list item's thread id. VERIFIED LIVE 2026-09-22: ``emailThreadId`` (== ``hashId``).

    There is no ``id`` key on a live item, so every caller reading one got "" and skipped
    the thread — which is why the sweep scanned nothing at all against a real account.
    """
    return str(item.get("emailThreadId") or item.get("hashId") or item.get("id") or "")


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
    # `threads_payload["threads"]` is this module's OWN normalized wrapper key
    # (built by agent.optout_sweep._fetch_all_threads), not the provider's — that
    # merge already renamed the provider's `items` onto it. `lastMessageTimestamp`
    # IS the provider's field name (was wrongly assumed `lastMessageAt`; fixed
    # 2026-09-21 alongside the endpoint path — see optout_sweep.py's comment).
    for t in threads_payload.get("threads") or []:
        ts = thread_ts(t)
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
        ts = thread_ts(t)
        if ts > seen_max:
            seen_max = ts
    if seen_max:
        out[key] = seen_max
    return out


# --------------------------------------------------------------------------- ledger


# --------------------------------------------------------------------------- SC6: unreadable
#
# Every pattern in `_PHRASE_PATTERNS` is English. A reply that opts out in Japanese, Russian or
# Arabic matches none of them, so before SC6 it fell through to the non-opt-out branch, was
# classified by the same English-only classifier, and could be handed a drafted reply — a reply
# to someone who may have just asked to be left alone.
#
# What this does NOT do: hold the watermark. There is one global timestamp, so holding it
# re-fetches every newer thread on every sweep, forever, with no release condition. Instead the
# reply is RECORDED as unreadable and escalated to a human before the watermark advances, which
# is the same durability the opt-out path already relies on.
#
# The residual, stated rather than hidden: a non-English opt-out written in LATIN script
# ("désinscrire", "abmelden") is readable by this test and still misses the English matcher. This
# catches a change of SCRIPT, which is the detectable half. The other half needs per-language
# patterns and is not in this change.

#: Minimum alphabetic characters before the whole-body test is allowed an opinion. Below it a
#: signature block, an emoji-only reply or a bare "ok" would decide the verdict on 2-3 letters.
_UNREADABLE_MIN_LETTERS = 8

#: Share of alphabetic characters that must be non-Latin before the whole reply is called
#: unreadable. Not 1.0: a real CJK reply routinely carries a Latin signature, product name or
#: URL. Biased LOW on purpose, for the reason the phrase list above is biased toward recall —
#: calling a readable reply unreadable costs the operator one glance, while the other way round
#: hands an English-drafted reply to someone who may have just asked to be left alone.
_UNREADABLE_NON_LATIN_SHARE = 0.2

#: Minimum non-Latin letters in the OPENING line before that line alone decides. A whole-body
#: share cannot see the shape that matters most: a four-character opt-out ("配信停止") above a
#: forty-word Latin signature scores 0.05 — indistinguishable from an English reply quoting one
#: foreign place name. The opening line separates them, because an opt-out demand is at the top
#: of a reply and a signature is at the bottom.
_UNREADABLE_MIN_OPENING_LETTERS = 3


def _non_latin_share(text: str) -> tuple[float, int]:
    """``(share of alphabetic chars that are not LATIN, count of non-Latin chars)``."""
    letters = [ch for ch in (text or "") if ch.isalpha()]
    if not letters:
        return 0.0, 0
    non_latin = 0
    for ch in letters:
        try:
            name = unicodedata.name(ch)
        except ValueError:  # unnamed codepoint — cannot claim it is Latin
            non_latin += 1
            continue
        if not name.startswith("LATIN"):
            non_latin += 1
    return non_latin / len(letters), non_latin


def is_unreadable(text: str) -> bool:
    """True if ``text`` is written in a script the English opt-out matcher cannot read.

    Deterministic and pattern-only (§R5: this reasons over untrusted text, it never follows
    it). Two tests, OR'd, because they catch different shapes:

      1. the whole body is substantially non-Latin — a reply written in another script;
      2. the OPENING line is non-Latin — a short demand above a long Latin signature, which
         test 1 cannot see because the signature dilutes the share below any usable threshold.

    The claim is about OUR coverage, never about the sender: "this matcher cannot read this".
    """
    letters = [ch for ch in (text or "") if ch.isalpha()]
    share, _ = _non_latin_share(text)
    if len(letters) >= _UNREADABLE_MIN_LETTERS and share > _UNREADABLE_NON_LATIN_SHARE:
        return True
    opening = next((ln for ln in (text or "").splitlines() if ln.strip()), "")
    open_share, open_count = _non_latin_share(opening)
    return (
        open_count >= _UNREADABLE_MIN_OPENING_LETTERS and open_share > _UNREADABLE_NON_LATIN_SHARE
    )


def record_unreadable_event(ledgers, match: OptOutMatch, *, escalated: bool) -> None:
    """Append an ``optout_unreadable`` record — an inbound reply this matcher cannot read.

    Written BEFORE the watermark advances, exactly as ``optout_detected`` is, so a process
    killed between the two re-derives the reply on the next sweep instead of losing it. Treated
    as an ambiguous opt-out: a human reads it, and no reply is drafted in the meantime.
    """
    ledgers.append_history(
        {
            "event": "optout_unreadable",
            "skill": "optout-watch",
            "thread_id": match.thread_id,
            "email": match.email,
            "subject": match.subject,
            "message_ts": match.message_ts,
            "snippet": match.snippet,
            "direction_known": match.direction_known,
            "escalated": escalated,
            "action_required": "a human reads this reply — the opt-out matcher is English-only "
            "and this one is not, so it is treated as an ambiguous opt-out: no reply is "
            "drafted, and nothing is suppressed until a person decides",
        }
    )


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
