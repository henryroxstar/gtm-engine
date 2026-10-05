"""R1.5: the observation record, its identity, and the append-only shards that hold it.

A membership fact is ``(source_id, account_key)``: the same member seen again, on another day or
by another writer, is one fact and the earliest ``observed`` wins. An event (a job post, a
mention) also keys on the day. A shard that cannot be read whole is refused whole and named; it
never half-loads.
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .switch import require_enabled

SCHEMA = 1
KINDS = frozenset(
    {
        "source_member",
        "source_join",
        "source_leave_noted",
        "job_post",
        "job_change",
        "community_mention",
        "content_engagement",
        "repo_engagement",
        "retracted",
    }
)
MEMBERSHIP_KINDS = frozenset({"source_member", "source_join", "source_leave_noted"})
#: The kinds that are one fact about ``(source_id, account_key)``. A join and a member line for the
#: same account are the same membership, so two checkouts out of step cannot make a second,
#: later-dated fact. A leave note is an event about a day, not the membership itself.
_IDENTITY_KINDS = frozenset({"source_member", "source_join"})
ROLES = frozenset({"buyer", "vendor", "mixed"})
_REQUIRED = (
    "schema",
    "kind",
    "product",
    "source_id",
    "source_url",
    "capture_sha256",
    "account_key",
    "observed",
    "observed_basis",
    "role",
    "premise_at_write",
    "writer",
    "obs_id",
)
#: Empty strings are not written for these, so a line carries them only when they mean something.
_OPTIONAL = ("person_key", "supersedes", "resolved_by", "member_name")
_SHARD_RE = re.compile(r"^[A-Za-z0-9_.\-]+-\d{4}-\d{2}\.jsonl\Z")
_WRITER_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}\Z")


#: The one shared file besides the shards that can be cut off mid-write and is repaired the same way.
QUEUE_FILE = "unresolved.jsonl"


class ObservationError(ValueError):
    """A record, a writer id or a shard name that may not be written."""


def obs_id(rec: dict) -> str:
    """Identity: no day for a membership, the day for an event; never the capture or the writer."""
    kind = "membership" if rec["kind"] in _IDENTITY_KINDS else rec["kind"]
    parts = [kind, rec["source_id"], rec["account_key"], rec.get("person_key", "")]
    if rec["kind"] not in _IDENTITY_KINDS:
        parts.append(rec["observed"])
    return hashlib.sha256("\x1f".join(parts).encode("utf-8", "backslashreplace")).hexdigest()


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _problems(rec: object) -> list[str]:
    if not isinstance(rec, dict):
        return ["the line is not a JSON object"]
    schema = rec.get("schema")
    if isinstance(schema, int) and not isinstance(schema, bool) and schema > SCHEMA:
        return [f"written by a newer version (schema {schema}), so pull and upgrade"]
    out = [f"{k} is missing" for k in _REQUIRED if k not in rec or rec[k] is None]
    out += [f"{k} is not allowed in schema {SCHEMA}" for k in rec if k not in _REQUIRED + _OPTIONAL]
    if out:
        return out
    if isinstance(schema, bool) or schema != SCHEMA or not isinstance(schema, int):
        return [f"schema {schema!r} is not {SCHEMA}"]
    # Every other field is text. A list or object here would otherwise blow up a set lookup or a
    # hash and take the whole reader down with it.
    out = [
        f"{k} is not text"
        for k in (*_REQUIRED[1:], *(k for k in _OPTIONAL if k in rec))
        if not isinstance(rec[k], str)
    ]
    if out:
        return out
    if not rec["product"].strip():
        out.append("product is empty")
    if rec["kind"] not in KINDS:
        out.append(f"kind {rec['kind']!r} is not one of the closed set")
    if rec["role"] not in ROLES:
        out.append(f"role {rec['role']!r} is not buyer, vendor or mixed")
    try:
        if not _DATE_RE.match(rec["observed"]):
            raise ValueError
        datetime.date.fromisoformat(rec["observed"])
    except ValueError:
        out.append(f"observed {rec['observed']!r} is not a YYYY-MM-DD date")
    if not out and rec["obs_id"] != obs_id(rec):
        out.append("obs_id does not match the line's own fields")
    return out


def make_observation(
    *,
    kind: str,
    product: str,
    source_id: str,
    source_url: str,
    capture_sha256: str,
    account_key: str,
    observed: str,
    observed_basis: str,
    role: str,
    premise_at_write: str,
    writer: str,
    person_key: str = "",
    supersedes: str = "",
    resolved_by: str = "",
    member_name: str = "",
) -> dict:
    """A validated schema-1 record with its ``obs_id``. Raises :class:`ObservationError`."""
    if kind not in KINDS:
        raise ObservationError(f"kind {kind!r} is not one of the closed set")
    rec: dict = {
        "schema": SCHEMA,
        "kind": kind,
        "product": product,
        "source_id": source_id,
        "source_url": source_url,
        "capture_sha256": capture_sha256,
        "account_key": account_key,
        "observed": observed,
        "observed_basis": observed_basis,
        "role": role,
        "premise_at_write": premise_at_write,
        "writer": writer,
    }
    for key, val in (
        ("person_key", person_key),
        ("supersedes", supersedes),
        ("resolved_by", resolved_by),
        ("member_name", member_name),
    ):
        if val:
            rec[key] = val
    if all(rec.get(k) for k in ("source_id", "account_key", "observed")):
        rec["obs_id"] = obs_id(rec)
    problems = _problems(rec)
    if problems:
        raise ObservationError("; ".join(problems))
    return rec


def shard_name(writer: str, now: datetime.datetime) -> str:
    """``<writer>-<YYYY-MM>.jsonl``, the month taken in UTC whatever zone ``now`` is in."""
    if not _WRITER_RE.match(writer.replace(".", "-")):
        raise ObservationError(f"writer {writer!r} is not a bare name")
    return f"{writer}-{now.astimezone(datetime.UTC):%Y-%m}.jsonl"


def writer_id(settings_path: Path, run_id: str) -> str:
    """``<observer_id>.<run_id>``: the colleague's id from settings, never the hostname."""
    try:
        observer = json.loads(settings_path.read_text(encoding="utf-8")).get("observer_id")
    except (OSError, ValueError, AttributeError):
        observer = None
    if not (isinstance(observer, str) and _WRITER_RE.match(observer)):
        raise ObservationError(
            f"observer_id is not set in {settings_path}. Why: a writer id must be set once per "
            "colleague, because a container's hostname changes every run. Fix: add "
            '"observer_id": "<your-first-name>" to that file.'
        )
    if not _WRITER_RE.match(run_id):
        raise ObservationError(f"run id {run_id!r} is not a bare name")
    return f"{observer}.{run_id}"


