"""Is this generated page still true? — a content-digest inventory of what it read.

A stale page renders **identically** to a current one. That is the whole problem: there is
no visual difference between a dashboard built five minutes ago and one built before the
list changed, so staleness is caught only when a human happens to notice a number they
remember differently. On 2026-09-05 a live two-campaign page was seventeen hours behind
the ``cells.toml`` it was built from, and looked perfectly current.

So the renderer records what it read, and a later check re-derives it:

* :func:`write_inventory` writes ``<page>.inputs.json`` — the page's own digest, the scope,
  **the globs it resolved**, and a sha256 per resolved input.
* :func:`verify_inventory` re-globs and re-digests, classifying every path as
  ``unchanged`` / ``changed`` / ``missing`` / ``new``.

**Digests, not mtimes.** This repo has been bitten twice by treating mtime as truth
(:mod:`gtm_core.knowledge_meta` — "a reformat bumps mtime but changes no fact";
:mod:`gtm_core.eval_calibration` — "a fresh clone, a ``cp -r``, or a restore rewrites every
mtime at once"). Either of those would make an mtime check pass on everything at once,
which is the worst possible failure for a check whose job is to convict. Digests survive
both, and they extend a precedent already in this package: a lint record carries
``spec_sha256``/``csv_sha256`` and reports drift the same way.

**Recording the globs is load-bearing, not bookkeeping.** A digest set alone can only
answer "did what I read change?" The globs also answer "did something appear that I should
have read?" — a campaign manifest added, a roster export dropped in — which is how a page
goes quietly out of date without a single recorded input changing.

WHAT THIS CANNOT CATCH — none of these are theoretical, and none are fixed by more hashing:

1. **Upstream truth moving with no local file changing.** The sequencer's live figures
   advanced but nobody re-ran the snapshot: every byte on disk is identical, this check
   says fresh, and every send number on the page is stale. This is the largest gap.
   ``email_campaign_dashboard.reconcile_snapshot`` half-covers it, and only for *which*
   sequences exist — never for their numbers.
2. **Stale-but-unchanged content.** A campaign's ``targets`` that should have been re-based
   after its list shrank still hashes the same. Freshness here is byte-identity, never
   truthfulness. (``targets_superseded`` exists in these manifests because that is routine.)
3. **Code changes.** The benchmark table, the view modules, the rate helpers are not inputs,
   and are deliberately NOT recorded. Failing on a code revision would mark every page stale
   after any refactor, and a check that cries wolf on every commit is one people learn to
   skip — so a page can be "fresh" here and rendered by different code than it says.
4. **Time-derived content.** The forecast's "done by" date and the "last run N days ago"
   pill are computed from *today*, so they are wrong the next calendar day with zero input
   drift. Input freshness is not page correctness.
"""

from __future__ import annotations

import glob as _glob
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

#: Read in 1 MiB blocks — `latest.json` runs to megabytes and this may hash a few hundred
#: files, so the whole-file read that would be simpler is not free.
_CHUNK = 1 << 20


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(_CHUNK):
            h.update(block)
    return h.hexdigest()


def inventory_path(page: Path) -> Path:
    return page.with_suffix(".inputs.json")


def _resolve(root: Path, globs: list[str]) -> list[Path]:
    seen: set[Path] = set()
    for g in globs:
        for hit in _glob.glob(str(root / g), recursive=True):
            p = Path(hit)
            if p.is_file():
                seen.add(p)
    return sorted(seen)


def write_inventory(
    page: Path, spec: tuple[Path, list[str]], *, scope: str, slugs: tuple[str, ...] = ()
) -> Path:
    """Record what ``page`` was built from. ``spec`` is ``(root, globs)``, globs relative
    to root so the inventory survives being moved with its tree."""
    root, globs = spec
    inputs = [
        {"path": str(p.relative_to(root)), "sha256": digest(p), "bytes": p.stat().st_size}
        for p in _resolve(root, globs)
    ]
    out = inventory_path(page)
    out.write_text(
        json.dumps(
            {
                "page": page.name,
                "page_sha256": digest(page) if page.exists() else "",
                "scope": scope,
                "slugs": list(slugs),
                "globs": sorted(globs),
                "inputs": inputs,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return out


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

    @property
    def ok(self) -> bool:
        return not (
            self.changed or self.missing or self.new or self.page_edited or self.no_inventory
        )

    def explain(self) -> str:
        if self.no_inventory:
            return (
                f"{self.page.name}: no {inventory_path(self.page).name} — this page was "
                "written by something that does not record its inputs, so it cannot be "
                "shown to be current. Re-render it."
            )
        if self.ok:
            return f"{self.page.name}: fresh — every recorded input is unchanged."
        bits = []
        if self.page_edited:
            bits.append("the page itself was edited after it was rendered")
        for label, paths in (
            ("changed since render", self.changed),
            ("read at render, now missing", self.missing),
            ("appeared since render, never read", self.new),
        ):
            if paths:
                shown = ", ".join(paths[:6]) + (
                    f" (+{len(paths) - 6} more)" if len(paths) > 6 else ""
                )
                bits.append(f"{len(paths)} {label}: {shown}")
        return f"{self.page.name}: STALE — " + "; ".join(bits) + ". Re-render before trusting it."


def verify_inventory(page: Path, root: Path | None = None) -> Report:
    """Re-glob and re-digest ``page``'s recorded inputs."""
    inv_path = inventory_path(page)
    if not inv_path.exists():
        return Report(page, no_inventory=True)
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    root = root or _root_for(page, inv)
    rep = Report(page)
    rep.page_edited = bool(
        inv.get("page_sha256") and page.exists() and digest(page) != inv["page_sha256"]
    )
    recorded = {row["path"]: row["sha256"] for row in inv.get("inputs", [])}
    for rel, sha in recorded.items():
        p = root / rel
        if not p.exists():
            rep.missing.append(rel)
        elif digest(p) != sha:
            rep.changed.append(rel)
    for p in _resolve(root, list(inv.get("globs", []))):
        rel = str(p.relative_to(root))
        if rel not in recorded:
            rep.new.append(rel)
    rep.changed.sort()
    rep.missing.sort()
    rep.new.sort()
    return rep


def _root_for(page: Path, inv: dict) -> Path:
    """The tree the recorded relative paths hang off.

    Recovered by walking up from the page until a recorded input resolves, so an inventory
    stays valid when its whole tree is moved (a clone, a backup restore) — the case that
    breaks an mtime check outright.
    """
    sample = next((row["path"] for row in inv.get("inputs", [])), None)
    here = page.parent
    for candidate in (here, *here.parents):
        if sample is None or (candidate / sample).exists():
            return candidate
    return here
