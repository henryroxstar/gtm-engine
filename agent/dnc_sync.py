"""Deterministic (non-LLM) DNC mirror refresh + reconciliation — the scheduled check.

Runs as a plain Python script on a systemd timer (``systemd/gtm-dnc-sync.*``), NOT through
the Claude Agent SDK loop, for the same reason :mod:`agent.optout_sweep` does not: reading a
suppression list and comparing two sets is arithmetic, not judgment, and routing it through
a model would spend tokens to make it less reliable.

**What this fixes.** ``suppression.csv`` carries ``dnc-optout`` rows — each one a claim about
a *third-party system*: that this person is on the provider's global Do Not Contact list and
therefore cannot be mailed by any sequence, including ones this repo never built. Every other
suppression reason is a claim about our own files that :func:`gtm_core.suppression.verify` can
settle by reading them. This one could not be settled locally at all, so until something
actually compared it against a live payload it was simply believed — and on 2026-08-11 three
real opt-outs sat unmirrored for six weeks while the ledger said otherwise.

**Two jobs, and the second one is the point.**

1. Refresh the freshness-gated cache the send path already blocks on
   (:func:`gtm_core.prospects_consolidate.paths.dnc_cache_path`). Nothing else in this repo
   refreshes it on a schedule, so it went stale and the gate started refusing.
2. Reconcile (:func:`gtm_core.suppression.reconcile_dnc`) and alert ONCE on divergence.

**What it must never do — and this is a boundary, not a preference.** It never writes to
``suppression.csv``. The reverse direction (an entry on the provider with no ledger row) is
*expected*: the provider's list legitimately holds hand-added exclusions that were never
opt-outs. Appending those as ``dnc-optout`` would relabel a hand-added exclusion as a
recipient-initiated opt-out, which inflates the opt-out rate the wave gate blocks on and
corrupts the one number that is supposed to mean "people asked us to stop".

**An empty payload refuses.** An unparsed response and a genuinely empty DNC list look
identical, so "0 entries" is never written over a cache that has real ones —
``reconcile_dnc`` already refuses on empty, and this script refuses to overwrite too.

Cost: zero metered calls (Saleshandy is a flat subscription; no LLM spend), so unlike
``gtm-outcomes-loop`` this needs no budget-guard ExecStartPre.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime

from agent.config import Config
from agent.ledgers import Ledgers
from gtm_core import suppression
from gtm_core.prospects_consolidate.paths import dnc_cache_path

logger = logging.getLogger("agent.dnc_sync")

_ERROR_PREFIX = "[saleshandy-error]"
_MAX_PAGES = 20
_PAGE_SIZE = 100


def _parse_or_none(raw: str) -> dict | None:
    if raw.startswith(_ERROR_PREFIX):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


async def _fetch_provider_dnc(
    list_dnc_lists, get_dnc_items, not_configured: str
) -> tuple[set[str], set[str], str]:
    """Read every DNC list to exhaustion. Returns ``(emails, domains, status)``.

    ``status`` is ``"ok"``, ``"not_configured"`` (no key reached this process — nothing to
    mirror, and nothing broken) or ``"error"`` (a real failure). Paging matters for
    correctness, not throughput: a short read looks exactly like a shrunken DNC list, and
    acting on that would return opted-out people to the sendable pool.
    """
    raw_lists = await list_dnc_lists()
    if raw_lists == not_configured:
        return set(), set(), "not_configured"
    body = _parse_or_none(raw_lists)
    if body is None:
        logger.error("dnc_sync: list_dnc_lists failed: %s", raw_lists[:200])
        return set(), set(), "error"

    node = body.get("payload", body)
    items = node.get("items", node) if isinstance(node, dict) else node
    if not isinstance(items, list):
        logger.error("dnc_sync: list_dnc_lists returned an unexpected shape")
        return set(), set(), "error"

    emails: set[str] = set()
    domains: set[str] = set()
    for entry in items:
        list_id = str((entry or {}).get("id") or "")
        if not list_id:
            continue
        for page in range(1, _MAX_PAGES + 1):
            raw = await get_dnc_items(dnc_list_id=list_id, page=page, page_size=_PAGE_SIZE)
            if raw == not_configured:
                return set(), set(), "not_configured"
            page_body = _parse_or_none(raw)
            if page_body is None:
                logger.error("dnc_sync: get_dnc_items(%s, page=%d) failed", list_id, page)
                return set(), set(), "error"
            page_emails, page_domains = suppression.normalize_dnc_payload(page_body)
            before = len(emails) + len(domains)
            emails |= page_emails
            domains |= page_domains
            # The endpoint's paging metadata is unverified live, so stop on a page that
            # added nothing rather than trusting a `totalPages` nobody has confirmed.
            if len(emails) + len(domains) == before:
                break
        else:
            # Ran the cap with every page still adding entries: coverage is incomplete, and
            # a partial DNC read is indistinguishable from a shrunken list. Refuse.
            logger.error(
                "dnc_sync: DNC list %s exceeded %d pages — refusing a partial mirror",
                list_id,
                _MAX_PAGES,
            )
            return set(), set(), "error"
    return emails, domains, "ok"


async def run(profile: str, *, cfg: Config | None = None) -> int:
    """Refresh the DNC cache and reconcile the ledger against it. Returns an exit code."""
    from agent.mcp.saleshandy.server import NOT_CONFIGURED, get_dnc_items, list_dnc_lists

    cfg = cfg or Config.from_env()
    ledgers = Ledgers(cfg, profile)

    emails, domains, status = await _fetch_provider_dnc(
        list_dnc_lists, get_dnc_items, NOT_CONFIGURED
    )

    if status == "not_configured":
        # Not a failure: this deployment has no Saleshandy connector wired, so there is no
        # DNC list to mirror. Exiting nonzero would fire systemd/notify.sh on every run —
        # an alert no operator can act on from its text, which is how someone learns to
        # ignore the one that matters. Quiet, but never silent.
        logger.warning(
            "dnc_sync: no Saleshandy connector configured for profile=%s — DNC not mirrored",
            profile,
        )
        ledgers.append_history(
            {
                "event": "dnc_sync_skipped",
                "skill": "dnc-sync",
                "reason": "saleshandy_not_configured",
                "action_required": "the provider DNC list is not being mirrored, so the "
                "freshness-gated cache the send path blocks on will go stale — set "
                "SALESHANDY_API_KEY and verify it reaches the container",
            }
        )
        return 0

    if status == "error":
        return 1

    if not emails and not domains:
        # An unparsed payload and a genuinely empty DNC list are indistinguishable here.
        # Writing "0 entries" over a cache that has real ones would return every
        # opted-out person to the sendable pool — the 2026-08-11 incident shape exactly.
        logger.error(
            "dnc_sync: provider returned no DNC entries at all for profile=%s — refusing to "
            "overwrite the cache (an unparsed payload looks identical to an empty list)",
            profile,
        )
        ledgers.append_history(
            {
                "event": "dnc_sync_refused",
                "skill": "dnc-sync",
                "reason": "empty_provider_payload",
                "action_required": "the DNC read returned nothing; the previous cache was "
                "KEPT. Confirm in the Saleshandy UI whether the list is genuinely empty "
                "before trusting any send window.",
            }
        )
        return 1

    cache_path = dnc_cache_path(profile, cfg.content_root)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "emails": sorted(emails),
                "domains": sorted(domains),
                "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    ledger_path = cfg.content_root / profile / "prospects" / ".pool" / "suppression.csv"
    ledger = suppression.load(ledger_path) if ledger_path.is_file() else {}
    findings = suppression.reconcile_dnc(ledger, emails, domains)

    ledger_addrs = {
        (row.email or "").strip().lower()
        for row in ledger.values()
        if (row.reason or "").strip().lower() in suppression.PROVIDER_DNC_REASONS
    }
    provider_only = len({e for e in emails if e} - ledger_addrs)

    ledgers.append_history(
        {
            "event": "dnc_reconciled",
            "skill": "dnc-sync",
            "provider_emails": len(emails),
            "provider_domains": len(domains),
            "findings": findings,
            # Reported as a COUNT, never as an action: a provider entry with no ledger row
            # is a hand-added exclusion, not an opt-out, and is never written to
            # suppression.csv. Mirroring it as `dnc-optout` would inflate the opt-out rate
            # the wave gate blocks on.
            "provider_only": provider_only,
        }
    )

    if findings:
        from agent.gate_notify import push_dnc_divergence

        try:
            await push_dnc_divergence(
                cfg, cfg.profiles_root, profile, findings, provider_only=provider_only
            )
        except Exception:
            # A notification failure must never turn a completed reconcile into a systemd
            # alert: the durable `dnc_reconciled` row above is already written.
            logger.warning("dnc_sync: push_dnc_divergence failed", exc_info=True)

    logger.info(
        "dnc_sync: profile=%s emails=%d domains=%d findings=%d provider_only=%d",
        profile,
        len(emails),
        len(domains),
        len(findings),
        provider_only,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent.dnc_sync",
        description="Refresh the provider DNC mirror and reconcile the suppression ledger.",
    )
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(run(args.profile))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
