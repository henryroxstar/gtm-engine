"""Per-profile run ledgers: history, cost, and run manifests (spec §10, §14).

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

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:  # POSIX advisory file lock; absent on non-POSIX (tests still run, just unlocked)
    import fcntl
except ImportError:  # pragma: no cover — Windows/other
    fcntl = None  # type: ignore[assignment]

#: Genesis marker for the first chained record in a ledger file (NIST AU-9 tamper-evidence).
GENESIS_HASH = "GENESIS"

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


def sum_month_costs(costs_path: Path, year_month: str | None = None) -> float:
    """Sum ``cost_usd`` in one ``costs.jsonl`` over a ``YYYY-MM`` window.

    Module-level on purpose. The multi-tenant cap in :mod:`gtm_core.metering` rolls up
    every profile's ledger under a workspace, and it must use the SAME parser the
    single-tenant cap uses — a second implementation would let the two totals drift
    apart silently, which is the failure this whole ledger split already caused once.

    Tolerant by design: an unparseable line, a missing ``ts``, or a non-numeric
    ``cost_usd`` is skipped rather than raising, so one corrupt append can never make a
    budget guard unreadable (and therefore fail-open).
    """
    window = year_month or _current_year_month()
    if not costs_path.is_file():
        return 0.0

    total = 0.0
    with costs_path.open("r", encoding="utf-8") as fh:
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


def _line_sha256(line: str) -> str:
    """SHA-256 hex digest of a single ledger line's raw bytes (trailing newline stripped)."""
    return hashlib.sha256(line.rstrip("\n").encode("utf-8")).hexdigest()


def _last_line(path: Path) -> str | None:
    """Return the last non-empty line of ``path`` (newline stripped), or ``None`` if empty/missing.

    Reads backwards from EOF in blocks so a large ledger is not scanned front-to-back on every
    append (O(tail), not O(file)).
    """
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)  # SEEK_END
            end = fh.tell()
            if end == 0:
                return None
            block = 8192
            data = b""
            pos = end
            while pos > 0:
                step = min(block, pos)
                pos -= step
                fh.seek(pos)
                data = fh.read(step) + data
                # Once we have a newline separating the last line from a prior one, stop.
                stripped = data.rstrip(b"\n")
                if b"\n" in stripped:
                    break
            last = data.rstrip(b"\n").rsplit(b"\n", 1)[-1]
            if not last:
                return None
            return last.decode("utf-8", errors="surrogateescape")
    except (OSError, FileNotFoundError):
        return None


