"""R1.6: ask a person one question about each name that could not be tied to an account.

``review`` renders a sheet; ``review --apply <sheet>`` is plan-first and only writes with
``apply=True``. A decision is recorded as an observation with ``resolved_by=operator``. A domain the
ledger does not have yet is never imported here: that is the ``prospects_state`` import path's job,
and its account faces every gate a new account faces.
"""

from __future__ import annotations

import csv
import datetime
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import prospects_state
from ..competitor_index import competitor_match, load_competitors
from ..paths import resolve_content_root
from . import observations as obs
from . import registry, resolve, state, switch, unresolved
from .members import normalise
from .resolve import norm_domain

QUESTION = "Which company is this?"
CHOICES = ("confirm", "different", "not-a-prospect", "skip")
SHEET_CAP = 50
MAX_AGE_DAYS = 90
_FIELDS = [
    "question",
    "source_id",
    "name",
    "suggested_domain",
    "reason",
    "choices",
    "choice",
    "domain",
]
#: A spreadsheet runs a cell that starts with one of these. A member name comes from a web page.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: str) -> str:
    """Text for a sheet cell: a leading quote stops a spreadsheet from running it as a formula.

    A value that already starts with a quote gets one too, so :func:`_uncell` can always take
    exactly one off and get the original back.
    """
    return "'" + value if value.startswith(_FORMULA_LEAD + ("'",)) else value


def _uncell(value: str) -> str:
    """The inverse of :func:`_cell`, so the name on a filled sheet matches the queue again."""
    return value[1:] if value.startswith("'") else value


_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$")


def obs_dir(profile: str, content_root: Path | None = None) -> Path:
    return (content_root or resolve_content_root()) / profile / "prospects" / "observations"


def _open_entries(
    profile: str,
    product: str | None,
    *,
    content_root: Path | None,
    profiles_root: Path | None,
    today: datetime.date | None,
    strict: bool,
) -> list[dict]:
    """This product's open, recent, still-unresolved entries, oldest first, NOT capped."""
    switch.require_enabled()
    today = today or datetime.date.today()
    reg = registry.load_registry(
        profile, product, profiles_root=profiles_root, today=today, content_root=content_root
    )
    directory = obs_dir(profile, content_root)
    entries = [e for e in unresolved.read(directory) if e.get("product") == reg.product]
    if strict:
        obs.read_all_strict(directory, product=reg.product)  # refuse while a shard is unreadable
    closed = {unresolved.key(e) for e in entries if e.get("status") in ("dismissed", "decided")}
    cutoff = today - datetime.timedelta(days=MAX_AGE_DAYS)
    states: dict[str, dict | None] = {}
    out: list[dict] = []
    seen: set[tuple] = set()
    for e in entries:
        k = unresolved.key(e)
        if e.get("status") in ("dismissed", "decided") or k in closed or k in seen:
            continue
        seen.add(k)
        if e["source_id"] not in states:
            try:
                states[e["source_id"]] = state.load_state(directory, reg.product, e["source_id"])
            except state.StateError:
                states[e["source_id"]] = None
        member = ((states[e["source_id"]] or {}).get("members") or {}).get(k[2]) or {}
        if member.get("account_key"):
            continue
        try:
            if datetime.date.fromisoformat(e.get("first_seen", "")) < cutoff:
                continue
        except ValueError:
            continue
        out.append(e)
    out.sort(key=lambda e: (e["first_seen"], normalise(e["name"])))
    return out


def pending(
    profile: str,
    product: str | None,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    today: datetime.date | None = None,
) -> list[dict]:
    """What a review sheet would ask now: this product's open entries, recent, oldest first, capped."""
    return _open_entries(
        profile,
        product,
        content_root=content_root,
        profiles_root=profiles_root,
        today=today,
        strict=True,
    )[:SHEET_CAP]


def waiting_count(
    profile: str,
    product: str | None,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    today: datetime.date | None = None,
) -> int:
    """How many names wait for a decision, for the status page: the sheet's rule without its cap.

    Unlike :func:`pending` it does not refuse while a shard is unreadable (a count is a read, and
    the status names the shard separately); an unreadable queue still raises.
    """
    return len(
        _open_entries(
            profile,
            product,
            content_root=content_root,
            profiles_root=profiles_root,
            today=today,
            strict=False,
        )
    )


def render_sheet(entries: list[dict]) -> str:
    buf = io.StringIO()
    wr = csv.DictWriter(buf, fieldnames=_FIELDS, lineterminator="\n")
    wr.writeheader()
    for e in entries:
        wr.writerow(
            {
                "question": QUESTION,
                "source_id": _cell(e["source_id"]),
                "name": _cell(e["name"]),
                "suggested_domain": _cell(e.get("candidate_domain", "")),
                "reason": _cell(e.get("reason", "")),
                "choices": " | ".join(CHOICES),
                "choice": "",
                "domain": "",
            }
        )
    return buf.getvalue()


@dataclass
class Decision:
    source_id: str
    name: str
    choice: str
    account_key: str = ""


@dataclass
class ApplyReport:
    decisions: list[Decision] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    written: int = 0


