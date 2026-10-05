"""R1.1-R1.2: read the source registry from one file, refuse it whole, never borrow another.

The default product's registry is ``knowledge/signal-sources.toml``. A second product's is
``products/<slug>/signal-sources.toml`` and **only** that: without its own file it has no sources,
and an invalid file means no sources, never the profile's. Runtime only reads; the operator edits
the file, or a draft is staged under ``profiles/.staging/`` and promoted.
"""

from __future__ import annotations

import datetime
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import run_scope
from ..paths import _safe_segment, resolve_profiles_root
from .registry_checks import (
    EXTRACTORS,
    PRECISION_RE,
    RegistryError,
    bad,
    check_args,
    check_attestation,
    check_enum,
    check_id,
    check_int,
    check_precision,
    check_unique_ids,
    check_url,
    load_premises,
    nonempty,
    shown,
)

FILENAME = "signal-sources.toml"
PRECISION_BAR = 0.7
#: A hand-checked sample smaller than this says nothing about a list: 3/3 is not 100%.
MIN_SAMPLE = 10
MAX_EXPIRY_DAYS = 180
MIN_CADENCE_DAYS = 7

KINDS = frozenset(
    {
        "member_directory",
        "regulator_register",
        "job_board_query",
        "partner_directory",
        "agent_registry",
    }
)
MEMBER_ROLES = frozenset({"buyer", "vendor", "mixed"})
TIMINGS = frozenset({"listing_date", "none"})
_REQUIRED = (
    "id",
    "title",
    "url",
    "kind",
    "claim_gap",
    "member_role",
    "timing",
    "cadence_days",
    "extractor",
    "max_members",
    "expires_on",
    "owner",
)
_OPTIONAL = ("premise", "precision", "extractor_args", "notes", "attestation", "agentic_basis")
_TEXT_FIELDS = (
    "id", "title", "url", "kind", "claim_gap", "member_role", "timing", "extractor", "owner",
    "premise", "precision", "notes", "attestation", "agentic_basis",
)  # fmt: skip


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    url: str
    kind: str
    premise: str
    claim_gap: str
    precision: str
    member_role: str
    timing: str
    cadence_days: int
    extractor: str
    extractor_args: dict
    max_members: int
    expires_on: datetime.date
    owner: str
    notes: str
    attestation: str = "pool"
    agentic_basis: str = ""
    #: (hits, graded) from the graded screen files; set by ``load_registry``, never by the file.
    measured: tuple[int, int] | None = None


@dataclass(frozen=True)
class Registry:
    profile: str
    product: str
    path: Path | None
    origin: str
    sources: tuple[Source, ...] = ()
    active: tuple[Source, ...] = ()
    #: The profile file a product file stands in front of, named because it is silent otherwise.
    shadows: Path | None = None
    by_id: dict = field(default_factory=dict, compare=False)
    #: Grading files that were not read or counted; those sources fall back to their stated prior.
    grading_problems: tuple[str, ...] = ()


def product_slug(profile: str, scope: run_scope.RunScope, profiles_root: Path) -> str:
    """The product an observation is stamped with: the scope's, else the profile's default.

    ``RunScope`` carries no default slug (it is ``None`` until a second product exists), so the
    default is read from the same facts ``run_scope`` decides with. A company that declares no
    product at all stamps its own name: a one-product company's product is itself.
    """
    if scope.product:
        return scope.product
    default = run_scope._load_facts(profile, profiles_root).default
    return default or profile


def locate(
    profile: str, product: str | None, *, profiles_root: Path | None = None
) -> tuple[run_scope.RunScope, str, Path, str, Path | None]:
    """``(scope, product, registry path, origin, shadowed profile file)`` for a run."""
    root = profiles_root or resolve_profiles_root()
    scope = run_scope.require(profile, product, profiles_root=root)
    base = root / _safe_segment(profile, "profile")
    profile_file = base / "knowledge" / FILENAME
    slug = product_slug(profile, scope, root)
    if scope.writes_as_second:
        own = base / "products" / _safe_segment(scope.product or "", "product") / FILENAME
        return scope, slug, own, "product file", (profile_file if profile_file.is_file() else None)
    return scope, slug, profile_file, "profile file", None


def _date(val: object) -> datetime.date | None:
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    if isinstance(val, str):
        try:
            return datetime.date.fromisoformat(val)
        except ValueError:
            return None
    return None


