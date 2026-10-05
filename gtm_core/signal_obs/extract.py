"""R1.4-R1.5: read the latest capture of one source and record who is on it, baseline first.

The first extraction only records who is already a member (``source_member``); it never says
anyone "just joined". Later extractions compare with the member set saved, per product, under
``observations/state/`` and call a new name a ``source_join`` only when the source dates its
listings (``timing = "listing_date"``), its members were read by rule rather than by the brain, and
the page itself changed. An empty or failed previous extraction, or a page that lost more than half
its members, refuses the diff: a changed layout must never read as a wave of departures and
arrivals.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from .. import prospects_state
from ..competitor_index import competitor_match, load_competitors
from ..paths import resolve_content_root
from ..signal_sources import index_problems, page_text, sources_dir_for
from . import captures, registry, resolve, state, switch, unresolved
from . import observations as obs
from .captures import MAX_CAPTURE_CHARS
from .members import Member, extract_members, normalise

SHRINK_LIMIT = 0.5
_MEMBERSHIP = ("source_member", "source_join")


@dataclass
class ExtractReport:
    status: str
    reason: str = ""
    written: int = 0
    joins: int = 0
    members: int = 0
    unresolved: int = 0
    refused_short: int = 0
    unverified: int = 0
    domain_dropped: int = 0
    capture_sha256: str = ""

    @property
    def baseline(self) -> bool:
        return self.status == "baseline"


def _refused(reason: str, **kw) -> ExtractReport:
    return ExtractReport("refused", reason=reason, **kw)


def _check_diff(prev: dict, current: dict) -> str:
    """Why a diff against ``prev`` may not be taken, or ``""``."""
    if prev["status"] != "ok" or not prev["members"]:
        return (
            f"the previous extraction was {prev['status']}, so there is nothing sound to compare "
            "with. Re-run with --rebaseline once the page shows its members"
        )
    kept = len(current)
    if kept < len(prev["members"]) * (1 - SHRINK_LIMIT):
        return (
            f"the page lists {kept} members where it listed {len(prev['members'])}: layout "
            "changed? Nothing was recorded. Check the page, then --rebaseline if it is right"
        )
    return ""


@dataclass
class _Ctx:
    reg: registry.Registry
    source: registry.Source
    root: Path
    obs_dir: Path
    writer: str
    prev: dict | None
    competitors: dict


def _prepare(
    profile: str,
    source_id: str,
    *,
    product: str | None,
    profiles_root: Path | None,
    content_root: Path | None,
    today: datetime.date,
    run_id: str,
    proposed: list[Member] | None,
) -> _Ctx | ExtractReport:
    """Everything that can refuse before a capture is opened."""
    if not switch.sources_enabled():
        return ExtractReport("disabled", reason=switch.DISABLED_MESSAGE)
    try:
        reg = registry.load_registry(
            profile, product, profiles_root=profiles_root, today=today, content_root=content_root
        )
    except registry.RegistryError as exc:
        return _refused(str(exc))
    source = reg.by_id.get(source_id)
    if source is None:
        return _refused(f"{source_id!r} is not a source in the registry for product {reg.product}")
    if (why := registry.inert_reason(source, today)) is not None:
        return _refused(f"the source is inert: {why}")
    if source.extractor == "brain_list" and proposed is None:
        return _refused("a brain_list source needs the brain's proposed names (--brain-list)")
    root = content_root or resolve_content_root()
    obs_dir = root / profile / "prospects" / "observations"
    try:
        writer = obs.writer_id(root / profile / "settings.json", run_id)
        prev = state.load_state(obs_dir, reg.product, source_id)
    except (obs.ObservationError, state.StateError, ValueError) as exc:
        return _refused(str(exc))
    try:
        competitors = load_competitors(profile, profiles_root)
    except (OSError, ValueError) as exc:
        # An unreadable competitor list must stop the run: recording a competitor as a buyer is
        # the mistake this list exists to prevent.
        return _refused(f"competitors.toml cannot be read ({type(exc).__name__}): {exc}")
    return _Ctx(reg, source, root, obs_dir, writer, prev, competitors)


def _early_exit(
    ctx: _Ctx, cap, current: dict, text: str, baseline: bool, base: dict
) -> ExtractReport | None:
    """A refusal or an empty/failed baseline, else ``None`` to go on."""
    if len(current) > ctx.source.max_members:
        return _refused(
            f"the page lists {len(current)} members, over max_members {ctx.source.max_members}",
            **base,
        )
    if not current and baseline and ctx.prev and ctx.prev.get("members"):
        return _refused(
            f"the page lists nothing where it listed {len(ctx.prev['members'])} members, so the "
            "saved member set was kept. Check the page before trying again",
            **base,
        )
    if not current and baseline:
        status = "empty" if text.strip() else "failed"
        state.save_state(
            ctx.obs_dir,
            ctx.reg.product,
            ctx.source.id,
            {
                "source_id": ctx.source.id,
                "status": status,
                "capture_sha256": cap.sha256,
                "fetched_at": cap.fetched_at,
                "members": {},
            },
        )
        return ExtractReport(status, reason="the capture yielded no members", **base)
    if not baseline and (why := _check_diff(ctx.prev, current)):
        return _refused(why, **base)
    return None


def run_extract(
    profile: str,
    source_id: str,
    *,
    product: str | None = None,
    profiles_root: Path | None = None,
    content_root: Path | None = None,
    today: datetime.date | None = None,
    now: datetime.datetime | None = None,
    run_id: str = "run",
    proposed: list[Member] | None = None,
    candidates: dict[str, str] | None = None,
    rebaseline: bool = False,
) -> ExtractReport:
    """Extract one source. Appends observations and saves the member set; never raises on data."""
    today = today or datetime.date.today()
    now = now or datetime.datetime.now(datetime.UTC)
    ctx = _prepare(
        profile,
        source_id,
        product=product,
        profiles_root=profiles_root,
        content_root=content_root,
        today=today,
        run_id=run_id,
        proposed=proposed,
    )
    if isinstance(ctx, ExtractReport):
        return ctx
    src = ctx.source
    sources_dir = sources_dir_for(profile, ctx.root)
    if problems := index_problems(sources_dir=sources_dir):
        return _refused(
            f"the capture index has {len(problems)} unreadable line(s), first at line "
            f"{problems[0]['line']}; a newer capture may be hidden, so nothing was read"
        )
    seen = captures.usable(src.url, sources_dir, today)
    if not seen:
        return ExtractReport("no-capture", reason="no capture of this source is on disk")
    cap = seen[-1]
    if len(cap.text) > MAX_CAPTURE_CHARS:
        return ExtractReport(
            "failed",
            reason=f"the capture is too large to be a member list ({len(cap.text)} characters)",
            capture_sha256=cap.sha256,
        )
    try:
        found = extract_members(
            src.extractor, src.extractor_args, cap.text, src.url, proposed=proposed
        )
    except (RecursionError, ValueError) as exc:
        return ExtractReport(
            "failed",
            reason=f"the capture could not be read ({type(exc).__name__})",
            capture_sha256=cap.sha256,
        )
    base = {
        "capture_sha256": cap.sha256,
        "refused_short": len(found.refused_short),
        "unverified": len(found.unverified),
        "domain_dropped": len(found.domain_dropped),
    }
    readable = [m for m in found.members if _writable(m)]
    base["unverified"] += len(found.members) - len(readable)
    current = {normalise(m.name): m for m in readable}
    baseline = ctx.prev is None or rebaseline
    if (early := _early_exit(ctx, cap, current, page_text(cap.text), baseline, base)) is not None:
        return early
    return _record(ctx, cap, current, baseline, candidates, today, now, base)


def _writable(member: Member) -> bool:
    """A name or domain that cannot be encoded (a lone surrogate) could not be written or read."""
    try:
        (member.name + member.domain).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _may_join(ctx: _Ctx, cap, baseline: bool) -> bool:
    """Only a page that dates its listings can say someone joined, and only if it changed."""
    return (
        not baseline
        and ctx.source.timing == "listing_date"
        and ctx.source.extractor != "brain_list"
        and ctx.prev is not None
        and ctx.prev.get("capture_sha256") != cap.sha256
    )


def _record(
    ctx: _Ctx,
    cap,
    current: dict[str, Member],
    baseline: bool,
    candidates: dict[str, str] | None,
    today: datetime.date,
    now: datetime.datetime,
    base: dict,
) -> ExtractReport:
    """Resolve every member, append the new observations, and save the member set."""
    src, product, source_id = ctx.source, ctx.reg.product, ctx.source.id
    try:
        existing = obs.read_all_strict(ctx.obs_dir, product=product)
        queued_before = unresolved.read(ctx.obs_dir)
    except obs.ObservationError as exc:
        return _refused(str(exc))
    have = {
        (o["source_id"], o["account_key"])
        for o in existing.observations
        if o["kind"] in _MEMBERSHIP
    }
    decided = {
        (e["source_id"], unresolved.key(e)[2]): e["account_key"]
        for e in queued_before
        if e.get("status") == "decided" and e.get("product") == product and e.get("account_key")
    }
    index = resolve.ledger_index(
        prospects_state.load_latest(ctx.reg.profile, ctx.root).get("items", [])
    )
    previous = set(ctx.prev["members"]) if not baseline and ctx.prev else set()
    may_join = _may_join(ctx, cap, baseline)
    day = captures.day_of(cap.fetched_at) or today
    new_records: list[dict] = []
    queue: list[dict] = []
    members_state: dict[str, dict] = {}
    joins = 0
    for key, member in current.items():
        if (source_id, key) in decided:
            res = resolve.Resolution(account_key=decided[(source_id, key)], resolved_by="operator")
        else:
            res = resolve.resolve_member(member, index, candidates)
        members_state[key] = {"name": member.name, "account_key": res.account_key}
        if not res.account_key:
            queue.append(
                {
                    "product": product,
                    "source_id": source_id,
                    "name": member.name,
                    "first_seen": day.isoformat(),
                    "candidate_domain": res.candidate_domain,
                    "reason": res.reason,
                }
            )
            continue
        if (source_id, res.account_key) in have:
            continue
        have.add((source_id, res.account_key))
        joined = may_join and key not in previous
        joins += joined
        rival = competitor_match(member.name, res.account_key, ctx.competitors)
        new_records.append(
            obs.make_observation(
                kind="source_join" if joined else "source_member",
                product=product,
                source_id=source_id,
                source_url=src.url,
                capture_sha256=cap.sha256,
                account_key=res.account_key,
                observed=day.isoformat(),
                observed_basis="first_seen_in_capture",
                role="vendor" if rival else src.member_role,
                premise_at_write=src.premise,
                writer=ctx.writer,
                resolved_by=res.resolved_by,
                member_name=member.name if res.resolved_by == "operator" else "",
            )
        )
    if new_records:
        obs.append(ctx.obs_dir, obs.shard_name(ctx.writer, now), new_records)
    queued = unresolved.record(ctx.obs_dir, queue)
    state.save_state(
        ctx.obs_dir,
        product,
        source_id,
        {
            "source_id": source_id,
            "status": "ok",
            "capture_sha256": cap.sha256,
            "fetched_at": cap.fetched_at,
            "members": members_state,
        },
    )
    return ExtractReport(
        "baseline" if baseline else "diff",
        written=len(new_records),
        joins=joins,
        members=len(current),
        unresolved=queued,
        **base,
    )
