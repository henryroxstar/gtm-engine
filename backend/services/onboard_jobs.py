"""Onboarding jobs: the onboarding POSTs answer 202 at once and the work runs here (issue #259).

A URL ingest (crawl, then extraction, render and stage) can outlast Cloudflare's ~100 s origin
limit. The edge then answers 524 with no CORS headers, the client never learns the draft_id,
and the server finishes anyway, leaving an orphan draft it has already paid for. So
``POST /v1/onboard`` and the product re-extract now create a job, start it as an in-process
task, and return; the client polls ``GET /v1/onboard/jobs/{job_id}``.

Properties:

* **The record is on disk, in the caller's own tree.** One JSON file per job under
  ``<workspace profiles root>/.staging/.jobs/``, next to the drafts it produces, so any worker
  sharing the data volume can answer a poll. The staging scanners skip it: it has no
  ``.onboard-meta.json``, and no slug can start with ``.``. ``job_id`` is a server-minted
  UUID and is re-parsed before it becomes a filename. The record also names its workspace,
  and a read for any other workspace is a miss.
* **Liveness is a heartbeat, not memory.** A running job rewrites ``heartbeat_at`` every
  :data:`HEARTBEAT_S`. A pending or running job whose heartbeat is older than
  :data:`STALE_AFTER_S` (its process died, or a redeploy killed it) reads as failed with
  ``onboarding_interrupted``. "Not in this process's memory" would be wrong with
  ``BACKEND_WORKERS > 1``, where another live process may own the job.
* **Failures carry the synchronous route's envelope.** The work still raises the same
  ``HTTPException``s. The job records their status, code and message, so a client switches
  on the same ``error.code`` values as before. Anything else is ``internal_error``, and only
  the log holds its detail.
* **An in-flight duplicate is reused, a finished one never is.** A second submit of the same
  source (same kind, same target, same bytes) while the first is pending or running gets the
  first job back, so a client retry does not pay for a second crawl. A finished job is not
  reused: its draft may already have been promoted or cancelled.
* **Old records are pruned.** Finished records older than :data:`RETENTION_S` are deleted
  when the next job is created.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from agent.onboard.slug import _STAGING_DIR

log = logging.getLogger(__name__)

JOBS_DIR = ".jobs"
HEARTBEAT_S = 15.0
STALE_AFTER_S = 90.0
RETENTION_S = 24 * 3600.0

_ACTIVE = frozenset({"pending", "running"})
_INTERRUPTED = {
    "status": 503,
    "code": "onboarding_interrupted",
    "message": "Onboarding was interrupted. Start it again.",
}
_INTERNAL = {
    "status": 500,
    "code": "internal_error",
    "message": "An unexpected error occurred.",
}

# Strong references: the event loop keeps only weak ones to a running task.
_tasks: set[asyncio.Task] = set()


def _now() -> datetime:
    return datetime.now(UTC)


def _age_s(iso: str | None) -> float | None:
    try:
        return (_now() - datetime.fromisoformat(str(iso))).total_seconds()
    except (TypeError, ValueError):
        return None


def source_key(*parts: str) -> str:
    """The dedupe key for a submit: a digest, so a large text source is not stored twice."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _jobs_dir(cfg: Any) -> Path:
    return Path(cfg.profiles_root) / _STAGING_DIR / JOBS_DIR


def _path(cfg: Any, job_id: str) -> Path | None:
    try:
        canonical = str(uuid.UUID(job_id))
    except (TypeError, ValueError):
        return None
    return _jobs_dir(cfg) / f"{canonical}.json"


