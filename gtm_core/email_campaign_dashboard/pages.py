"""What sits beside the rollup, and what each thing is — decided ONCE, for the check and the refresh.

``--check-fresh`` and ``--refresh-all`` both walk the sidecars next to the rollup, and until
2026-10-02 each had its own idea of what they found. The check called a page "retired" and the
refresh re-rendered it anyway and exited 1; the check named a sidecar-less page and the refresh
skipped it and exited 0; a page file deleted by hand read "fresh" and was resurrected. One
classification, read by both, is what keeps "the refresh says it is done" and "the check is green"
the same statement. The single-page check (``--check-fresh --scope X``) asks the same classifier
about one page, so the two forms cannot disagree about a sidecar either.

The kinds, and what each caller does with them:

====================  ==============================================  ==========================
kind                  ``--check-fresh``                                ``--refresh-all``
====================  ==============================================  ==========================
``LIVE``              verified like any page                           re-rendered at its scope
``RETIRED``           listed, not counted                              listed, **not** re-rendered
``GONE``              stale (the page file is missing)                 named; never recreated
``UNSCOPED``          stale (the sidecar cannot be trusted)            named with the command
``CORRUPT``           stale (the sidecar cannot be read)               named with the command
``SIDECARLESS``       stale (nothing records its inputs)               named with the command
``BLOCKED``           stale (a symlink, or an unreadable manifest)     named with its own remedy
``MIXED``             stale (some campaigns gone, some not)            named with the command
====================  ==============================================  ==========================

WHAT DECIDES A PAGE'S IDENTITY IS ITS FILE NAME, NOT ITS SIDECAR (red team C1, 2026-10-02).
``email_campaign_status.html`` is the rollup and can never be retired. ``campaign-open.html`` is the
open scope; ``campaign-<slug>.html`` and ``campaign-campaigns-<a>+<b>.html`` name their campaigns. A
sidecar's ``scope`` and ``slugs`` are untrusted text: they are checked AGAINST the file name — using
the very code that builds the name (``scope.resolve(...).stem``), not a second pattern — and a
sidecar that disagrees is stale and named, never retired and never rendered. Before this, a damaged
rollup sidecar (``scope=campaign, slugs=[ghost]``) read "retired candidate, not counted" and the
check exited 0 with a stale page on disk.

Retirement itself is a fact about the manifests: a page is retired only when EVERY campaign it names
has no manifest — and no manifest is merely unreadable. ``campaigns_dashboard._load_manifests``
skips a file that does not parse, so a typo in a manifest looked exactly like a removed campaign
and retired every page that named it (red team C2). Some campaigns gone and some alive is neither
finished nor re-renderable at its own scope: stale, named.

Everything read from a sidecar is untrusted (§R5). Its ``page`` field is never joined onto a path:
the page is the one the sidecar sits beside, and a field that disagrees is a finding, not a path. A
sidecar or a page that is a symlink is never opened for writing and never read.
"""

from __future__ import annotations

import glob
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..page_inputs_guard import LINK_FIX
from ..page_inputs_io import load_inventory, printable
from ..paths import _safe_segment
from .config import PAGE_NAME
from .scope import MODES, resolve

#: Pages beside the rollup that are NOT pages: the redirect stubs `render_dashboard` writes for
#: the two retired filenames. They carry no numbers and no inventory by design, so convicting
#: them would make every profile permanently red. Skipped by name, not by glob shape — a glob is
#: a guess about a filename, and this set is the fact.
NOT_A_PAGE = frozenset({"campaigns.html", "gtm.html"})

LIVE, RETIRED, GONE, UNSCOPED, CORRUPT, SIDECARLESS, BLOCKED, MIXED = (
    "live",
    "retired",
    "gone",
    "unscoped",
    "corrupt",
    "sidecarless",
    "blocked",
    "mixed",
)

_PREFIX, _SUFFIX = "campaign-", ".html"


@dataclass(frozen=True)
class Found:
    """One page and what it is. ``scope``/``slugs`` are read from the sidecar and meaningful for
    ``LIVE`` only; ``why`` is a finished clause for the kinds that print one."""

    kind: str
    page: str
    why: str = ""
    scope: str = ""
    slugs: tuple[str, ...] = ()

    @property
    def shown(self) -> str:
        """The page name as it is printed — a directory listing is untrusted text."""
        return printable(self.page)


@dataclass(frozen=True)
class Manifests:
    """The campaign manifests, as facts: the ones that load and the ones that do not.

    ``broken`` holds the file stems of every ``*.campaign.toml`` that is unreadable, not TOML, not
    UTF-8, or carries no ``slug`` — the ones ``_load_manifests`` drops without a word.
    """

    loaded: list
    broken: tuple[str, ...] = ()

    @property
    def slugs(self) -> set:
        return {m.get("slug") for m in self.loaded}


