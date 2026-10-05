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
   advanced but nobody re-ran the snapshot: every byte on disk is identical, so re-digesting says
   fresh while every send number is stale. **Partly closed 2026-09-30 (PRD F4):** a caller may
   record an opaque ``meta`` (the dashboard records the snapshot's own ``fetched``) and judge it
   at CHECK time, so figures past an age limit convict with nothing on disk having moved. Still
   open, and not closable here: whether the provider's numbers moved since that snapshot was
   taken — that needs a live call the page code may not make (§R6).
2. **Stale-but-unchanged content.** A campaign's ``targets`` that should have been re-based
   after its list shrank still hashes the same. Freshness here is byte-identity, never
   truthfulness. (``targets_superseded`` exists in these manifests because that is routine.)
3. **Code changes.** The benchmark table, the view modules, the rate helpers are not inputs,
   and are deliberately NOT recorded. Failing on a code revision would mark every page stale
   after any refactor, and a check that cries wolf on every commit is one people learn to
   skip — so a page can be "fresh" here and rendered by different code than it says. The
   dashboard records a fingerprint of its own code in ``meta`` and REPORTS a difference beside
   the verdict (``email_campaign_dashboard/fingerprint.py``); it never counts toward ``ok``.
4. **Time-derived content.** *Closed for the campaign status page 2026-09-30 (PRD F7):* its
   forecast and "last run" dates read the model's ``generated_at``, and no view there may call
   ``datetime.now`` (AST-asserted in tests/contracts/test_dashboard_page_is_dated_once.py). Still
   the warning for any OTHER artifact inventoried here: a page dated from *today* is wrong the
   next morning with zero input drift. Input freshness is not page correctness.
"""

from __future__ import annotations

import glob as _glob
import hashlib
import json
import os
import sys
from pathlib import Path, PurePosixPath

from . import page_input_names
from .page_inputs_guard import RefusedWrite, refuse_links, write_text
from .page_inputs_io import inventory_path
from .page_inputs_io import load_inventory as _load_inventory
from .page_inputs_io import recorded as _recorded
from .page_inputs_report import Report
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


def _cached_digest(path: Path, cache: dict[Path, str] | None) -> str:
    """:func:`digest`, memoised by path for the life of ONE check.

    The all-pages check (``freshness.check_all_pages``) verifies every page under a profile
    against the same tree: on the live tenant that is 1,583 inputs re-hashed once per page. The
    cache is caller-owned and per-call deliberately: a module-level one would answer a later check with an earlier run's digests, the one thing a check may not do.
    """
    if cache is None:
        return digest(path)
    key = Path(path)
    if key not in cache:
        cache[key] = digest(key)
    return cache[key]


def _resolve(root: Path, globs: list[str]) -> list[Path]:
    seen: set[Path] = set()
    for g in globs:
        # The ROOT is a path, never a pattern: a `[` or `*` in it would match nothing.
        for hit in _glob.glob(_glob.escape(str(root)) + os.sep + g, recursive=True):
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


def _profile_rel(name) -> str | None:
    """A ``profile_files`` entry as a root-relative path, or **None** when it cannot safely be
    one. The ONE guard, shared by :func:`write_inventory` and :func:`_verify_profile_inputs`, so
    the two can never disagree about what is dereferenceable.

    ``PROFILE_FILES`` held one bare filename (``PROFILE.md``) until 2026-09-30, so both halves
    guarded it with a lone ``_safe_segment`` — which refuses a ``/``. The first sub-path entry
    (``knowledge/BRAND.toml``) would have RAISED out of the verifier, taking the whole check with
    it. A sub-path is a path with MORE segments, never a weaker guard: every segment passes
    ``_safe_segment`` (no slash, backslash, ``..`` or NUL) AND the join passes :func:`_confine`.
    Both, because either alone has a hole — ``knowledge/../../etc`` normalises inside root only if
    you never inspect the segments.

    Returns None rather than raising: an entry this cannot confine makes its page STALE and NAMED
    (``Report.refused``), the fail-closed answer. Raising would let one malformed row veto the
    answer for every other page in the run (§R5).
    """
    if not isinstance(name, str) or not _confine(name):
        return None
    try:
        parts = [_safe_segment(s, "profile file") for s in PurePosixPath(name).parts]
    except ValueError:
        return None
    return str(PurePosixPath(*parts)) if parts else None


def build_record(
    page: Path,
    spec: tuple[Path, list[str]],
    *,
    scope: str,
    slugs: tuple[str, ...] = (),
    profile: str | None = None,
    profile_files: tuple[str, ...] = (),
    meta: dict | None = None,
    name_globs: tuple[str, ...] | None = None,
    page_bytes: bytes | None = None,
) -> dict:
    """What ``page`` was built from, as the dict :func:`write_inventory` writes — and nothing else.

    It touches no output file, so a caller can build the record FIRST and write the page and its
    sidecar only once every digest has succeeded (an unreadable input then leaves the previous pair
    untouched). ``page_bytes`` is the page about to be written: its digest is taken from the bytes
    rather than from a file that is not on disk yet.

    ``spec`` is ``(root, globs)``, globs relative
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
    if page_bytes is not None:
        page_sha = hashlib.sha256(page_bytes).hexdigest()
    else:
        page_sha = digest(page) if page.exists() else ""
    record = {
        "page": page.name,
        "page_sha256": page_sha,
        "scope": scope,
        "slugs": list(slugs),
        "globs": sorted(globs),
        "inputs": inputs,
    }
    if profile is not None:
        base = _profile_root(profile)
        profile_inputs = []
        for name in profile_files:
            safe = _profile_rel(name)
            if safe is None:
                # Recorded, NOT dereferenced and NOT dropped: the row is what makes the page
                # stale and named at verify. Dropping it would leave the page claiming to track
                # a file nobody ever checks, which is the "cannot show current" failure read as
                # "fine". The profile NAME still raises (above) — that is the tenant key, and a
                # page written under the wrong tenant is not a thing to report later.
                profile_inputs.append({"path": name, "sha256": None, "bytes": None})
                continue
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
    if name_globs is not None:
        record.update(page_input_names.record(root, name_globs))  # see that module
    if meta is not None:
        # OPAQUE to this module: a fact about the render that no amount of hashing could
        # recover afterwards (the dashboard records the sending figures' own date, which lives
        # in a third-party system and so ages with nothing on disk changing). `verify_inventory`
        # hands it back untouched; only the caller that wrote it can judge it. Its ABSENCE is
        # meaningful too — an inventory written before a caller started recording `meta` cannot
        # be shown current on whatever the `meta` was about.
        record["meta"] = dict(meta)
    return record


def write_inventory(page: Path, spec: tuple[Path, list[str]], **kw) -> Path:
    """Write ``<page>.inputs.json`` from :func:`build_record` (same keywords), atomically.

    Refuses, naming it and writing nothing, when the page or the sidecar is a symlink: a write
    through a link lands in whatever the link points at.
    """
    out = inventory_path(page)
    refuse_links(page, out)
    write_text(out, json.dumps(build_record(page, spec, **kw), indent=2) + "\n")
    return out


def write_inventory_or_warn(page: Path, spec: tuple[Path, list[str]], **kw) -> Path | None:
    """:func:`write_inventory` for a caller whose own data is already on disk and must not be
    undone by a refused sidecar (a symlink): prints the refusal and returns ``None``."""
    try:
        return write_inventory(page, spec, **kw)
    except RefusedWrite as exc:
        print(f"inventory not written: {exc}", file=sys.stderr)
        return None


def verify_inventory(
    page: Path,
    root: Path | None = None,
    *,
    profile: str | None = None,
    cache: dict[Path, str] | None = None,
    expect_names: bool = False,
) -> Report:
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
    if not os.path.lexists(inv_path):
        return Report(page, no_inventory=True)
    inv, why = _load_inventory(inv_path)
    if inv is None:
        return Report(page, unreadable=why)
    root = root or _root_for(page, inv)
    rep = Report(page)
    meta = inv.get("meta")
    if isinstance(meta, dict):
        rep.meta, rep.meta_present = dict(meta), True
    rep.incomplete = _missing_keys(inv, page)
    sha = inv.get("page_sha256")
    if isinstance(sha, str) and sha and _readable(rep, page.name, page.exists):
        rep.page_edited = bool(_digest_or_none(rep, page.name, page, cache) not in (None, sha))
    recorded, bad_rows = _recorded(inv)
    rep.refused.extend(bad_rows)
    _verify_content_inputs(rep, root, recorded, cache)
    globs = inv.get("globs", [])
    try:
        _verify_globs(rep, root, [g for g in globs if isinstance(g, str)], recorded)
        page_input_names.verify(rep, root, inv, expect_names, _confine)
    except OSError as exc:
        rep.unverified.append(f"names or globs ({type(exc).__name__})")
    rep.refused.extend(f"glob:{g!r}"[:120] for g in globs if not isinstance(g, str))
    if profile is not None:
        _verify_profile_inputs(rep, profile, inv, cache)
    for found in (rep.changed, rep.missing, rep.new, rep.refused, rep.unverified):
        found.sort()
    return rep


def _missing_keys(inv: dict, page: Path) -> list[str]:
    """The tracking keys an inventory must carry for its page to be shown unchanged. ``""`` is a
    legitimate ``page_sha256`` only for a page that did not exist when the inventory was written."""
    out = [k for k in ("inputs", "globs") if k not in inv]
    sha = inv.get("page_sha256")
    if not isinstance(sha, str) or (not sha and page.exists()):
        out.append("page_sha256")
    return out


def _readable(rep: Report, what: str, probe) -> bool:
    """``probe()`` as a truth value, with an ``OSError`` (a name over 255 bytes) recorded on ``rep``
    instead of raised."""
    try:
        return bool(probe())
    except OSError as exc:
        rep.unverified.append(f"{what} ({type(exc).__name__})")
        return False


def _digest_or_none(rep: Report, what: str, path: Path, cache) -> str | None:
    """The cached digest of ``path``, or None after recording why it could not be read."""
    try:
        return _cached_digest(path, cache)
    except OSError as exc:
        rep.unverified.append(f"{what} ({type(exc).__name__})")
        return None


def _verify_content_inputs(
    rep: Report, root: Path, recorded: dict[str, str], cache: dict[Path, str] | None = None
) -> None:
    """The ``inputs[].path`` half of :func:`verify_inventory` — split out to keep that
    function's branching under the §R10 complexity ceiling."""
    for rel, sha in recorded.items():
        if not _confine(rel):
            rep.refused.append(rel)  # untrusted row, escapes root by inspection alone
            continue
        p = root / rel  # lexical join only — never resolved, so an in-root symlink to
        # somewhere outside root is still followed (not refused) when hashed below.
        try:
            there = p.exists()
        except OSError as exc:  # a name over 255 bytes: this page's finding, not the run's
            rep.unverified.append(f"{rel} ({type(exc).__name__})")
            continue
        if not there:
            rep.missing.append(rel)
        elif (got := _digest_or_none(rep, rel, p, cache)) is not None and got != sha:
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


def _verify_profile_inputs(
    rep: Report, profile: str, inv: dict, cache: dict[Path, str] | None = None
) -> None:
    """The profile-rooted half — see :func:`verify_inventory`'s docstring."""
    if "profile_inputs" not in inv:
        rep.profile_untracked = True
        return
    base = _profile_root(profile)
    for row in inv["profile_inputs"]:
        raw = row.get("path") if isinstance(row, dict) else row
        name = _profile_rel(raw) if isinstance(row, dict) else None
        if name is None:
            # Unsafe, wrong-typed, or not a dict at all (a bare-string row used to pass the name
            # check and then raise on `row.get`). Named and stale — never raised, so one drifted
            # row cannot abort the check for the rest of the page or the run.
            rep.refused.append(
                f"profile:{raw!r}"[:120] if not isinstance(raw, str) else f"profile:{raw}"
            )
            continue
        p = base / name
        label = f"profile:{name}"
        recorded_sha = row.get("sha256")
        if p.is_file():
            if recorded_sha is None:
                rep.new.append(label)  # recorded absent, now appeared
            elif (got := _digest_or_none(rep, label, p, cache)) is not None and got != recorded_sha:
                rep.changed.append(label)
        elif recorded_sha is not None:
            rep.missing.append(label)


def _root_for(page: Path, inv: dict) -> Path:
    """The tree the recorded relative paths hang off.

    Recovered by walking up from the page until a recorded input resolves, so an inventory
    stays valid when its whole tree is moved (a clone, a backup restore) — the case that
    breaks an mtime check outright.
    """
    sample = next(
        (
            row["path"]
            for row in inv.get("inputs", [])
            if isinstance(row, dict) and isinstance(row.get("path"), str) and row["path"]
        ),
        None,
    )
    here = page.parent
    for candidate in (here, *here.parents):
        if sample is None or (candidate / sample).exists():
            return candidate
    return here
