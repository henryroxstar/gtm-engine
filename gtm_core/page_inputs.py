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
import os
from dataclasses import dataclass, field
from pathlib import Path

from .paths import _safe_segment, resolve_profiles_root

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


def _profile_root(profile: str) -> Path:
    """``resolve_profiles_root()/<profile>``, guarded like every other profile-scoped path
    (CLAUDE.md tenant boundary) — ``profile`` reaches here straight from a caller's
    ``--profile``, never from the inventory JSON."""
    return resolve_profiles_root() / _safe_segment(profile, "profile")


def _confine(rel: str) -> bool:
    """True if ``rel`` — an untrusted, JSON-sourced relative path OR glob pattern — cannot
    escape its root by inspection of the STRING alone. Refuses an absolute path and
    anything whose normalized form still climbs above root with a leading ``..``.

    Deliberately LEXICAL, never ``Path.resolve()``: resolving follows symlinks, and a real
    in-root symlink pointing outside root is a legitimate tenant layout (CLAUDE.md: a
    ``content/<tenant>`` tree backed by external storage is exactly this shape) — it must
    still be verified by FOLLOWING it when hashing/globbing, not refused for merely
    existing. Untrusted input is judged by what it SAYS (§R5), not by what the filesystem
    currently does with it.
    """
    if not rel or os.path.isabs(rel):
        return False
    normalized = os.path.normpath(rel)
    return normalized != ".." and not normalized.startswith(".." + os.sep)


