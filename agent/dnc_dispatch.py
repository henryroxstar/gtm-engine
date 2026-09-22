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

Never raises: every failure maps to an outcome the caller records and shows the operator.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from agent.permissions import dnc_context

log = logging.getLogger("agent.dnc_dispatch")

#: Kill switch, closed by default. A write to a third party's suppression record is opt-in
#: per deployment, and the closed list of enabling values is the same strict parse the
#: publish/schedule switches use — an unrecognised value leaves it OFF.
_ENABLED_ENV = "GTM_DNC_ADD_ENABLED"

#: Which DNC list to write to when the account has more than one. A single list needs no
#: configuration; two or more without this set is ambiguous and refuses, because guessing
#: would write a suppression to a list no sequence reads.
_LIST_ID_ENV = "SALESHANDY_DNC_LIST_ID"

#: Ledger events that make an address a legitimate candidate — both mean "this person, or
#: someone this system could not read, asked us to stop".
_OPEN_EVENTS = frozenset({"optout_detected", "optout_unreadable"})

#: The event that closes a candidate: already mirrored, nothing to do.
_CLOSED_EVENT = "dnc_added"


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
    """
    opened: set[str] = set()
    closed: set[str] = set()
    for row in ledgers.iter_history():
        event = row.get("event")
        addr = str(row.get("email") or "").strip().lower()
        if not addr:
            continue
        if event in _OPEN_EVENTS:
            opened.add(addr)
        elif event == _CLOSED_EVENT:
            closed.add(addr)
    return opened - closed


async def _resolve_list_id(api_key: str) -> tuple[str | None, str]:
    """``(list_id, detail)``. Resolved HERE, never from the draft.

    One list on the account needs no configuration. Two or more without
    :data:`_LIST_ID_ENV` refuses: writing a suppression to the wrong list suppresses
    nobody, and looks exactly like success.
    """
    from agent.mcp.saleshandy.server import NOT_CONFIGURED, _call

    configured = (os.getenv(_LIST_ID_ENV) or "").strip()
    raw = await _call("GET", "/dnc-lists", params={"page": 1, "limit": 100}, api_key=api_key)
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

    # VERIFY: endpoint path/verb/body are taken from the vendor's CLI reference
    # (`saleshandy dnc add --dnc-list-id ID --items …`, read 2026-09-21) and have NOT been
    # run against a live account. The first live add is an operator task; until it passes,
    # this marker stays and `GTM_DNC_ADD_ENABLED` stays closed by default.
    #
    # NARROWED 2026-09-22, read-only, WITHOUT performing an add. `dncListId` is confirmed as
    # the right key. The `items` SHAPE is genuinely ambiguous and is the thing the live add
    # must settle, because the two available sources disagree:
    #   - the connected MCP server's `add_dnc_items` takes flat strings (["a@b.com"]) and says
    #     "the API will automatically detect whether each item is an email or domain";
    #   - but the live GET returns items AS OBJECTS, {"id","value","type","createdAt","addedBy"},
    #     which is what the body below mirrors.
    # So do not "fix" this to flat strings on the strength of the tool description alone — that
    # is the believed-not-read failure this whole contract exists to stop. Run the add, and if
    # the provider rejects the object form, the flat form is the next thing to try.
    """
    from agent.mcp.saleshandy.server import _call

    return await _call(
        "POST",
        "/dnc-lists/items",
        json_body={
            "dncListId": list_id,
            "items": [{"value": a, "type": "email"} for a in addresses],
        },
        api_key=api_key,
    )


async def _read_back(api_key: str, list_id: str, addresses: list[str]) -> set[str]:
    """Which of ``addresses`` the provider actually holds after the write.

    The provider accepting a call is not evidence it applied it. An empty or unreadable
    read-back yields an empty set, so nothing is recorded — the conservative direction: a
    real add that we failed to confirm is retried next time, while a phantom add recorded
    as done would leave the person reachable with the ledger claiming otherwise.

    CONFIRMED live 2026-09-22 (read-only): the envelope is
    ``{"payload": {"dncListDetails": [{"id","value","type",...}], "meta": {...}}}`` and
    :func:`suppression.normalize_dnc_payload` already keys on ``dncListDetails``, so the
    parse is right. Note the connected MCP server's own tool description says ``dncDetails``
    and is WRONG — the live read is the authority.

    UNRESOLVED, and deliberately not "fixed" blind: this sends ``limit``; the documented
    parameter is ``pageSize``, and the documented ``type`` filter is omitted entirely. If
    ``limit`` is ignored the page defaults to 25, and the live list already holds 57 items,
    so a newly added address could sit outside page 1 and never be confirmed. This function
    also does not page to exhaustion the way :func:`agent.optout_categories._page_to_exhaustion`
    does for the same "a short read looks like a smaller answer" reason. It fails SAFE — an
    unconfirmed add is retried, never recorded as done — which is why the fix waits for the
    live add rather than guessing at request parameters that currently work.
    """
    from agent.mcp.saleshandy.server import _call

    raw = await _call(
        "GET",
        f"/dnc-lists/{list_id}/items",
        params={"page": 1, "limit": 100},
        api_key=api_key,
    )
    try:
        body = json.loads(raw)
    except ValueError:
        return set()
    from gtm_core import suppression

    emails, domains = suppression.normalize_dnc_payload(body)
    wanted = {a.lower() for a in addresses}
    confirmed = wanted & emails
    confirmed |= {a for a in wanted if a.rpartition("@")[2] in domains}
    return confirmed


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
        raw = await _add_items(api_key, list_id, addresses)
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
