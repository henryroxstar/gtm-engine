"""`due`: which sources are stale, and the manifest that lets a capture run fetch only those.

A source is due when its newest capture is older than ``cadence_days``, or it has none. An inert
source (below the precision bar, unsampled, expired) is never due. The manifest is the allow-list
the capture gate enforces (R0.3); with nothing due, no manifest is written, because a manifest that
allows nothing is refused by the writer.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import capture_manifest
from ..paths import resolve_content_root
from ..signal_sources import sources_dir_for
from . import captures, registry, switch

#: The most pages one capture run may fetch (PRD §4A: the cap counts pages, not sources). A profile
#: changes it with ``signal_monitor_max_captures`` in its ``settings.json``; the rest wait for the next run.
DEFAULT_MAX_PAGES = 10
_MAX_PAGES_CEILING = 200

#: How old a cached page Firecrawl may hand back. One day: a stale copy is what a capture must avoid.
MAX_AGE_MS = 86_400_000


@dataclass
class DueReport:
    registry: registry.Registry
    due: list[tuple[registry.Source, str]] = field(default_factory=list)
    fresh: list[registry.Source] = field(default_factory=list)
    inert: list[tuple[registry.Source, str]] = field(default_factory=list)


def _age_days(fetched_at: str, today: datetime.date) -> int | None:
    day = captures.day_of(fetched_at)
    return None if day is None else (today - day).days


def due_sources(
    profile: str,
    product: str | None = None,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    today: datetime.date | None = None,
) -> DueReport:
    today = today or datetime.date.today()
    reg = registry.load_registry(
        profile, product, profiles_root=profiles_root, today=today, content_root=content_root
    )
    report = DueReport(reg)
    sdir = sources_dir_for(profile, content_root or resolve_content_root())
    for s in reg.sources:
        if (why := registry.inert_reason(s, today)) is not None:
            report.inert.append((s, why))
            continue
        caps = captures.usable(s.url, sdir, today)
        age = _age_days(caps[-1].fetched_at, today) if caps else None
        if not caps:
            report.due.append((s, "never captured"))
        elif age is None or age >= s.cadence_days:
            report.due.append(
                (s, "last capture is stale" if age is not None else "capture date unreadable")
            )
        else:
            report.fresh.append(s)
    return report


def max_pages_setting(profile: str, content_root: Path | None = None) -> int:
    """The profile's page budget per capture run, or the default when unset or not a sane count."""
    path = (content_root or resolve_content_root()) / profile / "settings.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))["signal_monitor_max_captures"]
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_MAX_PAGES
    ok = isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_PAGES_CEILING
    return value if ok else DEFAULT_MAX_PAGES


def pages_for_run(report: DueReport, max_pages: int | None = None):
    """``(chosen, deferred)``: the due sources this run fetches, and the ones that wait, in registry order."""
    budget = DEFAULT_MAX_PAGES if max_pages is None else max_pages
    return report.due[:budget], report.due[budget:]


def write_due_manifest(
    profile: str,
    product: str | None = None,
    *,
    run_id: str,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    today: datetime.date | None = None,
    max_pages: int | None = None,
) -> Path | None:
    """Write the manifest for the due sources this run may fetch, or ``None`` when none is due.

    At most ``max_pages`` pages (default: the profile's ``signal_monitor_max_captures``, else 10);
    the rest stay due and wait for the next run. The manifest's cap is the number of pages it lists.
    """
    switch.require_enabled()
    report = due_sources(
        profile, product, content_root=content_root, profiles_root=profiles_root, today=today
    )
    budget = max_pages if max_pages is not None else max_pages_setting(profile, content_root)
    chosen, _deferred = pages_for_run(report, budget)
    urls = [s.url for s, _ in chosen]
    if not urls:
        return None
    return capture_manifest.write_manifest(
        profile=profile,
        run_id=run_id,
        urls=urls,
        cap=len(urls),
        max_age_ms=MAX_AGE_MS,
        content_root=content_root,
    )


def print_report(report: DueReport) -> None:
    reg = report.registry
    print(
        f"Registry read ({reg.origin}, product {reg.product}): {reg.path or 'none: no sources file'}"
    )
    for s, why in report.due:
        print(f"- due: {s.id} ({s.title}), {why} [{s.url}]")
    for s in report.fresh:
        print(f"- fresh: {s.id} ({s.title}) [{s.url}]")
    for s, why in report.inert:
        print(f"- inert: {s.id} ({s.title}), {why} [{s.url}]")
    for problem in reg.grading_problems:
        print(f"- grading: {problem}")
