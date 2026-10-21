"""Inputs a page depends on by NAME, not by bytes (PRD 2026-09-30 §9, item 2).

``account_has_dossier`` globs an account folder and never opens what it finds, so a dossier that
appears flips a Tier-A account's row while there is nothing to digest. The names are recorded
beside the digests, and a name that appears or vanishes convicts the page. Editing what such a file
CONTAINS is not a change here: the page never read its bytes, and convicting an edit would send an
operator to a refresh that changes nothing.

A DIRECTORY matches too, because the page's own lookup is ``any(folder.glob(pat))`` and counts one.
The one residual: a lookup that resolves an account to a different folder (an alias) is not seen;
the globs cover every account folder, so a match under any of them is.

No import from ``page_inputs``: it imports this, and the lexical confinement guard is passed in.
"""

from __future__ import annotations

import glob as _glob
import os
from collections.abc import Callable
from pathlib import Path


def matching(root: Path, globs: list[str]) -> list[str]:
    """Root-relative names matching ``globs``, files and directories alike."""
    return sorted(
        {
            str(Path(hit).relative_to(root))
            for g in globs
            # The ROOT is a path, never a pattern: `[` or `*` in it would match nothing.
            for hit in _glob.glob(_glob.escape(str(root)) + os.sep + g)
        }
    )


def record(root: Path, name_globs: tuple[str, ...]) -> dict:
    return {"name_globs": sorted(name_globs), "names": matching(root, list(name_globs))}


def _strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def verify(rep, root: Path, inv: dict, expect: bool, confine: Callable[[str], bool]) -> None:
    """Fold appeared / vanished names into ``rep``. ``expect`` says the caller renders something
    that reads names: an inventory with no record of them is then STALE, never "nothing to track"."""
    if "name_globs" not in inv and "names" not in inv:
        if expect:
            rep.meta_stale.append("inventory predates name tracking — re-render")
        return
    globs, names = inv.get("name_globs"), inv.get("names")
    if not (_strings(globs) and _strings(names)):
        rep.damaged.append("names:malformed")
        return
    safe = [g for g in globs if confine(g)]
    rep.refused.extend(f"glob:{g}" for g in globs if not confine(g))
    now, then = set(matching(root, safe)), set(names)
    rep.new.extend(f"name:{n}" for n in sorted(now - then))
    rep.missing.extend(f"name:{n}" for n in sorted(then - now))
