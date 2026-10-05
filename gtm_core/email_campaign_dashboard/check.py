"""The CHECK: is every page current, and what turns it green? Judged at check time, from files.

Split from :mod:`freshness` because the render path may not read the clock
(tests/contracts/test_dashboard_page_is_dated_once.py asserts it by AST over ``render.py`` and
``freshness.py``) and this module's whole job is to read it: the sending figures age with no byte
on disk changing, so only a check made LATER can see it. ``now`` is an argument everywhere so a
test can ask as of another day; the clock is only its default. Nothing here reaches a rendered page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..page_inputs import Report, inventory_path, verify_inventory
from ..page_inputs_io import printable
from ..prospects_consolidate import _prospects_dir
from . import health, pages
from .config import FIGURES_MAX_AGE_DAYS, PAGE_NAME, input_globs
from .fingerprint import code_fingerprint, code_note

# --- the check: is EVERY page current, and what turns it green? -------------------------------
#
# F4 (2026-09-30). `--check-fresh` checked the one page its own `--scope` named. The rollup is
# the only page anything re-renders on its own (`consolidate`'s tail), so a check with no scope
# asked about the one page that had just been rewritten and reported the profile current while
# three scoped pages were ten outreach packs behind. Measured on a live tenant 2026-09-21 and
# again 2026-09-30.
#
# Two properties are load-bearing, and both are about the check being USABLE rather than strict:
# it must be able to reach green by one documented command, and one bad page must never decide
# the answer for the others.


#: The tail both figures-age clauses end on. A clause that ends here cannot be cleared by a
#: re-render — only a refresh of the figures themselves — so the refresh's verdict keys on it.
REFRESH_FIRST = "refresh them first"


def figures_stale_clause(rep, now: datetime) -> str | None:
    """The clause that convicts a page for its FIGURES' age, judged at CHECK time — or None.

    This is the half of staleness a content digest structurally cannot see. The sending figures
    describe a third-party system; they age with no byte on disk changing, so a page rendered on
    day 0 re-digested clean on day 20 and the check said fresh. §R14: the limit and the wording
    come from the constant, never typed.
    """
    if not rep.meta_present:
        # An inventory from before this was recorded. "We cannot tell how old the figures were"
        # must not read as "they were fine" — and one `--refresh-all` clears every one of them.
        return "the inventory predates figure tracking — re-render"
    present = rep.meta.get("figures_present")
    if not isinstance(present, bool):
        # Absent or mistyped: a truncated or hand-built `meta` is not "no figures to judge" —
        # only a recorded False is. Reading it as nothing-to-judge made it fresh at any age.
        return "the inventory predates figure tracking — re-render"
    if not present:
        return None
    fetched = rep.meta.get("figures_fetched")
    exact = health._figures_age_exact_days(fetched, now)
    if exact is None:
        # Unparseable, or dated past the future tolerance. The raw value is untrusted text and is
        # never echoed back — it would print as if it were a date.
        return (
            f"the sending figures carry no usable date, so their age is unknown — {REFRESH_FIRST}"
        )
    if exact > FIGURES_MAX_AGE_DAYS:
        day = health.figures_date(fetched)
        return f"sending figures are from {day}, over {FIGURES_MAX_AGE_DAYS} days old — {REFRESH_FIRST}"
    return None


def page_gone_clause(rep) -> str | None:
    """The clause for a page whose FILE is missing while its sidecar survives.

    ``verify_inventory`` judges inputs and says nothing about the page itself, so a page deleted
    by hand read "fresh" — and `--refresh-all` then resurrected it, so "or delete it" never stuck.
    """
    if rep.page.exists():
        return None
    inv = printable(inventory_path(rep.page).name)
    if rep.page.name == PAGE_NAME:
        return "page file is gone — run --refresh-all to re-render it"
    return f"page file is gone — delete {inv}, or re-render it with its explicit --scope"


def check_page(
    page: Path,
    root: Path,
    profile: str,
    *,
    cache: dict | None = None,
    now: datetime | None = None,
) -> Report:
    """One page, judged completely: its inputs, its file, and its figures' age at CHECK time.

    The ONE place these clauses are applied, so ``--check-fresh`` agrees with itself whether it
    asks about every page or only one. Before 2026-10-02 only the all-pages form added the
    figures clause, and ``--scope open`` said "fresh" on figures twenty days old. Each page is
    judged by its OWN recorded ``meta`` — a scoped page records the age of the sequences IT shows
    — so a scoped check never convicts a page for figures it does not carry.
    """
    rep = verify_inventory(page, root, profile=profile, cache=cache, expect_names=True)
    if rep.no_inventory or rep.unreadable:
        return rep
    when = now or datetime.now(UTC)
    figures = figures_stale_clause(rep, when)
    rep.figures_old = bool(figures and figures.endswith(REFRESH_FIRST))
    rep.meta_stale.extend(c for c in (page_gone_clause(rep), figures) if c)
    return rep


def judge(f: pages.Found, base: Path, root: Path, profile: str, **kw) -> Report | None:
    """The :class:`Report` for one classified page — or None for a retired one, which is listed and
    not counted. The ONE place a :class:`pages.Found` becomes a verdict, so the all-pages walk and
    the single-page check cannot decide differently about the same sidecar."""
    page = base / f.page
    if f.kind == pages.RETIRED:
        return None
    if f.kind == pages.UNSCOPED:
        return Report(page, unreadable=f.why)
    if f.kind in (pages.BLOCKED, pages.MIXED):
        # Nothing is opened: the page may be a link, or a page whose manifest does not load.
        more = " — delete it, or re-render it with its own explicit --scope" * (
            f.kind == pages.MIXED
        )
        return Report(page, blocked=f.why + more)
    return check_page(page, root, profile, **kw)


@dataclass
class PagesReport:
    """Every page under one profile, judged in one pass.

    ``retired`` is deliberately NOT a conviction and not counted: a page whose campaign no longer
    resolves cannot be re-rendered (there is no scope to render it AT), so counting it stale would
    leave the check permanently red with no command that clears it — and nothing here deletes a
    page, which is the operator's call alone (PRD D8).
    """

    reports: list = field(default_factory=list)
    retired: list = field(default_factory=list)
    #: Page name -> a finding that is REPORTED and never counted: it is not part of ``ok``.
    notes: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.reports)

    @property
    def summary(self) -> str:
        fresh = sum(1 for r in self.reports if r.ok)
        return (
            f"{len(self.reports)} pages checked: {fresh} fresh, "
            f"{len(self.reports) - fresh} stale, {len(self.retired)} retired candidate(s)."
        )

    def explain(self) -> str:
        lines = []
        for r in self.reports:
            lines.append(r.explain())
            if r.page.name in self.notes:
                lines.append(f"  note: {self.notes[r.page.name]}")
        lines += [f"{page}: retired candidate — {why}; not counted" for page, why in self.retired]
        return "\n".join([*lines, self.summary])


def check_all_pages(
    profile: str,
    content_root: Path | None = None,
    *,
    profiles_root: Path | None = None,
    now: datetime | None = None,
    package_dir: Path | None = None,
) -> PagesReport:
    """Every page under ``profile``: each ``*.inputs.json`` beside the rollup, plus any
    ``campaign-*.html`` that has none (stale, and named). What each thing IS comes from
    :func:`pages.walk`, the same walk ``--refresh-all`` makes.

    Read-only by construction — it opens files for reading and writes nothing, so it can be run
    on a live profile between renders. ``now`` is an argument rather than a clock read so a test
    can ask the question as of a later day against real files on disk.

    Every input is hashed ONCE for the whole call (``cache``): on the live tenant this is 1,583
    inputs against five pages, which is the difference between a check the operator runs and one
    they skip.
    """
    from .render import _validate_profile  # deferred: render imports this module's header line

    _validate_profile(profile, profiles_root=profiles_root, content_root=content_root)
    root, _ = input_globs(profile, content_root)
    base = _prospects_dir(profile, content_root).parent
    when = now or datetime.now(UTC)
    cache: dict[Path, str] = {}
    out = PagesReport()
    code_now = code_fingerprint(package_dir)
    for f in pages.walk(base, pages.read_manifests(profile, content_root)):
        rep = judge(f, base, root, profile, cache=cache, now=when)
        if rep is None:
            out.retired.append((f.shown, f.why))
            continue
        if note := code_note(rep, code_now):
            out.notes[rep.page.name] = note
        out.reports.append(rep)
    return out


def check_one(
    profile: str,
    content_root: Path | None = None,
    *,
    campaign: str | None = None,
    scope: str | None = None,
    profiles_root: Path | None = None,
) -> Report:
    """Is the ONE page this scope names still current? The single-page ``--check-fresh``.

    It asks :func:`pages.classify_page` and :func:`judge` — the all-pages walk's own classifier —
    so a damaged sidecar, a disagreeing scope, a missing key or a symlink convicts here exactly as
    it does there. Before 2026-10-02 this went straight to the digests, and ``--scope open`` said
    "fresh" on a sidecar the walk convicts.

    It refuses loudly rather than ignore what it was asked: a ``--campaign`` that goes with no
    ``--scope campaign``, and a slug no manifest owns (a page left behind by a removed campaign is
    listed by the all-pages check as a retired candidate). A slug whose manifest is merely
    unreadable is a STALE page, named, not an unknown campaign.
    """
    from .render import _mode, _validate_profile, page_path
    from .scope import resolve

    _validate_profile(profile, profiles_root=profiles_root, content_root=content_root)
    mans = pages.read_manifests(profile, content_root)
    mode = scope or ("campaign" if campaign else "all")
    if campaign and mode != "campaign":
        raise SystemExit(
            f"--campaign names the campaigns for --scope campaign; with --scope {mode} it would be "
            "ignored, so it is refused. Drop --campaign, or use --scope campaign --campaign <slug>."
        )
    base = _prospects_dir(profile, content_root).parent
    root, _ = input_globs(profile, content_root)
    if mode == "open" and not mans.loaded and mans.broken:
        # `_mode` would call this "no campaign manifest yet" and check the rollup instead: it is a
        # manifest that does not load.
        return Report(base / "campaign-open.html", blocked=pages.manifest_clause(mans.broken))
    sc = resolve(_mode(mode, campaign, mans.loaded), campaign, mans.loaded)
    unknown = [s for s in sc.slugs if mode == "campaign" and s not in mans.slugs]
    if unknown and not mans.broken:
        raise SystemExit(
            f"unknown campaign {', '.join(printable(s) for s in unknown)} — no campaign manifest for it. Add "
            "content/<profile>/plans/campaigns/<slug>.campaign.toml, or run --check-fresh with "
            "no --scope to list a page left behind by a removed campaign."
        )
    page = page_path(profile, content_root, sc)
    if unknown:
        return Report(page, blocked=pages.manifest_clause(mans.broken))
    found = pages.classify_page(base, page.name, mans)
    return judge(found, base, root, profile) or Report(
        page, blocked="its campaign no longer resolves, so it is a retired candidate"
    )
