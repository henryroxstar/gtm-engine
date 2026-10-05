"""How old these numbers are — the one place that turns a figures date into words.

`render.py` and `model.py` sit at their §R10 ceilings, so this lives beside them rather than
in them. It holds no state and reads no file: :func:`health.figures_state` has already
resolved the date, the age and the limit from the model's single clock, and this module only
writes the sentences. That split is the point — the header, the page-wide strip and (F3) the
sources table are three wordings of ONE fact, and the fact is not re-derived per surface.

WHY THE COPY IS PINNED HERE AND IN ITS TESTS. Commit ``3ff97acf`` deleted the old-figures
sentence from ``render._warnings_strip`` with no reason in its message, and the trust tests
were rewritten to assert its absence — so for weeks the warning was computed, recorded in
``data-warn``, and said nothing to the reader. A sentence nobody asserts is a sentence that
can be deleted by accident.

§R14: the day count and the limit are arguments, never typed. The wording past the limit is
deliberately "over {limit} days" rather than the age count, because ``age_days`` truncates —
at 7.5 days it reads 7, and "7 days old, over 7 days" is a sentence that argues with itself.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..page_inputs import build_record, inventory_path
from ..page_inputs_guard import RefusedWrite, refuse_links, write_text
from ..page_inputs_io import printable
from ..prospects_consolidate import _prospects_dir
from . import figure_ages, pages
from .check import PagesReport, check_all_pages, check_page, figures_stale_clause  # noqa: F401
from .config import NAME_GLOBS, PAGE_NAME, PROFILE_FILES, TAB_LABELS
from .fingerprint import code_fingerprint
from .format import _e

#: How the header names each :data:`health.FIGURES_STATES` value. Four absences, four
#: sentences: "nothing refreshed yet" is a setup step, "could not be read" is a broken file,
#: "carries no date" is a malformed stamp, and a future date is a clock or a typo. One shared
#: wording would send the reader to the wrong one of the four.
_HEADER_ABSENT = {
    "none": "No sending figures yet",
    "unreadable": "Sending figures couldn't be read",
    "undated": "Sending figures carry no date",
}


def _days(n: int) -> str:
    return f"{n} day{'' if n == 1 else 's'} old"


def figures_phrase(fig: dict) -> str:
    """The header's second clause: what is known about the figures' date, in plain words.

    The age shows on EVERY render, not only past the limit. A warning that appears on one day
    in seven tells a reader nothing on the other six, which is how figures five days old were
    read as current on a page whose model had already flagged them.
    """
    state = fig.get("state")
    # The VALUES are escaped, the copy is not — `_e` over the whole sentence turned our own
    # "couldn't" into `couldn&#x27;t`, which is valid and unreadable. Nothing interpolated here
    # is untrusted (`health.figures_date` returns `date.isoformat()` or None, `age_days` is an
    # int), so this is defence in depth rather than the control (§R5).
    if state == "dated":
        basis = figure_ages.basis_text(fig)
        tail = f", {basis}" if basis else ""
        return (
            f"Sending figures from {_e(str(fig['date']))} ({_days(int(fig['age_days']))}{_e(tail)})"
        )
    if state == "future":
        return (
            f"Sending figures are dated {_e(str(fig['date']))}, which is in the future, "
            "so their age is unknown"
        )
    return _HEADER_ABSENT.get(state, _HEADER_ABSENT["undated"])


def header_line(m: dict) -> str:
    """ "Page built … · Sending figures from … (N days old)" — REPLACES the old "refreshed
    {generated_at}" line rather than sitting beside it.

    One line, because the build stamp alone is the fact a reader is least likely to be misled
    by: the page is always built now, and it is the figures underneath it that go stale. The
    age is frozen at build time and says so in the sources table (F3); ``--check-fresh`` is
    what judges age at CHECK time.
    """
    stamp = _e(str(m.get("generated_at") or ""))
    return f"Page built {stamp} · {figures_phrase(m.get('figures') or {})}"


def figures_strip_sentence(m: dict) -> str:
    """The ``figures-old`` sentence for the page-wide strip — one sentence, one command.

    Two wordings, because there are two findings. A dated snapshot past the limit can name its
    date and the limit it broke. An undated, unparseable or future-dated one cannot name an age
    at all, and printing the raw ``fetched`` value would echo untrusted agent/provider text
    (§R5) as if it were a date. Both end the same way — refresh, then the ONE command — because
    the undated wording used to end "re-render as above" with nothing above it.

    The cause clause is escaped HERE, at the interpolation: it joins sequence ids read from the
    provider's own file, and the card cannot be escaped whole because the rest of the sentence
    carries a real ``<code>`` element.
    """
    fig = m.get("figures") or {}
    cmd = (
        "python -m gtm_core.email_campaign_dashboard "
        f"--profile {m.get('profile', '<profile>')} --refresh-all"
    )
    tail = (
        f"Refresh the sending figures, then run <code>{_e(cmd)}</code>. Sources are listed under "
        f"{_e(TAB_LABELS['ops'])}."
    )
    if fig.get("state") != "dated":
        why = _e(figure_ages.cause_text(fig))
        return f"The sending figures carry no usable date, so their age is unknown{why}. {tail}"
    return (
        f"The sending figures are from {_e(str(fig['date']))}, over {fig['limit']} days before "
        f"this page was built. Sent, replied and bounce numbers below may be behind. {tail}"
    )


def figures_meta(m: dict) -> dict:
    """What ``write_inventory`` records so a LATER check can judge the figures' age.

    Two values, and the second is why this is not just a date. ``figures_fetched`` is the raw
    stamp, recorded verbatim so the check re-derives the age itself rather than trusting an age
    the writer computed. ``figures_present`` is the same predicate the page uses to decide
    whether to warn at all — a tenant that has never refreshed has no figures to BE stale, and
    convicting it would be a gate that can never go green on a new profile.

    Nothing here convicts on a code change: the fingerprint that :func:`page_meta` adds is
    reported by ``fingerprint.code_note``, never counted toward ``ok`` (PRD §9). A section map is
    still deferred.
    """
    snap = ((m.get("status") or {}).get("snapshot")) or {}
    fetched = snap.get("fetched")
    # `figures_present` comes from the ONE resolved `figures_state`, never re-derived from this
    # model's `status`. `scope_to_campaign` filters `status["sequences"]` to the campaign's own
    # ids, so re-deriving it here recorded `False` for a scoped page whose campaign lists no
    # sequence — while `scoped_trust` kept the profile-wide `figures-old` and that same page
    # rendered the strip. The page said the figures were old and its inventory said there were
    # none to judge, which is exactly the surface disagreement §4.5 exists to refuse.
    return {
        "figures_fetched": fetched if isinstance(fetched, str) else None,
        "figures_present": (m.get("figures") or {}).get("state") != "none",
    }


def page_meta(m: dict, package_dir: Path | None = None) -> dict:
    """The whole ``meta`` a dashboard page records: the figures' date and the code that built it.

    Consolidate's own inventory passes no ``meta`` at all, so it is never stamped with a
    fingerprint of a renderer it does not run.
    """
    return {**figures_meta(m), "code_fingerprint": code_fingerprint(package_dir)}


def write_page(out: Path, html: str, spec, model: dict, *, scope: str, slugs, profile: str) -> Path:
    """The one write of a dashboard page AND its inventory, so the rollup and a scoped page cannot
    drift on what they record (digests, profile files, dossier names, ``meta``) — and so the two
    files are a pair.

    Everything that can fail on the INPUTS (a digest of an unreadable file) happens first, from the
    bytes about to be written, before a single output file is touched; a link at either path is
    refused before that. Then the page is replaced, then the sidecar; if the sidecar cannot be
    written the previous page is put back, so a failure leaves the old pair, never a new page
    beside an old inventory (which reads "edited").
    """
    sidecar = inventory_path(out)
    refuse_links(out, sidecar)
    record = build_record(
        out,
        spec,
        scope=scope,
        slugs=slugs,
        profile=profile,
        profile_files=PROFILE_FILES,
        name_globs=NAME_GLOBS,
        meta=page_meta(model),
        page_bytes=html.encode("utf-8"),
    )
    backup = out.with_name(f".{out.name}.prev") if out.exists() else None
    if backup is not None:
        shutil.copyfile(out, backup)
    try:
        write_text(out, html)
        write_text(sidecar, json.dumps(record, indent=2) + "\n")
    except BaseException:
        if backup is not None:
            os.replace(backup, out)  # byte-for-byte, whatever the page held
            backup = None
        raise
    finally:
        if backup is not None:
            backup.unlink(missing_ok=True)
    return out


@dataclass
class Refreshed:
    """What :func:`refresh_pages` did. ``failures`` fail the routine (it exits non-zero);
    ``retired`` are listed only — a retired page is not re-rendered and is not a failure."""

    written: list[Path]
    failures: list[tuple[str, str]] = field(default_factory=list)
    retired: list[tuple[str, str]] = field(default_factory=list)


def refresh_pages(
    profile: str, content_root: Path | None = None, *, stubs: bool = True
) -> Refreshed:
    """Re-render **every page that already exists**, each under its own recorded scope.

    The refresh after a consolidation only ever rendered the profile rollup, and nothing else
    re-renders a scoped page: there is no timer, and the only other automated trigger names the
    unscoped command too. Measured on a live tenant 2026-09-21: of five pages on disk, the rollup
    was fresh and the other four were behind the same ``history.jsonl``.

    What each page is comes from :func:`pages.walk` — the check's own walk — so "this refresh
    exited 0" and "the check is green" cannot disagree about a page. A retired page is listed and
    left alone; a page whose scope cannot be recovered (no sidecar, a damaged one, or a page file
    that is gone) is NAMED with the command that clears it and counts as a failure, rather than
    being rendered under a guessed scope, which would replace one campaign's numbers with
    another's. The rollup is always rendered, so a missing rollup file is the one gone page this
    recreates.

    Each page is rendered inside its own guard. Before F4 the loop had none, so the FIRST failure
    left every later page un-refreshed and silently stale — the exact condition this exists to
    clear. `SystemExit` is caught explicitly beside `Exception`: every refusal in this package
    raises it, and it derives from `BaseException`, so `except Exception` would miss all of them.
    """
    from .render import _validate_profile, render_dashboard  # deferred: render imports this module

    _validate_profile(profile, content_root=content_root)  # a typo is an abort, not a failed page
    base = _prospects_dir(profile, content_root).parent
    found = pages.walk(base, pages.read_manifests(profile, content_root))
    out = Refreshed([])
    # The rollup is rendered first and inside its own guard: a refusal (a linked page, an input
    # that cannot be read) is that page's failure, and the scoped pages are independent of it.
    # `LaneStateUnreadable` is deliberately not caught here — the CLI reports it as its own abort.
    try:
        out.written.append(render_dashboard(profile, content_root, stubs=stubs))
    except (OSError, RefusedWrite, SystemExit) as exc:
        out.failures.append((PAGE_NAME, _why(exc)))
    for f in found:
        if f.page == PAGE_NAME:
            continue  # rendered above
        if f.kind == pages.RETIRED:
            out.retired.append((f.shown, f.why))
        elif f.kind != pages.LIVE:
            out.failures.append((f.shown, pages.cannot_refresh(f, profile)))
        else:
            try:
                out.written.append(
                    render_dashboard(
                        profile,
                        content_root,
                        stubs=False,  # the redirect stubs belong to the rollup, written above
                        campaign=",".join(f.slugs) if f.scope == "campaign" else None,
                        scope=f.scope,
                    )
                )
            except (Exception, SystemExit) as exc:  # noqa: BLE001
                out.failures.append((f.shown, _why(exc)))
    return out


def _why(exc: BaseException) -> str:
    """A failure as one printable line. A refusal carries finished text; anything else is named by
    its type, because "No such file" alone does not say whose."""
    if isinstance(exc, RefusedWrite):
        return str(exc)
    return f"{type(exc).__name__}: {printable(exc)}"


def refresh_all_reporting(
    profile: str, content_root: Path | None = None, *, stubs: bool = True
) -> tuple[list[Path], list[tuple[str, str]]]:
    """:func:`refresh_pages` as ``(written, [(page, why), ...])`` — the two-part shape callers
    and tests have always unpacked. The retired pages are on :func:`refresh_pages`."""
    done = refresh_pages(profile, content_root, stubs=stubs)
    return done.written, done.failures


def refresh_all(
    profile: str, content_root: Path | None = None, *, stubs: bool = True
) -> list[Path]:
    """The paths :func:`refresh_pages` wrote. Failures are DROPPED from this return shape for the
    callers that only want the list; the CLI and `consolidate` use the reporting forms, because a
    refresh that half-worked and reported success is how the scoped pages went stale in the first
    place."""
    return refresh_all_reporting(profile, content_root, stubs=stubs)[0]
