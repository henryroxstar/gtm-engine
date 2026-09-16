"""The ``voice-polish`` command — its own module because ``cli.py`` is at the §R10 cap.

Split rather than ceiling-raised (docs/RULES.md §R10), same seam ``cli_narration.py`` already
uses for the same reason — see that module's docstring.
"""

from __future__ import annotations

import json
import sys

from .confine import _confined_output
from .errors import FfmpegUnavailable, PolishError
from .voice_polish import polish_voice


def _cmd_voice_polish(args) -> int:
    try:
        out = _confined_output(args.out, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    try:
        result = polish_voice(args.src, out)
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 4
    if args.as_json:
        print(
            json.dumps(
                {
                    "out_path": str(result["out_path"]),
                    "input_duration_s": result["input_duration_s"],
                    "output_duration_s": result["output_duration_s"],
                    "sample_rate": result["sample_rate"],
                    "channels": result["channels"],
                },
                indent=2,
            )
        )
    else:
        print(
            f"video-finish: wrote {result['out_path']} "
            f"({result['output_duration_s']:.2f}s, {result['sample_rate']}Hz, "
            f"{result['channels']}ch)"
        )
    return 0