def read_manifests(profile: str, content_root: Path | None = None) -> Manifests:
    """Every manifest the page renderer would see, plus the files it silently skips.

    The directory is the one ``_load_manifests`` reads (``_campaigns_dir``), and a manifest counts
    as loaded on the same terms (it parses and has a ``slug``); a test pins the two together. It is
    re-read here, rather than wrapped, because the loader does not say what it dropped.
    """
    from ..campaigns_dashboard import _campaigns_dir

    loaded, broken = [], []
    for path in sorted(glob.glob(str(_campaigns_dir(profile, content_root) / "*.campaign.toml"))):
        stem = os.path.basename(path).removesuffix(".campaign.toml")
        try:
            with open(path, "rb") as fh:
                manifest = tomllib.load(fh)
        except (OSError, ValueError):  # TOMLDecodeError and UnicodeDecodeError are ValueErrors
            broken.append(stem)
            continue
        if manifest.get("slug"):
            loaded.append(manifest)
        else:
            broken.append(stem)
    return Manifests(loaded, tuple(broken))


def manifest_clause(broken: tuple[str, ...] | list[str]) -> str:
    """The one sentence for a page whose campaign manifest does not load — said identically by the
    check and the refresh, because its remedy is the same command."""
    names = ", ".join(printable(s) for s in broken)
    if len(broken) == 1:
        return f"its campaign manifest {names} cannot be read — fix it, then run --refresh-all"
    return f"its campaign manifests {names} cannot be read — fix them, then run --refresh-all"


def safe_stem(stem: str) -> str:
    """``stem`` if it can be one segment of a page's file name, else a named ``SystemExit``.

    The stem is built from manifest slugs and ``--campaign``; a ``/`` or ``..`` in one would write
    a page above the profile folder (red team, pre-existing). Called where the page path is built,
    so the renderer and the check refuse the same names."""
    try:
        return _safe_segment(stem, "campaign slug")
    except ValueError:
        raise SystemExit(
            f"unsafe campaign slug {printable(stem)!r} — a slug becomes part of a file name, so it "
            "must be a plain name with no '/', '\\' or '..'. Nothing was written."
        ) from None


def _bare_html(name) -> bool:
    return (
        isinstance(name, str)
        and name.endswith(".html")
        and name != ".html"
        and name == os.path.basename(name)
        and "\\" not in name
        and "\x00" not in name
    )


def _own_page(inv: Path, rec: dict | None) -> tuple[str, str]:
    """``(the page this sidecar sits beside, why its own ``page`` field is not that)``.

    ``inventory_path`` is ``page.with_suffix('.inputs.json')``, so the sidecar's own name decides
    which page it describes. The ``page`` field it records is only ever compared against that.
    """
    own = inv.name.removesuffix(".inputs.json") + ".html"
    if rec is None:
        return own, ""
    named = rec.get("page")
    if named == own:
        return own, ""
    if not isinstance(named, str) or not named:
        return own, "names no page"
    if not _bare_html(named):
        return own, "names a page outside this folder"
    return own, "names a page other than the one it sits beside"


def _stem_of(mode: str, slugs: tuple[str, ...]) -> str | None:
    """The file stem the renderer would give this scope — from ``scope.resolve``, the code that
    names every page, fed synthetic always-active manifests so only the NAMING is exercised."""
    try:
        return resolve(mode, ",".join(slugs), [{"slug": s, "status": "active"} for s in slugs]).stem
    except SystemExit:
        return None


def _agrees(page: str, mode: str, slugs: tuple[str, ...]) -> bool:
    """Does a sidecar's claim (``mode``, ``slugs``) describe the page it sits beside?

    The rollup is ``all`` with no slugs and nothing else; a campaign page's name must be exactly
    what the renderer names a page with that scope. A page that is neither is not a page this
    package renders, so no scope describes it."""
    if page == PAGE_NAME:
        return mode == "all" and not slugs
    if mode == "all" or not (page.startswith(_PREFIX) and page.endswith(_SUFFIX)):
        return False
    return _stem_of(mode, slugs) == page[len(_PREFIX) : -len(_SUFFIX)]


def scope_of(rec: dict, page: str, mans: Manifests) -> Found:
    """The :class:`Found` for one usable sidecar — the scope is READ BACK from the record and held
    against the page's own file name, never trusted to decide the page's fate on its own."""
    mode, raw = rec.get("scope"), rec.get("slugs", [])
    if not (isinstance(mode, str) and mode in MODES) or not isinstance(raw, list):
        return Found(UNSCOPED, page, "records no usable scope")
    slugs = tuple(s for s in raw if isinstance(s, str) and s)
    if (
        len(slugs) != len(raw)
        or (mode != "all" and not slugs)
        or (mode == "all" and page != PAGE_NAME)
    ):
        return Found(UNSCOPED, page, "records no usable scope")
    if not _agrees(page, mode, slugs):
        return Found(UNSCOPED, page, "records a scope that disagrees with its own file name")
    gone = _gone(mode, slugs, mans)
    if not gone:
        return Found(LIVE, page, scope=mode, slugs=slugs)
    if mans.broken:
        # Not retired: the campaign may be exactly the file that does not load.
        return Found(BLOCKED, page, manifest_clause(mans.broken))
    if len(gone) == len(slugs):
        return Found(RETIRED, page, "its campaign no longer resolves")
    listed = ", ".join(printable(s) for s in gone)
    return Found(
        MIXED,
        page,
        f"it names campaigns that no longer resolve ({listed}) while others still do, so it is "
        "not retired",
    )


