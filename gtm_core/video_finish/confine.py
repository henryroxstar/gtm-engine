"""``video_finish``'s three confinement guards — now thin wrappers over :mod:`gtm_core.confine`.

The predicate itself moved to ``gtm_core/confine.py`` on 2026-09-06 so the Gemini image worker's
new file-path parameter could share it instead of becoming the sixth copy in the tree. These
three names stay, with their ``PolishError`` type and their exact messages, because ``cli.py``
maps ``PolishError`` to exit 2 and five golden CLI cases pin both the code and the string.
"""

from __future__ import annotations

from pathlib import Path

from ..confine import ConfinementError, confined_dir, confined_output_path, confined_source_file
from .errors import PolishError


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
        raise PolishError(str(exc)) from exc


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
        raise PolishError(str(exc)) from exc


def _safe_asset_path(asset_path: Path, *, content_root: Path) -> Path:
    """Refuse an asset outside the resolved content root, missing, or non-file."""
    try:
        return confined_source_file(asset_path, content_root=content_root, action="polish a file")
    except ConfinementError as exc:
        raise PolishError(str(exc)) from exc
