"""The verdict of a page-freshness check — what changed, what is missing, and how to say so.

Split from :mod:`gtm_core.page_inputs` (re-exported there, so ``page_inputs.Report`` is unchanged)
because that module sits at its §R10 ceiling. A :class:`Report` holds findings only; it reads no
file and writes none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .page_inputs_io import inventory_path, printable


@dataclass
class Report:
    """What changed under a page since it was rendered. Falsy ``ok`` means stale."""

    page: Path
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    #: The page itself was edited after it was written — a hand-edit, or a second writer.
    page_edited: bool = False
    #: No inventory at all. Treated as stale, deliberately: a page nobody recorded the
    #: inputs for cannot be shown to be current, and "unknown" must not read as "fine".
    no_inventory: bool = False
    #: ``profile`` was passed to :func:`verify_inventory` but the inventory has no
    #: ``"profile_inputs"`` key at all — it predates profile tracking, so PROFILE.md cannot
    #: be shown current either way. Distinct from an EMPTY ``profile_inputs`` list, which
    #: means "no profile files were declared" and is legitimately fresh.
    profile_untracked: bool = False
    #: A recorded content path or glob that `_confine` refused (absolute, or climbs above
    #: root) — a crafted or corrupted inventory row. Never silently skipped-and-forgotten:
    #: a row this page cannot show is safe to dereference is exactly the "cannot show
    #: current" failure `ok` exists to convict, prefixed ``glob:`` when it was a glob entry.
    refused: list[str] = field(default_factory=list)
    #: The sidecar exists but could not be read AS an inventory: not JSON, not an object, or a
    #: wrong-typed field. Named and stale rather than raised — under the all-pages check one
    #: corrupt sidecar would otherwise decide the answer for every other page in the profile.
    unreadable: str = ""
    #: The opaque ``meta`` the writer recorded, and whether it was there at all. This module
    #: never interprets either: the caller that wrote the `meta` is the only thing that can
    #: judge it, and it says so by appending to :attr:`meta_stale`.
    meta: dict = field(default_factory=dict)
    meta_present: bool = False
    #: Finished clauses a CALLER derived from :attr:`meta` — a staleness this module cannot
    #: see, because it is a fact about something other than the bytes on disk. Counts toward
    #: ``ok`` exactly like a changed digest does.
    meta_stale: list[str] = field(default_factory=list)
    #: Tracking keys the sidecar lacks (`inputs`, `globs`, `page_sha256`). A key that is ABSENT is
    #: not "nothing to track": `.get("inputs", [])` read an edited input as fresh (red team I3).
    incomplete: list[str] = field(default_factory=list)
    #: Something recorded was present but could not be READ to compare (a directory where a file
    #: was, a permission error, a name over 255 bytes), as ``"path (OSError class)"``. One such
    #: path convicts its own page; it never aborts the check of the others.
    unverified: list[str] = field(default_factory=list)
    #: The inventory is damaged in a way that is not an escape (a half-recorded names pair).
    damaged: list[str] = field(default_factory=list)
    #: A finished sentence, remedy included, for a page this module could not even look at (a
    #: symlink, an unreadable campaign manifest). Set by the classifier in `pages`, never here.
    blocked: str = ""
    #: The figures-age clause fired (not the "predates tracking" one): a re-render cannot clear it.
    figures_old: bool = False

    @property
    def ok(self) -> bool:
        return not (
            self.blocked
            or self.incomplete
            or self.unverified
            or self.damaged
            or self.changed
            or self.missing
            or self.new
            or self.page_edited
            or self.no_inventory
            or self.profile_untracked
            or self.refused
            or self.unreadable
            or self.meta_stale
        )

    def explain(self) -> str:
        name, inv = printable(self.page.name), printable(inventory_path(self.page).name)
        if self.no_inventory:
            return (
                f"{name}: no {inv} — this page was "
                "written by something that does not record its inputs, so it cannot be "
                "shown to be current. Re-render it with its own explicit --scope, or delete it."
            )
        if self.unreadable:
            return (
                f"{name}: STALE — its {inv} could not be "
                f"read as an inventory ({self.unreadable}), so nothing about this page can be "
                "shown current. Re-render it."
            )
        if self.blocked:
            return f"{name}: STALE — {self.blocked}."
        if self.ok:
            return f"{name}: fresh — every recorded input is unchanged."
        bits = []
        if self.incomplete:
            bits.append(f"inventory is incomplete: missing {', '.join(self.incomplete)}")
        if self.page_edited:
            bits.append("the page itself was edited after it was rendered")
        if self.profile_untracked:
            bits.append("inventory predates profile tracking — re-render")
        for label, paths in (
            ("changed since render", self.changed),
            ("read at render, now missing", self.missing),
            ("appeared since render, never read", self.new),
            ("recorded but refused as unsafe (escapes root)", self.refused),
            ("recorded but could not be read", self.unverified),
            ("recorded in a damaged form", self.damaged),
        ):
            if paths:
                shown = ", ".join(printable(p) for p in paths[:6]) + (
                    f" (+{len(paths) - 6} more)" if len(paths) > 6 else ""
                )
                bits.append(f"{len(paths)} {label}: {shown}")
        bits += self.meta_stale  # each already a finished clause, from the caller that wrote it
        return f"{name}: STALE — " + "; ".join(bits) + ". Run --refresh-all before trusting it."
