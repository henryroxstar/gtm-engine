"""The ``narration-track`` command — its own module because ``cli.py`` is at the §R10 cap.

Split rather than ceiling-raised (docs/RULES.md §R10): ``cli.py`` was 30 lines from the 500-line
cap for unlisted files when this command arrived, and a ratchet answered by moving the ratchet is
not a ratchet. The seam is the one the rest of this package already uses — a concern per module,
with ``cli.py`` left as the router.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .confine import _confined_output
from .errors import FfmpegUnavailable, PolishError


def _cmd_narration_track(args) -> int:
    """Build the VO master from the shot list's narration lane.

    THE LINT IS A GATE HERE, not a report. ``shots_lint``'s narration rules run first and a
    failure refuses the build, which is the difference between a checked field and an annotated
    one: an overlapping read or a line past the end of the cut cannot reach the mix by way of a
    warning nobody read. Only the narration rules are consulted — the craft rules govern the
    picture, and a camera-move finding has no business blocking an audio build.
    """
    from gtm_core.shots_lint.narration import (
        _lint_narration_duty_cycle,
        _lint_narration_timeline,
    )

    from .narration import NarrationError, build_narration_track, lines_from_shotlist

    try:
        doc = json.loads(args.shots.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"video-finish: unreadable shot list {args.shots}: {exc}", file=sys.stderr)
        return 2
    try:
        out = _confined_output(args.out, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2

    lint_errors: list[str] = []
    warnings: list[str] = []
    _lint_narration_timeline(doc, lint_errors)
    _lint_narration_duty_cycle(doc, warnings)
    if lint_errors:
        for message in lint_errors:
            print(f"video-finish: {message}", file=sys.stderr)
        return 4

    total = doc.get("total_duration_s")
    if not isinstance(total, (int, float)) or isinstance(total, bool) or total <= 0:
        print("video-finish: shot list has no usable total_duration_s", file=sys.stderr)
        return 4
    try:
        result = build_narration_track(
            lines_from_shotlist(doc),
            base_dir=(args.base_dir or Path.cwd()).resolve(),
            out_path=out,
            total_duration_s=float(total),
        )
    except NarrationError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 4
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3

    for message in warnings:
        print(f"video-finish: {message}", file=sys.stderr)
    if args.as_json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(
            f"video-finish: wrote {result.out_path} — {len(result.lines)} lines, "
            f"{result.speech_duty_pct:.1f}% speech duty over {result.duration_s:g}s"
        )
    return 0