class Ledgers:
    """Reads/writes the per-profile JSONL/JSON ledgers under ``content/<profile>/``.

    Construct one per ``(config, profile)``. Directories are created lazily on
    first write, so instantiation is cheap and side-effect-free.

    ``cfg`` must have a ``content_root: Path`` attribute (both PathConfig and
    agent.config.Config qualify).
    """

    def __init__(self, cfg: _ConfigLike, profile: str) -> None:
        from gtm_core.paths import _safe_segment

        _safe_segment(profile, "profile")  # B1 defense-in-depth: profile selects the write root
        self._cfg = cfg
        self._profile = profile
        self._base: Path = cfg.content_root / profile
        self._history_path: Path = self._base / "history.jsonl"
        self._costs_path: Path = self._base / "costs.jsonl"
        self._denials_path: Path = self._base / "denials.jsonl"
        self._runs_dir: Path = self._base / "runs"

    @property
    def profile(self) -> str:
        """The tenant profile this ledger is bound to.

        Public because the enrollment dispatcher needs it to resolve the lane state for a
        PII egress, and reaching into ``_profile`` from another module would make that
        control depend on a private attribute.
        """
        return self._profile

    def _ensure_base(self) -> None:
        self._base.mkdir(parents=True, exist_ok=True)

    def _append_jsonl(self, path: Path, record: dict) -> dict:
        """Append one record, chaining it to the previous raw line (NIST AU-9).

        Each record carries ``prev_sha256`` = SHA-256 of the previous line's raw bytes (newline
        stripped), or :data:`GENESIS_HASH` for the first line. Editing any earlier line changes its
        hash and breaks the following link, so ``gtm_core.ledger_verify`` can detect tampering. The
        read-tail + write happen under an exclusive advisory lock so a second process (the
        RocketReach MCP worker also appends here) chains to whatever was truly last, not a stale
        tail. Legacy unchained lines need no migration — the appender hashes whatever line is last.
        """
        self._ensure_base()
        enriched = dict(record)
        enriched.setdefault("ts", _utc_now_iso())
        with path.open("a+", encoding="utf-8") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                last = _last_line(path)
                enriched["prev_sha256"] = _line_sha256(last) if last is not None else GENESIS_HASH
                line = json.dumps(enriched, ensure_ascii=False)
                fh.write(line + "\n")
                fh.flush()
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return enriched

    def append_history(self, record: dict) -> None:
        """Append an audit record to ``history.jsonl`` (timestamped if needed)."""
        self._append_jsonl(self._history_path, record)

    def append_cost(self, record: dict) -> dict:
        """Append a cost record to ``costs.jsonl`` (timestamped if needed).

        Returns the record as written, including the stamped ``ts``. The ``ts`` is the ledger
        row's natural key (rows carry no id of their own) and is what a render manifest records
        as ``cost_ledger_ts`` so a spend can be tied back to the row that metered it — see
        :func:`gtm_core.render_manifest.write_render_manifest`.
        """
        return self._append_jsonl(self._costs_path, record)

    def append_denial(self, record: dict) -> None:
        """Append a permission-policy event to ``denials.jsonl`` (timestamped if needed).

        One record per denied/escalated tool call (and per operator approve/deny on the
        cockpit ask path). This is the observability half of the least-privilege policy:
        interactive Claude Code shows every borderline call to a human, so nothing is ever
        silently lost — headless runs get this ledger instead, so "what was blocked this
        week, by which tool, how often" is a query, not archaeology.
        """
        self._append_jsonl(self._denials_path, record)

    def denials_summary(self, days: int = 7, recent: int = 10) -> dict:
        """Aggregate ``denials.jsonl`` over the trailing ``days`` window.

        Returns ``{"profile", "days", "total", "by_tool", "by_decision", "by_outcome",
        "recent"}`` where ``recent`` holds the newest ``recent`` records (ts/tool/
        decision/outcome/detail only). Malformed lines and records without a parseable
        ``ts`` are skipped — a summary must never crash on a hand-edited ledger.
        """
        out: dict = {
            "profile": self._profile,
            "days": days,
            "total": 0,
            "by_tool": {},
            "by_decision": {},
            "by_outcome": {},
            "recent": [],
        }
        if not self._denials_path.is_file():
            return out
        cutoff = datetime.now(UTC).timestamp() - days * 86400
        rows: list[dict] = []
        with self._denials_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                try:
                    when = datetime.fromisoformat(
                        str(rec.get("ts", "")).replace("Z", "+00:00")
                    ).timestamp()
                except ValueError:
                    continue
                if when < cutoff:
                    continue
                rows.append(rec)
        out["total"] = len(rows)
        for rec in rows:
            for field, bucket in (
                ("tool", "by_tool"),
                ("decision", "by_decision"),
                ("outcome", "by_outcome"),
            ):
                key = str(rec.get(field, "unknown"))
                out[bucket][key] = out[bucket].get(key, 0) + 1
        out["recent"] = [
            {k: rec.get(k) for k in ("ts", "tool", "decision", "outcome", "detail")}
            for rec in rows[-recent:]
        ]
        return out

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
        return sum_month_costs(self._costs_path, year_month)

    def over_monthly_cap(self, cap_usd: float) -> bool:
        """True when the current month's cost total has reached/exceeded ``cap_usd``."""
        return self.month_cost_total() >= cap_usd

    def month_unit_total(self, tool: str, unit_key: str, year_month: str | None = None) -> float:
        """Sum a finite non-dollar unit (e.g. RocketReach ``lookups``) for one tool this month.

        Flat-subscription connectors record ``cost_usd: 0`` — the real constraint is a unit COUNT,
        which the dollar-based cap can never see. This sums that count for ``tool`` across the
        ``YYYY-MM`` window, tolerating both record shapes: the dict form
        (``{"units": {"lookups": N}}``, written by the RocketReach worker) and the ``CostRecord``
        scalar form (``{"units": N, "unit_kind": "<key>"}``). Corrupt/blank lines are skipped (same
        contract as :meth:`month_cost_total`); a missing file returns ``0.0``.
        """
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
                except (TypeError, ValueError):
                    continue
        return total

    def iter_history(self):
        """Yield every history record in file order. Skips blank/corrupt lines
        (same robustness contract as ``published_content_hashes``). Missing file
        -> nothing."""
        if not self._history_path.exists():
            return
        with self._history_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def published_content_hashes(self) -> set[str]:
        """Return the ``content_sha256`` values already COMMITTED for this profile.

        Committed means ``published`` **or** ``scheduled``. A scheduled post has not
        gone out yet, but it is booked and will — so for the question this set exists
        to answer ("would sending these bytes now be a double-post?") it counts. Left
        out, a restart between scheduling and send-time would let the same content be
        scheduled a second time and both would fire.

        A ``schedule_voided`` event (written by ``gtm_core.ledger_cli
        record-schedule-void`` when an operator cancels a scheduled post in the
        scheduler's own UI) frees a hash — but ONLY when that hash's most recent
        committed event was ``scheduled``. It can never retroactively clear a
        ``published`` hash: those bytes went out live, and "voiding" a schedule that
        already fired must not re-enable sending them again. Processed in file
        order with last-event-wins per hash, so a later ``scheduled`` after a void
        (re-approving the same content for a new slot) re-blocks it, as it should.

        The method name predates scheduling and is kept: it is wired in as the
        publisher's ``is_published`` predicate in several places, and the meaning it
        carries there — "already committed, do not send again" — is unchanged.
        """
        committed = {"published", "scheduled"}
        last_event: dict[str, str] = {}
        if not self._history_path.is_file():
            return set()
        with self._history_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = record.get("event")
                if event not in committed and event != "schedule_voided":
                    continue
                digest = record.get("content_sha256")
                if not (isinstance(digest, str) and digest):
                    continue
                if event == "schedule_voided":
                    if last_event.get(digest) == "scheduled":
                        last_event.pop(digest, None)
                    continue
                last_event[digest] = event
        return set(last_event)
