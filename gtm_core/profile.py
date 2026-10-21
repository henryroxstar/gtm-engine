"""CLI: report the active tenant profile and list available ones.

Local Claude Code sessions have no session store binding a profile the way the VPS
cockpit does (``agent/session.py`` binds one profile per Telegram ``chat_id`` and
switches only via the operator's ``/profile`` command — see ``cockpit/commands.py``).
Locally the active profile is just ``ACTIVE_PROFILE`` / ``GTM_PROFILES_ROOT`` env,
which is easy to leave unset or stale — the exact gap that lets a Syften pull or a
community-signal run silently land in the wrong tenant's ``content/`` tree.

This CLI never switches a profile (that stays the operator's call, per CLAUDE.md's
tenant-boundary invariant: "you do not switch it yourself"). It only makes the
currently-resolved tenant visible, so a mistyped or unset ``ACTIVE_PROFILE`` is loud
instead of silent, and lists what profiles actually exist to switch to.

VPS invocation:   python -m gtm_core.profile status|list
Local invocation: python "$CLAUDE_PLUGIN_ROOT/lib/gtm_core/profile.py" status|list

``status`` prints a human line then a JSON line ``{"profile","content_root",
"profiles_root","profile_dir_exists"}`` and exits 3 (instead of 0) when
``profiles/<profile>/PROFILE.md`` is missing — a mistyped/unbound tenant should be
loud, not a silent write into a directory that doesn't represent a real profile.

``list`` prints one profile name per line (valid profiles only — a directory counts
only if it has a ``PROFILE.md``, same rule as ``agent.profiles.list_profiles``),
marking the active one with ``*``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .paths import PathConfig


def _list_profiles(profiles_root: Path) -> list[str]:
    """Names of valid profile directories, sorted. A directory counts only if it
    has a PROFILE.md — mirrors agent.profiles.list_profiles, reimplemented here
    (rather than imported) so gtm_core stays stdlib-only / SDK-free."""
    if not profiles_root.is_dir():
        return []
    return sorted(
        child.name
        for child in profiles_root.iterdir()
        if child.is_dir() and (child / "PROFILE.md").is_file()
    )


def _status(args) -> int:
    cfg = PathConfig.from_env(repo_root=args.repo_root)
    profile = args.profile or cfg.default_profile
    profile_dir = cfg.profiles_root / profile
    exists = (profile_dir / "PROFILE.md").is_file()
    content_root = cfg.content_root / profile

    print(f"active profile: {profile}")
    print(f"  content root:   {content_root}")
    print(f"  profiles root:  {cfg.profiles_root}")
    if not exists:
        print(f"  WARNING: profiles/{profile}/PROFILE.md not found — not a real profile")
    print(
        json.dumps(
            {
                "profile": profile,
                "content_root": str(content_root),
                "profiles_root": str(cfg.profiles_root),
                "profile_dir_exists": exists,
            }
        )
    )
    return 0 if exists else 3


def _list(args) -> int:
    cfg = PathConfig.from_env(repo_root=args.repo_root)
    active = args.profile or cfg.default_profile
    names = _list_profiles(cfg.profiles_root)
    if not names:
        print(f"(no profiles found under {cfg.profiles_root})")
        return 0
    for name in names:
        marker = "* " if name == active else "  "
        print(f"{marker}{name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gtm_core.profile",
        description="Report the active tenant profile (never switches one).",
    )
    parser.add_argument("--repo-root", type=Path, default=None, help="Override repo root.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("status", help="Print the resolved active profile + paths.")
    st.add_argument(
        "--profile", default=None, help="Check this profile instead of the resolved default."
    )

    ls = sub.add_parser("list", help="List available profiles, marking the active one.")
    ls.add_argument(
        "--profile", default=None, help="Which profile to mark active (default: resolved)."
    )

    args = parser.parse_args(argv)
    if args.cmd == "status":
        return _status(args)
    if args.cmd == "list":
        return _list(args)
    parser.error(f"unknown command {args.cmd!r}")
    return 1  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
