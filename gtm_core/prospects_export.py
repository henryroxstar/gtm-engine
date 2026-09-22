"""The run's HubSpot CSV — each row built for the ACCOUNT its item merged into.

Split out of :mod:`gtm_core.prospects_import`, which keeps the column contract and renders a
row; this module decides what that row is OF. The CSV is the file a human pools and loads
into a sequencer, so two things are decided here and nowhere else:

  * **Whose row it is.** An item names its account however that run's source spelled it — a
    cleaned name, no domain. The ledger already knows the account (:mod:`prospects_merge`
    matched it), so the row takes ``company`` and ``domain`` from the MERGED ledger row and
    only the contact's own fields from the item. Written from the item, a contact at
    "Contoso Freight, Inc." (``do-not-contact``) was exported as "Contoso Freight" with no
    domain — a row the pool's exact-key exclusion could not place, which then listed the
    contact as ready to load.
  * **Whether it is a row at all.** An item whose account is retired, has replied, or
    carries the researcher's ``drop`` is not exported, whatever the item says about itself.

Rows are built by :meth:`RunExport.plan`, which ``upsert_latest`` calls BEFORE the ledger is
written: a row that cannot be rendered stops the run with the ledger untouched, instead of
leaving a ledger that ran ahead of its export.

stdlib-only, matching the rest of gtm_core.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from gtm_core.prospects_item import ItemError, is_refusal, new_account_defaults
from gtm_core.prospects_state import RETIRED_STATUSES
from gtm_core.signal_record import Verdict

#: An account in one of these is out of the cold motion: nothing at it reaches the send CSV.
NEVER_EXPORTED_STATUSES = RETIRED_STATUSES | {"replied"}


def exclusion_reason(account: dict) -> str:
    """Why nothing at this ledger account may be exported — ``""`` when it may."""
    status = str(account.get("status") or "").strip().lower()
    if status in NEVER_EXPORTED_STATUSES:
        return f"status is {status}"
    if str(account.get("verdict") or "").strip().lower() == Verdict.DROP:
        return "verdict is drop"
    return ""


class RunExport:
    """One run's CSV: planned against the merged accounts, written after the ledger."""

    def __init__(
        self, items: list[dict], columns: Sequence[str], render: Callable[[dict], list[Any]]
    ) -> None:
        self.items = items
        self._columns, self._render = columns, render
        self.rows: list[list[Any]] = []
        self.refused = 0  # the item itself is a recorded refusal
        self.excluded: list[tuple[str, str]] = []  # (ledger company, why) — account is out

    def plan(self, landed: list[dict]) -> None:
        """``upsert_latest``'s ``on_merged`` hook: ``landed[i]`` is ``items[i]``'s account."""
        for n, (item, account) in enumerate(zip(self.items, landed, strict=True), start=1):
            if is_refusal(item):
                self.refused += 1
                continue
            company = str(account.get("company") or item.get("company") or "")
            why = exclusion_reason(account)
            if why:
                self.excluded.append((company, why))
                continue
            row = new_account_defaults(item)
            row.update(company=company, domain=str(account.get("domain") or ""))
            try:
                self.rows.append(self._render(row))
            except (AttributeError, TypeError, ValueError) as exc:
                raise ItemError(
                    f"row {n} ({item.get('company')!r}): cannot be written to the CSV — {exc}"
                ) from exc

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(self._columns)
            w.writerows(self.rows)
