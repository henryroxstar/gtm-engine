"""Issue-to-issue delta for market-intelligence signals.

The delta is deterministic: it diffs two ``signals-<date>.json`` files by ``id`` and
classifies each change into one of five buckets. This is what lets §0 "Since last issue"
answer "what changed this week" from data rather than from a model's memory of last
week's prose.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from ..paths import PathConfig, _safe_segment
from . import signals as sig

# Direction on an ordinal threat scale. "Escalation" means the signal moved toward
# ``threat`` or materialized (material false → true).
_THREAT_LEVEL = {
    "neutral": 0,
    "validation": 1,
    "opportunity": 2,
    "threat": 3,
}


def _threat_level(direction: str) -> int:
    return _THREAT_LEVEL.get(direction, 0)


def is_escalation(previous: sig.SignalRecord, current: sig.SignalRecord) -> bool:
    """Whether *current* is more urgent than *previous*.

    Escalation = direction moved toward ``threat``, or an immaterial signal became
    material. A change in the opposite direction is not flagged here.
    """
    if not current.material and previous.material:
        # A signal becoming immaterial is not escalation.
        return False
    if current.material and not previous.material:
        return True
    return _threat_level(current.direction) > _threat_level(previous.direction)


def _expired(signal: sig.SignalRecord, today: date) -> bool:
    try:
        event = date.fromisoformat(signal.date)
    except ValueError:
        return False
    return (today - event).days > signal.decay_days


def _by_id(signals: Iterable[sig.SignalRecord]) -> dict[str, sig.SignalRecord]:
    return {s.id: s for s in signals}


def _signals_to_ids(signals: Iterable[sig.SignalRecord]) -> list[str]:
    return [s.id for s in signals]


def carry_forward_ignored(
    previous: list[sig.SignalRecord],
    current: list[sig.SignalRecord],
) -> list[sig.SignalRecord]:
    """Return prior ``triage: ignore`` signals that are missing from *current*.

    Ignore decisions must survive across issues; this list is what the writer merges
    into the current issue so they are not re-litigated every week.
    """
    current_ids = {s.id for s in current}
    return [s for s in previous if s.triage == "ignore" and s.id not in current_ids]


def diff(
    previous: list[sig.SignalRecord] | None,
    current: list[sig.SignalRecord],
    today: date | None = None,
) -> dict:
    """Diff *current* against *previous*.

    Returns a dict with:
      - ``baseline`` (bool): true when there is no previous issue.
      - ``new``: signals in *current* not seen before.
      - ``escalated``: signals that became more threatening or material.
      - ``resolved``: signals explicitly closed this run (triage → ignore).
      - ``still_ignored``: ``ignore`` signals carried forward.
      - ``decayed``: signals from the previous issue that expired and were not
        re-observed (absent from *current*).
      - ``carry_forward_ignored``: prior ignores missing from *current* — the writer
        should merge these in.
    """
    today = today or date.today()
    previous = previous or []
    prev_map = _by_id(previous)
    curr_map = _by_id(current)

    new = [curr_map[i] for i in curr_map if i not in prev_map]

    both_ids = curr_map.keys() & prev_map.keys()
    escalated = [curr_map[i] for i in both_ids if is_escalation(prev_map[i], curr_map[i])]
    resolved = [
        curr_map[i]
        for i in both_ids
        if prev_map[i].triage != "ignore" and curr_map[i].triage == "ignore"
    ]
    still_ignored = [
        curr_map[i]
        for i in both_ids
        if prev_map[i].triage == "ignore" and curr_map[i].triage == "ignore"
    ]

    # Decayed = prior signals that are absent from the current issue and past their
    # decay window. ``ignore`` signals are not flagged as decayed; they were closed.
    decayed = [
        prev_map[i]
        for i in prev_map
        if i not in curr_map and prev_map[i].triage != "ignore" and _expired(prev_map[i], today)
    ]

    return {
        "kind": "voc-signal-delta",
        "as_of": today.isoformat(),
        "baseline": not previous,
        "new": _signals_to_ids(new),
        "escalated": _signals_to_ids(escalated),
        "resolved": _signals_to_ids(resolved),
        "still_ignored": _signals_to_ids(still_ignored),
        "decayed": _signals_to_ids(decayed),
        "carry_forward_ignored": _signals_to_ids(carry_forward_ignored(previous, current)),
        "previous_count": len(previous),
        "current_count": len(current),
    }


def _render_bucket(name: str, ids: list[str]) -> str:
    if not ids:
        return f"{name}: none"
    return f"{name}: {len(ids)}\n  " + "\n  ".join(ids)


def render(delta: dict) -> str:
    """Plain-text rendering suitable for the brief's §0 and for CLI output."""
    if delta.get("baseline"):
        lines = ["No prior issue — this is the baseline."]
    else:
        lines = [f"Since last issue ({delta['as_of']}):"]
        lines.append(_render_bucket("New", delta.get("new", [])))
        lines.append(_render_bucket("Escalated", delta.get("escalated", [])))
        lines.append(_render_bucket("Resolved", delta.get("resolved", [])))
        lines.append(_render_bucket("Decayed", delta.get("decayed", [])))
        lines.append(_render_bucket("Still ignored", delta.get("still_ignored", [])))
    carry = delta.get("carry_forward_ignored", [])
    if carry:
        lines.append(_render_bucket("Ignored signals carried forward", carry))
    return "\n".join(lines) + "\n"


def _latest_pair(content_root: Path, profile: str) -> tuple[Path | None, Path | None]:
    """Return (previous_path, current_path) for the two newest signal files."""
    files = sig.list_files(content_root, profile)
    if not files:
        return None, None
    if len(files) == 1:
        return None, files[-1]
    return files[-2], files[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.voc.delta",
        description="Compute the issue-to-issue delta for market-intelligence signals.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument(
        "--prev",
        type=Path,
        default=None,
        help="Override the previous signal file (default: second-newest for profile).",
    )
    parser.add_argument(
        "--current",
        type=Path,
        default=None,
        help="Override the current signal file (default: newest for profile).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the brief §0 text.",
    )
    args = parser.parse_args(argv)

    cfg = PathConfig.from_env(repo_root=args.repo_root)
    prof = _safe_segment(args.profile, "profile")

    prev_path, curr_path = args.prev, args.current
    if not prev_path and not curr_path:
        prev_path, curr_path = _latest_pair(cfg.content_root, prof)

    if curr_path is None:
        raise SystemExit(f"[voc-delta] no signals file found for profile {prof!r}")

    previous = sig.load(prev_path) if prev_path else []
    current = sig.load(curr_path)

    result = diff(previous, current)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