def _write(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _view(record: dict) -> dict:
    """The record as a client should see it: a pending/running job with a stale heartbeat
    has failed."""
    if record.get("status") in _ACTIVE:
        age = _age_s(record.get("heartbeat_at"))
        if age is None or age > STALE_AFTER_S:
            return {**record, "status": "failed", "error": dict(_INTERRUPTED)}
    return record


def _scan(cfg: Any, workspace_id: str, kind: str, key: str) -> dict | None:
    """Return a live in-flight job for this submit, pruning expired finished records on the way."""
    found = None
    directory = _jobs_dir(cfg)
    if not directory.is_dir():
        return None
    for path in directory.glob("*.json"):
        record = _read(path)
        if record is None or record.get("workspace_id") != workspace_id:
            continue
        view = _view(record)
        if view["status"] not in _ACTIVE:
            age = _age_s(record.get("updated_at"))
            if age is not None and age > RETENTION_S:
                with contextlib.suppress(OSError):
                    path.unlink()
            continue
        if found is None and record.get("kind") == kind and record.get("source_key") == key:
            found = record
    return found


def create_job(cfg: Any, workspace_id: str, kind: str, key: str) -> tuple[dict, bool]:
    """Return ``(record, created)``. ``created`` is False when an in-flight duplicate is reused."""
    existing = _scan(cfg, workspace_id, kind, key)
    if existing is not None:
        return existing, False
    now = _now().isoformat()
    record = {
        "job_id": str(uuid.uuid4()),
        "workspace_id": workspace_id,
        "kind": kind,
        "source_key": key,
        "status": "pending",
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "heartbeat_at": now,
    }
    path = _path(cfg, record["job_id"])
    assert path is not None  # a uuid4 always parses
    _write(path, record)
    return record, True


def get_job(cfg: Any, workspace_id: str, job_id: str) -> dict | None:
    """The caller's job as a client should see it, or ``None``. Any other workspace's job is
    ``None`` too, so its existence does not leak."""
    path = _path(cfg, job_id)
    record = _read(path) if path is not None else None
    if record is None or record.get("workspace_id") != workspace_id:
        return None
    return _view(record)


def public(record: dict) -> dict:
    """The fields ``OnboardJobResponse`` carries."""
    keys = ("job_id", "kind", "status", "result", "error", "created_at", "updated_at")
    return {k: record.get(k) for k in keys}


def _error_from(exc: HTTPException) -> dict:
    from backend.errors import _status_to_code

    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return {
            "status": exc.status_code,
            "code": str(detail["code"]),
            "message": str(detail.get("message", "")),
        }
    message = str(detail)
    return {
        "status": exc.status_code,
        "code": _status_to_code(exc.status_code, message),
        "message": message,
    }


async def _heartbeat(path: Path, record: dict) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        record["heartbeat_at"] = _now().isoformat()
        with contextlib.suppress(OSError):
            _write(path, record)


async def run_job(cfg: Any, record: dict, work: Callable[[], Awaitable[dict]]) -> None:
    """Run ``work`` and record its outcome. Never raises."""
    path = _path(cfg, record["job_id"])
    assert path is not None

    def _save(**changes: Any) -> None:
        now = _now().isoformat()
        record.update(changes, updated_at=now, heartbeat_at=now)
        _write(path, record)

    beat: asyncio.Task | None = None
    try:
        _save(status="running")
        beat = asyncio.create_task(_heartbeat(path, record))
        result = await work()
    except HTTPException as exc:
        _safe_save(_save, status="failed", error=_error_from(exc))
    except Exception:
        log.exception("onboarding job %s failed", record["job_id"])
        _safe_save(_save, status="failed", error=dict(_INTERNAL))
    else:
        _safe_save(_save, status="succeeded", result=result)
    finally:
        if beat is not None:
            beat.cancel()


def _safe_save(save: Callable[..., None], **changes: Any) -> None:
    try:
        save(**changes)
    except OSError:
        # Unrecorded, the job goes stale and reads as interrupted. That is the right
        # outcome for a job whose result cannot be kept.
        log.exception("could not record an onboarding job outcome")


def spawn(cfg: Any, record: dict, work: Callable[[], Awaitable[dict]]) -> None:
    """Start the job on the running loop and keep it referenced until it finishes."""
    task = asyncio.create_task(run_job(cfg, record, work))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
