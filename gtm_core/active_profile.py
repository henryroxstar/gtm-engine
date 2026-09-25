"""Read/write content/.active-profile for the desktop Claude session.

On the desktop path (is_desktop_session() is True), setup and /profile confirmation
write content/.active-profile so subsequent commands automatically resolve that
profile. On headless paths (VPS, cockpit, backend), the marker is inert by construction.
"""

from __future__ import annotations

import sys
from pathlib import Path

from gtm_core.paths import _safe_segment, resolve_content_root, resolve_profiles_root
from gtm_core.runtime_kind import is_desktop_session

MARKER_FILENAME = ".active-profile"


def set_active(slug: str, content_root: Path | None = None) -> Path:
    """Set the active profile slug in content/.active-profile."""
    clean_slug = _safe_segment(slug.strip(), "profile_slug")
    root = resolve_content_root() if content_root is None else content_root
    marker_path = root / MARKER_FILENAME
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(f"{clean_slug}\n", encoding="utf-8")
    return marker_path


def show(content_root: Path | None = None) -> str | None:
    """Return the currently marked active profile slug, if any."""
    root = resolve_content_root() if content_root is None else content_root
    marker_path = root / MARKER_FILENAME
    if not marker_path.exists():
        return None
    raw = marker_path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return _safe_segment(raw, "profile_slug")


def read_active_marker(
    content_root: Path | None = None,
    profiles_root: Path | None = None,
) -> str | None:
    """Read the active profile marker if running in a desktop session and profile exists.

    Returns None if:
    - Not a desktop session (headless mode)
    - Marker file does not exist or is empty
    - The profile's PROFILE.md does not exist (warns to stderr)
    """
    if not is_desktop_session():
        return None

    try:
        slug = show(content_root)
    except ValueError:
        return None

    if not slug:
        return None

    p_root = resolve_profiles_root() if profiles_root is None else profiles_root
    profile_md = p_root / slug / "PROFILE.md"
    if not profile_md.exists():
        print(
            f"warning: active profile marker points at {slug}, which no longer exists",
            file=sys.stderr,
        )
        return None

    return slug


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: `python -m gtm_core.active_profile [set <slug> | show]`."""
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] == "show":
        current = show()
        if current:
            print(current)
        else:
            print("No active profile set.")
        return 0

    if args[0] == "set":
        if len(args) < 2:
            print("Usage: python -m gtm_core.active_profile set <slug>", file=sys.stderr)
            return 2
        slug = args[1]
        try:
            set_active(slug)
        except ValueError as err:
            print(f"Error: {err}", file=sys.stderr)
            return 1
        print(f"Active profile set to {slug}")
        return 0

    print(f"Unknown command: {args[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
