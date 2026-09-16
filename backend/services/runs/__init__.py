"""Run lifecycle services — the backend twin of the VPS agent root, split out of
backend/routers/runs.py as pure motion (PRD 2026-09-01 §7 Phase 1a) and then unified onto
one spine (Phase 1b, the 2026-09-02 run-lifecycle unification note).

submodules: ``state`` (the one home of in-process run state) · ``budget`` · ``events`` ·
``gates`` · ``persistence`` · ``lifecycle`` (the transitions both engines share) ·
``executor`` (prompt mode) · ``pack_executor`` (pack mode) · ``fake`` (the dev-only scripted
executor behind ``GTM_FAKE_RUNS``) · ``reconcile`` (startup) · ``admission`` / ``queries`` /
``decisions`` / ``stream`` (the API's own work).

The import is **eager**: a lazy ``__init__`` would leave a submodule unloaded until first
use, which is how a registry ends up half-populated. The router keeps the FastAPI handlers
and re-imports the lifecycle names from here, so its pre-split import surface is unchanged.
"""

from __future__ import annotations

from . import (  # noqa: F401 — eager: every submodule loads with the package
    admission,
    budget,
    decisions,
    events,
    executor,
    fake,
    gates,
    lifecycle,
    pack_executor,
    persistence,
    queries,
    reconcile,
    state,
    stream,
)