def _check_source(path: Path, raw: object, today: datetime.date, premises: set[str]) -> Source:
    if not isinstance(raw, dict):
        raise RegistryError(
            f"{shown(path)}: a [[source]] entry is not a table.",
            "each source must be a table.",
            "use [[source]] with key = value lines",
        )
    sid = str(raw.get("id", "?"))[:60]
    for key in _TEXT_FIELDS:
        if key in raw and not isinstance(raw[key], str):
            raise bad(
                path, sid, key, f"{key} must be text, got {type(raw[key]).__name__}.",
                f"write {key} as a quoted string",
            )  # fmt: skip
    unknown = sorted(set(raw) - set(_REQUIRED) - set(_OPTIONAL))
    if unknown:
        raise bad(
            path,
            sid,
            unknown[0],
            f"{unknown[0]!r} is not a registry field.",
            f"remove {unknown[0]!r}",
        )
    for key in _REQUIRED:
        if key not in raw or raw[key] in ("", None):
            raise bad(path, sid, key, f"{key} is required.", f"add {key}")
    check_id(path, sid, raw["id"])
    for key in ("title", "claim_gap", "owner"):
        if not nonempty(raw[key]):
            raise bad(path, sid, key, f"{key} must be text.", f"write a sentence for {key}")
    check_url(path, sid, raw["url"])
    check_enum(path, sid, raw, "kind", KINDS)
    check_enum(path, sid, raw, "member_role", MEMBER_ROLES)
    check_enum(path, sid, raw, "timing", TIMINGS)
    check_enum(path, sid, raw, "extractor", EXTRACTORS)
    premise = raw.get("premise", "")
    if premise and premise not in premises:
        raise bad(
            path,
            sid,
            "premise",
            f"{premise!r} is not in premise-vocab.toml.",
            "name a premise that file declares, or leave it empty",
        )
    expires = _date(raw["expires_on"])
    if expires is None:
        raise bad(
            path,
            sid,
            "expires_on",
            f"{raw['expires_on']!r} is not a date.",
            "write it as YYYY-MM-DD",
        )
    if expires > today + datetime.timedelta(days=MAX_EXPIRY_DAYS):
        raise bad(
            path,
            sid,
            "expires_on",
            f"a source may run at most {MAX_EXPIRY_DAYS} days before someone re-reads it.",
            f"set a date within {MAX_EXPIRY_DAYS} days of today",
        )
    attestation, agentic_basis = check_attestation(path, sid, raw)
    return Source(
        id=raw["id"],
        title=raw["title"].strip(),
        url=raw["url"],
        kind=raw["kind"],
        premise=premise,
        claim_gap=raw["claim_gap"].strip(),
        precision=check_precision(path, sid, raw),
        member_role=raw["member_role"],
        timing=raw["timing"],
        cadence_days=check_int(path, sid, raw, "cadence_days", MIN_CADENCE_DAYS),
        extractor=raw["extractor"],
        extractor_args=check_args(path, sid, raw),
        max_members=check_int(path, sid, raw, "max_members", 1),
        expires_on=expires,
        owner=raw["owner"].strip(),
        notes=str(raw.get("notes", "")),
        attestation=attestation,
        agentic_basis=agentic_basis,
    )


def inert_reason(source: Source, today: datetime.date) -> str | None:
    """Why a valid source does nothing yet, or ``None`` when it is live."""
    m = PRECISION_RE.fullmatch(source.precision)
    if not m:
        return "precision not yet sampled"
    if int(m.group(2)) < MIN_SAMPLE:
        return f"precision {source.precision} is from fewer than {MIN_SAMPLE} checked members"
    if int(m.group(1)) / int(m.group(2)) < PRECISION_BAR:
        return f"precision {source.precision} is below {PRECISION_BAR:.0%}"
    if source.measured is not None:
        hits, graded = source.measured
        if graded >= MIN_SAMPLE and hits / graded < PRECISION_BAR:
            return (
                f"the graded members say {hits}/{graded} run agents, below {PRECISION_BAR:.0%}, "
                f"though precision is stated as {source.precision}"
            )
    if source.expires_on < today:
        return f"expired on {source.expires_on.isoformat()}"
    return None


def parse(path: Path, *, today: datetime.date, premises: set[str]) -> tuple[Source, ...]:
    """Every source in ``path``, or :class:`RegistryError` for the whole file."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError, RecursionError) as exc:
        raise RegistryError(
            f"{shown(path)} could not be read as TOML.",
            f"{type(exc).__name__}: {exc}",
            "fix the syntax, or restore the file from the content repo",
        ) from None
    extra = sorted(set(data) - {"schema", "source"})
    if extra:
        raise RegistryError(
            f"{shown(path)} has an unknown key {extra[0]!r}.",
            "only schema and [[source]] entries are allowed.",
            f"remove {extra[0]!r}",
        )
    schema = data.get("schema", 1)
    if type(schema) is not int or schema != 1:  # a bool is an int to Python, and is not a version
        raise RegistryError(
            f"{shown(path)} is schema {schema!r}.",
            "this reader knows schema 1.",
            "set schema = 1, or update the reader",
        )
    raws = data.get("source", [])
    if not isinstance(raws, list):
        raise RegistryError(
            f"{shown(path)} has a source that is not a list of [[source]] tables.",
            f"source is {type(raws).__name__}, and each source must be its own table.",
            "write each source under its own [[source]] header",
        )
    sources = tuple(_check_source(path, raw, today, premises) for raw in raws)
    check_unique_ids(path, sources)
    return sources


def _with_measured(sources: tuple[Source, ...], profile: str, content_root: Path | None):
    """Attach each source's graded tally. A grading file that cannot be read leaves the prior alone."""
    from .precision import measure

    got = measure(profile, content_root)
    return (
        tuple(
            replace(s, measured=(t.hits, t.graded)) if (t := got.by_source.get(s.id)) else s
            for s in sources
        ),
        tuple(got.problems),
    )


