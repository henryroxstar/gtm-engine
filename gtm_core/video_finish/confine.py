from __future__ import annotations

from pathlib import Path

from .errors import PolishError


def _confined_output(out_path: Path, *, content_root: Path | None) -> Path:
    """Resolve a CLI ``--out`` and refuse anything outside the content root.

    The write-side mirror of :func:`_safe_asset_path` (which guards reads of an existing file):
    this one's target does not exist yet, so it checks the resolved *parent* instead. CLAUDE.md's
    "the only writable state is the resolved content root" is the rule being enforced — the `mux`
    and `stitch` verbs exist so ffmpeg runs from inside this module rather than from a raw Bash
    call, and that boundary would be theatre if the module then wrote anywhere it was pointed.
    """
    from ..paths import resolve_content_root

    root = (content_root if content_root is not None else resolve_content_root()).resolve()
    resolved = out_path.expanduser().resolve()
    try:
        resolved.parent.relative_to(root)
    except ValueError as exc:
        raise PolishError(
            f"refusing to write outside the resolved content root: {resolved} (root: {root})"
        ) from exc
    return resolved


def _confined_dir(out_dir: Path, *, content_root: Path | None) -> Path:
    """Resolve a CLI ``--out-dir`` and refuse anything outside the content root.

    :func:`_confined_output` checks a not-yet-existing FILE by testing its parent. A verb that
    writes many files into one directory (:func:`split`) needs the directory itself checked —
    testing its parent would only prove the directory's neighbourhood is inside the root, which
    is one level too loose.
    """
    from ..paths import resolve_content_root

    root = (content_root if content_root is not None else resolve_content_root()).resolve()
    resolved = out_dir.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PolishError(
            f"refusing to write outside the resolved content root: {resolved} (root: {root})"
        ) from exc
    return resolved


def _safe_asset_path(asset_path: Path, *, content_root: Path) -> Path:
    """Refuse an asset outside the resolved content root, missing, or non-file."""
    resolved = asset_path.expanduser().resolve()
    root = content_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PolishError(
            f"refusing to polish a file outside the resolved content root: {resolved} "
            f"(root: {root})"
        ) from exc
    if not resolved.is_file():
        raise PolishError(f"source file does not exist: {resolved}")
    return resolved
