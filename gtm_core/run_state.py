"""Persistent step-by-step state machine for prospect runs.

Enables resuming an interrupted or failed run from the last completed stage
without re-spending metered credits or re-discovering existing candidates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from gtm_core.prospect_paths import run_state_json

StageStatus = Literal["pending", "running", "completed", "failed"]

STAGES: tuple[str, ...] = (
    "init",
    "discovery",
    "signal_hunt",
    "gate_score",
    "enrichment",
    "output",
)


@dataclass
class StageState:
    """State of an individual coarse-grained stage in a prospect run."""

    status: StageStatus = "pending"
    started_at: str | None = None
    completed_at: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class RunState:
    """Cumulative state of an active or recent prospect run."""

    profile: str
    run_id: str
    mode: str = "full"
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None
    stages: dict[str, StageState] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        profile: str,
        mode: str = "full",
        run_id: str | None = None,
    ) -> RunState:
        """Initialize a fresh RunState with all stages pending."""
        active_id = (
            run_id or f"run-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        )
        stages = {name: StageState() for name in STAGES}
        return cls(
            profile=profile,
            run_id=active_id,
            mode=mode,
            started_at=datetime.now(UTC).isoformat(),
            stages=stages,
        )

    def _check_stage(self, name: str) -> None:
        if name not in self.stages:
            raise ValueError(f"Unknown stage: {name!r}. Must be one of {STAGES}")

    def start_stage(self, name: str) -> None:
        """Mark a stage as running."""
        self._check_stage(name)
        stage = self.stages[name]
        stage.status = "running"
        stage.started_at = datetime.now(UTC).isoformat()
        stage.error = None

    def complete_stage(self, name: str, metrics: dict[str, Any] | None = None) -> None:
        """Mark a stage as completed and record metrics."""
        self._check_stage(name)
        stage = self.stages[name]
        stage.status = "completed"
        stage.completed_at = datetime.now(UTC).isoformat()
        if metrics:
            stage.metrics.update(metrics)
        stage.error = None

        # If all stages completed, set run completed_at
        if self.is_completed:
            self.completed_at = datetime.now(UTC).isoformat()

    def fail_stage(self, name: str, error: str) -> None:
        """Mark a stage as failed with an error message."""
        self._check_stage(name)
        stage = self.stages[name]
        stage.status = "failed"
        stage.completed_at = datetime.now(UTC).isoformat()
        stage.error = error

    def can_resume(self, stage_name: str) -> bool:
        """Return True if stage has already completed and can be skipped."""
        self._check_stage(stage_name)
        return self.stages[stage_name].status == "completed"

    def resume_from(self) -> str | None:
        """Return the first incomplete (pending, running, or failed) stage name.

        Returns None if all stages have completed.
        """
        for name in STAGES:
            if self.stages[name].status != "completed":
                return name
        return None

    @property
    def is_completed(self) -> bool:
        """Return True if every stage in the run has completed."""
        return all(s.status == "completed" for s in self.stages.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "run_id": self.run_id,
            "mode": self.mode,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "stages": {name: asdict(stage) for name, stage in self.stages.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunState:
        raw_stages = data.get("stages", {})
        stages: dict[str, StageState] = {}
        for name in STAGES:
            if name in raw_stages:
                s = raw_stages[name]
                stages[name] = StageState(
                    status=s.get("status", "pending"),
                    started_at=s.get("started_at"),
                    completed_at=s.get("completed_at"),
                    metrics=s.get("metrics", {}),
                    error=s.get("error"),
                )
            else:
                stages[name] = StageState()

        return cls(
            profile=str(data.get("profile", "")),
            run_id=str(data.get("run_id", "")),
            mode=str(data.get("mode", "full")),
            started_at=str(data.get("started_at", "")),
            completed_at=data.get("completed_at"),
            stages=stages,
        )


def save_run_state(state: RunState, path: Path) -> None:
    """Atomically write the run state to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False)

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        prefix="run_state_",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    ) as tf:
        tf.write(payload)
        tf.flush()
        os.fsync(tf.fileno())
        tmp_name = tf.name

    os.replace(tmp_name, path)


def load_run_state(path: Path) -> RunState | None:
    """Load RunState from path, returning None if missing or corrupt."""
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        return RunState.from_dict(data)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def get_or_create_run_state(
    profile: str,
    mode: str = "full",
    content_root: Path | None = None,
    force_new: bool = False,
    max_resume_age_hours: float = 48.0,
) -> tuple[RunState, bool]:
    """Load existing uncompleted run state for resumption, or create a new one.

    Returns:
        (state, resumed) tuple where resumed is True if an existing incomplete state was reused.
    """
    path = run_state_json(profile, content_root)
    if not force_new:
        existing = load_run_state(path)
        if existing is not None and not existing.is_completed:
            try:
                t_start = datetime.fromisoformat(existing.started_at.replace("Z", "+00:00"))
                age_h = (datetime.now(UTC) - t_start).total_seconds() / 3600.0
                if age_h <= max_resume_age_hours:
                    return existing, True
            except (ValueError, TypeError):
                pass

    fresh = RunState.new(profile=profile, mode=mode)
    save_run_state(fresh, path)
    return fresh, False


def main(argv: list[str] | None = None) -> int:
    """CLI for checking and managing prospect run state."""
    parser = argparse.ArgumentParser(
        prog="gtm_core.run_state",
        description="Inspect and manage prospect run resumption state.",
    )
    parser.add_argument("--profile", required=True, help="Tenant profile name")
    parser.add_argument(
        "action",
        choices=["status", "resume-from", "reset"],
        help="Action to perform",
    )
    args = parser.parse_args(argv)

    path = run_state_json(args.profile)
    state = load_run_state(path)

    if args.action == "status":
        if state is None:
            print(f"No run state found for profile '{args.profile}'.")
            return 0
        print(f"Run ID:    {state.run_id} (mode={state.mode})")
        print(f"Started:   {state.started_at}")
        print(f"Completed: {state.completed_at or 'In progress'}")
        print("\nStages:")
        for name, stage in state.stages.items():
            status_str = stage.status.upper()
            metrics_str = f" {stage.metrics}" if stage.metrics else ""
            err_str = f" [ERROR: {stage.error}]" if stage.error else ""
            print(f"  {name:<14} {status_str:<10}{metrics_str}{err_str}")
        next_stage = state.resume_from()
        if next_stage:
            print(f"\nNext stage to execute: {next_stage}")
        else:
            print("\nAll stages completed.")
        return 0

    if args.action == "resume-from":
        if state is None:
            print("init")
            return 0
        next_stage = state.resume_from()
        print(next_stage or "none")
        return 0

    if args.action == "reset":
        if path.exists():
            path.unlink()
            print(f"Profile '{args.profile}': run state reset.")
        else:
            print(f"Profile '{args.profile}': no run state to reset.")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
