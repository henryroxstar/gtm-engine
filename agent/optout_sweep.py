"""Deterministic (non-LLM) opt-out-reply sweep — the scheduled trigger.

Runs as a plain Python script on a systemd timer (``systemd/gtm-optout-watch.*``),
NOT through the Claude Agent SDK loop. Classifying a reply as an opt-out is a fixed
keyword match (``gtm_core.optout_watch``), never a model's judgment call on untrusted
inbound text (RULES.md §R5) — so this job spends no LLM tokens and cannot be talked
into anything by a crafted reply, unlike routing it through an agent prompt would risk.

Reuses the in-repo Saleshandy wrapper's read-only inbox tools directly (imported as
plain async functions — ``@mcp.tool()`` doesn't change that) rather than duplicating
the HTTP/auth logic. Those two endpoints carry a ``# VERIFY:`` marker in
``agent/mcp/saleshandy/server.py`` (never confirmed live) — a wrong path fails closed
to a ``[saleshandy-error] …`` string, which this script treats as a hard failure (exit
1, triggers ``systemd/notify.sh``) rather than silently reporting "0 opt-outs found."

Cost: zero metered calls (Saleshandy is a flat subscription; no LLM spend), so unlike
``gtm-outcomes-loop`` this needs no budget-guard ExecStartPre.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from agent.config import Config
from agent.ledgers import Ledgers
from gtm_core.optout_watch import (
    advance_watermark,
    find_optouts_in_thread,
    first_inbound_message,
    load_watermark,
    new_threads,
    record_optout_event,
    save_watermark,
)
from gtm_core.signals import build_signal, new_signals, record_signals

logger = logging.getLogger("agent.optout_sweep")

_ERROR_PREFIX = "[saleshandy-error]"


def _parse_or_none(raw: str) -> dict | None:
    if raw.startswith(_ERROR_PREFIX):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


_PAGE_SIZE = 100
_MAX_PAGES = 20


async def _fetch_all_threads(get_inbox_threads) -> dict | None:
    """Page the inbox to exhaustion. Returns a merged payload, or ``None`` on failure.

    Paging matters for correctness, not throughput: the watermark advances to the max
    ``lastMessageAt`` across everything returned, so fetching only the first page would
    strand any thread past it — permanently, since the next sweep starts above the
    advanced mark. That is a silent miss, the one failure mode this module exists to
    prevent. Pages to exhaustion rather than breaking early on a page of already-seen
    threads: the endpoint's sort order is unverified (``server.py`` ``# VERIFY:``), so
    "this page is old, therefore the rest are older" is an assumption not worth a miss.
    """
    merged: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        raw = await get_inbox_threads(unread_only=False, page=page, page_size=_PAGE_SIZE)
        body = _parse_or_none(raw)
        if body is None:
            logger.error("optout_sweep: get_inbox_threads(page=%d) failed: %s", page, raw[:200])
            return None
        batch = (body.get("payload") or {}).get("threads") or []
        merged.extend(batch)
        if len(batch) < _PAGE_SIZE:
            return {"threads": merged}

    # Hit the cap with a still-full page: coverage is incomplete. Refuse to advance the
    # watermark over threads never fetched — fail loud (exit 1 → systemd/notify.sh)
    # rather than bank a partial sweep as if it were complete.
    logger.error(
        "optout_sweep: inbox exceeded %d pages of %d — refusing to advance the watermark "
        "over unfetched threads; raise _MAX_PAGES or narrow the inbox query",
        _MAX_PAGES,
        _PAGE_SIZE,
    )
    return None


async def run(profile: str, *, cfg: Config | None = None) -> int:
    """Sweep new inbox threads for opt-out replies. Returns a process exit code."""
    from agent.mcp.saleshandy.server import get_inbox_threads, get_thread

    cfg = cfg or Config.from_env()
    ledgers = Ledgers(cfg, profile)
    watermark_path = (
        cfg.content_root / profile / "prospects" / "sequences" / ".watchstate" / "optout-watch.json"
    )
    # Same file gtm_core.ledgers.Ledgers appends to; new_signals dedups against it.
    history_path = cfg.content_root / profile / "history.jsonl"

    threads_payload = await _fetch_all_threads(get_inbox_threads)
    if threads_payload is None:
        return 1

    watermark = load_watermark(watermark_path)
    candidates = new_threads(threads_payload, watermark)

    found = 0
    signals: list[dict] = []
    for t in candidates:
        thread_id = str(t.get("id") or "")
        if not thread_id:
            continue
        raw_detail = await get_thread(thread_id)
        detail_body = _parse_or_none(raw_detail)
        if detail_body is None:
            # The watermark still advances past this thread below, so it is never
            # retried — that would be a silent miss. Pinning the watermark instead
            # would re-escalate every prior opt-out on each sweep until the thread
            # heals, and alert fatigue is how an operator starts ignoring the one
            # alert that matters. So: skip, but leave a durable row someone can
            # reconcile, the same fallback trail record_optout_event relies on.
            logger.error("optout_sweep: get_thread(%s) failed: %s", thread_id, raw_detail[:200])
            ledgers.append_history(
                {
                    "event": "optout_scan_failed",
                    "skill": "optout-watch",
                    "thread_id": thread_id,
                    "error": raw_detail[:200],
                    "action_required": "thread was never scanned for opt-out language and "
                    "will not be retried — open it in the Saleshandy inbox and check by hand",
                }
            )
            continue
        payload = detail_body.get("payload") or {}
        match = find_optouts_in_thread(payload)
        if not match:
            # Not an opt-out — but it IS a new inbound reply, which this sweep used to
            # fetch, read and discard. `gtm_core.signals` maps reply_received ->
            # draft_reply; recording it here is what gives that map a producer. Envelope
            # fields only: the body stays untrusted data (§R5) and is never read here.
            inbound = first_inbound_message(payload)
            if inbound:
                who = str(inbound.get("from") or "").strip()
                if who:
                    signals.append(
                        build_signal(
                            who,
                            "reply_received",
                            # Thread-scoped on purpose. signal_id() keys on
                            # (who, type, source) with no timestamp, so a constant source
                            # would dedup the SAME person's every future reply away
                            # permanently once one was recorded. Per-thread means a new
                            # conversation surfaces and a re-scan of the same one does not.
                            f"saleshandy-inbox:{thread_id}",
                            ts=str(inbound.get("sentAt") or "") or None,
                            meta={
                                "thread_id": thread_id,
                                "subject": str(payload.get("subject") or ""),
                            },
                        )
                    )
            continue
        found += 1
        escalated = await _escalate(cfg, profile, match)
        record_optout_event(ledgers, match, escalated=escalated)

    # Dedup against history.jsonl (the same source the radar uses) so a thread that stays
    # in the inbox across sweeps is surfaced once, not every 4 hours. Recorded BEFORE the
    # watermark advances: if the process dies here, the next sweep re-derives these from
    # the un-advanced watermark rather than losing them.
    fresh = new_signals(signals, history_path)
    if fresh:
        record_signals(ledgers, fresh)
    logger.info("optout_sweep: signals seen=%d new=%d", len(signals), len(fresh))

    # W4: act on what was just recorded. Best-effort and last, because opt-out detection
    # is the compliance-critical half of this sweep and must never be jeopardised by the
    # dispatch half — a dispatch failure leaves the signals in history.jsonl for the next
    # run, whereas a failure that aborted the sweep would strand the watermark too.
    # `dispatch` drains the ledger rather than taking `fresh`, so a signal recorded by an
    # earlier run that never got dispatched is picked up here as well.
    await _dispatch_signals(cfg, profile)

    watermark = advance_watermark(watermark, threads_payload)
    save_watermark(watermark_path, watermark)

    logger.info("optout_sweep: profile=%s checked=%d found=%d", profile, len(candidates), found)
    return 0


async def _dispatch_signals(cfg: Config, profile: str) -> None:
    """Turn recorded signals into staged, gated drafts; never abort the sweep for it."""
    from agent.signal_dispatch import dispatch

    try:
        await dispatch(profile, cfg=cfg)
    except Exception:
        logger.warning(
            "optout_sweep: signal dispatch failed for profile=%s", profile, exc_info=True
        )


async def _escalate(cfg: Config, profile: str, match) -> bool:
    """Push the Telegram alert; never let a notification failure abort the sweep."""
    from agent.gate_notify import push_optout_alert

    try:
        await push_optout_alert(cfg, cfg.profiles_root, profile, match)
        return True
    except Exception:
        logger.warning("optout_sweep: push_optout_alert failed for %s", match.email, exc_info=True)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent.optout_sweep",
        description="Deterministic sweep for inbound opt-out replies (systemd-timer entrypoint).",
    )
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(run(args.profile))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
