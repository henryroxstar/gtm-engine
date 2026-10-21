"""Python-only dispatcher for an operator-approved DNC add (SC9) — the third external effect.

The same shape as :mod:`agent.publish_dispatch` and :mod:`agent.email_dispatch`, for the same
reason: the brain must not hold the capability at all. ``add_dnc_items`` is denied to it on
every connector (:mod:`agent.permissions`), the pack's ``dnc-add`` node is never run by the
brain, and the write happens here, in Python, inside the narrow ``dnc_context()`` window this
module opens after the operator approves the exact addresses.

**ADD ONLY, and that is structural rather than a policy note.** There is no removal function
in this module, no removal verb on the connector, and no removal effect in the loader's closed
set. A false-positive add is recoverable by a human in the provider UI; an automated removal
would un-suppress someone who asked to be left alone, which nothing in this system may do.

**The brain can narrow the list, never widen it.** Every address in an approved draft is
intersected with the profile's OPEN ledger candidates — rows carrying ``optout_detected`` or
``optout_unreadable`` and not yet ``dnc_added``. An address the brain proposes with no such row
is refused for that address, so a crafted reply that talks the model into naming a competitor
cannot suppress them. The DNC list id is resolved here, never taken from the draft.

**Read-back before the ledger row.** The provider accepting a call is not evidence it applied
it — the 2026-08-11 shape (``modifiedAt`` moved, the value did not). An add is recorded only
after a re-read shows the address actually on the list.

**Second caller: the sweep, for a clear opt-out (2026-09-24).** :mod:`agent.optout_sweep`
calls :func:`dispatch_approved_dnc_add` directly, no gate, for a reply that passed
``gtm_core.optout_watch.is_clear_optout`` — one address, the reply's own sender, which the
sweep has just recorded as an open ``optout_detected`` row. No model is involved on that
path, and every step below (kill switch, intersection, read-back) still runs.

Never raises: every failure maps to an outcome the caller records and shows the operator.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from agent.permissions import dnc_context
from gtm_core.optout_sets import DNC_ADDED_EVENT, optout_sets

log = logging.getLogger("agent.dnc_dispatch")

#: Kill switch, closed by default. A write to a third party's suppression record is opt-in
#: per deployment, and the closed list of enabling values is the same strict parse the
#: publish/schedule switches use — an unrecognised value leaves it OFF.
_ENABLED_ENV = "GTM_DNC_ADD_ENABLED"

#: Which DNC list to write to when the account has more than one. A single list needs no
#: configuration; two or more without this set is ambiguous and refuses, because guessing
#: would write a suppression to a list no sequence reads.
_LIST_ID_ENV = "SALESHANDY_DNC_LIST_ID"

#: The event this dispatcher WRITES once a read-back confirms an add. Sourced from
#: gtm_core.optout_sets so the writer here and the reader there share one spelling of
#: "already mirrored" rather than each hardcoding ``"dnc_added"`` separately.
_CLOSED_EVENT = DNC_ADDED_EVENT


def enabled() -> bool:
    """True only if :data:`_ENABLED_ENV` is set to a recognised true. Default: off."""
    return (os.getenv(_ENABLED_ENV) or "").strip().lower() in {"true", "1", "yes", "on"}


@dataclass
class DncDispatchOutcome:
    """What the dispatch did. ``detail`` carries the provider response or the refusal
    reason for the operator/ledger — never a secret; the connector's own robustness
    contract guarantees the key is never echoed into it."""

    ok: bool
    status: str  # added | dnc_add_failed | not_configured | disabled | dry_run | nothing_to_do
    detail: str = ""
    added: tuple[str, ...] = ()
    refused: tuple[str, ...] = field(default=())

    def operator_line(self) -> str:
        if self.status == "disabled":
            return (
                f"⚠️ DNC add is switched off ({_ENABLED_ENV} unset). Nothing was written to "
                "the provider and nothing was suppressed locally — add the address by hand "
                "in the Saleshandy UI today (same-day is the standing rule)."
            )
        if self.status == "not_configured":
            return (
                "⚠️ No Saleshandy API key for this profile — the DNC gate was approved but "
                "nothing could be written. Add the address by hand in the provider UI."
            )
        if self.status == "dry_run":
            return f"🧪 Dry run — would have added {len(self.added)} address(es); nothing sent."
        if self.status == "nothing_to_do":
            return "No address in the approved draft has an open opt-out row — nothing added."
        if self.ok:
            line = f"✅ Added {len(self.added)} address(es) to the provider DNC list."
            if self.refused:
                line += f" Refused {len(self.refused)} with no open opt-out row."
            return line
        return f"❌ DNC add failed: {self.detail}"


def open_candidates(ledgers: Any) -> set[str]:
    """Addresses with an OPEN opt-out row — detected/unreadable and not yet mirrored.

    This is the whole of the brain's permitted vocabulary for a DNC add. It is derived from
    the ledger, never from the draft, so the draft can only ever name a subset of it.

    The address key and the three per-event sets come from
    :func:`gtm_core.optout_sets.optout_sets`, shared with the campaign Results page so the
    two can never quietly disagree on either. This formula — OPEN is detected-or-unreadable,
    minus whatever is already mirrored — stays here, unchanged, and is pinned by
    ``tests/agent/test_dnc_dispatch.py::test_open_candidates_is_derived_from_the_ledger_not_the_draft``.
    """
    detected, unreadable, added, _unattributable = optout_sets(ledgers.iter_history())
    return (detected | unreadable) - added


async def _resolve_list_id(api_key: str) -> tuple[str | None, str]:
    """``(list_id, detail)``. Resolved HERE, never from the draft.

    One list on the account needs no configuration. Two or more without
    :data:`_LIST_ID_ENV` refuses: writing a suppression to the wrong list suppresses
    nobody, and looks exactly like success.

    FIXED 2026-09-24 (SC9b): this called ``GET /dnc-lists``, which this repo's OWN
    ``agent/mcp/saleshandy/server.py:list_dnc_lists`` already recorded, live-verified on
    2026-07-24, as a 404 (`"/dnc-lists and /do-not-contact both 404"`). The correct,
    verified path is ``GET /dnc`` with a ``pageSize`` param, not ``limit`` — the exact
    shape ``list_dnc_lists`` already uses. This was never caught because the PENDING SC9
    note that says "`_resolve_list_id` resolves cleanly" was reasoning from a DIFFERENT,
    correctly-pathed call (the connected `list_dnc_lists` tool), not from an actual run of
    this function — the believed-not-read failure this file's own culture warns against,
    just pointed at itself.
    """
    from agent.mcp.saleshandy.server import NOT_CONFIGURED, _call

    configured = (os.getenv(_LIST_ID_ENV) or "").strip()
    raw = await _call("GET", "/dnc", params={"page": 1, "pageSize": 100}, api_key=api_key)
    if raw == NOT_CONFIGURED:
        return None, "not_configured"
    try:
        body = json.loads(raw)
    except ValueError:
        return None, f"could not read the DNC lists: {raw[:120]}"
    node = body.get("payload", body)
    items = node.get("items", node) if isinstance(node, dict) else node
    ids = [str(i.get("id")) for i in items if isinstance(i, dict) and i.get("id")]
    if configured:
        if configured not in ids:
            return None, (
                f"{_LIST_ID_ENV}={configured!r} is not a DNC list on this account "
                f"({len(ids)} found) — refusing rather than writing to a list nobody reads"
            )
        return configured, ""
    if len(ids) == 1:
        return ids[0], ""
    if not ids:
        return None, "the account has no DNC list to write to"
    return None, (
        f"the account has {len(ids)} DNC lists and {_LIST_ID_ENV} is unset — refusing to "
        "guess which one a suppression belongs on"
    )


async def _add_items(api_key: str, list_id: str, addresses: list[str]) -> str:
    """The add request. Lives HERE, not in ``server.py``, so it is not an ``@mcp.tool()``
    and therefore never appears on the brain's surface at all.

    # VERIFY: path and body now come from the vendor's OpenAPI spec, not a guess — the
    # live add that would retire this marker has not run against the corrected call yet.
    #
    # CORRECTED 2026-09-24. The first live add (operator-approved) sent
    # `POST /dnc-lists/items` with object items and got HTTP 404 — the path was invented
    # from the CLI's `dnc add` verb. The vendor spec (open-api.saleshandy.com/api-doc-json,
    # operationId `DncController_addItemsToDncList`, read 2026-09-24) is `POST /v1/dnc` with
    # `AddDncListDto` = {"items": [string], "dncListId": string}, both required, and items
    # described as "emails or domains". So the items are FLAT STRINGS: the object shape the
    # GET returns is the stored record, not the request body. Same path as the list lookup
    # in `_resolve_list_id`; only the verb differs.
    """
    from agent.mcp.saleshandy.server import _call

    return await _call(
        "POST",
        "/dnc",
        json_body={"dncListId": list_id, "items": list(addresses)},
        api_key=api_key,
    )


#: Mirrors get_dnc_items's own documented max (`page_size: Items per page (max 100)`).
_READ_BACK_PAGE_SIZE = 100

#: A DNC list beyond this many pages (at 100/page) is far larger than any this repo has
#: observed (57 items at last read) — refusing a partial map is the safe direction, the
#: same choice :func:`agent.optout_categories._page_to_exhaustion` makes for the same
#: "a short read looks like a smaller answer" reason.
_READ_BACK_MAX_PAGES = 20


async def _read_back(api_key: str, list_id: str, addresses: list[str]) -> set[str]:
    """Which of ``addresses`` the provider actually holds after the write.

    The provider accepting a call is not evidence it applied it. An empty or unreadable
    read-back yields an empty set, so nothing is recorded — the conservative direction: a
    real add that we failed to confirm is retried next time, while a phantom add recorded
    as done would leave the person reachable with the ledger claiming otherwise.

    FIXED 2026-09-24 (SC9b), on evidence already sitting in this repo rather than a live
    call. Three defects, all now corrected:

    1. **Wrong endpoint.** This called ``GET /dnc-lists/{id}/items``.
       ``agent/mcp/saleshandy/server.py:get_dnc_items`` already recorded, live-verified on
       2026-07-24, that ``/dnc/{id}/items`` rejects — a different but adjacent path to the
       one actually used here, and the working, verified path is ``GET /dnc/{id}`` with no
       ``/items`` suffix. The 2026-09-22 "CONFIRMED live" note above this docstring verified
       the RESPONSE ENVELOPE shape by a read that evidently did not go through THIS
       function's own (wrong) path — another instance of the believed-not-read trap.
    2. **Wrong param name.** ``limit`` is not read by the API; the documented and verified
       parameter is ``pageSize`` (:func:`agent.mcp.saleshandy.server.get_dnc_items`).
    3. **No exhaustion paging.** Now pages until ``meta.currentPage >= meta.totalPages`` —
       literally what that function's own docstring instructs ("Page through until
       meta.currentPage >= meta.totalPages"). A page with missing or malformed ``meta``
       cannot be proven complete, so it is treated as unreadable and the WHOLE read-back
       refuses (returns no confirmations) rather than banking a partial page — matching
       :func:`agent.optout_categories._page_to_exhaustion`'s "a short read looks like a
       smaller answer" rule. ``type="email"`` is passed because :func:`_add_items` only
       ever adds emails; this is the documented ``item_type`` values ("all"/"email"/
       "domain"), not a guess.

    :func:`_add_items`'s endpoint was corrected separately, from the vendor's spec — see
    its own comment.
    """
    from agent.mcp.saleshandy.server import _call
    from gtm_core import suppression

    wanted = {a.lower() for a in addresses}
    confirmed: set[str] = set()
    for page in range(1, _READ_BACK_MAX_PAGES + 1):
        raw = await _call(
            "GET",
            f"/dnc/{list_id}",
            params={"type": "email", "page": page, "pageSize": _READ_BACK_PAGE_SIZE},
            api_key=api_key,
        )
        try:
            body = json.loads(raw)
        except ValueError:
            return set()
        payload = body.get("payload") if isinstance(body, dict) else None
        meta = payload.get("meta") if isinstance(payload, dict) else None
        current_page = meta.get("currentPage") if isinstance(meta, dict) else None
        total_pages = meta.get("totalPages") if isinstance(meta, dict) else None
        if not isinstance(current_page, int) or not isinstance(total_pages, int):
            # No trustworthy page-count signal — cannot tell whether this is the whole
            # list or page 1 of several, so this is an UNREADABLE read-back, not a
            # confirmed-empty one. Refuse rather than bank a possibly-partial page.
            log.warning(
                "dnc_dispatch: read-back page %d for list %s carried no usable "
                "meta.currentPage/totalPages — refusing rather than confirming a "
                "possibly-partial page",
                page,
                list_id,
            )
            return set()

        emails, domains = suppression.normalize_dnc_payload(body)
        confirmed |= wanted & emails
        confirmed |= {a for a in wanted if a.rpartition("@")[2] in domains}

        if current_page >= total_pages:
            return confirmed

    log.error(
        "dnc_dispatch: read-back for list %s exceeded %d pages — refusing a partial map",
        list_id,
        _READ_BACK_MAX_PAGES,
    )
    return set()


def _preflight_refusal(cfg: Any) -> DncDispatchOutcome | None:
    """The two refusals that precede any reading of the draft: the kill switch, then the key.

    Ordered deliberately. With the switch off nothing is attempted at all — not even a
    lookup — so a deployment that has not opted in cannot reach the provider by any path.
    """
    if not enabled():
        return DncDispatchOutcome(
            ok=False, status="disabled", detail=f"{_ENABLED_ENV} is not set to a recognised true"
        )
    if not getattr(cfg, "saleshandy_api_key", None):
        log.warning("dnc_dispatch: no Saleshandy API key — refusing to dispatch")
        return DncDispatchOutcome(
            ok=False, status="not_configured", detail="no Saleshandy API key for this profile"
        )
    return None


async def dispatch_approved_dnc_add(
    cfg: Any,
    ledgers: Any,
    *,
    draft: dict,
    dry_run: bool = False,
    approved_by: str = "operator",
) -> DncDispatchOutcome:
    """Add exactly the approved-and-evidenced addresses to the provider DNC list, or refuse.

    Never raises. Order matters and is asserted by tests:

      1. kill switch (:data:`_ENABLED_ENV`) — off means nothing is attempted;
      2. an API key must be present — a missing key fails closed, with no fallback source;
      3. ``dry_run`` returns before anything can reach the provider;
      4. the draft is INTERSECTED with :func:`open_candidates` — narrowing only;
      5. the list id is resolved here, never read from the draft;
      6. the write happens inside ``dnc_context()``, the only window that admits it;
      7. a read-back confirms each address before any ``dnc_added`` row or local
         suppression entry is written.

    ``approved_by`` is recorded on each ``dnc_added`` row: ``"operator"`` for a gate
    approval, ``"auto:clear-optout"`` when :mod:`agent.optout_sweep` adds a clear opt-out
    itself — the same seven steps either way, so the audit says which path ran.
    """
    refusal = _preflight_refusal(cfg)
    if refusal is not None:
        return refusal

    api_key = cfg.saleshandy_api_key
    proposed = [str(a).strip().lower() for a in (draft.get("addresses") or []) if str(a).strip()]
    if not proposed:
        return DncDispatchOutcome(
            ok=False, status="nothing_to_do", detail="the draft names no address"
        )

    allowed = open_candidates(ledgers)
    addresses = sorted(a for a in proposed if a in allowed)
    refused = tuple(sorted(a for a in proposed if a not in allowed))
    if refused:
        log.warning(
            "dnc_dispatch: refusing %d address(es) with no open opt-out ledger row", len(refused)
        )
    if not addresses:
        return DncDispatchOutcome(
            ok=False,
            status="nothing_to_do",
            detail="no proposed address has an open optout_detected/optout_unreadable row",
            refused=refused,
        )

    if dry_run:
        return DncDispatchOutcome(
            ok=True,
            status="dry_run",
            detail=f"would add {len(addresses)} address(es)",
            added=tuple(addresses),
            refused=refused,
        )

    list_id, detail = await _resolve_list_id(api_key)
    if list_id is None:
        status = "not_configured" if detail == "not_configured" else "dnc_add_failed"
        return DncDispatchOutcome(ok=False, status=status, detail=detail, refused=refused)

    with dnc_context():
        # Send only what the list does not already hold. The provider rejects a request
        # naming an existing entry with HTTP 400 ("Inserted email or domain already exist in
        # this list", observed live 2026-09-24) — and the connector hides that body — so an
        # opt-out someone had already added by hand would otherwise fail forever. An
        # unreadable pre-read yields an empty set, so everything is sent: the safe direction.
        already = await _read_back(api_key, list_id, addresses)
        to_add = [a for a in addresses if a not in already]
        if to_add:
            raw = await _add_items(api_key, list_id, to_add)
            if raw.startswith("[saleshandy-error]"):
                return DncDispatchOutcome(
                    ok=False, status="dnc_add_failed", detail=raw[:200], refused=refused
                )
        confirmed = await _read_back(api_key, list_id, addresses)

    missing = [a for a in addresses if a not in confirmed]
    if missing:
        # The call was accepted and the addresses are not there. Record NOTHING: a ledger
        # row claiming suppression that the provider does not hold is worse than no row,
        # because the next reconcile trusts it and the person stays reachable.
        return DncDispatchOutcome(
            ok=False,
            status="dnc_add_failed",
            detail=(
                f"the provider accepted the add but a re-read does not show "
                f"{len(missing)} address(es) — nothing recorded, so the next run retries"
            ),
            added=tuple(sorted(confirmed)),
            refused=refused,
        )

    from gtm_core import suppression

    for addr in addresses:
        ledgers.append_history(
            {
                "event": _CLOSED_EVENT,
                "skill": "dnc-add",
                "email": addr,
                "dnc_list_id": list_id,
                "confirmed_by_read_back": True,
                "approved_by": approved_by,
            }
        )
    ledger_path = cfg.content_root / ledgers.profile / "prospects" / ".pool" / "suppression.csv"
    try:
        suppression.append(
            ledger_path,
            [
                suppression.Suppression(email=a, reason="dnc-optout", note="mirrored by dnc_add")
                for a in addresses
            ],
        )
    except Exception as exc:  # noqa: BLE001 — the provider write already succeeded
        log.warning("dnc_dispatch: local suppression append failed: %s", exc)

    return DncDispatchOutcome(
        ok=True,
        status="added",
        detail=f"added {len(addresses)} address(es) to DNC list {list_id}",
        added=tuple(addresses),
        refused=refused,
    )
