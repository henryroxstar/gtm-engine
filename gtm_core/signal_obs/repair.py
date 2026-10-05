"""`repair`: cut a torn last line off one observation shard, for one product's profile.

The low-level truncation is ``observations.repair``. This is its only entry point: it holds the
kill switch, takes the run scope like every other reader, and refuses a shard that is not a plain
file under this profile's observation directory (a symlink out of it is refused).
"""

from __future__ import annotations

from pathlib import Path

from .. import run_scope
from ..confine import ConfinementError, confined_source_file
from ..paths import _safe_segment, resolve_content_root
from . import observations as obs
from . import switch


def run_repair(
    profile: str,
    product: str | None,
    shard: str,
    *,
    apply: bool = False,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
) -> obs.RepairPlan:
    switch.require_enabled()
    run_scope.require(profile, product, profiles_root=profiles_root)
    root = content_root or resolve_content_root()
    directory = root / _safe_segment(profile, "profile") / "prospects" / "observations"
    if shard != obs.QUEUE_FILE and not obs._SHARD_RE.match(shard):
        raise obs.ObservationError(f"shard name {shard!r} is not <writer>-<YYYY-MM>.jsonl")
    try:
        confined_source_file(directory / shard, content_root=directory, action="repair")
    except ConfinementError as exc:
        raise obs.ObservationError(str(exc)) from exc
    return obs.repair(directory, shard, apply=apply)
