"""R1.6: the queue of members this code could not tie to one account, for a person to decide.

The file is shared by everyone who runs ``extract`` and is append-only. A line that cannot be read
is never skipped: a skipped "not a prospect" line would put the name back in front of a person,
and a skipped entry would hide a name. A reader that meets damage says so and stops.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .members import normalise
from .observations import QUEUE_FILE, ObservationError, append_lines
from .switch import require_enabled

FILENAME = QUEUE_FILE
_TEXT = ("product", "source_id", "name", "first_seen", "candidate_domain", "reason", "status")


def queue_path(obs_dir: Path) -> Path:
    return obs_dir / FILENAME


def _bad(why: str, lineno: int) -> ObservationError:
    return ObservationError(
        f"{FILENAME} line {lineno} cannot be read ({why}). Run `signal_obs repair` if the last "
        "line was cut off; otherwise a person has to look at the file. Nothing was written."
    )


def read(obs_dir: Path) -> list[dict]:
    """Every entry, in file order. Raises :class:`ObservationError` on any line it cannot trust."""
    path = queue_path(obs_dir)
    if path.is_symlink():
        raise _bad("it is a link", 0)
    if not path.is_file():
        return []
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _bad(type(exc).__name__, 0) from exc
    out: list[dict] = []
    # Split on "\n" only: str.splitlines also breaks on U+2028 and friends, which a web page can
    # put inside a name and which json.dumps(ensure_ascii=False) writes out whole.
    for n, line in enumerate(text.split("\n"), 1):
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise _bad("not valid JSON", n) from exc
        if not isinstance(rec, dict) or not rec.get("name") or not rec.get("source_id"):
            raise _bad("not a queue entry", n)
        if any(not isinstance(rec[k], str) for k in _TEXT if k in rec):
            raise _bad("a field is not text", n)
        out.append(rec)
    return out


def key(entry: dict) -> tuple[str, str, str]:
    return (entry.get("product", ""), entry["source_id"], normalise(entry["name"]))


@contextmanager
def _locked(obs_dir: Path) -> Iterator[None]:
    """One writer at a time through read-then-append, so a repeat is never queued twice."""
    require_enabled()
    obs_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(obs_dir / f".{FILENAME}.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _line(entry: dict, **extra) -> str:
    return json.dumps({**entry, **extra}, ensure_ascii=False, sort_keys=True) + "\n"


def record(obs_dir: Path, entries: list[dict]) -> int:
    """Append entries not already queued for ``(product, source, name)``. Returns how many."""
    with _locked(obs_dir):
        have = {key(e) for e in read(obs_dir)}
        fresh: list[dict] = []
        for e in entries:
            if key(e) not in have:
                have.add(key(e))
                fresh.append(e)
        if fresh:
            append_lines(queue_path(obs_dir), "".join(_line(e) for e in fresh))
    return len(fresh)


def decide(obs_dir: Path, entry: dict, account_key: str) -> None:
    """Remember which account a person said this name is.

    The queue, not the observation, is the record of the decision: an observation for an
    account that was already seen is folded into the earlier one, so it cannot be.
    """
    with _locked(obs_dir):
        append_lines(queue_path(obs_dir), _line(entry, status="decided", account_key=account_key))


def dismiss(obs_dir: Path, entry: dict) -> None:
    """Remember that a person said this is not a prospect, so it is never queued again."""
    with _locked(obs_dir):
        append_lines(queue_path(obs_dir), _line(entry, status="dismissed"))
