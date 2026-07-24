"""CLI: resolve a profile knowledge file with a product-level override.

Skills are markdown executed by the brain via tools; they cannot import Python.
So instead of hardcoding ``profiles/<active>/knowledge/<file>`` in every SKILL.md
(which ignores per-product knowledge), product-bound skills shell out to this CLI,
which delegates to :func:`gtm_core.paths.resolve_knowledge_file` — the single source
of truth for the product→profile fallback.

Resolution: ``products/<product>/<file>`` if it exists, else ``knowledge/<file>``.
This is backward-compatible — a profile with no per-product files always resolves
to the profile level, so nothing changes for it.

VPS invocation:   python -m gtm_core.resolve_knowledge <file> --profile P [--product S]
Local invocation: python "$CLAUDE_PLUGIN_ROOT/lib/gtm_core/resolve_knowledge.py" <file> --profile P [--product S]

Prints the resolved absolute path to stdout and exits 0. With ``--require-exists``,
exits 3 (and prints nothing to stdout) when the resolved path does not exist —
useful when the caller wants to branch on a genuinely-missing knowledge file.
Unsafe profile/product/filename segments (path traversal) exit 2.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .paths import PathConfig, resolve_knowledge_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.resolve_knowledge",
        description="Resolve a profile knowledge file, product overriding profile.",
    )
    parser.add_argument("filename", help="bare knowledge filename, e.g. icp-personas.md")
    parser.add_argument("--profile", required=True, help="active profile slug")
    parser.add_argument(
        "--product",
        default=None,
        help="active product slug; omit for profile-wide resolution",
    )
    parser.add_argument(
        "--profiles-root",
        default=None,
        help="override profiles root (defaults to GTM_PROFILES_ROOT / repo)",
    )
    parser.add_argument(
        "--require-exists",
        action="store_true",
        help="exit 3 if the resolved file does not exist (print nothing)",
    )
    args = parser.parse_args(argv)

    profiles_root = (
        Path(args.profiles_root).expanduser().resolve()
        if args.profiles_root
        else PathConfig.from_env().profiles_root
    )

    try:
        path = resolve_knowledge_file(profiles_root, args.profile, args.filename, args.product)
    except ValueError as exc:
        print(f"[resolve-knowledge] {exc}", file=sys.stderr)
        return 2

    if args.require_exists and not path.is_file():
        print(f"[resolve-knowledge] not found: {path}", file=sys.stderr)
        return 3

    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
