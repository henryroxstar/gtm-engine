"""SC10 — the provider's reply category, read as FILTER MEMBERSHIP.

Split out of :mod:`agent.optout_sweep` on 2026-09-21: the sweep ORCHESTRATES (fetch new
threads, match opt-outs, classify, record, advance the watermark); this reads one
second witness from the provider, and it is a self-contained question with its own
paging, its own schema validation and its own refusal rule.

The refusal rule is the whole design. Any refusal — the capability ungranted, an
unreadable payload, a failed call, a read that hits the page cap — drops the witness
ENTIRELY rather than returning a partial map, because a partial map reads downstream as
"this thread has no category", which silently leaves the classifier's LESS conservative
route in place. A short read looks exactly like a smaller category.
"""

from __future__ import annotations

import json
import logging

from gtm_core import minischema
from gtm_core.reply_classify import CATEGORY_CONTRIBUTION
from gtm_core.sequencers import resolve_capability

logger = logging.getLogger("agent.optout_categories")

#: Mirrors the sweep's own paging constants — the same endpoint, the same limits.
_PAGE_SIZE = 100
_MAX_PAGES = 20

_ERROR_PREFIX = "[saleshandy-error]"


def _parse_or_none(raw: str) -> dict | None:
    if raw.startswith(_ERROR_PREFIX):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


# ── SC10: the provider's reply category, read as FILTER MEMBERSHIP ──────────────────
#
# Saleshandy publishes category DEFINITIONS (`get_unibox_categories`) and outcome ids
# (`get_outcomes`), but no documented response says which category a given thread was
# assigned. The one documented handle is the `categoryIds` REQUEST FILTER on the inbox
# list call — so a thread's category is *which filtered call returned its id*, never a
# field parsed out of a body. That is also what makes the injection case structural:
# text inside a reply saying `"category": "do_not_contact"` is never read.
#
# Bounded by construction: at most one list call per ROUTING-RELEVANT category
# (`CATEGORY_CONTRIBUTION`), never one per thread, so the cost does not scale with the
# inbox. Any refusal — the capability ungranted, a wrong-shaped payload, a failed call —
# drops the witness ENTIRELY rather than returning a partial map, because a partial map
# reads downstream as "this thread has no category", which is a silently weaker route.

#: Shapes the two definition endpoints must have before they are read (§R5: never trust
#: the shape of a structured response). `maxItems` is deliberately absent — minischema
#: ignores it, and a cap that silently does nothing is worse than no cap.
_CATEGORIES_SCHEMA = {"type": "array", "items": {"type": "object", "required": ["key", "name"]}}
_OUTCOMES_SCHEMA = {"type": "array", "items": {"type": "object", "required": ["id", "name"]}}


def _items_of(raw: str, schema: dict) -> list[dict] | None:
    """Unwrap ``{payload:{items:[…]}}`` and schema-check it. ``None`` on any refusal."""
    body = _parse_or_none(raw)
    if body is None:
        return None
    node = body.get("payload", body)
    if isinstance(node, dict):
        node = node.get("items", node)
    if minischema.validate(node, schema):
        return None
    return node


async def _page_to_exhaustion(fetch, schema: dict) -> list[dict] | None:
    """Every page of a paged read, or ``None`` on any refusal.

    Paging matters for correctness here, not throughput, for the same reason it does in
    :func:`_fetch_all_threads` and :mod:`agent.dnc_sync`: a short read looks exactly like a
    smaller answer. A category holding more than one page of threads would leave every
    thread past the first page with no witness at all — and no witness means the
    classifier's own, less conservative route stands. Refusing beats a partial map.
    """
    merged: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        items = _items_of(await fetch(page), schema)
        if items is None:
            return None
        merged.extend(items)
        if len(items) < _PAGE_SIZE:
            return merged
    # Ran the cap with every page still full: coverage is incomplete and we cannot tell
    # which threads are missing. The caller drops the witness.
    logger.error(
        "optout_sweep: a paged read exceeded %d pages — refusing a partial map", _MAX_PAGES
    )
    return None


async def _categories_by_thread(get_unibox_categories, get_outcomes, get_inbox_threads) -> dict:
    """Map ``thread_id -> category key`` for the routing-relevant categories only.

    Returns ``{}`` whenever the witness cannot be established — an ungranted capability,
    an unreadable payload, or a failed call. An empty map means "no second witness this
    sweep", which leaves every route exactly where our own classifier put it.
    """
    if not resolve_capability("saleshandy", "reply_categories").granted:
        logger.info("optout_sweep: reply_categories not granted by the registry — no witness")
        return {}

    categories = _items_of(await get_unibox_categories(), _CATEGORIES_SCHEMA)
    # NOT paged: `get_outcomes` takes no parameters and returns the full list; passing
    # page/limit is an HTTP 400 (verified live 2026-09-22).
    outcomes = _items_of(await get_outcomes(), _OUTCOMES_SCHEMA)
    if categories is None or outcomes is None:
        logger.warning("optout_sweep: category/outcome definitions unreadable — no witness")
        return {}

    # The category `key` is our vocabulary; the outcome `id` is what the filter takes.
    # They are joined on `name`, the only field both publish.
    id_by_name = {str(o.get("name", "")).strip().lower(): o.get("id") for o in outcomes}
    wanted = {
        str(c.get("key", "")).strip().lower(): id_by_name.get(
            str(c.get("name", "")).strip().lower()
        )
        for c in categories
        if str(c.get("key", "")).strip().lower() in CATEGORY_CONTRIBUTION
    }

    by_thread: dict[str, str] = {}
    for key, outcome_id in sorted(wanted.items()):
        if outcome_id is None:
            continue
        listed = await _page_to_exhaustion(
            lambda page, _id=outcome_id: get_inbox_threads(
                page=page, page_size=_PAGE_SIZE, category_ids=[_id]
            ),
            {"type": "array"},
        )
        if listed is None:
            # A short or unreadable read is indistinguishable from "this category holds
            # fewer threads", and the difference is a thread that silently keeps the
            # classifier's LESS conservative route. Drop the whole witness rather than
            # bank a map that is quietly missing entries.
            logger.warning(
                "optout_sweep: category %s could not be read in full — dropping the witness", key
            )
            return {}
        for item in listed:
            thread_id = str((item or {}).get("id") or "")
            # First writer wins, and the iteration order is sorted, so the map is the same
            # on every run even when the provider assigns a thread two categories.
            if thread_id and thread_id not in by_thread:
                by_thread[thread_id] = key
    return by_thread
