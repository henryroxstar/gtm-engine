"""Per-profile run ledgers: history, cost, and run manifests.

All runtime state is namespaced per profile under ``content/<profile>/`` (a
gitignored volume — never committed). Three artifacts:

  - ``history.jsonl`` — append-only audit of what the brain did (one JSON/line).
  - ``costs.jsonl``    — append-only cost telemetry for the monthly-cap guard.
  - ``runs/<run_id>.json`` — per-run manifest (resume-from-failure + status).

Pure stdlib (``json``, ``pathlib``, ``datetime``). IMPORTANT: this module never
calls ``datetime.now()`` at import time — timestamps are stamped only inside
functions, when a record is actually written, so importing the module has no
side effects and is deterministic.

Accepted config duck-type: any object with a ``content_root: Path`` attribute.
Both ``gtm_core.paths.PathConfig`` and ``agent.config.Config`` satisfy this.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Accept any config-like object with a content_root attribute.
    # Both PathConfig and agent.config.Config satisfy this at runtime.
    _ConfigLike = Any


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a trailing ``Z``."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _current_year_month() -> str:
    """Current ``YYYY-MM`` (UTC). Used as the default cost-aggregation window."""
    return datetime.now(UTC).strftime("%Y-%m")


class Ledgers:
    """Reads/writes the per-profile JSONL/JSON ledgers under ``content/<profile>/``.

    Construct one per ``(config, profile)``. Directories are created lazily on
    first write, so instantiation is cheap and side-effect-free.

    ``cfg`` must have a ``content_root: Path`` attribute (both PathConfig and
    agent.config.Config qualify).
    """

    def __init__(self, cfg: _ConfigLike, profile: str) -> None:
        self._cfg = cfg
        self._profile = profile
        self._base: Path = cfg.content_root / profile
        self._history_path: Path = self._base / "history.jsonl"
        self._costs_path: Path = self._base / "costs.jsonl"
        self._runs_dir: Path = self._base / "runs"

    def _ensure_base(self) -> None:
        self._base.mkdir(parents=True, exist_ok=True)

    def _append_jsonl(self, path: Path, record: dict) -> None:
        self._ensure_base()
        enriched = dict(record)
        enriched.setdefault("ts", _utc_now_iso())
        line = json.dumps(enriched, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def append_history(self, record: dict) -> None:
        """Append an audit record to ``history.jsonl`` (timestamped if needed)."""
        self._append_jsonl(self._history_path, record)

    def append_cost(self, record: dict) -> None:
        """Append a cost record to ``costs.jsonl`` (timestamped if needed)."""
        self._append_jsonl(self._costs_path, record)

    def write_run_manifest(self, manifest: dict) -> Path:
        """Write/overwrite the run manifest at ``runs/<run_id>.json``.

        Returns the path written. Raises ``ValueError`` if ``run_id`` is missing.
        """
        run_id = manifest.get("run_id")
        if not run_id:
            raise ValueError("write_run_manifest requires a 'run_id' in the manifest.")
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(manifest)
        payload.setdefault("profile", self._profile)
        path = self._runs_dir / f"{run_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def month_cost_total(self, year_month: str | None = None) -> float:
        """Sum ``cost_usd`` across all cost records in the given ``YYYY-MM`` window."""
        window = year_month or _current_year_month()
        if not self._costs_path.is_file():
            return 0.0

        total = 0.0
        with self._costs_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = record.get("ts", "")
                if not isinstance(ts, str) or not ts.startswith(window):
                    continue
                cost = record.get("cost_usd")
                try:
                    total += float(cost)
                except (TypeError, ValueError):
                    continue
        return total

    def over_monthly_cap(self, cap_usd: float) -> bool:
        """True when the current month's cost total has reached/exceeded ``cap_usd``."""
        return self.month_cost_total() >= cap_usd

    def published_content_hashes(self) -> set[str]:
        """Return the set of ``content_sha256`` values already PUBLISHED for this profile."""
        hashes: set[str] = set()
        if not self._history_path.is_file():
            return hashes
        with self._history_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("event") != "published":
                    continue
                digest = record.get("content_sha256")
                if isinstance(digest, str) and digest:
                    hashes.add(digest)
        return hashes