def write_inventory(
    page: Path,
    spec: tuple[Path, list[str]],
    *,
    scope: str,
    slugs: tuple[str, ...] = (),
    profile: str | None = None,
    profile_files: tuple[str, ...] = (),
) -> Path:
    """Record what ``page`` was built from. ``spec`` is ``(root, globs)``, globs relative
    to root so the inventory survives being moved with its tree.

    ``profile_files`` are named (not globbed) files under a DIFFERENT root entirely —
    ``resolve_profiles_root()/<profile>`` — e.g. ``PROFILE.md``. Recorded only when
    ``profile`` is given; each name is guarded like any other profile-scoped segment
    (CLAUDE.md tenant boundary). The profile itself is never written into the inventory:
    :func:`verify_inventory` always takes it fresh from ITS OWN caller, never from this JSON.

    A ``profile_files`` name that does not exist YET is still recorded, with a null digest —
    the profile-rooted analogue of recording a glob even when nothing matches it: a file
    that later APPEARS is then reported ``new`` by :func:`verify_inventory`, rather than
    silently never having been tracked at all.
    """
    root, globs = spec
    inputs = [
        {"path": str(p.relative_to(root)), "sha256": digest(p), "bytes": p.stat().st_size}
        for p in _resolve(root, globs)
    ]
    record = {
        "page": page.name,
        "page_sha256": digest(page) if page.exists() else "",
        "scope": scope,
        "slugs": list(slugs),
        "globs": sorted(globs),
        "inputs": inputs,
    }
    if profile is not None:
        base = _profile_root(profile)
        profile_inputs = []
        for name in profile_files:
            safe = _safe_segment(name, "profile file")
            p = base / safe
            if p.is_file():
                profile_inputs.append(
                    {"path": safe, "sha256": digest(p), "bytes": p.stat().st_size}
                )
            else:
                profile_inputs.append({"path": safe, "sha256": None, "bytes": None})
        # Present (even as `[]` for a page that declares no profile files) only when a
        # caller actually asked about a profile — its ABSENCE, not an empty list, is what
        # :func:`verify_inventory` reads as "predates profile tracking".
        record["profile_inputs"] = profile_inputs
    out = inventory_path(page)
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
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

    @property
    def ok(self) -> bool:
        return not (
            self.changed
            or self.missing
            or self.new
            or self.page_edited
            or self.no_inventory
            or self.profile_untracked
            or self.refused
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
        if self.profile_untracked:
            bits.append("inventory predates profile tracking — re-render")
        for label, paths in (
            ("changed since render", self.changed),
            ("read at render, now missing", self.missing),
            ("appeared since render, never read", self.new),
            ("recorded but refused as unsafe (escapes root)", self.refused),
        ):
            if paths:
                shown = ", ".join(paths[:6]) + (
                    f" (+{len(paths) - 6} more)" if len(paths) > 6 else ""
                )
                bits.append(f"{len(paths)} {label}: {shown}")
        return f"{self.page.name}: STALE — " + "; ".join(bits) + ". Re-render before trusting it."


def verify_inventory(page: Path, root: Path | None = None, *, profile: str | None = None) -> Report:
    """Re-glob and re-digest ``page``'s recorded inputs.

    ``profile``, when given, also re-checks the profile-rooted files :func:`write_inventory`
    recorded (e.g. ``PROFILE.md``) — resolved fresh under ``resolve_profiles_root()`` from
    THIS argument, never from the inventory JSON (CLAUDE.md tenant boundary: a stored profile
    name could otherwise redirect a later check at a different tenant's files — the JSON is
    never even asked for one). An inventory written before this parameter existed carries no
    ``"profile_inputs"`` key at all; passing ``profile`` against one reports
    ``profile_untracked`` (stale) rather than silently treating "never recorded" as "fine".
    Omitting ``profile`` verifies the content-root inputs only — the call shape from before
    this parameter existed, unchanged.

    A recorded ``inputs[].path`` or a ``globs`` entry that :func:`_confine` refuses is
    reported via ``refused`` (stale), never dereferenced and never silently dropped.
    """
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
    _verify_content_inputs(rep, root, recorded)
    _verify_globs(rep, root, list(inv.get("globs", [])), recorded)
    if profile is not None:
        _verify_profile_inputs(rep, profile, inv)
    rep.changed.sort()
    rep.missing.sort()
    rep.new.sort()
    rep.refused.sort()
    return rep


def _verify_content_inputs(rep: Report, root: Path, recorded: dict[str, str]) -> None:
    """The ``inputs[].path`` half of :func:`verify_inventory` — split out to keep that
    function's branching under the §R10 complexity ceiling."""
    for rel, sha in recorded.items():
        if not _confine(rel):
            rep.refused.append(rel)  # untrusted row, escapes root by inspection alone
            continue
        p = root / rel  # lexical join only — never resolved, so an in-root symlink to
        # somewhere outside root is still followed (not refused) when hashed below.
        if not p.exists():
            rep.missing.append(rel)
        elif digest(p) != sha:
            rep.changed.append(rel)


def _verify_globs(rep: Report, root: Path, globs: list[str], recorded: dict[str, str]) -> None:
    """The ``globs`` half — confines each pattern the same lexical way before it ever
    reaches `_resolve`/`glob.glob`, so a crafted ``../other/*`` cannot enumerate a sibling
    tenant's filenames into ``new`` and an absolute glob cannot crash `relative_to` below."""
    safe_globs = [g for g in globs if _confine(g)]
    rep.refused.extend(f"glob:{g}" for g in globs if not _confine(g))
    for p in _resolve(root, safe_globs):
        rel = str(p.relative_to(root))
        if rel not in recorded:
            rep.new.append(rel)


def _verify_profile_inputs(rep: Report, profile: str, inv: dict) -> None:
    """The profile-rooted half — see :func:`verify_inventory`'s docstring."""
    if "profile_inputs" not in inv:
        rep.profile_untracked = True
        return
    base = _profile_root(profile)
    for row in inv["profile_inputs"]:
        name = _safe_segment(row["path"], "profile file")
        p = base / name
        label = f"profile:{name}"
        recorded_sha = row.get("sha256")
        if p.is_file():
            if recorded_sha is None:
                rep.new.append(label)  # recorded absent, now appeared
            elif digest(p) != recorded_sha:
                rep.changed.append(label)
        elif recorded_sha is not None:
            rep.missing.append(label)


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
