"""A fingerprint of the code that built a page — recorded, and REPORTED, never convicted on.

The digest inventory catches every input that moved. It cannot see the renderer: a changed view
function turns the same bytes into a different page, and a page built by last week's code reads
exactly like one built by today's. This closes that gap for the reader, not for the gate.

REPORT-ONLY BY DESIGN (PRD §9, decided 2026-09-30). A code change does not always change a
number, so counting it toward ``Report.ok`` would send an operator to ``--refresh-all`` for a
comment edit and teach them to ignore the command. The note is printed beside the verdict and
touches nothing else.

WHAT IS COVERED, stated because a fingerprint's claim is only as wide as its file list: every
file under this package (``*.py``, the filter script, the template) plus the sibling
``gtm_core/sequencers.toml``, which the page reads and no inventory digests. What it does NOT
cover is a module OUTSIDE this package that the page imports — a helper edited there changes the
page and not this digest.

Paths are hashed RELATIVE to the package, so a copy, a checkout elsewhere or another machine
gives the same digest for the same code.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
_SUFFIXES = frozenset({".py", ".js", ".html", ".css", ".toml"})


def _files(base: Path) -> list[tuple[str, Path]]:
    found = [
        (p.relative_to(base).as_posix(), p)
        for p in base.rglob("*")
        if p.is_file() and p.suffix in _SUFFIXES and "__pycache__" not in p.parts
    ]
    sibling = base.parent / "sequencers.toml"
    if sibling.is_file():
        found.append(("../sequencers.toml", sibling))
    return found


def code_fingerprint(package_dir: Path | None = None) -> str:
    """sha256 over every covered file's relative path and bytes, in sorted order."""
    digest = hashlib.sha256()
    for rel, path in sorted(_files((package_dir or PACKAGE_DIR).resolve())):
        digest.update(f"{rel}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    return digest.hexdigest()


def code_note(rep, current: str) -> str | None:
    """The note for one page, or None when the code is the code that built it.

    ``rep`` is a :class:`page_inputs.Report`. A page with no inventory, or an unreadable one,
    has nothing recorded to compare and is already convicted for a stronger reason.
    """
    if rep.no_inventory or rep.unreadable or not rep.meta_present:
        return None
    recorded = rep.meta.get("code_fingerprint")
    if not isinstance(recorded, str) or not recorded:
        return (
            "its inventory predates fingerprinting, so which code built it is unknown — re-render"
        )
    if recorded != current:
        return (
            "built by different code than is on disk now. The numbers may be the same; a "
            "re-render (--refresh-all) is the only way to know."
        )
    return None
