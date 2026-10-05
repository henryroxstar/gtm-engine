"""The signal view (R2.1-R2.8): what a row's own fields and the source lists together say.

Computed on read and never stored: not in the ledger columns, a lane file, an export or
``latest.json``. While ``GTM_SIGNAL_SOURCES_ENABLED`` is closed no shard is opened and every
function here answers as if no list existed. While ``GTM_SIGNAL_VIEW_ROUTING`` is closed the
lists are measured (the shadow line) but never change which email a row gets.

    python -m gtm_core.signal_view explain --profile P --product S --domain D [--premise KEY]
    python -m gtm_core.signal_view shadow  --profile P --product S --premise KEY
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from . import run_scope
from .hook_coverage.premise import Premise, load_premise_vocab, premise_unsupported
from .hook_coverage.source_attest import AttestContext, attesting_observation, row_domain
from .merge_hygiene.signal_clean import signal_clause
from .paths import resolve_content_root, resolve_profiles_root
from .signal_obs import observations, switch
from .signal_obs.precision import grading_files
from .signal_obs.registry import load_for_run, locate
from .signal_sources import read_tolerant, sources_dir_for

_JOIN_KIND = "source_join"

TIMING_KINDS: frozenset[str] = frozenset(
    {
        "source_join",
        "job_post",
        "job_change",
        "community_mention",
        "content_engagement",
        "repo_engagement",
        "news_event",
        "topic_intent",
        "none",
    }
)
PREMISE_VIAS: frozenset[str] = frozenset({"evidence", "source", "industry", "seat", "none"})
VIEW_BASES: frozenset[str] = frozenset({"observations", "legacy", "mixed", "refused"})


@dataclass(frozen=True)
class SignalView:
    timing_kind: str = "none"
    timing_observed: str = ""
    timing_source: str = ""
    premise_key: str = ""
    premise_via: str = "none"
    premise_source: str = ""
    relevance_line: str = ""
    view_basis: str = "legacy"


_CACHE: dict[tuple, AttestContext | None] = {}


def _stat(path: Path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (path.name, None)
    return (path.name, st.st_mtime_ns, st.st_size)


def _signature(profile: str, product: str | None, directory: Path, sources: Path, today) -> tuple:
    """Everything the context is read from, as stat results: a run reads each file once, not once per row."""
    shards = sorted(directory.iterdir()) if directory.is_dir() else []
    return (
        profile,
        product,
        str(directory),
        today,
        tuple(_stat(p) for p in shards),
        _stat(sources / "index.jsonl"),
        _stat(sources / "sources.jsonl"),
    )


def attest_context(
    profile: str,
    product: str | None = None,
    *,
    profiles_root: Path | None = None,
    content_root: Path | None = None,
    today: datetime.date | None = None,
) -> AttestContext | None:
    """This run's source lists, loaded once; ``None`` when lists are off or cannot be read.

    An unreadable registry or shard never stops a run: the run goes on without that list, which
    can only make a premise harder to attest, never easier.
    """
    if not switch.sources_enabled():
        return None
    today = today or datetime.date.today()
    root = content_root or resolve_content_root()
    directory = root / profile / "prospects" / "observations"
    sources = sources_dir_for(profile, root)
    _, _, registry_path, _, _ = locate(profile, product, profiles_root=profiles_root)
    try:
        graded = tuple(_stat(p) for p in grading_files(profile, root))
    except (OSError, ValueError):
        graded = ()
    key = (*_signature(profile, product, directory, sources, today), _stat(registry_path), graded)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = _build(profile, product, profiles_root, root, directory, sources, today)
    return _CACHE[key]


def _build(
    profile, product, profiles_root, root, directory, sources, today
) -> AttestContext | None:
    registry, _why = load_for_run(
        profile, product, profiles_root=profiles_root, today=today, content_root=root
    )
    if registry is None or not registry.sources:
        return None
    got = observations.read_all(directory, product=registry.product)
    index = read_tolerant(sources)[0]
    refused = tuple(got.problems)
    return AttestContext(
        product=registry.product,
        registry=registry,
        # Fail closed: a shard that cannot be read may hold a retraction, and the reader drops a
        # shard whole, so with any shard refused no observation is trusted (audit 2026-10-02).
        observations=() if refused else tuple(got.observations),
        refused=refused,
        capture_shas=frozenset(str(e["sha256"]) for e in index if e.get("sha256")),
        today=today,
    )


def routing_context(profile: str, product: str | None = None, **kw) -> AttestContext | None:
    """:func:`attest_context`, but only when lists may also change which email a row gets."""
    if not switch.view_routing_enabled():
        return None
    return attest_context(profile, product, **kw)


def _mine(row: dict, ctx: AttestContext | None) -> list[dict]:
    key = row_domain(row)
    if ctx is None or not key:
        return []
    return [
        o
        for o in ctx.observations
        if o.get("product") == ctx.product
        and str(o.get("account_key") or "").strip().lower().removeprefix("www.") == key
    ]


def derive(
    row: dict,
    ctx: AttestContext | None,
    *,
    premise: Premise | None = None,
    today: datetime.date | None = None,
) -> SignalView:
    """The view of one row. Observations add to it and never subtract; the freshest timing wins."""
    legacy_date = str(row.get("signal_observed") or "").strip()[:10]
    source_url = str(row.get("signal_source_url") or "").strip()
    clause = signal_clause(str(row.get("why_now") or ""))
    timing = (
        ("news_event", legacy_date, source_url) if (legacy_date or source_url) else ("none", "", "")
    )
    raw_feeds = row.get("intent_feeds")
    if isinstance(raw_feeds, str):
        feeds = [f.strip() for f in raw_feeds.split(";") if f.strip()]
    elif isinstance(raw_feeds, (list, tuple)):
        feeds = [str(f).strip() for f in raw_feeds if str(f).strip()]
    else:
        feeds = []
    if feeds:
        intent_date = str(row.get("intent_observed") or "").strip()[:10]
        feed_source = feeds[0] if len(feeds) == 1 else ", ".join(feeds)
        if not legacy_date or (intent_date and intent_date > legacy_date):
            timing = ("topic_intent", intent_date, feed_source)
    mine = _mine(row, ctx)
    for o in mine:
        if o["kind"] == _JOIN_KIND and o["observed"] > timing[1]:
            timing = (_JOIN_KIND, o["observed"], o["source_id"])
    run_today = today or (ctx.today if ctx is not None else datetime.date.today())
    prof = (
        (getattr(ctx.registry, "profile", None) if ctx and hasattr(ctx, "registry") else None)
        or str(row.get("profile") or "").strip()
        or None
    )
    via, source = "none", ""
    if premise is not None:
        won = attesting_observation(row, premise, ctx)
        if won is not None:
            via, source = "source", won["source_id"]
        elif premise.attested_by_seat:
            via = "seat"
        elif not premise_unsupported([row], premise, today=run_today, profile=prof):
            via = "evidence"
    has_legacy = bool(legacy_date or source_url or clause or feeds)
    basis = "legacy" if not mine else "mixed" if has_legacy else "observations"
    if ctx is not None and ctx.refused:
        basis = "refused"
    t_kind = timing[0] if timing[0] in TIMING_KINDS else "none"
    p_via = via if via in PREMISE_VIAS else "none"
    v_basis = basis if basis in VIEW_BASES else "legacy"
    return SignalView(
        timing_kind=t_kind,
        timing_observed=timing[1],
        timing_source=timing[2],
        premise_key=premise.key if premise else "",
        premise_via=p_via,
        premise_source=source,
        relevance_line=clause,
        view_basis=v_basis,
    )


def refusal_lines(ctx: AttestContext | None) -> list[str]:
    """One plain sentence per observation shard that could not be read, for the run header."""
    if ctx is None:
        return []
    return [
        f"Source lists are not used in this run: {p['shard']} cannot be read ({p['why']}). "
        "The rest of the record is unchanged."
        for p in ctx.refused
    ]


def _only_a_list_qualifies(r: dict, premise: Premise, ctx: AttestContext, profile, today) -> bool:
    kw = {"profile": profile, "today": today}
    return bool(premise_unsupported([r], premise, **kw)) and not premise_unsupported(
        [r], premise, source_ctx=ctx, **kw
    )


def shadow_count(
    rows: Sequence[dict],
    premise: Premise,
    ctx: AttestContext | None,
    *,
    profile: str | None = None,
    today: datetime.date | None = None,
) -> int:
    """Rows only a list would qualify for ``premise``: refused today, supported with the list."""
    if ctx is None:
        return 0
    return sum(1 for r in rows if _only_a_list_qualifies(r, premise, ctx, profile, today))


def shadow_total(
    rows: Sequence[dict],
    premises: Sequence[Premise],
    ctx: AttestContext | None,
    *,
    profile: str | None = None,
    today: datetime.date | None = None,
) -> int:
    """Companies a list would newly qualify for ANY of ``premises``, each counted once."""
    if ctx is None:
        return 0
    return sum(
        1 for r in rows if any(_only_a_list_qualifies(r, p, ctx, profile, today) for p in premises)
    )


def shadow_line(count: int) -> str:
    """The one plain line the record section shows while lists are measured but not applied."""
    if count == 0:
        return "No company is on an approved industry list that would change its email yet."
    who = "1 company is" if count == 1 else f"{count} companies are"
    return (
        f"{who} on an approved industry list that would qualify them for a list-specific "
        "email once list evidence is switched on."
    )


def mode_line() -> str:
    if not switch.sources_enabled():
        return "Source lists: off"
    if not switch.view_routing_enabled():
        return "Source lists: collecting only"
    return "Source lists: used for choosing emails"


def ledger_rows(content_root: Path, profile: str) -> list[dict]:
    path = content_root / profile / "prospects" / "latest.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [i for i in items if isinstance(i, dict)]


def main(
    argv: list[str] | None = None,
    *,
    profiles_root: Path | None = None,
    content_root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtm_core.signal_view", description=__doc__)
    parser.add_argument("command", choices=("explain", "shadow"))
    parser.add_argument("--profile", required=True)
    parser.add_argument("--product", default=None)
    parser.add_argument("--domain", default="")
    parser.add_argument("--company", default="")
    parser.add_argument("--premise", default="")
    args = parser.parse_args(argv)
    profiles_root = profiles_root or resolve_profiles_root()
    content_root = content_root or resolve_content_root()
    try:
        product = run_scope.require(args.profile, args.product).product
    except run_scope.ScopeError as exc:
        print(f"Refused: {exc}")
        return 2
    ctx = attest_context(
        args.profile, product, profiles_root=profiles_root, content_root=content_root
    )
    vocab = load_premise_vocab(args.profile, profiles_root, product) if args.premise else {}
    premise = vocab.get(args.premise)
    if args.premise and premise is None:
        print(f"Premise {args.premise!r} is not in this profile's premise-vocab.toml.")
        return 2
    rows = ledger_rows(content_root, args.profile)
    print(mode_line(), file=sys.stderr)
    for line in refusal_lines(ctx):
        print(line, file=sys.stderr)
    if args.command == "shadow":
        if premise is None:
            print("shadow needs --premise.")
            return 2
        print(shadow_line(shadow_count(rows, premise, ctx)))
        return 0
    reg_path = getattr(ctx.registry, "path", None) if ctx and hasattr(ctx, "registry") else None
    if reg_path:
        print(f"Registry: {reg_path}", file=sys.stderr)
    else:
        print("Registry: none", file=sys.stderr)
    wanted = args.domain.strip().lower().removeprefix("www.")
    if args.company:
        from .account_folder import AmbiguousFolder
        from .account_folder import resolve as resolve_account
        from .slugify import slug

        try:
            folder_slug, _ = resolve_account(
                args.company, args.profile, domain=args.domain, content_root=content_root
            )
        except AmbiguousFolder as exc:
            print(f"Ambiguous folder for {args.company!r}: {exc.candidates}", file=sys.stderr)
            return 3
        matched = next(
            (
                r
                for r in rows
                if slug(str(r.get("company") or "")) == folder_slug
                or slug(str(r.get("id") or "")) == folder_slug
                or (wanted and str(r.get("domain") or "").lower().removeprefix("www.") == wanted)
            ),
            None,
        )
        row = (
            matched
            if matched is not None
            else {"company": args.company, "domain": args.domain or wanted}
        )
    else:
        row = next(
            (r for r in rows if str(r.get("domain") or "").lower().removeprefix("www.") == wanted),
            {"domain": wanted},
        )
    print(json.dumps(asdict(derive(row, ctx, premise=premise)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