def _gone(mode: str, slugs: tuple[str, ...], mans: Manifests) -> list[str]:
    """The campaigns this page names that no manifest owns (all of them, for an open page that
    resolves to nothing). ``scope.resolve`` does not refuse a NAMED slug with no manifest — the
    renderer does, later — so a removed campaign's page would otherwise read stale forever."""
    if mode == "campaign":
        return [s for s in slugs if s not in mans.slugs]
    if mode == "open":
        try:
            resolve("open", None, mans.loaded)
        except SystemExit:
            # `--scope open` against a profile with nothing active: every campaign it showed has
            # completed or been removed. Not a stale page — a finished one.
            return list(slugs)
    return []


def classify_sidecar(base: Path, inv: Path, mans: Manifests) -> Found | None:
    """What the page this sidecar sits beside is, or None for a redirect stub's leftover sidecar."""
    page = inv.name.removesuffix(".inputs.json") + ".html"
    if page in NOT_A_PAGE:
        return None
    if inv.is_symlink():
        return Found(
            BLOCKED,
            page,
            f"its {printable(inv.name)} is a symlink — replace it with a regular file or delete it",
        )
    if (base / page).is_symlink():
        return Found(BLOCKED, page, LINK_FIX)
    rec, why = load_inventory(inv)
    own, bad = _own_page(inv, rec)
    if rec is None:
        return Found(CORRUPT, page, why)
    if bad:
        return Found(UNSCOPED, page, bad)
    if not (base / own).exists():
        return Found(GONE, page, "page file is gone")
    return scope_of(rec, page, mans)


def classify_page(base: Path, page: str, mans: Manifests) -> Found:
    """:func:`classify_sidecar` for ONE named page — the single-page check's entry to the same
    rules. A page with no sidecar at all is ``SIDECARLESS`` (or ``BLOCKED`` if it is a link)."""
    inv = base / (page.removesuffix(".html") + ".inputs.json")
    if os.path.lexists(inv):
        return classify_sidecar(base, inv, mans) or Found(SIDECARLESS, page)
    if (base / page).is_symlink():
        return Found(BLOCKED, page, LINK_FIX)
    return Found(SIDECARLESS, page)


def walk(base: Path, mans: Manifests) -> list[Found]:
    """Every page under ``base``: each ``*.inputs.json`` beside the rollup, then the rollup and any
    ``campaign-*.html`` that has no sidecar. Reads files; writes nothing."""
    found: list[Found] = []
    seen: set[str] = set()
    for inv in sorted(base.glob("*.inputs.json")):
        if (f := classify_sidecar(base, inv, mans)) is not None:
            seen.add(f.page)
            found.append(f)
    # The rollup is asked about unconditionally: "the page an operator opens does not exist yet"
    # is an answer, and skipping it would let an empty profile report zero pages and green.
    for page in sorted({PAGE_NAME, *(p.name for p in base.glob("campaign-*.html"))}):
        if page not in seen and page not in NOT_A_PAGE:
            found.append(
                Found(BLOCKED, page, LINK_FIX)
                if (base / page).is_symlink()
                else Found(SIDECARLESS, page)
            )
    return found


def cannot_refresh(f: Found, profile: str) -> str:
    """Why ``--refresh-all`` leaves this page alone, and the exact command that clears it.

    A page is never rendered under a guessed scope: that would replace one campaign's numbers with
    another's, which is the one thing this package refuses everywhere else."""
    cmd = f"python -m gtm_core.email_campaign_dashboard --profile {printable(profile)}"
    inv = printable(f.page.removesuffix(".html") + ".inputs.json")
    rerender = f"re-render it with its own explicit scope ({cmd} --scope open, or {cmd} --scope campaign --campaign <slug>)"
    if f.kind == BLOCKED:
        return f.why  # a finished sentence: it names its own remedy
    if f.kind == MIXED:
        return f"{f.why} — {rerender}, or delete it"
    if f.kind == GONE:
        return f"the page file is gone — delete {inv}, or {rerender}"
    why = {
        SIDECARLESS: f"no {inv}, so its scope cannot be recovered",
        CORRUPT: f"{inv} could not be read ({f.why})",
    }.get(f.kind, f"{inv} {f.why}")
    return f"{why} — {rerender}, or delete it"