def append_lines(path: Path, data: str) -> None:
    """Append ``data`` as a single ``write()`` under an exclusive lock on the file."""
    require_enabled()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data.encode("utf-8")
    try:
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    except OSError as exc:
        raise ObservationError(
            f"{path.name} cannot be opened for appending ({exc.strerror}); a link is never followed"
        ) from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            # Gluing a record onto a cut-off line would make the whole shard unreadable.
            raise ObservationError(
                f"{path.name} ends mid-line (an earlier write was cut off), so nothing was added. "
                "Run `signal_obs repair` on it first."
            )
        if os.write(fd, raw) != len(raw):  # pragma: no cover - a short write on a regular file
            raise OSError(f"short write to {path}")
    finally:
        os.close(fd)  # closing releases the lock


def append(directory: Path, shard: str, records: list[dict]) -> Path:
    """Append ``records`` to one shard as a single ``write()`` under an exclusive lock."""
    if not _SHARD_RE.match(shard):
        raise ObservationError(f"shard name {shard!r} is not <writer>-<YYYY-MM>.jsonl")
    for rec in records:
        problems = _problems(rec)
        if problems:
            raise ObservationError("; ".join(problems))
    path = directory / shard
    if records:
        append_lines(
            path, "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
        )
    return path


@dataclass
class ReadResult:
    observations: list[dict] = field(default_factory=list)
    #: ``{shard, line, why}`` for each shard refused.
    problems: list[dict] = field(default_factory=list)
    refused_shards: list[str] = field(default_factory=list)


def _read_shard(path: Path) -> tuple[list[dict], dict | None]:
    try:
        return _read_shard_bytes(path.name, path.read_bytes())
    except OSError as exc:
        return [], {"shard": path.name, "line": 0, "why": f"cannot be read ({type(exc).__name__})"}


