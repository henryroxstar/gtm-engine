"""Metering Firecrawl calls into ``costs.jsonl`` (signal-first R0.4)."""

from __future__ import annotations

import datetime
import hashlib
import json
from types import SimpleNamespace

from .credit_rates import (
    FIRECRAWL_RATE_BASIS,
    firecrawl_credits_to_usd,
    firecrawl_search_credits_estimate,
)
from .ledgers import Ledgers
from .paths import resolve_content_root
from .signal_sources import url_norm

#: One page reported by two hooks on the same tool call lands within this many seconds.
DEDUPE_WINDOW_S = 120


def _blocks_as_json(raw: object) -> object:
    """A tool result delivered as MCP content blocks, ``[{"type": "text", "text": "<json>"}]``, as the object."""
    if isinstance(raw, list):
        for block in raw:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                try:
                    return json.loads(block["text"])
                except ValueError:
                    continue
    return raw


def reported_credits(raw: object) -> int | None:
    """The credits a Firecrawl response says it used (``creditsUsed``), or ``None`` if it says nothing."""
    raw = _blocks_as_json(raw)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if isinstance(raw, dict):
        data = raw.get("data")
        for holder in (raw, raw.get("metadata"), data if isinstance(data, dict) else None):
            if isinstance(holder, dict):
                val = holder.get("creditsUsed")
                if isinstance(val, int | float) and not isinstance(val, bool) and val >= 0:
                    return int(val)
    return None


def credits_used(raw: object) -> int:
    """Credits a Firecrawl response reports it used (``creditsUsed``), else 1 per page."""
    got = reported_credits(raw)
    return 1 if got is None else got


def _recent(ts: object, now: datetime.datetime) -> bool:
    try:
        then = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return False
    if then.tzinfo is None:
        then = then.replace(tzinfo=datetime.UTC)
    return abs((now - then).total_seconds()) <= DEDUPE_WINDOW_S


def _ledgers(profile: str, content_root) -> Ledgers:
    return Ledgers(SimpleNamespace(content_root=content_root or resolve_content_root()), profile)


def record_capture_cost(
    *, profile: str, url: str, sha256: str, credits: int, tool: str, content_root=None
) -> dict:
    """Append the ``costs.jsonl`` row for one capture and return it as written.

    The cap guard (§R2) reads this ledger, so a capture that is not metered is a capture the cap
    cannot see. ``rate_basis`` marks the dollar figure as derived, not measured. ``content_root``
    names the tree for a caller that already holds one (the capture run's own hook); the editor
    hook leaves it out and gets the ambient root.

    Idempotent: the same page (URL and content hash) billed again within a couple of minutes is the
    same paid call seen by a second hook, so the first row is returned and nothing is added.
    """
    norm = url_norm(url)
    now = datetime.datetime.now(datetime.UTC)

    def same(row: dict) -> bool:
        return (
            row.get("op") == "capture"
            and row.get("url_norm") == norm
            and row.get("sha256") == sha256
            and _recent(row.get("ts"), now)
        )

    return _ledgers(profile, content_root).append_cost(
        {
            "tool": "firecrawl",
            "op": "capture",
            "profile": profile,
            "cost_usd": firecrawl_credits_to_usd(credits),
            "units": {"credits": credits},
            "rate_basis": FIRECRAWL_RATE_BASIS,
            "url_norm": norm,
            "sha256": sha256,
            "source_tool": tool,
        },
        once=same,
    )


def record_search_cost(
    *,
    profile: str,
    query: str,
    results: int,
    credits: int | None,
    credits_source: str | None = None,
    tool: str,
    content_root=None,
) -> dict:
    """Append the ``costs.jsonl`` row for one Firecrawl search.

    ``credits`` is what the response reported, or ``None`` to use the published per-block estimate;
    the row says which (``credits_source``). The query text is not stored, only its hash and length:
    the ledger is for spend, and a query can carry a person's name.
    """
    if credits is None:
        credits, credits_source = firecrawl_search_credits_estimate(results), "estimate"
    return _ledgers(profile, content_root).append_cost(
        {
            "tool": "firecrawl",
            "op": "search",
            "profile": profile,
            "cost_usd": firecrawl_credits_to_usd(credits),
            "units": {"credits": credits, "results": results},
            "credits_source": credits_source or "response",
            "rate_basis": FIRECRAWL_RATE_BASIS,
            "query_sha256": hashlib.sha256((query or "").encode("utf-8")).hexdigest(),
            "query_chars": len(query or ""),
            "source_tool": tool,
        }
    )


def record_call_cost(
    *,
    profile: str,
    op: str,
    credits: int | None,
    tool: str,
    url: str = "",
    content_root=None,
) -> dict:
    """Append a ``costs.jsonl`` row for a Firecrawl call that filed no page: a scrape that came back
    empty but was billed, or a map, crawl, agent or extract call.

    ``credits`` is what the response reported. With none reported the row carries a floor of one
    credit and says so (``credits_source = "unreported-minimum"``), so the ledger is never silent
    about a call it cannot price and never reads as a measurement either.
    """
    source = "response" if credits is not None else "unreported-minimum"
    credits = credits if credits is not None else 1
    row = {
        "tool": "firecrawl",
        "op": op,
        "profile": profile,
        "cost_usd": firecrawl_credits_to_usd(credits),
        "units": {"credits": credits},
        "credits_source": source,
        "rate_basis": FIRECRAWL_RATE_BASIS,
        "source_tool": tool,
    }
    if url:
        row["url_norm"] = url_norm(url)
    return _ledgers(profile, content_root).append_cost(row)
