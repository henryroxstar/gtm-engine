"""Reading a page's ``.inputs.json`` back, and printing what it names — the untrusted half of
:mod:`gtm_core.page_inputs`, split out because that module sits at its §R10 ceiling.

Everything here treats the sidecar as untrusted (§R5): a hand edit, a truncated write, a
non-UTF-8 byte or a producer's schema drift. Nothing here raises for a bad file.
"""

from __future__ import annotations

import json
from pathlib import Path


def inventory_path(page: Path) -> Path:
    """The sidecar that records what ``page`` was built from."""
    return page.with_suffix(".inputs.json")


def printable(text) -> str:
    """``text`` with every character that is not safely printable written as an escape.

    A file name, a recorded path and a recorded scope are all untrusted text that a check prints
    to a terminal and, through the ``status`` skill, into an agent's context. A raw ESC byte
    rewrites the terminal; a ``⟦GATE:…⟧`` in a name reads as a marker. Both are DATA (CLAUDE.md),
    so they print as ``\\x1b`` / ``\\u27e6``. The two bracket characters are escaped by name
    because they are printable and so would otherwise pass. Ordinary names are returned as-is.
    """
    return "".join(
        c if c.isprintable() and c not in "⟦⟧" else c.encode("unicode_escape").decode("ascii")
        for c in str(text)
    )


def load_inventory(inv_path: Path) -> tuple[dict | None, str]:
    """``(record, why it is unusable)`` — never an exception.

    Before F4 a bad sidecar raised straight out of :func:`verify_inventory` — survivable for a
    one-page check, and not for an all-pages one, where one corrupt file would decide the answer
    for every page in the profile.

    The type checks are not belt-and-braces: ``inputs`` as a string iterates to characters and
    ``globs`` as a dict iterates to keys, so both reach ``glob.glob`` and the digest loop as
    plausible-looking garbage rather than failing.
    """
    if inv_path.is_symlink():
        # Never opened: a link can point at ANOTHER tenant's sidecar, whose recorded paths a check
        # would then print as this page's own (red team S4).
        return None, "it is a symlink, so it is not read"
    try:
        raw = inv_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{type(exc).__name__}"
    try:
        rec = json.loads(raw)
    except ValueError:
        return None, "not valid JSON"
    if not isinstance(rec, dict):
        return None, f"the top level is a {type(rec).__name__}, not an object"
    for key in ("inputs", "globs"):
        if rec.get(key) is None:
            rec.pop(key, None)  # null is ABSENT: the verifier names it, it does not iterate it
    for key, want in (("inputs", list), ("globs", list), ("profile_inputs", list), ("meta", dict)):
        if key in rec and not isinstance(rec[key], want):
            return None, f"{key!r} is a {type(rec[key]).__name__}, not a {want.__name__}"
    return rec, ""


def recorded(inv: dict) -> tuple[dict[str, str], list[str]]:
    """``({path: sha256}, refused)`` from ``inputs``, dropping nothing silently. A row that is
    not a dict, or whose ``path`` is not a string, is a refusal — it names itself in the report
    rather than vanishing from the set of things this page claims to track."""
    out: dict[str, str] = {}
    refused: list[str] = []
    for row in inv.get("inputs", []):
        rel = row.get("path") if isinstance(row, dict) else None
        if not isinstance(rel, str) or not rel:
            refused.append(f"row:{row!r}"[:120])
            continue
        out[rel] = row.get("sha256")
    return out, refused