def load_registry(
    profile: str,
    product: str | None = None,
    *,
    profiles_root: Path | None = None,
    today: datetime.date | None = None,
    premises: set[str] | None = None,
    content_root: Path | None = None,
) -> Registry:
    """Read the registry for a run. Raises :class:`RegistryError` (whole file) or a scope error."""
    root = profiles_root or resolve_profiles_root()
    scope, slug, path, origin, shadows = locate(profile, product, profiles_root=root)
    today = today or datetime.date.today()
    if not path.is_file():
        return Registry(profile=profile, product=slug, path=None, origin=origin, shadows=shadows)
    if premises is None:
        premises = load_premises(profile, root, scope.product)
    sources, grading_problems = _with_measured(
        parse(path, today=today, premises=premises), profile, content_root
    )
    active = tuple(s for s in sources if inert_reason(s, today) is None)
    return Registry(
        profile=profile,
        product=slug,
        path=path,
        origin=origin,
        sources=sources,
        active=active,
        shadows=shadows,
        by_id={s.id: s for s in sources},
        grading_problems=grading_problems,
    )


def load_for_run(
    profile: str, product: str | None = None, **kw
) -> tuple[Registry | None, str | None]:
    """A run's view of the registry: it never aborts and never falls back.

    An invalid registry yields ``(None, one sentence)`` for the run header: the run goes on
    without sources, and the sentence names the file and the fix. A scope refusal is not a
    registry problem and propagates.
    """
    try:
        return load_registry(profile, product, **kw), None
    except RegistryError as exc:
        _, _, path, _, _ = locate(profile, product, profiles_root=kw.get("profiles_root"))
        what = " ".join(exc.what.split())
        return None, (
            f"Sources are off for this run: {what} The run goes on without sources. "
            f"Fix: {' '.join(exc.fix.split())}. File: {shown(path)}."
        )
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        # A registry that is wrong in a way no check named yet still must not stop a run.
        return None, (
            f"Sources are off for this run: the sources file could not be read "
            f"({type(exc).__name__}). The run goes on without sources."
        )


def check(
    profile: str,
    product: str | None = None,
    *,
    profiles_root: Path | None = None,
    today: datetime.date | None = None,
    premises: set[str] | None = None,
    sources_dir: Path | None = None,
) -> int:
    """Validate the registry and print it. Writes nothing. ``0`` valid, ``2`` refused."""
    try:
        got = load_registry(
            profile, product, profiles_root=profiles_root, today=today, premises=premises
        )
    except RegistryError as exc:
        print(f"What: {exc.what}\nWhy: {exc.why}\nFix: {exc.fix}")
        return 2
    today = today or datetime.date.today()
    if got.path is None:
        _, _, path, _, _ = locate(profile, product, profiles_root=profiles_root)
        print(f"No registry: {path} does not exist, so this run has no sources.")
        return 0
    print(f"Registry ({got.origin}, product {got.product}): {got.path}")
    if got.shadows is not None:
        print(f"  It stands in for {got.shadows}, which this product does not use.")
    for s in got.sources:
        why = inert_reason(s, today)
        state = "live" if why is None else f"inert ({why})"
        print(f"- {s.id}: {s.title} [{s.kind}, every {s.cadence_days} days, {state}]")
        print(f"    url: {s.url}")
        print(f"    {s.claim_gap}")
        print(
            f"    attests: {s.attestation}" + (f" ({s.agentic_basis})" if s.agentic_basis else "")
        )
        if sources_dir is not None:
            _print_preview(s, sources_dir)
    return 0


def _print_preview(s: Source, sources_dir: Path, *, show: int = 5) -> None:
    """Who the latest capture names, before anything is written (R1.2)."""
    from . import captures
    from .members import extract_members

    caps = captures.usable(s.url, sources_dir, datetime.date.today())
    if not caps:
        print("    preview: no capture yet")
        return
    cap = caps[-1]
    if len(cap.text) > captures.MAX_CAPTURE_CHARS:
        print(f"    preview: captured {cap.fetched_at[:10]}; the capture is too large to read")
        return
    if s.extractor == "brain_list":
        print(
            f"    preview: captured {cap.fetched_at[:10]}; a brain_list source is read by the brain"
        )
        return
    try:
        found = extract_members(s.extractor, s.extractor_args, cap.text, s.url)
    except (RecursionError, ValueError):
        print(f"    preview: captured {cap.fetched_at[:10]}; the page could not be read")
        return
    names = ", ".join(m.name for m in found.members[:show])
    more = f", and {len(found.members) - show} more" if len(found.members) > show else ""
    print(
        f"    preview: captured {cap.fetched_at[:10]}; {len(found.members)} members: {names}{more}"
    )
