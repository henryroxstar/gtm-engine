"""Path confinement to the resolved content root — one predicate, shared.

CLAUDE.md's tenant rule is that the only writable state is the resolved content root, and
that nothing reads one profile's content while bound to another. By 2026-09-06 that predicate
had been written five times — ``video_finish/confine.py``, ``reap_upload.py``,
``media_host.py``, ``media_fetch.py``, ``backend/artifacts.py`` — each a near-identical
``expanduser().resolve()`` → ``relative_to(root)`` → ``is_file()`` body with its own exception
class and its own message. Five bodies are five places the ban can quietly stop holding
(docs/RULES.md §R13), and the sixth caller — a file-path parameter on an MCP worker that ships
the bytes to a third party — is exactly the one where a loosened copy would be an exfiltration
primitive rather than a bug. So the predicate moves here and the callers delegate.

Three shapes, because the callers need three:

* :func:`confined_source_file` — an EXISTING file to read. Refuses outside the root, missing,
  not-a-file, and (optionally) over a byte cap.
* :func:`confined_output_path` — a file that does NOT exist yet, so the resolved *parent* is
  what gets checked.
* :func:`confined_dir` — a directory many files will be written into; checking its parent would
  only prove the directory's neighbourhood is inside the root, one level too loose.

Two properties are deliberate and are pinned by ``tests/test_confine.py`` rather than left
implicit. ``Path.resolve()`` follows symlinks, so a symlink INSIDE the root that points OUTSIDE
is refused on its target, not its name — and, by the same mechanism, a path outside the root
that symlinks INTO it resolves inside and is accepted; the check is about where the bytes live.
And ``content_root`` on the read side is an explicit argument, never resolved internally: one
caller (``video_finish.narration``) confines to a shot-list base directory, not the content
root, and binding the helper to ``resolve_content_root()`` would silently widen that caller's
boundary.

:class:`ConfinementError` subclasses ``Exception`` directly — not ``ValueError`` — because
``video_finish/cli.py`` maps ``ValueError`` to exit 4 and its own ``PolishError`` to exit 2, and
a refusal that fell into the wrong ``except`` arm would change a golden-pinned exit code. The
``video_finish`` wrappers translate to ``PolishError`` themselves.

Stdlib only: this module sits under the §R6 egress scan and must never import an HTTP client.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "ConfinementError",
    "confined_dir",
    "confined_output_path",
    "confined_source_file",
]


class ConfinementError(Exception):
    """A path resolved outside the permitted root, or an asset under it is unusable."""


def _root(content_root: Path | None) -> Path:
    from .paths import resolve_content_root

    return (content_root if content_root is not None else resolve_content_root()).resolve()


def confined_source_file(
    path: Path | str,
    *,
    content_root: Path,
    max_bytes: int | None = None,
    action: str = "read",
) -> Path:
    """Resolve an existing file and refuse it unless it lives under ``content_root``.

    ``action`` is the verb in the refusal ("refusing to <action> outside …") so a caller's
    message can name what it was about to do — ``video_finish`` says "polish a file", the
    Gemini worker says "read". ``max_bytes`` refuses an oversized file by name and size rather
    than silently truncating or resizing it; ``None`` means no cap.
    """
    resolved = Path(path).expanduser().resolve()
    root = content_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ConfinementError(
            f"refusing to {action} outside the resolved content root: {resolved} (root: {root})"
        ) from exc
    if not resolved.is_file():
        raise ConfinementError(f"source file does not exist: {resolved}")
    if max_bytes is not None:
        size = resolved.stat().st_size
        if size > max_bytes:
            raise ConfinementError(
                f"source file {resolved.name} is {size} bytes, over the {max_bytes} byte cap"
            )
    return resolved


def confined_output_path(path: Path | str, *, content_root: Path | None = None) -> Path:
    """Resolve a not-yet-existing output file and refuse it unless its parent is under the root.

    The write-side mirror of :func:`confined_source_file`: the target does not exist, so the
    resolved *parent* is what is checked. ``content_root=None`` falls back to
    :func:`gtm_core.paths.resolve_content_root` — every ``video_finish`` CLI verb passes its
    ``--content-root`` option through here, and that option defaults to ``None``.
    """
    root = _root(content_root)
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.parent.relative_to(root)
    except ValueError as exc:
        raise ConfinementError(
            f"refusing to write outside the resolved content root: {resolved} (root: {root})"
        ) from exc
    return resolved


def confined_dir(path: Path | str, *, content_root: Path | None = None) -> Path:
    """Resolve an output directory and refuse it unless the directory itself is under the root."""
    root = _root(content_root)
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ConfinementError(
            f"refusing to write outside the resolved content root: {resolved} (root: {root})"
        ) from exc
    return resolved
