from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api import lint, report
from .model import ERROR, READER, RECORD
from .parse import infer_surface


def main(argv: list[str] | None = None) -> int:
    # The package docstring is the text `description=__doc__` printed before the split;
    # imported here rather than at module scope because __init__ imports this module.
    from . import __doc__ as package_doc

    parser = argparse.ArgumentParser(prog="gtm_core.brief_lint", description=package_doc)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--surface", choices=[READER, RECORD], default=None)
    parser.add_argument("--strict", action="store_true", help="treat T5/T7 warnings as failures")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    failed = False
    payload: dict[str, list[dict]] = {}

    for path in args.paths:
        if not path.exists():
            print(f"✗ no such file: {path}", file=sys.stderr)
            failed = True
            continue
        surface = args.surface or infer_surface(path)
        findings = lint(path.read_text(encoding="utf-8"), surface)
        if args.as_json:
            payload[str(path)] = [f.__dict__ | {"surface": surface} for f in findings]
        else:
            if findings:
                report(path, findings, args.strict)
            else:
                print(f"✓ {path} ({surface} surface) — clean")
        if any(f.severity == ERROR for f in findings) or (args.strict and findings):
            failed = True

    if args.as_json:
        print(json.dumps(payload, indent=2))
    elif failed:
        print(
            "\n  The reader surface is for a product, sales or strategy person who has never"
            "\n  opened this repo. Mechanics belong in the markdown brief, which is the"
            "\n  internal record and is linted far more loosely."
            "\n\n  A rule that is genuinely wrong about a line can be suppressed with"
            "\n  <!-- lint-ok T2: reason --> on that line. Name the tier and say why."
        )
    return 1 if failed else 0
