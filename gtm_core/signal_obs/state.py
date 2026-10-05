"""R1.4: the member set each source last showed, kept outside any prunable capture (QA-1).

A diff is taken against this file, never against an older capture, so ``signal_sources prune``
can delete captures without changing what "new since last time" means. The file is kept per
product: two products may use the same source id and must never read each other's record.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..paths import _safe_segment
from .switch import require_enabled

SCHEMA = 1
_SHA = re.compile(r"[0-9a-f]{64}")


class StateError(ValueError):
    """A state file that is present but cannot be trusted."""


def state_path(obs_dir: Path, product: str, source_id: str) -> Path:
    seg = _safe_segment
    return obs_dir / "state" / seg(product, "product") / f"{seg(source_id, 'source id')}.json"


def _refuse_links(obs_dir: Path, path: Path) -> None:
    for link in (obs_dir / "state", path.parent, path):
        if link.is_symlink():
            raise StateError(f"{link.name} is a link, which a member-set file never follows")


def load_state(obs_dir: Path, product: str, source_id: str) -> dict | None:
    path = state_path(obs_dir, product, source_id)
    _refuse_links(obs_dir, path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StateError(f"{path.name} cannot be read ({type(exc).__name__})") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise StateError(f"{path.name} is not a schema {SCHEMA} member-set file")
    if data.get("status") not in ("ok", "empty", "failed") or not isinstance(
        data.get("members"), dict
    ):
        raise StateError(f"{path.name} has no valid status or member set")
    if not all(
        isinstance(m, dict) and isinstance(m.get("account_key", ""), str)
        for m in data["members"].values()
    ):
        raise StateError(f"{path.name} has a member that is not a name and an account")
    return data


def save_state(obs_dir: Path, product: str, source_id: str, state: dict) -> Path:
    """Write atomically: a reader sees the old file or the whole new one."""
    require_enabled()
    path = state_path(obs_dir, product, source_id)
    _refuse_links(obs_dir, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps({"schema": SCHEMA, **state}, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)
    return path


def _shas_from_state(root: Path) -> set[str]:
    out: set[str] = set()
    if not root.is_dir():
        return out
    for link in (q for q in root.rglob("*") if q.is_symlink()):
        raise StateError(f"{link.name} under state/ is a link, which this guard does not follow")
    for p in sorted(root.rglob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StateError(f"state file {p.name} cannot be read ({type(exc).__name__})") from exc
        sha = data.get("capture_sha256") if isinstance(data, dict) else None
        if not isinstance(sha, str):
            raise StateError(f"state file {p.name} has no capture sha")
        if sha == "":
            continue  # an empty or failed extraction that never pointed at a capture
        if not _SHA.fullmatch(sha.lower()):
            raise StateError(f"state file {p.name} names {sha[:12]!r}, which is not a capture sha")
        out.add(sha.lower())
    return out


def _shas_from_shards(obs_dir: Path) -> set[str]:
    out: set[str] = set()
    if not obs_dir.is_dir():
        return out
    for shard in sorted(obs_dir.glob("*.jsonl")):
        if shard.is_symlink() or shard.name == "unresolved.jsonl":
            continue
        try:
            for line in shard.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        c_sha = record.get("capture_sha256")
                        if isinstance(c_sha, str) and _SHA.fullmatch(c_sha.lower()):
                            out.add(c_sha.lower())
                except ValueError:
                    continue
        except OSError:
            continue
    return out


def referenced_shas(obs_dir: Path) -> set[str]:
    """Capture shas a state file or an observation shard still points at: the ones ``prune`` must not delete.

    Fails closed. A state file that cannot be read, or that names something other than a
    capture sha, raises :class:`StateError`: deleting captures on the word of a damaged guard
    would make "new since last time" unanswerable, so the caller must delete nothing.
    """
    return _shas_from_state(obs_dir / "state") | _shas_from_shards(obs_dir)
