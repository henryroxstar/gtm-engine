"""Strict and windowed READS of a ``costs.jsonl`` — the totals a budget is built from (MM2/MM4).

Split out of :mod:`gtm_core.ledgers` (which re-exports every public name here, so no import path
changed) when the strict-read posture arrived. Two postures, one parser:

* lenient (default) — an unparseable line is skipped and an I/O error propagates untouched: the
  contract the single-tenant VPS and ``tests/contracts/test_metering.py`` rely on;
* ``strict`` — the multi-tenant posture: an unreadable ledger, an interior corrupt line or a cost that
  is not a number raises :class:`LedgerUnreadableError`, while a torn FINAL line (a worker killed
  mid-append) is tolerated. A tenant whose spend cannot be totalled is refused, never read as zero.

Stdlib only; reads, never writes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def _current_year_month() -> str:
    """Current ``YYYY-MM`` (UTC). Used as the default cost-aggregation window."""
    return datetime.now(UTC).strftime("%Y-%m")


class LedgerUnreadableError(RuntimeError):
    """A cost ledger could not be trusted to total (strict reads only): unreadable, or a row that
    cannot be read is sitting where a recorded spend should be."""


def _cost_records(costs_path: Path, strict: bool):
    """Yield each parsed record of one ``costs.jsonl``.

    Lenient (default): an unparseable line is skipped, an I/O error propagates untouched — the
    contract every existing caller (and ``tests/contracts/test_metering.py``) relies on.

    ``strict`` is the multi-tenant posture. A line that is not JSON is tolerated ONLY as the very last
    one — a worker killed mid-append leaves exactly that, and must not make a tenant unbillable — and
    anywhere else it is spend that vanished from the total, so it raises :class:`LedgerUnreadableError`,
    as does any read failure. A ledger that does not exist is not unreadable: nothing was metered.
    """
    if not costs_path.is_file():
        return
    try:
        with costs_path.open("r", encoding="utf-8") as fh:
            lines = [line.strip() for line in fh]
    except (OSError, UnicodeDecodeError) as exc:
        if strict:
            raise LedgerUnreadableError(f"cannot read {costs_path.name}: {exc}") from exc
        raise
    last = max((i for i, line in enumerate(lines) if line), default=-1)
    for i, line in enumerate(lines):
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            if strict and i != last:
                raise LedgerUnreadableError(
                    f"{costs_path.name} line {i + 1} is not valid JSON; its spend cannot be totalled"
                ) from exc
            continue
        if not isinstance(record, dict):
            if strict:
                raise LedgerUnreadableError(f"{costs_path.name} line {i + 1} is not a record")
            continue
        yield record


def _cost_of(record: dict, costs_path: Path, strict: bool) -> float | None:
    """The row's ``cost_usd`` as a float, or None to skip it (lenient). Strict raises instead."""
    try:
        return float(record.get("cost_usd"))
    except (TypeError, ValueError) as exc:
        if strict:
            raise LedgerUnreadableError(
                f"{costs_path.name} has a row whose cost_usd is not a number"
            ) from exc
        return None


def sum_month_costs(
    costs_path: Path, year_month: str | None = None, *, strict: bool = False
) -> float:
    """Sum ``cost_usd`` in one ``costs.jsonl`` over a ``YYYY-MM`` window.

    Module-level on purpose. The multi-tenant cap in :mod:`gtm_core.metering` rolls up
    every profile's ledger under a workspace, and it must use the SAME parser the
    single-tenant cap uses — a second implementation would let the two totals drift
    apart silently, which is the failure this whole ledger split already caused once.

    Tolerant by design: an unparseable line, a missing ``ts``, or a non-numeric
    ``cost_usd`` is skipped rather than raising, so one corrupt append can never make a
    budget guard unreadable (and therefore fail-open). ``strict=True`` is the tenant opt-in that
    refuses instead — see :func:`_cost_records`.
    """
    window = year_month or _current_year_month()
    total = 0.0
    for record in _cost_records(costs_path, strict):
        ts = record.get("ts", "")
        if not isinstance(ts, str) or not ts.startswith(window):
            continue
        cost = _cost_of(record, costs_path, strict)
        if cost is not None:
            total += cost
    return total


def sum_run_costs(costs_path: Path, run_id: str, *, strict: bool = False) -> float:
    """Sum ``cost_usd`` of the rows stamped with ``run_id`` (MM2), over ALL time.

    Not month-windowed: a run that straddled the 1st still owes every cent. A blank ``run_id``
    matches nothing — ``""`` must never mean "all the rows that name no run".
    """
    if not isinstance(run_id, str) or not run_id.strip():
        return 0.0
    total = 0.0
    for record in _cost_records(costs_path, strict):
        if record.get("run_id") != run_id:
            continue
        cost = _cost_of(record, costs_path, strict)
        if cost is not None:
            total += cost
    return total


def unit_total_in(
    costs_path: Path,
    tool: str,
    unit_key: str,
    year_month: str | None = None,
    *,
    strict: bool = False,
) -> float:
    """Sum a non-dollar unit for ``tool`` in one ``costs.jsonl`` over a ``YYYY-MM`` window.

    Lenient by default (a corrupt line or an unreadable ``units`` is skipped). ``strict`` follows
    :func:`sum_month_costs`: an unreadable file, an interior corrupt line or a unit count that is not
    a number raises :class:`LedgerUnreadableError`; a torn FINAL line is tolerated.
    """
    window = year_month or _current_year_month()
    total = 0.0
    for record in _cost_records(costs_path, strict):
        if record.get("tool") != tool:
            continue
        ts = record.get("ts", "")
        if not isinstance(ts, str) or not ts.startswith(window):
            continue
        units = record.get("units")
        try:
            if isinstance(units, dict):
                total += float(units.get(unit_key, 0) or 0)
            elif record.get("unit_kind") == unit_key:
                total += float(units or 0)
        except (TypeError, ValueError) as exc:
            if strict:
                raise LedgerUnreadableError(
                    f"{costs_path.name} has a row whose units are not a number"
                ) from exc
    return total


def content_root_unit_total(
    content_root: Path, tool: str, unit_key: str, *, strict: bool = False
) -> float:
    """``tool``'s ``unit_key`` this month across EVERY profile ledger under one content root.

    A provider share is allocated per WORKSPACE and a workspace's content root holds all its
    profiles, so the count that a share is measured against is this one — a single profile's ledger
    would let a workspace spend a full share per profile.
    """
    if not content_root.is_dir():
        return 0.0
    try:
        return sum(
            unit_total_in(p, tool, unit_key, strict=strict)
            for p in content_root.glob("*/costs.jsonl")
        )
    except OSError as exc:
        if strict:
            raise LedgerUnreadableError(
                f"cannot list the ledgers under {content_root}: {exc}"
            ) from exc
        raise


def _read_rows_strict(path: Path) -> list[dict]:
    """All rows of a JSONL ledger; any line that is not a JSON object raises ``ValueError``."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    rows: list[dict] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path.name} line {n} is not valid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name} line {n} is not a JSON object")
        rows.append(row)
    return rows