def _decide(row: dict, known: dict[tuple, dict]) -> tuple[Decision | None, str]:
    choice = (row.get("choice") or "").strip().lower()
    if not choice or choice == "skip":
        return None, ""
    name, source_id = _uncell(row.get("name") or ""), _uncell(row.get("source_id") or "")
    entry = known.get((source_id, normalise(name)))
    if entry is None:
        return (
            None,
            f"{name!r} is not waiting on the sheet for this product (already decided, or stale)",
        )
    if choice not in CHOICES:
        return None, f"{name!r}: choice {choice!r} is not one of {list(CHOICES)}"
    if choice == "not-a-prospect":
        return Decision(source_id, name, choice), ""
    raw = (
        _uncell((row.get("domain") or "").strip())
        if choice == "different"
        else entry.get("candidate_domain", "")
    )
    domain = norm_domain(raw)
    if not _DOMAIN_RE.match(domain):
        return None, f"{name!r}: {raw!r} is not a domain, so there is nothing to confirm"
    return Decision(source_id, name, choice, domain), ""


def _parse_sheet_rows(sheet_text: str, known: dict, report: ApplyReport) -> None:
    try:
        rows = list(csv.DictReader(io.StringIO(sheet_text)))
    except csv.Error as exc:
        report.errors.append(f"the sheet cannot be read as a table ({exc})")
        return
    for row in rows:
        decision, error = _decide(row, known)
        if error:
            report.errors.append(error)
        elif decision:
            report.decisions.append(decision)


def _prepare_decision(
    d: Decision,
    known: dict,
    reg: registry.Registry,
    directory: Path,
    competitors: list,
    writer: str,
    today: datetime.date,
    index: dict,
) -> tuple[str, dict, dict | None, str | None]:
    if d.account_key:
        d.account_key = resolve.canonical_key(d.account_key, index)
    entry = known[(d.source_id, normalise(d.name))]
    if d.choice == "not-a-prospect":
        return ("dismiss", entry, None, None)
    source = reg.by_id.get(d.source_id)
    if source is None:
        return ("", {}, None, f"{d.source_id!r} is no longer a source in the registry")
    try:
        snap = state.load_state(directory, reg.product, d.source_id) or {}
    except state.StateError as exc:
        return ("", {}, None, f"state for {d.source_id!r} cannot be read: {exc}")

    rival = competitor_match(d.name, d.account_key, competitors)
    role = "vendor" if (rival or source.member_role == "vendor") else source.member_role

    rec = obs.make_observation(
        kind="source_member",
        product=reg.product,
        source_id=d.source_id,
        source_url=source.url,
        capture_sha256=snap.get("capture_sha256", ""),
        account_key=d.account_key,
        observed=today.isoformat(),
        observed_basis="operator_decision",
        role=role,
        premise_at_write=source.premise,
        writer=writer,
        resolved_by="operator",
        member_name=d.name,
    )
    return ("decide", entry, rec, None)


def apply_sheet(
    profile: str,
    sheet_text: str,
    *,
    product: str | None = None,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    today: datetime.date | None = None,
    now: datetime.datetime | None = None,
    run_id: str = "review",
    apply: bool = False,
) -> ApplyReport:
    """Plan the decisions on a filled-in sheet; write them only when ``apply`` is true."""
    switch.require_enabled()
    today = today or datetime.date.today()
    now = now or datetime.datetime.now(datetime.UTC)
    root = content_root or resolve_content_root()
    report = ApplyReport()
    reg = registry.load_registry(
        profile, product, profiles_root=profiles_root, today=today, content_root=root
    )
    directory = obs_dir(profile, root)
    known = {
        (e["source_id"], unresolved.key(e)[2]): e
        for e in pending(
            profile, product, content_root=root, profiles_root=profiles_root, today=today
        )
    }
    _parse_sheet_rows(sheet_text, known, report)
    if report.errors or not apply or not report.decisions:
        return report
    try:
        writer = obs.writer_id(root / profile / "settings.json", run_id)
    except obs.ObservationError as exc:
        report.errors.append(str(exc))
        return report
    try:
        competitors = load_competitors(profile, profiles_root)
    except (OSError, ValueError) as exc:
        report.errors.append(f"competitors.toml cannot be read ({type(exc).__name__}): {exc}")
        return report

    # Phase 1: validate decisions and prepare records in memory
    prepared: list[tuple[str, dict, dict | None]] = []
    records: list[dict] = []
    index = resolve.ledger_index(prospects_state.load_latest(profile, root).get("items", []))
    for d in report.decisions:
        action, entry, rec, err = _prepare_decision(
            d, known, reg, directory, competitors, writer, today, index
        )
        if err:
            report.errors.append(err)
            continue
        prepared.append((action, entry, rec))
        if rec:
            records.append(rec)

    if report.errors:
        return report

    # Phase 2: atomic write execution
    for action, entry, rec in prepared:
        if action == "dismiss":
            unresolved.dismiss(directory, entry)
        elif action == "decide":
            assert rec is not None
            unresolved.decide(directory, entry, rec["account_key"])

    if records:
        obs.append(directory, obs.shard_name(writer, now), records)
    report.written = len(records)
    if records:
        report.notes.append(
            "A domain the ledger does not have is not added here. To make it a prospect, bring "
            "it in through the prospects_state import path, where it faces every gate."
        )
    return report
