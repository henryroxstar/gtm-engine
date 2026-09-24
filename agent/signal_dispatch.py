"""Signal → action. The half of the always-on loop that never shipped.

:mod:`gtm_core.signals` landed 2026-07-21 with ``SUGGESTED_ACTIONS`` mapping a signal
type to an action verb, and for five weeks nothing read the map. W1b gave it a producer
(``agent.optout_sweep`` records a ``reply_received`` for every new inbound thread it used
to fetch, read and discard). This module is the consumer: it turns a recorded signal into
a **staged, gated draft** or an operator ping, and nothing else.

**It drains the ledger; it is not handed a batch.** Undispatched signals are the
``event: "signal"`` rows in ``history.jsonl`` minus the ``event: "signal_dispatched"``
rows. That makes the dispatcher idempotent, crash-safe, and runnable on its own — a
crash between the pack run and the audit row costs one duplicate draft, not a lost reply,
and there is no queue file to keep in sync with the ledger that already knows.

**What it cannot do.** A ``PackTarget`` names a pack and a variant. It has no channel, no
account and no address field, so a signal — which is untrusted data (RULES.md §R5) — can
inform *what* is drafted and never *where* anything goes. The pack it runs ends at the
operator's reply gate, and ``agent/reply.py`` is inert by default with no representable
destination. Nothing here sends, books, or enrolls.

**Two ceilings, both before any dispatch.** The §R2 monthly budget guard runs before
*every* batch, mirroring ``PipelineRunner.run`` — W4 makes run count signal-driven rather
than calendar-bounded, so a once-per-run check would be the wrong shape. On top of it a
per-day dispatch ceiling (``signal_dispatch_daily_cap`` in the profile's ``settings.json``,
default 10) bounds an unattended lane whose input volume this repo does not control. Over
either limit a signal **defers** — it stays in ``history.jsonl`` and the next sweep sees
it again. Nothing is ever dropped.

**"Today" is scoped in the profile's timezone (SC14, fixed 2026-09-24), not the ledger's.**
Every ``ts`` is stamped UTC, and until this fix the daily counter also computed "today" in
UTC, so the cap reset at whatever local hour the profile's UTC offset happened to land on —
08:00 for a UTC+8 profile, not local midnight. ``profile_timezone`` reads an optional
``timezone`` key from the same ``settings.json`` (default UTC, so an unconfigured profile
counts exactly as before), and ``dispatched_today`` converts each row's UTC ``ts`` into
that zone before comparing its date, rather than string-matching a UTC-formatted prefix
against a differently-scoped "today".

Usage::

    python -m agent.signal_dispatch --profile <profile> --dry-run
    python -m agent.signal_dispatch --profile <profile>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agent.budget import vps_budget_ok
from agent.config import Config
from agent.ledgers import Ledgers
from gtm_core.signals import SUGGESTED_ACTIONS

#: ``SUGGESTED_ACTIONS`` is re-exported deliberately: T9 asserts that every action verb
#: the signal layer can produce has an entry here, and reading both halves off one module
#: is what keeps that assertion from drifting into comparing two unrelated tables.
__all__ = [
    "SUGGESTED_ACTIONS",
    "ACTION_DISPATCH",
    "DISPATCHED_EVENT",
    "DEFAULT_DAILY_CAP",
    "PackTarget",
    "NotifyTarget",
    "daily_cap",
    "profile_timezone",
    "undispatched",
    "dispatched_today",
    "dispatch",
    "main",
]

logger = logging.getLogger(__name__)

#: Ledger event written for every dispatch attempt, success or defer. This is the other
#: half of the drain: `undispatched` subtracts these from the recorded signals.
DISPATCHED_EVENT = "signal_dispatched"

#: Default per-day ceiling. Enough for a real reply day; low enough that a runaway
#: producer cannot drain a month's budget overnight while nobody is watching.
DEFAULT_DAILY_CAP = 10

#: Fallback when a profile has not set its own timezone. Every ledger `ts` is stamped in
#: UTC (`gtm_core.ledgers._utc_now_iso`), so this is also what "unset" behaves as today —
#: a profile that never configures `timezone` sees byte-identical counting to before this
#: was introduced.
DEFAULT_TIMEZONE = "UTC"


@dataclass(frozen=True)
class PackTarget:
    """Run a pack variant. Two fields, and deliberately no third.

    Adding a channel/account/destination field here would give a signal somewhere to put
    one — the same unrepresentable-destination property the publish gate relies on, at
    the layer that decides what runs.
    """

    pack: str
    variant: str


@dataclass(frozen=True)
class NotifyTarget:
    """Ping the operator. No draft, no spend, no pack run."""

    notify: Callable


def _notify_signal(cfg: Config, profile: str, signal: dict):
    from agent.gate_notify import push_signal_alert

    return push_signal_alert(cfg, cfg.profiles_root, profile, signal)


#: Action verb -> what to do about it. `SUGGESTED_ACTIONS`' values are verbs, not skill
#: slugs — the distinction that made the original T9 unwritable (PRD §11.1c).
ACTION_DISPATCH: dict[str, PackTarget | NotifyTarget | None] = {
    # A warm reply. The inbound pack's single node ends at ⟦GATE:reply⟧.
    "draft_reply": PackTarget("inbound", "inbound-reply"),
    # A meeting request is still a reply — the booking link lives in PROFILE.md and the
    # triage skill already knows to offer it. A separate pack would be one graph with
    # the same node in it.
    "propose_booking_link": PackTarget("inbound", "inbound-reply"),
    # Buyer intent, a pricing question. Drafting a reply to these on a timer is worse
    # than saying nothing; wake someone.
    "escalate_to_operator": NotifyTarget(_notify_signal),
    # SC9. An opt-out to mirror onto the provider's Do Not Contact list. The pack's first
    # node drafts the address list from the ledger's own evidence and stops at a gate; its
    # second node carries `external_effect = "dnc_add"` and is dispatched by Python
    # (agent/dnc_dispatch.py) only after a human approves. Same shape as publish and
    # enroll — a PackTarget with no destination field, because a signal is untrusted data
    # (§R5) and must never be able to choose who gets suppressed.
    "suppress_on_provider": PackTarget("inbound", "optout-suppress"),
    # Recorded, deduped, and deliberately not acted on. An explicit no-op rather than a
    # missing key, so T9 can tell "decided against" from "forgotten".
    "review": None,
}


def daily_cap(cfg: Config, profile: str) -> int:
    """Per-day dispatch ceiling from the profile's settings (default 10).

    Read as defensively as ``agent.budget.monthly_cap_usd`` — a missing or corrupt
    settings file falls back to the default rather than to unlimited.
    """
    path = cfg.content_root / profile / "settings.json"
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        return int(stored.get("signal_dispatch_daily_cap", DEFAULT_DAILY_CAP))
    except Exception:  # noqa: BLE001 — missing/corrupt settings → default cap
        return DEFAULT_DAILY_CAP


def profile_timezone(cfg: Config, profile: str) -> str:
    """The IANA zone "today" is scoped in for :func:`dispatched_today` (default UTC).

    Read from the same ``settings.json`` as :func:`daily_cap`, as defensively: a missing
    or corrupt settings file, or an unrecognised zone name, falls back to
    :data:`DEFAULT_TIMEZONE` rather than raising mid-dispatch or silently landing on the
    wrong zone. The zone name is validated HERE, eagerly, so a typo in ``settings.json``
    (a tenant-writable file) degrades to "counts like UTC" — the same behaviour as never
    setting it — rather than surfacing as a crash the first time a dispatch runs.
    """
    path = cfg.content_root / profile / "settings.json"
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        name = str(stored.get("timezone") or DEFAULT_TIMEZONE)
        ZoneInfo(name)
        return name
    except Exception:  # noqa: BLE001 — matches daily_cap's own posture: a settings.json
        # that parses to something other than the expected shape (a bad zone name, a
        # non-dict top level, non-JSON bytes) must degrade to the default, never raise
        # mid-dispatch and strand pending signals. Found by review: a narrower tuple
        # (OSError/ValueError/TypeError/ZoneInfoNotFoundError/JSONDecodeError) missed
        # AttributeError from `stored.get(...)` when settings.json parses as valid JSON
        # whose top level is a list/number/string rather than an object.
        return DEFAULT_TIMEZONE


def _history_rows(history_path: Path) -> list[dict]:
    if not history_path.is_file():
        return []
    rows: list[dict] = []
    with history_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # matches the ledger reader's robustness
    return rows


def _signal_ids(row: dict) -> list[str]:
    return [str(i) for i in (row.get("source_items") or []) if i]


def undispatched(history_path: Path) -> list[dict]:
    """Recorded signals that have not been dispatched, oldest first.

    The ledger is the queue. A signal is identified by the id ``record_signals`` puts in
    ``source_items`` (the radar dedup convention), so this needs no second store to drift
    out of sync with the first.
    """
    rows = _history_rows(history_path)
    # `dispatched is True` specifically, not "an audit row exists". A deferral writes a
    # row too — that is how the deferral is auditable — and treating it as done would
    # drop the signal permanently the first time either ceiling was hit, which is the
    # exact opposite of what the ceilings are for.
    done = {
        sid
        for r in rows
        if r.get("event") == DISPATCHED_EVENT and r.get("dispatched") is True
        for sid in _signal_ids(r)
    }
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        if r.get("event") != "signal":
            continue
        for sid in _signal_ids(r):
            if sid in done or sid in seen:
                continue
            seen.add(sid)
            out.append({**r, "id": sid})
    return out


def dispatched_today(
    history_path: Path, *, today: str | None = None, tz: str = DEFAULT_TIMEZONE
) -> int:
    """How many dispatches already happened "today", scoped in ``tz`` (default UTC).

    Durable across restarts, unlike an in-process window, which matters for a lane that
    runs as a fresh oneshot every hour — the same reason ``gtm_core.ledgers.month_cost_total``
    reads the ledger rather than counting in memory.

    SC14 fix (2026-09-24): every ``ts`` is stamped UTC
    (``gtm_core.ledgers._utc_now_iso``), so counting by a raw ``%Y-%m-%d`` PREFIX match
    only gives the right answer when "today" is ALSO computed in UTC — which was exactly
    the bug: the ceiling reset at whatever local hour a profile's UTC offset happened to
    land on (08:00 for a UTC+8 profile), not that profile's local midnight. Each row's
    ``ts`` is now parsed and converted into ``tz`` before its date is taken, so ``today``
    and every row are compared in the SAME zone regardless of which one ``tz`` names.
    ``tz="UTC"`` (the default) reproduces the old behaviour exactly.
    """
    try:
        zone = ZoneInfo(tz)
    except Exception:  # noqa: BLE001 — same posture as `profile_timezone`, for the same
        # reason: this is a public function and `tz` is whatever a caller passed. A bad
        # zone NAME raises ZoneInfoNotFoundError/ValueError, but a bad zone TYPE (None,
        # an int) raises TypeError — and `None` is the likely mistake here, since the
        # `today` parameter right beside it takes None to mean "default". Degrade to UTC.
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    day = today or datetime.now(zone).strftime("%Y-%m-%d")
    count = 0
    for r in _history_rows(history_path):
        if r.get("event") != DISPATCHED_EVENT or r.get("dispatched") is not True:
            continue
        try:
            ts = datetime.fromisoformat(str(r.get("ts", "")).replace("Z", "+00:00"))
        except ValueError:
            continue  # a row with no parseable ts cannot be dated — matches the old
            # prefix-match's behaviour on an empty/malformed ts (it never matched either)
        if ts.tzinfo is None:
            # A naive datetime is UTC here, never "whatever zone this process happens to
            # be running in" — datetime.astimezone() on a naive value silently assumes
            # the LATTER, which would make this function's answer depend on which
            # machine ran it. Every real writer stamps an explicit Z; this only guards a
            # hand-edited or third-party-written row.
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        if ts.astimezone(zone).strftime("%Y-%m-%d") == day:
            count += 1
    return count


def _record(ledgers, signal: dict, action: str, *, dispatched: bool, note: str) -> None:
    ledgers.append_history(
        {
            "event": DISPATCHED_EVENT,
            "skill": "signal-dispatch",
            "signal_type": signal.get("signal_type"),
            "who": signal.get("who"),
            "suggested_action": action,
            "dispatched": dispatched,
            "note": note,
            "source_items": [signal.get("id")],
        }
    )


async def _run_pack_for(cfg: Config, profile: str, target: PackTarget, signal: dict) -> None:
    """Run the target pack. Imported lazily — the CLI must stay light to --dry-run."""
    from agent.__main__ import _run_pack

    await _run_pack(cfg, profile, target.pack, target.variant)


async def dispatch(
    profile: str,
    *,
    cfg: Config | None = None,
    dry_run: bool = False,
    today: str | None = None,
) -> int:
    """Dispatch every undispatched signal for ``profile``. Returns an exit code."""
    cfg = cfg or Config.from_env()
    ledgers = Ledgers(cfg, profile)
    history_path = cfg.content_root / profile / "history.jsonl"

    pending = undispatched(history_path)
    if not pending:
        logger.info("signal_dispatch: profile=%s nothing pending", profile)
        return 0

    cap = daily_cap(cfg, profile)
    tz = profile_timezone(cfg, profile)
    already = dispatched_today(history_path, today=today, tz=tz)
    logger.info(
        "signal_dispatch: profile=%s pending=%d dispatched_today=%d cap=%d%s",
        profile,
        len(pending),
        already,
        cap,
        " (dry-run)" if dry_run else "",
    )
    if dry_run:
        for s in pending:
            action = s.get("suggested_action") or "review"
            print(
                f"{s['id']}  {s.get('signal_type')}  -> {action}  [{ACTION_DISPATCH.get(action)}]"
            )
        return 0

    # §R2: before EVERY batch, not once per run. W4 makes run count signal-driven rather
    # than calendar-bounded, so the guard has to sit where the volume is.
    if not vps_budget_ok(cfg, profile):
        for s in pending:
            _record(
                ledgers,
                s,
                s.get("suggested_action") or "review",
                dispatched=False,
                note="deferred: monthly cost cap reached",
            )
        logger.warning(
            "signal_dispatch: profile=%s over monthly cap — %d deferred", profile, len(pending)
        )
        return 0

    for signal in pending:
        action = signal.get("suggested_action") or "review"
        target = ACTION_DISPATCH.get(action)

        if target is None:
            _record(ledgers, signal, action, dispatched=False, note="no-op action")
            continue

        if already >= cap:
            # Defer, never drop: the signal keeps its history row and no
            # `dispatched: true` marker, so tomorrow's sweep finds it again.
            _record(
                ledgers,
                signal,
                action,
                dispatched=False,
                note=f"deferred: daily dispatch cap {cap} reached",
            )
            continue

        try:
            if isinstance(target, NotifyTarget):
                await target.notify(cfg, profile, signal)
            else:
                await _run_pack_for(cfg, profile, target, signal)
        except Exception:  # noqa: BLE001 — one bad signal must not strand the rest
            logger.warning(
                "signal_dispatch: %s failed for %s", action, signal.get("id"), exc_info=True
            )
            _record(ledgers, signal, action, dispatched=False, note="dispatch raised")
            continue

        _record(ledgers, signal, action, dispatched=True, note="dispatched")
        already += 1

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent.signal_dispatch",
        description="Turn recorded signals into staged, gated drafts. Never sends.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be dispatched, spend nothing, run no pack",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(dispatch(args.profile, dry_run=args.dry_run))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
