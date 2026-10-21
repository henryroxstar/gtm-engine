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
import os
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


# Re-exported: the strict/windowed reads live in ledger_reads (split out under the §R10 ceiling);
# every name below was importable from here before and still is.
from gtm_core.ledger_reads import (  # noqa: E402, F401
    LedgerUnreadableError,
    _cost_of,
    _cost_records,
    _current_year_month,
    _read_rows_strict,
    content_root_unit_total,
    sum_month_costs,
    sum_run_costs,
    unit_total_in,
)


def _stamp_run_id(record: dict) -> dict:
    """Name the run a spend belongs to, from ``GTM_RUN_ID`` (MM2).

    The MCP workers are stdio subprocesses with no run context and no database credentials; the
    backend sets ``GTM_RUN_ID`` in each one's env for a pack run, and the one funnel every worker
    meters through stamps it here, so a worker added later is covered without being edited. A row
    that already names a run keeps it. Absent/blank env ⇒ the row is returned unchanged (the VPS and
    every non-pack path write exactly what they always did).
    """
    run_id = (os.environ.get("GTM_RUN_ID") or "").strip()
    if not run_id or record.get("run_id"):
        return record
    return {**record, "run_id": run_id}


def _line_sha256(line: str) -> str:
    """SHA-256 hex digest of a single ledger line's raw bytes (trailing newline stripped)."""
    return hashlib.sha256(line.rstrip("\n").encode("utf-8")).hexdigest()


def _is_whole_row(fragment: bytes) -> bool:
    """True when ``fragment`` is one complete JSON object: a record, not the front half of one."""
    try:
        return isinstance(json.loads(fragment.decode("utf-8")), dict)
    except (ValueError, UnicodeDecodeError):
        return False


def _cut_torn_tail(path: Path) -> None:
    """Remove an unacknowledged trailing fragment: bytes after the last newline.

    Every record is written as ``line + "\\n"`` in one write, so a file that does not end in a newline
    ends in a write that never finished — a worker killed mid-append. That fragment was never a record
    (nothing was told it succeeded), and left in place the NEXT append lands on the same line and
    fuses the two into one corrupt line: the new spend is lost to a lenient read and a strict read
    refuses for ever (:mod:`gtm_core.ledger_reads`). Called under the append lock, so it cannot be
    another writer's write in progress. A file that ends in a newline, or is empty, is untouched. A fragment
    that is itself one whole JSON object is a record that merely lacks its newline: it is terminated and kept,
    never cut, because erasing it would lose a row from an append-only audit ledger.
    """
    try:
        with path.open("rb+") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size == 0:
                return
            fh.seek(size - 1)
            if fh.read(1) == b"\n":
                return
            data_end = size
            block = 65536
            pos = size
            keep = 0  # no newline anywhere: the whole file is one fragment
            while pos > 0:
                start = max(0, pos - block)
                fh.seek(start)
                chunk = fh.read(pos - start)
                idx = chunk.rfind(b"\n")
                if idx != -1:
                    keep = start + idx + 1
                    break
                pos = start
            if keep < data_end:
                fh.seek(keep)
                if _is_whole_row(fh.read(data_end - keep)):
                    fh.seek(0, os.SEEK_END)
                    fh.write(
                        b"\n"
                    )  # a whole record that lacks only its newline: terminate it, keep it
                else:
                    fh.truncate(keep)
    except FileNotFoundError:
        return


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


