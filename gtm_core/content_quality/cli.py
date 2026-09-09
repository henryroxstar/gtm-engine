from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..paths import resolve_content_root
from .post import post_check
from .pre import pre_check
from .register import register_check
from .script import script_check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.content_quality",
        description="Deterministic local quality gates for content items.",
    )
    parser.add_argument("--content-root", default=None, help="override content root")
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--profile", required=True, help="active profile slug")
    parent.add_argument("--item", required=True, help="ContentItem id")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pre", parents=[parent], help="pre-generation checks")
    sub.add_parser("post", parents=[parent], help="post-generation checks")
    sub.add_parser(
        "script",
        parents=[parent],
        help="script front-block checks (claims_verified) before storyboard/render spend",
    )
    sub.add_parser(
        "register",
        parents=[parent],
        help="advisory: countable tells that a spoken script was written, not spoken",
    )
    args = parser.parse_args(argv)

    content_root = (
        Path(args.content_root).expanduser().resolve()
        if args.content_root
        else resolve_content_root()
    )

    if args.cmd == "pre":
        result = pre_check(args.profile, args.item, content_root=content_root)
    elif args.cmd == "script":
        result = script_check(args.profile, args.item, content_root=content_root)
    elif args.cmd == "register":
        result = register_check(args.profile, args.item, content_root=content_root)
    else:
        result = post_check(args.profile, args.item, content_root=content_root)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["proceed"] else 1
