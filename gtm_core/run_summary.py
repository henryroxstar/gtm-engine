"""Structured machine-readable run summary for prospecting.

Produces a run_summary.json artifact alongside markdown reports, allowing
the backend, control plane, or client app to inspect run metrics without scraping markdown.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gtm_core.paths import resolve_content_root
from gtm_core.prospect_paths import (
    run_state_json,
    run_summary_json,
)
from gtm_core.run_state import load_run_state


@dataclass
class RunSummary:
    """Structured summary of a prospect run."""

    run_id: str
    profile: str
    mode: str = "full"
    started_at: str = ""
    completed_at: str | None = None

    # Pipeline counts
    accounts_discovered: int = 0
    accounts_gated: int = 0
    accounts_dropped: int = 0
    accounts_enriched: int = 0
    accounts_ready: int = 0
    accounts_generic: int = 0

    # Costs
    cost_breakdown: dict[str, float] = field(default_factory=dict)
    total_cost_usd: float = 0.0

    # Connector status
    connectors: dict[str, str] = field(default_factory=dict)

    # Errors & warnings
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Durations in seconds
    stage_durations: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunSummary:
        return cls(
            run_id=str(data.get("run_id", "")),
            profile=str(data.get("profile", "")),
            mode=str(data.get("mode", "full")),
            started_at=str(data.get("started_at", "")),
            completed_at=data.get("completed_at"),
            accounts_discovered=int(data.get("accounts_discovered", 0)),
            accounts_gated=int(data.get("accounts_gated", 0)),
            accounts_dropped=int(data.get("accounts_dropped", 0)),
            accounts_enriched=int(data.get("accounts_enriched", 0)),
            accounts_ready=int(data.get("accounts_ready", 0)),
            accounts_generic=int(data.get("accounts_generic", 0)),
            cost_breakdown=data.get("cost_breakdown", {}),
            total_cost_usd=float(data.get("total_cost_usd", 0.0)),
            connectors=data.get("connectors", {}),
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
            stage_durations=data.get("stage_durations", {}),
        )


def _parse_iso(iso_str: str | None) -> datetime | None:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_run_summary(
    profile: str,
    run_id: str | None = None,
    content_root: Path | None = None,
) -> RunSummary:
    """Construct a RunSummary by aggregating state and cost files."""
    root = content_root if content_root is not None else resolve_content_root()
    state_path = run_state_json(profile, root)
    state = load_run_state(state_path)

    active_id = run_id or (state.run_id if state else "unknown-run")
    mode = state.mode if state else "full"
    started_at = state.started_at if state else datetime.now(UTC).isoformat()
    completed_at = state.completed_at if state else None

    # Pipeline counts
    counts = {
        "accounts_discovered": 0,
        "accounts_gated": 0,
        "accounts_dropped": 0,
        "accounts_enriched": 0,
        "accounts_ready": 0,
        "accounts_generic": 0,
    }
    connectors: dict[str, str] = {}
    errors: list[str] = []
    stage_durations: dict[str, float] = {}

    if state:
        for stage_name, stage in state.stages.items():
            if stage.error:
                errors.append(f"{stage_name}: {stage.error}")

            # Compute duration if timestamps present
            t_start = _parse_iso(stage.started_at)
            t_end = _parse_iso(stage.completed_at)
            if t_start and t_end:
                stage_durations[stage_name] = max(0.0, (t_end - t_start).total_seconds())

            # Aggregate metrics
            m = stage.metrics
            if "connectors" in m and isinstance(m["connectors"], dict):
                connectors.update(m["connectors"])
            for key in counts:
                if key in m and isinstance(m[key], (int, float)):
                    counts[key] = int(m[key])

    # Parse costs from costs.jsonl
    cost_breakdown: dict[str, float] = {}
    total_cost = 0.0
    costs_file = root / profile / "costs.jsonl"
    if not costs_file.exists():
        costs_file = root / profile / "prospects" / "costs.jsonl"
    if costs_file.exists():
        try:
            with costs_file.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        raw_cost = rec.get("cost_usd")
                        cost_usd = float(raw_cost) if raw_cost is not None else 0.0
                        source = str(rec.get("source") or rec.get("tool") or "other")
                        cost_breakdown[source] = round(
                            cost_breakdown.get(source, 0.0) + cost_usd, 4
                        )
                        total_cost += cost_usd
                    except (json.JSONDecodeError, ValueError, TypeError):
                        continue
        except OSError:
            pass

    return RunSummary(
        run_id=active_id,
        profile=profile,
        mode=mode,
        started_at=started_at,
        completed_at=completed_at,
        accounts_discovered=counts["accounts_discovered"],
        accounts_gated=counts["accounts_gated"],
        accounts_dropped=counts["accounts_dropped"],
        accounts_enriched=counts["accounts_enriched"],
        accounts_ready=counts["accounts_ready"],
        accounts_generic=counts["accounts_generic"],
        cost_breakdown=cost_breakdown,
        total_cost_usd=round(total_cost, 4),
        connectors=connectors,
        errors=errors,
        warnings=[],
        stage_durations=stage_durations,
    )


def save_run_summary(summary: RunSummary, path: Path) -> None:
    """Atomically write the RunSummary to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(summary.to_dict(), indent=2, ensure_ascii=False)

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        prefix="run_summary_",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    ) as tf:
        tf.write(payload)
        tf.flush()
        os.fsync(tf.fileno())
        tmp_name = tf.name

    os.replace(tmp_name, path)


def load_run_summary(path: Path) -> RunSummary | None:
    """Load RunSummary from path, returning None if missing or corrupt."""
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        return RunSummary.from_dict(data)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI to generate and inspect run_summary.json."""
    parser = argparse.ArgumentParser(
        prog="gtm_core.run_summary",
        description="Generate machine-readable prospect run summary.",
    )
    parser.add_argument("--profile", required=True, help="Tenant profile name")
    parser.add_argument("--run-id", default=None, help="Optional run ID")
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save run_summary.json in the profile prospects directory",
    )
    args = parser.parse_args(argv)

    summary = build_run_summary(args.profile, run_id=args.run_id)
    out_json = json.dumps(summary.to_dict(), indent=2)

    if args.save:
        dest = run_summary_json(args.profile)
        save_run_summary(summary, dest)
        print(f"Saved run summary to {dest}")
    else:
        print(out_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
