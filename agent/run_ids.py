"""agent.run_ids — a scheduled pipeline run's DETERMINISTIC id, and what an existing manifest means (F5).

``--pipeline`` used to mint ``r-YYYYMMDD-HHMM-<source>``, so a run that crashed was never resumed: the next
invocation got a new id and redid everything. The id is now ``r-YYYYMMDD-<source>`` — the **UTC** date, so it
does not shift with the host's timezone — and re-running it the same day finds the same manifest, where the
existing per-node resume (:meth:`agent.pipeline.PipelineRunner.run`) picks up at the node that failed.

* ``--backfill`` carries its own suffix (``-backfill``), so it can never collide with the daily run.
* ``--rerun`` is the deliberate second run in a day: ``-2``, ``-3``, …
* A run id from 23:58 UTC and its re-run at 00:03 UTC are DIFFERENT ids, by rule.

No automatic retry is added (PRD D10): a failure still alerts through ``notify.sh`` and a human re-runs.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

#: What an existing manifest means for today's invocation.
NEW = "new"  # no (or unreadable) manifest: start fresh
RESUME = "resume"  # partly done or failed: run again and the runner resumes at the unfinished node
COMPLETE = "complete"  # every stage finished: nothing to do today
GATE = "gate"  # parked at a human gate: do not run (or push) again

_DONE = frozenset({"ok", "skipped"})


def manifest_state(path: Path) -> str:
    """Classify the run manifest at ``path``. Unreadable or empty ⇒ ``new`` (never blocks the day)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return NEW
    stages = [
        s
        for s in (data.get("stages") if isinstance(data, dict) else None) or []
        if isinstance(s, dict)
    ]
    if not stages:
        return NEW
    statuses = [s.get("status") for s in stages]
    if "failed" in statuses:
        return RESUME  # resume at the failed node
    if "awaiting_approval" in statuses:
        return GATE
    if all(s in _DONE for s in statuses):
        return COMPLETE
    return RESUME


def daily_run_id(
    runs_dir: Path, source: str, now_utc: datetime, *, backfill: bool = False, rerun: bool = False
) -> tuple[str, str]:
    """``(run_id, state)`` for this invocation. See the module docstring for the rules."""
    base = f"r-{now_utc:%Y%m%d}-{source}" + ("-backfill" if backfill else "")
    if rerun:
        n = 2
        while (runs_dir / f"{base}-{n}.json").exists():
            n += 1
        return f"{base}-{n}", NEW
    return base, manifest_state(runs_dir / f"{base}.json")
