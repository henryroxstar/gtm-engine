"""``video_finish``'s three confinement guards — now thin wrappers over :mod:`gtm_core.confine`.

The predicate itself moved to ``gtm_core/confine.py`` on 2026-09-06 so the Gemini image worker's
new file-path parameter could share it instead of becoming the sixth copy in the tree. These
three names stay, with their ``PolishError`` type and their exact messages, because ``cli.py``
maps ``PolishError`` to exit 2 and five golden CLI cases pin both the code and the string.

The symlink hint (plan change #8) lives here, not in ``gtm_core/confine.py``: that module also
backs the Gemini and Higgsfield MCP servers, so a hint text landing there could reach a model
directly, and a wrong hint is coaching an agent to widen its own root toward an attacker-
controlled symlink. ``gtm_core.confine._first_symlink_under_root`` (a bare path-walk, no message)
is shared; only THIS module decides whether its result becomes an appended hint, and only for a
"outside the resolved content root" refusal — never for "does not exist" or an over-cap file.
"""

from __future__ import annotations

from pathlib import Path

from ..confine import (
    ConfinementError,
    _first_symlink_under_root,
    confined_dir,
    confined_output_path,
    confined_source_file,
)
from .errors import PolishError

_OUTSIDE_ROOT = "outside the resolved content root"


def _unresolved_absolute(path: Path) -> Path:
    """``path``, made absolute the way a shell would (cwd-joined), WITHOUT following symlinks —
    the walk needs the path as the caller wrote it, not where it ultimately resolves to."""
    p = Path(path).expanduser()
    return p if p.is_absolute() else Path.cwd() / p


def _symlink_hint(raw_path: Path, root: Path) -> str:
    """The ` — <link> is a symlink to <target>; ...` suffix, or "" when no hint applies."""
    link = _first_symlink_under_root(raw_path, root)
    if link is None:
        return ""
    try:
        target = link.resolve()
    except OSError:
        return ""
    return (
        f" — {link} is a symlink to {target}; if that is this profile's content dir, pass "
        f"--content-root {link} (or set GTM_CONTENT_ROOT)"
    )


def _hinted_root(content_root: Path | None) -> Path:
    """The same fallback ``gtm_core.confine._root`` uses, duplicated here (rather than importing
    that private helper) because it is one line and this module already treats ``confine.py``'s
    internals as off-limits beyond the one path-walk it explicitly shares."""
    if content_root is not None:
        return content_root.resolve()
    from ..paths import resolve_content_root

    return resolve_content_root().resolve()


def _confined_output(out_path: Path, *, content_root: Path | None) -> Path:
    """Resolve a CLI ``--out`` and refuse anything outside the content root.

    The write-side mirror of :func:`_safe_asset_path` (which guards reads of an existing file):
    this one's target does not exist yet, so it checks the resolved *parent* instead. CLAUDE.md's
    "the only writable state is the resolved content root" is the rule being enforced — the `mux`
    and `stitch` verbs exist so ffmpeg runs from inside this module rather than from a raw Bash
    call, and that boundary would be theatre if the module then wrote anywhere it was pointed.
    """
    try:
        return confined_output_path(out_path, content_root=content_root)
    except ConfinementError as exc:
        msg = str(exc)
        if _OUTSIDE_ROOT in msg:
            msg += _symlink_hint(_unresolved_absolute(out_path), _hinted_root(content_root))
        raise PolishError(msg) from exc


def _confined_dir(out_dir: Path, *, content_root: Path | None) -> Path:
    """Resolve a CLI ``--out-dir`` and refuse anything outside the content root.

    :func:`_confined_output` checks a not-yet-existing FILE by testing its parent. A verb that
    writes many files into one directory (:func:`split`) needs the directory itself checked —
    testing its parent would only prove the directory's neighbourhood is inside the root, which
    is one level too loose.
    """
    try:
        return confined_dir(out_dir, content_root=content_root)
    except ConfinementError as exc:
        msg = str(exc)
        if _OUTSIDE_ROOT in msg:
            msg += _symlink_hint(_unresolved_absolute(out_dir), _hinted_root(content_root))
        raise PolishError(msg) from exc


def _safe_asset_path(asset_path: Path, *, content_root: Path) -> Path:
    """Refuse an asset outside the resolved content root, missing, or non-file."""
    try:
        return confined_source_file(asset_path, content_root=content_root, action="polish a file")
    except ConfinementError as exc:
        msg = str(exc)
        if _OUTSIDE_ROOT in msg:
            msg += _symlink_hint(_unresolved_absolute(asset_path), content_root.resolve())
        raise PolishError(msg) from exc