def _recent_rows(path: Path, nbytes: int = 65536) -> list[dict]:
    """The JSON rows in the last ``nbytes`` of ``path``, oldest first; a partial or bad line is skipped."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            end = fh.tell()
            fh.seek(max(0, end - nbytes))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict] = []
    for line in data.splitlines()[1 if end > nbytes else 0 :]:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


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
        self._dispatch_path: Path = self._base / "dispatch-intents.jsonl"

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

    def _append_jsonl(self, path: Path, record: dict, *, once: Any = None) -> dict:
        """Append one record, chaining it to the previous raw line (NIST AU-9).

        Each record carries ``prev_sha256`` = SHA-256 of the previous line's raw bytes (newline
        stripped), or :data:`GENESIS_HASH` for the first line. Editing any earlier line changes its
        hash and breaks the following link, so ``gtm_core.ledger_verify`` can detect tampering. The
        read-tail + write happen under an exclusive advisory lock so a second process (the
        RocketReach MCP worker also appends here) chains to whatever was truly last, not a stale
        tail. Legacy unchained lines need no migration — the appender hashes whatever line is last.

        ``once`` (a predicate over a recent row) makes the append idempotent: if a row in the
        ledger's tail satisfies it, that row is returned and nothing is written. The check runs
        inside the lock, so two writers that race to record the same event write one row.
        """
        self._ensure_base()
        enriched = dict(record)
        enriched.setdefault("ts", _utc_now_iso())
        with path.open("a+", encoding="utf-8") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                _cut_torn_tail(path)
                if once is not None:
                    for seen in reversed(_recent_rows(path)):
                        if once(seen):
                            return seen
                last = _last_line(path)
                enriched["prev_sha256"] = _line_sha256(last) if last is not None else GENESIS_HASH
                line = json.dumps(enriched, ensure_ascii=False)
                fh.write(line + "\n")
                fh.flush()
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return enriched

    # -- dispatch intents (F4) — the store logic lives in gtm_core.dispatch_intents --------------------

    def read_dispatch_rows(self) -> list[dict]:
        """Every row of ``dispatch-intents.jsonl``, STRICTLY: a line that is not a JSON object raises
        ``ValueError`` (a send must be refused against a ledger it cannot read, never authorised by
        skipping the unreadable part). A missing file is simply empty."""
        return _read_rows_strict(self._dispatch_path)

    def append_dispatch(self, record: dict) -> dict:
        """Append one intent/outcome/resolve row (chained like every other ledger)."""
        return self._append_jsonl(self._dispatch_path, record)

    def claim_dispatch(self, guard: Any, record: dict) -> tuple[Any, dict | None]:
        """Check-and-append under ONE exclusive lock: the claim-before-call primitive.

        ``guard(rows)`` sees every existing row and returns ``None`` to allow the claim or any other
        value to refuse it. Because the check and the append share the lock, two processes racing to
        claim overlapping recipients cannot both pass (the ``once=`` predicate used elsewhere reads only
        the last 64 KB and cannot express "any prior intent"). Returns ``(refusal, None)`` or
        ``(None, written_row)``.
        """
        self._ensure_base()
        enriched = dict(record)
        enriched.setdefault("ts", _utc_now_iso())
        path = self._dispatch_path
        with path.open("a+", encoding="utf-8") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                _cut_torn_tail(path)
                refusal = guard(_read_rows_strict(path))
                if refusal is not None:
                    return refusal, None
                last = _last_line(path)
                enriched["prev_sha256"] = _line_sha256(last) if last is not None else GENESIS_HASH
                fh.write(json.dumps(enriched, ensure_ascii=False) + "\n")
                fh.flush()
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return None, enriched

    def append_history(self, record: dict) -> None:
        """Append an audit record to ``history.jsonl`` (timestamped if needed)."""
        self._append_jsonl(self._history_path, record)

    def append_cost(self, record: dict, *, once: Any = None) -> dict:
        """Append a cost record to ``costs.jsonl`` (timestamped if needed).

        ``once`` makes it idempotent (see :meth:`_append_jsonl`).

        Returns the record as written, including the stamped ``ts``. The ``ts`` is the ledger
        row's natural key (rows carry no id of their own) and is what a render manifest records
        as ``cost_ledger_ts`` so a spend can be tied back to the row that metered it — see
        :func:`gtm_core.render_manifest.write_render_manifest`.
        """
        record = _stamp_run_id(record)
        return self._append_jsonl(self._costs_path, record, once=once)

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

    def month_unit_total(
        self, tool: str, unit_key: str, year_month: str | None = None, *, strict: bool = False
    ) -> float:
        """Sum a finite non-dollar unit (e.g. RocketReach ``lookups``) for one tool this month.

        Flat-subscription connectors record ``cost_usd: 0`` — the real constraint is a unit COUNT,
        which the dollar-based cap can never see. This sums that count for ``tool`` across the
        ``YYYY-MM`` window, tolerating both record shapes: the dict form
        (``{"units": {"lookups": N}}``, written by the RocketReach worker) and the ``CostRecord``
        scalar form (``{"units": N, "unit_kind": "<key>"}``). Corrupt/blank lines are skipped (same
        contract as :meth:`month_cost_total`); a missing file returns ``0.0``. ``strict`` is the
        tenant opt-in that refuses instead (see :func:`unit_total_in`).
        """
        return unit_total_in(self._costs_path, tool, unit_key, year_month, strict=strict)

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