def _read_shard_bytes(name: str, raw: bytes) -> tuple[list[dict], dict | None]:
    recs: list[dict] = []
    try:
        lines = raw.decode("utf-8").split("\n")
    except UnicodeDecodeError as exc:
        return [], {"shard": name, "line": 0, "why": f"cannot be read ({type(exc).__name__})"}
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):
            partial = n == len(lines)
            return [], {
                "shard": name,
                "line": n,
                "why": "a line is not valid JSON"
                + (
                    " (the last line looks cut off mid-write: run `signal_obs repair`)"
                    if partial
                    else ""
                ),
            }
        problems = _problems(rec)
        if problems:
            return [], {"shard": name, "line": n, "why": "; ".join(problems)}
        recs.append(rec)
    return recs, None


def _read_queue_bytes(name: str, raw: bytes) -> tuple[list[dict], dict | None]:
    """Like :func:`_read_shard_bytes`, but a queue line only has to be a JSON object."""
    try:
        lines = raw.decode("utf-8").split("\n")
        for n, line in enumerate(lines, start=1):
            if line.strip() and not isinstance(json.loads(line), dict):
                return [], {"shard": name, "line": n, "why": "a line is not a JSON object"}
    except (ValueError, RecursionError) as exc:
        return [], {
            "shard": name,
            "line": 0,
            "why": f"a line cannot be read ({type(exc).__name__})",
        }
    return [], None


def read_all(directory: Path, *, product: str) -> ReadResult:
    """Every live observation for ``product``: deduped, retractions and ``supersedes`` applied."""
    out = ReadResult()
    if not directory.is_dir():
        return out
    pool: dict[str, dict] = {}
    removed: set[str] = set()
    for path in sorted(p for p in directory.iterdir() if _SHARD_RE.match(p.name)):
        recs, problem = _read_shard(path)
        if problem:
            out.problems.append(problem)
            out.refused_shards.append(path.name)
            continue
        for rec in recs:
            if rec["product"] != product:
                continue
            if rec.get("supersedes") and rec["supersedes"] != rec["obs_id"]:
                removed.add(rec["supersedes"])
            if rec["kind"] == "retracted":
                continue
            best = pool.get(rec["obs_id"])
            if best is None or (rec["observed"], rec["writer"]) < (
                best["observed"],
                best["writer"],
            ):
                pool[rec["obs_id"]] = rec
    out.observations = sorted(
        (r for i, r in pool.items() if i not in removed),
        key=lambda r: (r["source_id"], r["account_key"], r["observed"], r["obs_id"]),
    )
    return out


def read_all_strict(directory: Path, *, product: str) -> ReadResult:
    """:func:`read_all`, but a shard that cannot be read is an error, not a silent gap.

    A skipped shard hides records, and a caller that does not know writes a duplicate of one
    and forgets a person's decision. Callers that write or decide go through this.
    """
    got = read_all(directory, product=product)
    if got.refused_shards:
        first = got.problems[0]
        raise ObservationError(
            f"{first['shard']} cannot be read ({first['why']}); nothing was written"
        )
    return got


@dataclass
class RepairPlan:
    shard: str
    drop_bytes: int = 0
    applied: bool = False
    reason: str = ""


def repair(directory: Path, shard: str, *, apply: bool = False) -> RepairPlan:
    """Truncate a cut-off last line. Plan-first: nothing is written without ``apply``.

    Only one kind of damage is repaired: a final line with no newline that is not valid JSON,
    after lines that are all whole. Anything else is for a person, and is refused.
    """
    require_enabled()
    plan = RepairPlan(shard)
    if shard != QUEUE_FILE and not _SHARD_RE.match(shard):
        raise ObservationError(f"shard name {shard!r} is not <writer>-<YYYY-MM>.jsonl")
    check = _read_queue_bytes if shard == QUEUE_FILE else _read_shard_bytes
    path = directory / shard
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        size = os.fstat(fd).st_size
        data = os.read(fd, size)
        head, _, tail = data.rpartition(b"\n")
        if not tail.strip():
            _, damage = check(path.name, data)
            plan.reason = (
                f"damage in a whole line ({damage['why']}): not a cut-off tail"
                if damage
                else "the shard ends on a whole line; nothing to repair"
            )
            return plan
        try:
            json.loads(tail)
            plan.reason = "the last line is whole JSON but has no newline; nothing is cut off"
            return plan
        except (ValueError, RecursionError):
            pass
        _, problem = check(path.name, head + b"\n" if head else b"")
        if problem:
            plan.reason = f"damage before the last line ({problem['why']}): not a cut-off tail"
            return plan
        plan.drop_bytes = len(tail)
        if apply:
            os.ftruncate(fd, size - len(tail))
            plan.applied = True
        return plan
    finally:
        os.close(fd)
