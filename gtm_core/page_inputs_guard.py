"""Writes that cannot follow a link — the other half of :mod:`gtm_core.page_inputs_io`.

A page and its ``.inputs.json`` sit in an operator-owned folder, and a symlink there is a way for a
re-render to overwrite a file somewhere else: ``Path.write_text`` follows the link. The refresh now
reaches every page that has a sidecar automatically, so "the operator would not do that" stopped
being a defence. Every page-side write goes through :func:`write_text`, which REFUSES a link by name
and writes nothing, rather than replacing the link (that would quietly turn the operator's
arrangement into something else) or following it.

A link to the profile folder itself is not refused: a tenant tree backed by external storage is
exactly that shape (CLAUDE.md), and it is the page and sidecar *leaves* that are checked.
"""

from __future__ import annotations

import os
from pathlib import Path

from .fsio import atomic_write_text
from .page_inputs_io import printable

#: The one remedy for a linked page or sidecar, said the same way by the check and the refresh.
LINK_FIX = "it is a symlink — replace it with a regular file or delete it"


class RefusedWrite(Exception):
    """A write this module will not make. The message is finished text, safe to print."""


def refuse_links(*paths: Path) -> None:
    """Raise :class:`RefusedWrite` naming the first path that is a symlink (a dangling one too)."""
    for path in paths:
        if os.path.islink(path):
            raise RefusedWrite(
                f"refused: {printable(Path(path).name)} — {LINK_FIX}. Nothing was written."
            )


def write_text(path: Path, text: str) -> None:
    """``text`` to ``path`` atomically, unless ``path`` is a symlink."""
    refuse_links(path)
    atomic_write_text(path, text)
