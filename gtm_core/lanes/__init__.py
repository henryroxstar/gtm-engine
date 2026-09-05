"""Lanes — every pooled row ends in exactly one lane, never stranded.

Until 2026-09-03 the enrollment gate hard-dropped any row whose researcher ``verdict`` was
not ``send``, with no fallback destination: of 598 pooled rows, 461 could reach no sequence
at all, and the judge's rejections (237 re-angles, 87 drops on the 2026-09-01 sweep) were a
report, never a route. This package is the route.

* :mod:`.router` — the deterministic ladder: excluded → hold → personalised / repair / generic.
* :mod:`.triggers` — what holds and what excludes, each reading an existing artifact.
* :mod:`.context` — those artifacts, loaded once; a missing optional one is a note, never a crash.
* :mod:`.decisions` — the hold queue's ledgers; ``plan`` by default, ``apply`` on request.
* :mod:`.cli` — ``python -m gtm_core.prospects lanes route|hold-apply|suggest-rules``.

The judge advises (its normalised defect scope decides hold vs repair); deterministic code
routes; the operator decides only what is held. A judge ``drop`` never removes a row here.
"""

from __future__ import annotations

from .cli import main  # noqa: F401
from .context import RouterContext, load_context  # noqa: F401
from .decisions import (  # noqa: F401
    ApplyPlan,
    DecisionEntry,
    apply,
    plan_apply,
    read_decisions,
    read_filled,
    read_state,
    write_hold_csv,
    write_state,
)
from .model import (  # noqa: F401
    DECISIONS,
    HOLD_COPY,
    HOLD_ORDER,
    LANE_COLUMNS,
    LANES,
    SALVAGE_KINDS,
    Routed,
)
from .router import (  # noqa: F401
    RoutingResult,
    account_key,
    judge_index,
    route,
    route_row,
    summary,
    write_lanes,
)
from .sheet import build_sheet_payload, render_sheet, write_sheet  # noqa: F401

__all__ = [
    "LANES",
    "LANE_COLUMNS",
    "DECISIONS",
    "SALVAGE_KINDS",
    "HOLD_ORDER",
    "HOLD_COPY",
    "Routed",
    "RouterContext",
    "load_context",
    "RoutingResult",
    "route",
    "route_row",
    "judge_index",
    "account_key",
    "write_lanes",
    "summary",
    "DecisionEntry",
    "ApplyPlan",
    "read_decisions",
    "read_state",
    "write_state",
    "write_hold_csv",
    "read_filled",
    "plan_apply",
    "apply",
    "build_sheet_payload",
    "render_sheet",
    "write_sheet",
    "main",
]
